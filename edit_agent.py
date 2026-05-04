import argparse
import difflib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client import call_llm
from debug_agent import run_debug_agent
from edit_prompts import EDIT_SYSTEM_PROMPT, build_edit_prompt
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


def is_python_file(file_path: str) -> bool:
    return file_path.endswith(".py")


def add_unique(items: list[str], item: str):
    if item and item not in items:
        items.append(item)


def is_schema_related_error(error_text: str, debug_result: dict) -> bool:
    joined = json.dumps(debug_result, ensure_ascii=False, default=str)
    signals = [
        "ValidationError",
        "pydantic",
        "QueryResponse",
        "retrieved",
        "BaseModel",
        "string_type",
        "input_type=list",
    ]
    return any(s in error_text or s in joined for s in signals)


def guess_related_keywords(error_text: str, debug_result: dict) -> list[str]:
    keywords = []

    if "ModuleNotFoundError" in error_text:
        keywords.extend(["import", "ModuleNotFoundError"])

    if "Checkpointer requires" in error_text:
        keywords.extend(["graph.invoke", "thread_id", "configurable", "config"])

    if is_schema_related_error(error_text, debug_result):
        keywords.extend([
            "class QueryResponse",
            "QueryResponse",
            "retrieved",
            "RetrievedChunk",
            "BaseModel",
        ])

    final_answer = debug_result.get("final_answer", {})
    root_cause = final_answer.get("root_cause", "")
    fix_suggestion = " ".join(final_answer.get("fix_suggestion", []))

    for token in [
        "utils",
        "mypkg",
        "main.py",
        "agent_stage",
        "agent_cli.py",
        "graph.invoke",
        "thread_id",
        "config",
        "schema",
        "schemas.py",
        "QueryResponse",
        "RetrievedChunk",
        "retrieved",
        "ValidationError",
    ]:
        if token in error_text or token in root_cause or token in fix_suggestion:
            keywords.append(token)

    return list(dict.fromkeys(keywords))


def collect_related_files(project_dir: str, error_text: str, debug_result: dict, max_files: int = 8) -> list[dict]:
    files_result = list_files(project_dir)
    all_files = files_result.get("files", [])

    selected_files = []

    schema_related = is_schema_related_error(error_text, debug_result)

    if schema_related and "schemas.py" in all_files:
        add_unique(selected_files, "schemas.py")

    for file_path in all_files:
        if is_python_file(file_path) and file_path in error_text:
            add_unique(selected_files, file_path)

    for keyword in guess_related_keywords(error_text, debug_result):
        search_result = search_text(project_dir=project_dir, keyword=keyword, max_results=20)
        for item in search_result.get("matches", []):
            file_path = item["file"]

            if not is_python_file(file_path):
                continue

            if schema_related:
                if file_path == "schemas.py":
                    add_unique(selected_files, file_path)
                elif "schema" in file_path.lower() or "model" in file_path.lower():
                    add_unique(selected_files, file_path)
                elif keyword in {"QueryResponse", "RetrievedChunk", "retrieved", "class QueryResponse"}:
                    add_unique(selected_files, file_path)
            else:
                add_unique(selected_files, file_path)

    for fallback in [
        "schemas.py",
        "agent_stage/agent_cli.py",
        "main.py",
        "cli.py",
        "api.py",
        "pipeline.py",
        "README.md",
    ]:
        if fallback in all_files and fallback not in selected_files:
            if schema_related and fallback in {"schemas.py", "api.py", "pipeline.py"}:
                add_unique(selected_files, fallback)
            elif not schema_related:
                add_unique(selected_files, fallback)

    selected_files = selected_files[:max_files]

    related = []
    for file_path in selected_files:
        content_result = read_file(project_dir=project_dir, file_path=file_path, max_chars=8000)
        related.append(content_result)

    return related


def normalize_verify_command(command: str) -> str:
    command = command.strip()

    if "&&" in command:
        parts = [p.strip() for p in command.split("&&")]
        command = parts[-1]

    if command.startswith("cd "):
        return ""

    if command.startswith("python -m agent_cli"):
        command = command.replace("python -m agent_cli", "python -m agent_stage.agent_cli", 1)

    if command.startswith("python agent_stage/agent_cli.py"):
        command = command.replace("python agent_stage/agent_cli.py", "python -m agent_stage.agent_cli", 1)

    if "your_module" in command:
        return ""

    if command.startswith("grep "):
        return ""

    return command


def validate_edit_result(data: dict) -> tuple[bool, str]:
    if "need_patch" not in data:
        return False, "缺少 need_patch 字段"

    if not isinstance(data["need_patch"], bool):
        return False, "need_patch 必须是 bool"

    required_keys = ["reason", "edits", "verify_command"]
    missing = [key for key in required_keys if key not in data]
    if missing:
        return False, f"缺少字段: {missing}"

    if not isinstance(data["edits"], list):
        return False, "edits 必须是 list"

    if data["need_patch"] and not data["edits"]:
        return False, "need_patch=true 时 edits 不能为空"

    for idx, edit in enumerate(data["edits"], 1):
        for key in ["file_path", "operation", "old_text", "new_text"]:
            if key not in edit:
                return False, f"第 {idx} 个 edit 缺少字段: {key}"

        if edit["operation"] != "replace":
            return False, f"第 {idx} 个 edit operation 目前只支持 replace"

        if not edit["old_text"]:
            return False, f"第 {idx} 个 edit old_text 不能为空"

    return True, ""


def apply_structured_edits(project_dir: str, edits: list[dict]) -> tuple[bool, str, dict[str, tuple[str, str]]]:
    project_root = Path(project_dir).resolve()
    changed_files: dict[str, tuple[str, str]] = {}
    file_text_cache: dict[str, str] = {}

    for idx, edit in enumerate(edits, 1):
        file_path = edit["file_path"]
        operation = edit["operation"]
        old_text = edit["old_text"]
        new_text = edit["new_text"]

        if operation != "replace":
            return False, f"第 {idx} 个 edit operation 不支持: {operation}", {}

        target = (project_root / file_path).resolve()

        if not str(target).startswith(str(project_root)):
            return False, f"第 {idx} 个 edit 试图修改项目目录外文件: {file_path}", {}

        if not target.exists() or not target.is_file():
            return False, f"第 {idx} 个 edit 目标文件不存在: {file_path}", {}

        if file_path not in file_text_cache:
            file_text_cache[file_path] = target.read_text(encoding="utf-8", errors="ignore")

        current_text = file_text_cache[file_path]

        if old_text not in current_text:
            return False, (
                f"第 {idx} 个 edit 的 old_text 在文件中找不到: {file_path}。"
                "这说明模型生成的编辑意图不可靠，不能生成 patch。"
            ), {}

        file_text_cache[file_path] = current_text.replace(old_text, new_text, 1)

    for file_path, modified_text in file_text_cache.items():
        original_text = (project_root / file_path).read_text(encoding="utf-8", errors="ignore")
        if modified_text != original_text:
            changed_files[file_path] = (original_text, modified_text)

    if not changed_files:
        return False, "edits 没有产生任何实际修改", {}

    return True, "", changed_files


def build_patch_from_changed_files(changed_files: dict[str, tuple[str, str]]) -> str:
    patch_parts = []

    for file_path, (original_text, modified_text) in changed_files.items():
        original_lines = original_text.splitlines()
        modified_lines = modified_text.splitlines()

        diff_lines = difflib.unified_diff(
            [line + "\n" for line in original_lines],
            [line + "\n" for line in modified_lines],
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        )

        patch_parts.append("".join(diff_lines))

    patch = "\n".join(part for part in patch_parts if part.strip())

    if patch and not patch.endswith("\n"):
        patch += "\n"

    return patch


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


def run_edit_agent(
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

    user_prompt = build_edit_prompt(
        error_text=error_text,
        debug_result=debug_result,
        related_files=related_files,
        project_dir=project_dir,
    )

    print("\n========== 生成结构化编辑意图 ==========")
    raw_output = call_llm(
        system_prompt=EDIT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        timeout=60,
        max_retries=3,
    )
    print(raw_output)

    try:
        edit_result = safe_json_loads(raw_output)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": f"模型输出不是合法 JSON: {e}",
            "raw_output": raw_output,
            "debug_result": debug_result,
            "related_files": related_files,
        }

    ok, error = validate_edit_result(edit_result)
    if not ok:
        return {
            "ok": False,
            "error": error,
            "edit_result": edit_result,
            "debug_result": debug_result,
            "related_files": related_files,
        }

    edit_result["verify_command"] = normalize_verify_command(edit_result.get("verify_command", ""))

    if not edit_result.get("need_patch"):
        return {
            "ok": True,
            "debug_result": debug_result,
            "related_files": related_files,
            "edit_result": edit_result,
            "patch_result": {
                "need_patch": False,
                "reason": edit_result.get("reason", ""),
                "target_files": [],
                "patch": "",
                "verify_command": edit_result.get("verify_command", ""),
            },
            "saved_patch_path": None,
            "manual_commands": None,
        }

    ok, error, changed_files = apply_structured_edits(
        project_dir=project_dir,
        edits=edit_result["edits"],
    )

    if not ok:
        return {
            "ok": False,
            "error": error,
            "edit_result": edit_result,
            "debug_result": debug_result,
            "related_files": related_files,
        }

    patch_text = build_patch_from_changed_files(changed_files)

    if not patch_text.strip():
        return {
            "ok": False,
            "error": "结构化 edits 没有生成有效 patch",
            "edit_result": edit_result,
            "debug_result": debug_result,
            "related_files": related_files,
        }

    target_files = list(changed_files.keys())

    patch_result = {
        "need_patch": True,
        "reason": edit_result.get("reason", ""),
        "target_files": target_files,
        "patch": patch_text,
        "verify_command": edit_result.get("verify_command", ""),
    }

    saved_patch_path = None
    manual_commands = None

    if save_patch:
        patch_name = make_patch_filename(project_dir)
        saved_patch_path = save_patch_file(
            patch_text=patch_text,
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
        "edit_result": edit_result,
        "patch_result": patch_result,
        "saved_patch_path": saved_patch_path,
        "manual_commands": manual_commands,
    }


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Structured Edit Agent")
    parser.add_argument("--project", required=True, help="要分析的项目目录")
    parser.add_argument("--error-file", help="报错文本文件")
    parser.add_argument("--error-text", help="直接传入报错文本")
    parser.add_argument("--max-debug-steps", type=int, default=5)
    parser.add_argument("--save-patch", action="store_true")
    parser.add_argument("--patch-dir", default="patches")
    args = parser.parse_args()

    error_text = load_error_text(args.error_file, args.error_text)

    result = run_edit_agent(
        project_dir=args.project,
        error_text=error_text,
        max_debug_steps=args.max_debug_steps,
        save_patch=args.save_patch,
        patch_dir=args.patch_dir,
    )

    print("\n========== Structured Edit Agent 最终结果 ==========")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
