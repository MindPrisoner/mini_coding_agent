import argparse
import difflib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client import call_llm
from debug_agent import run_debug_agent
from patch_prompts import PATCH_SYSTEM_PROMPT, build_patch_prompt
from agent_tools.file_tools import list_files, read_file, search_text


def safe_json_loads(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    return json.loads(text)


def load_error_text(error_file: str | None, error_text: str | None) -> str:
    if error_file:
        return Path(error_file).read_text(encoding="utf-8")
    if error_text:
        return error_text
    raise ValueError("必须提供 --error-file 或 --error-text")


def compact_json(data: Any, max_chars: int = 4000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + "\n... [内容过长，已截断]"
    return text


def guess_related_keywords(error_text: str, debug_result: dict) -> list[str]:
    keywords = []

    if "ModuleNotFoundError" in error_text:
        keywords.append("import")

    final_answer = debug_result.get("final_answer", {})
    root_cause = final_answer.get("root_cause", "")
    fix_suggestion = " ".join(final_answer.get("fix_suggestion", []))

    for token in ["utils", "agent_stage", "mypkg", "main.py"]:
        if token in error_text or token in root_cause or token in fix_suggestion:
            keywords.append(token)

    return list(dict.fromkeys(keywords))


def collect_related_files(project_dir: str, error_text: str, debug_result: dict, max_files: int = 5) -> list[dict]:
    files_result = list_files(project_dir)
    all_files = files_result.get("files", [])

    selected_files = []

    for file_path in all_files:
        if file_path in error_text:
            selected_files.append(file_path)

    for keyword in guess_related_keywords(error_text, debug_result):
        search_result = search_text(project_dir=project_dir, keyword=keyword, max_results=10)
        for item in search_result.get("matches", []):
            file_path = item["file"]
            if file_path not in selected_files:
                selected_files.append(file_path)

    for fallback in ["main.py", "README.md"]:
        if fallback in all_files and fallback not in selected_files:
            selected_files.append(fallback)

    selected_files = selected_files[:max_files]

    related = []
    for file_path in selected_files:
        content_result = read_file(project_dir=project_dir, file_path=file_path, max_chars=4000)
        related.append(content_result)

    return related


def validate_patch_result(data: dict) -> tuple[bool, str]:
    if "need_patch" not in data:
        return False, "缺少 need_patch 字段"

    if not isinstance(data["need_patch"], bool):
        return False, "need_patch 必须是 bool"

    required_keys = ["reason", "target_files", "patch", "verify_command"]
    missing = [key for key in required_keys if key not in data]
    if missing:
        return False, f"缺少字段: {missing}"

    if data["need_patch"] and not data["patch"].strip():
        return False, "need_patch=true 时 patch 不能为空"

    return True, ""


def strip_project_prefix(path: str, project_dir: str) -> str:
    path = path.strip()
    project_prefix = Path(project_dir).as_posix().strip("./")

    for prefix in [project_prefix + "/", "./" + project_prefix + "/"]:
        if path.startswith(prefix):
            return path[len(prefix):]

    return path


def normalize_patch_paths(patch: str, project_dir: str) -> str:
    project_prefix = Path(project_dir).as_posix().strip("./")

    replacements = {
        f"--- a/{project_prefix}/": "--- a/",
        f"+++ b/{project_prefix}/": "+++ b/",
        f"--- {project_prefix}/": "--- ",
        f"+++ {project_prefix}/": "+++ ",
        f"diff --git a/{project_prefix}/": "diff --git a/",
        f" b/{project_prefix}/": " b/",
    }

    normalized = patch
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)

    return normalized.rstrip() + "\n"


def normalize_verify_command(command: str) -> str:
    command = command.strip()

    if "&&" in command:
        parts = [p.strip() for p in command.split("&&")]
        command = parts[-1]

    if command.startswith("cd "):
        return ""

    return command


def extract_simple_replacements_from_patch(patch: str) -> list[tuple[str, str]]:
    """
    从 LLM 生成的 patch 中提取简单的单行替换。

    例如：
    -from utils import hello
    +from mypkg.utils import hello

    会提取为：
    ("from utils import hello", "from mypkg.utils import hello")
    """
    removed_lines = []
    added_lines = []

    for line in patch.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("-"):
            removed_lines.append(line[1:])

        elif line.startswith("+"):
            added_lines.append(line[1:])

    pairs = []

    if len(removed_lines) == len(added_lines):
        for old, new in zip(removed_lines, added_lines):
            if old.strip() and new.strip():
                pairs.append((old, new))

    return pairs


def rebuild_patch_with_context(project_dir: str, target_files: list[str], patch_text: str) -> str:
    """
    根据 LLM patch 中的修改意图，重新生成带上下文的标准 unified diff。

    这一步是为了避免 LLM 生成的 patch 虽然语义正确，
    但 git apply 无法稳定应用。
    """
    if not target_files:
        return patch_text

    if len(target_files) != 1:
        return patch_text

    target_file = target_files[0]
    project_root = Path(project_dir).resolve()
    file_path = project_root / target_file

    if not file_path.exists() or not file_path.is_file():
        return patch_text

    original_text = file_path.read_text(encoding="utf-8", errors="ignore")
    modified_text = original_text

    replacements = extract_simple_replacements_from_patch(patch_text)

    if not replacements:
        return patch_text

    changed = False

    for old, new in replacements:
        if old in modified_text:
            modified_text = modified_text.replace(old, new, 1)
            changed = True

    if not changed or modified_text == original_text:
        return patch_text

    original_lines = original_text.splitlines(keepends=True)
    modified_lines = modified_text.splitlines(keepends=True)

    diff_lines = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{target_file}",
        tofile=f"b/{target_file}",
        n=3,
    )

    rebuilt_patch = "".join(diff_lines)

    if not rebuilt_patch.endswith("\n"):
        rebuilt_patch += "\n"

    return rebuilt_patch


def normalize_patch_result(patch_result: dict, project_dir: str) -> dict:
    result = dict(patch_result)

    target_files = result.get("target_files", [])
    normalized_target_files = [
        strip_project_prefix(path, project_dir)
        for path in target_files
    ]

    result["target_files"] = normalized_target_files

    raw_patch = normalize_patch_paths(result.get("patch", ""), project_dir)

    if result.get("need_patch"):
        raw_patch = rebuild_patch_with_context(
            project_dir=project_dir,
            target_files=normalized_target_files,
            patch_text=raw_patch,
        )

    result["patch"] = normalize_patch_paths(raw_patch, project_dir)
    result["verify_command"] = normalize_verify_command(result.get("verify_command", ""))

    return result


def make_patch_filename(project_dir: str) -> str:
    project_name = Path(project_dir).name or "project"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{project_name}_{timestamp}.patch"


def save_patch_file(patch_text: str, patch_dir: str = "patches", patch_name: str | None = None) -> str:
    output_dir = Path(patch_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if patch_name is None:
        patch_name = datetime.now().strftime("patch_%Y%m%d_%H%M%S.patch")

    patch_path = output_dir / patch_name
    patch_path.write_text(patch_text, encoding="utf-8")

    return str(patch_path)


def build_manual_commands(project_dir: str, patch_path: str, verify_command: str) -> dict:
    project_abs = Path(project_dir).resolve()
    patch_abs = Path(patch_path).resolve()

    return {
        "apply_patch": f"cd {project_abs} && git apply {patch_abs}",
        "verify": f"cd {project_abs} && {verify_command}" if verify_command else "",
        "revert_patch": f"cd {project_abs} && git apply -R {patch_abs}",
    }


def run_patch_agent(
    project_dir: str,
    error_text: str,
    max_debug_steps: int = 5,
    save_patch: bool = False,
    patch_dir: str = "patches",
) -> dict:
    print("\n========== 先运行 Debug Agent ==========")
    debug_result = run_debug_agent(
        project_dir=project_dir,
        error_text=error_text,
        max_steps=max_debug_steps,
    )

    print("\n========== 收集相关文件 ==========")
    related_files = collect_related_files(
        project_dir=project_dir,
        error_text=error_text,
        debug_result=debug_result,
    )

    print(compact_json(related_files, max_chars=3000))

    user_prompt = build_patch_prompt(
        error_text=error_text,
        debug_result=debug_result,
        related_files=related_files,
        project_dir=project_dir,
    )

    print("\n========== 生成 Patch 建议 ==========")
    raw_output = call_llm(
        system_prompt=PATCH_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        timeout=60,
        max_retries=3,
    )

    print(raw_output)

    try:
        patch_result = safe_json_loads(raw_output)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": f"模型输出不是合法 JSON: {e}",
            "raw_output": raw_output,
            "debug_result": debug_result,
            "related_files": related_files,
        }

    patch_result = normalize_patch_result(patch_result, project_dir=project_dir)

    ok, error = validate_patch_result(patch_result)
    if not ok:
        return {
            "ok": False,
            "error": error,
            "patch_result": patch_result,
            "debug_result": debug_result,
            "related_files": related_files,
        }

    saved_patch_path = None
    manual_commands = None

    if save_patch and patch_result.get("need_patch"):
        patch_name = make_patch_filename(project_dir)
        saved_patch_path = save_patch_file(
            patch_text=patch_result["patch"],
            patch_dir=patch_dir,
            patch_name=patch_name,
        )
        manual_commands = build_manual_commands(
            project_dir=project_dir,
            patch_path=saved_patch_path,
            verify_command=patch_result.get("verify_command", ""),
        )

    return {
        "ok": True,
        "debug_result": debug_result,
        "related_files": related_files,
        "patch_result": patch_result,
        "saved_patch_path": saved_patch_path,
        "manual_commands": manual_commands,
    }


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Patch Agent")
    parser.add_argument("--project", required=True, help="要分析的项目目录")
    parser.add_argument("--error-file", help="报错文本文件")
    parser.add_argument("--error-text", help="直接传入报错文本")
    parser.add_argument("--max-debug-steps", type=int, default=5)
    parser.add_argument("--save-patch", action="store_true", help="是否把 patch 保存到文件")
    parser.add_argument("--patch-dir", default="patches", help="patch 保存目录")
    args = parser.parse_args()

    error_text = load_error_text(args.error_file, args.error_text)

    result = run_patch_agent(
        project_dir=args.project,
        error_text=error_text,
        max_debug_steps=args.max_debug_steps,
        save_patch=args.save_patch,
        patch_dir=args.patch_dir,
    )

    print("\n========== 最终 Patch Agent 结果 ==========")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
