import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from llm_client import call_llm
from agent_tools.file_tools import read_file
from patch_review_prompts import PATCH_REVIEW_SYSTEM_PROMPT, build_patch_review_prompt


def safe_json_loads(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()

    return json.loads(text)


def load_text(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def compact_json(data: Any, max_chars: int = 5000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    if len(text) > max_chars:
        return text[:max_chars] + "\n... [内容过长，已截断]"

    return text


def extract_target_files_from_patch(patch_text: str) -> list[str]:
    """
    从 unified diff 中提取被修改的目标文件。

    支持格式：
    --- a/main.py
    +++ b/main.py
    """
    targets = []

    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            file_path = line.replace("+++ b/", "", 1).strip()
            if file_path != "/dev/null":
                targets.append(file_path)

    return list(dict.fromkeys(targets))


def read_target_files(project_dir: str, target_files: list[str]) -> list[dict]:
    """
    读取 patch 涉及的目标文件当前内容。
    如果文件不存在，也返回错误信息，交给审查 Agent 判断。
    """
    results = []

    for file_path in target_files:
        result = read_file(
            project_dir=project_dir,
            file_path=file_path,
            max_chars=5000,
        )
        results.append(result)

    return results


def run_git_apply_check(project_dir: str, patch_file: str, timeout: int = 30) -> dict:
    """
    执行 git apply --check，检查 patch 是否能干净应用。
    这个命令不会修改文件，只做检查。
    """
    project_root = Path(project_dir).resolve()
    patch_path = Path(patch_file).resolve()

    if not project_root.exists():
        return {
            "ok": False,
            "error": f"项目目录不存在: {project_root}",
        }

    if not patch_path.exists():
        return {
            "ok": False,
            "error": f"patch 文件不存在: {patch_path}",
        }

    try:
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-3000:],
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"git apply --check 超时: {timeout}s",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"git apply --check 执行异常: {type(e).__name__}: {e}",
        }


def validate_review_result(data: dict) -> tuple[bool, str]:
    required_keys = [
        "safe_to_apply",
        "risk_level",
        "summary",
        "evidence",
        "issues",
        "suggestion",
        "verify_command",
    ]

    missing = [key for key in required_keys if key not in data]
    if missing:
        return False, f"缺少字段: {missing}"

    if not isinstance(data["safe_to_apply"], bool):
        return False, "safe_to_apply 必须是 bool"

    if data["risk_level"] not in {"low", "medium", "high"}:
        return False, "risk_level 必须是 low / medium / high"

    if not isinstance(data["evidence"], list):
        return False, "evidence 必须是 list"

    if not isinstance(data["issues"], list):
        return False, "issues 必须是 list"

    return True, ""


def fallback_review(
    patch_text: str,
    apply_check_result: dict,
    target_files: list[str],
) -> dict:
    """
    当模型输出不稳定时，给一个程序级兜底审查。
    """
    modifies_many_files = len(target_files) > 3
    check_ok = apply_check_result.get("ok") is True

    if check_ok and not modifies_many_files:
        return {
            "safe_to_apply": True,
            "risk_level": "low",
            "summary": "git apply --check 通过，且修改文件数量较少，可以手动应用后再运行验证命令。",
            "evidence": [
                "git apply --check 通过",
                f"patch 涉及文件数量: {len(target_files)}",
            ],
            "issues": [],
            "suggestion": "建议手动应用 patch 后运行验证命令。",
            "verify_command": "python main.py",
        }

    return {
        "safe_to_apply": False,
        "risk_level": "medium" if not modifies_many_files else "high",
        "summary": "patch 未通过自动检查，暂不建议直接应用。",
        "evidence": [
            f"git apply --check ok={apply_check_result.get('ok')}",
            f"patch 涉及文件: {target_files}",
        ],
        "issues": [
            apply_check_result.get("stderr") or apply_check_result.get("error") or "未知检查失败原因"
        ],
        "suggestion": "建议先查看 patch 是否已经应用过，或者重新生成 patch。",
        "verify_command": "",
    }


def run_patch_review(
    project_dir: str,
    patch_file: str,
    error_text: str,
) -> dict:
    patch_text = load_text(patch_file)
    target_files = extract_target_files_from_patch(patch_text)
    target_file_contents = read_target_files(project_dir, target_files)
    apply_check_result = run_git_apply_check(project_dir, patch_file)

    user_prompt = build_patch_review_prompt(
        error_text=error_text,
        patch_text=patch_text,
        target_files=target_file_contents,
        apply_check_result=apply_check_result,
    )

    raw_output = call_llm(
        system_prompt=PATCH_REVIEW_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        timeout=60,
        max_retries=3,
    )

    try:
        review_result = safe_json_loads(raw_output)
    except json.JSONDecodeError:
        review_result = fallback_review(
            patch_text=patch_text,
            apply_check_result=apply_check_result,
            target_files=target_files,
        )

    ok, error = validate_review_result(review_result)
    if not ok:
        review_result = fallback_review(
            patch_text=patch_text,
            apply_check_result=apply_check_result,
            target_files=target_files,
        )

    return {
        "ok": True,
        "patch_file": patch_file,
        "target_files": target_files,
        "apply_check_result": apply_check_result,
        "review_result": review_result,
    }


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Patch Review Agent")
    parser.add_argument("--project", required=True, help="要审查的项目目录")
    parser.add_argument("--patch-file", required=True, help="patch 文件路径")
    parser.add_argument("--error-file", help="报错文本文件")
    parser.add_argument("--error-text", help="直接传入报错文本")

    args = parser.parse_args()

    if args.error_file:
        error_text = load_text(args.error_file)
    elif args.error_text:
        error_text = args.error_text
    else:
        raise ValueError("必须提供 --error-file 或 --error-text")

    result = run_patch_review(
        project_dir=args.project,
        patch_file=args.patch_file,
        error_text=error_text,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
