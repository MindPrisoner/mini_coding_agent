import argparse
import json
import subprocess
from pathlib import Path

from edit_agent import run_edit_agent, load_error_text
from patch_review_agent import run_patch_review
from agent_tools.shell_tools import run_command


def build_user_next_steps(result: dict) -> dict:
    patch_result = result.get("patch_result") or {}
    review_result = result.get("review_result") or {}
    manual_commands = result.get("manual_commands") or {}

    need_patch = patch_result.get("need_patch", False)
    safe_to_apply = review_result.get("safe_to_apply", False)

    if not need_patch:
        return {
            "status": "no_patch_needed",
            "message": "当前结果判断不需要生成代码补丁。",
            "commands": {}
        }

    if not safe_to_apply:
        return {
            "status": "patch_not_safe",
            "message": "Patch Review 判断该补丁暂不适合直接应用，请先人工检查。",
            "commands": manual_commands
        }

    return {
        "status": "ready_to_apply",
        "message": "补丁已生成并通过审查。你可以手动应用 patch，或使用 --apply --yes 自动应用。",
        "commands": manual_commands
    }


def apply_patch_file(project_dir: str, patch_file: str, timeout: int = 30) -> dict:
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
            ["git", "apply", str(patch_path)],
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
            "error": f"git apply 超时: {timeout}s",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"git apply 执行异常: {type(e).__name__}: {e}",
        }


def revert_patch_file(project_dir: str, patch_file: str, timeout: int = 30) -> dict:
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
            ["git", "apply", "-R", str(patch_path)],
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
            "error": f"git apply -R 超时: {timeout}s",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"git apply -R 执行异常: {type(e).__name__}: {e}",
        }


def should_allow_auto_apply(review_result: dict, apply: bool, yes: bool) -> tuple[bool, str]:
    review_result = review_result or {}

    if not apply:
        return False, "未启用 --apply，默认不自动应用 patch。"

    if not yes:
        return False, "启用了 --apply 但没有传入 --yes，为安全起见不自动应用。"

    if not review_result.get("safe_to_apply"):
        return False, "Patch Review 未通过，不允许自动应用。"

    if review_result.get("risk_level") != "low":
        return False, f"Patch 风险等级为 {review_result.get('risk_level')}，只有 low 才允许自动应用。"

    return True, "允许自动应用 patch。"


def run_optional_apply_and_verify(
    project_dir: str,
    patch_file: str,
    verify_command: str,
    apply: bool,
    yes: bool,
) -> dict:
    if not apply or not yes:
        return {
            "auto_apply_enabled": False,
            "apply_result": None,
            "verify_result": None,
        }

    apply_result = apply_patch_file(
        project_dir=project_dir,
        patch_file=patch_file,
    )

    verify_result = None

    if apply_result.get("ok") and verify_command:
        verify_result = run_command(
            project_dir=project_dir,
            command=verify_command,
            timeout=30,
        )

    return {
        "auto_apply_enabled": True,
        "apply_result": apply_result,
        "verify_result": verify_result,
    }


def run_fix_agent(
    project_dir: str,
    error_text: str,
    max_debug_steps: int = 5,
    patch_dir: str = "patches",
    apply: bool = False,
    yes: bool = False,
) -> dict:
    print("\n========== Step 1: 生成结构化编辑 Patch ==========")

    edit_agent_result = run_edit_agent(
        project_dir=project_dir,
        error_text=error_text,
        max_debug_steps=max_debug_steps,
        save_patch=True,
        patch_dir=patch_dir,
    )

    if not edit_agent_result.get("ok"):
        return {
            "ok": False,
            "stage": "structured_edit_generation",
            "error": edit_agent_result.get("error", "结构化编辑生成失败"),
            "edit_agent_result": edit_agent_result,
        }

    patch_result = edit_agent_result.get("patch_result") or {}
    saved_patch_path = edit_agent_result.get("saved_patch_path")

    if not patch_result.get("need_patch"):
        combined_result = {
            "ok": True,
            "stage": "no_patch_needed",
            "project_dir": project_dir,
            "saved_patch_path": saved_patch_path,
            "debug_result": edit_agent_result.get("debug_result"),
            "edit_result": edit_agent_result.get("edit_result"),
            "patch_result": patch_result,
            "review_result": None,
            "apply_check_result": None,
            "manual_commands": edit_agent_result.get("manual_commands"),
            "auto_apply": {
                "auto_apply_enabled": False,
                "reason": "不需要 patch，因此不会自动应用。",
                "apply_result": None,
                "verify_result": None,
            },
        }

        combined_result["next_steps"] = build_user_next_steps(combined_result)
        return combined_result

    if not saved_patch_path:
        return {
            "ok": False,
            "stage": "patch_save",
            "error": "需要 patch，但没有生成 saved_patch_path",
            "edit_agent_result": edit_agent_result,
        }

    print("\n========== Step 2: 审查 Patch ==========")

    review_agent_result = run_patch_review(
        project_dir=project_dir,
        patch_file=saved_patch_path,
        error_text=error_text,
    )

    review_result = review_agent_result.get("review_result") or {}

    allow_apply, apply_reason = should_allow_auto_apply(
        review_result=review_result,
        apply=apply,
        yes=yes,
    )

    auto_apply_result = {
        "auto_apply_enabled": False,
        "reason": apply_reason,
        "apply_result": None,
        "verify_result": None,
    }

    if allow_apply:
        print("\n========== Step 3: 自动应用并验证 ==========")

        auto_apply_result = run_optional_apply_and_verify(
            project_dir=project_dir,
            patch_file=saved_patch_path,
            verify_command=patch_result.get("verify_command", ""),
            apply=apply,
            yes=yes,
        )
        auto_apply_result["reason"] = apply_reason

    combined_result = {
        "ok": True,
        "stage": "completed",
        "project_dir": project_dir,
        "saved_patch_path": saved_patch_path,
        "debug_result": edit_agent_result.get("debug_result"),
        "edit_result": edit_agent_result.get("edit_result"),
        "patch_result": patch_result,
        "review_result": review_result,
        "apply_check_result": review_agent_result.get("apply_check_result"),
        "manual_commands": edit_agent_result.get("manual_commands"),
        "auto_apply": auto_apply_result,
    }

    combined_result["next_steps"] = build_user_next_steps(combined_result)

    return combined_result


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Fix Agent")
    parser.add_argument("--project", required=True, help="要分析和生成补丁的项目目录")
    parser.add_argument("--error-file", help="报错文本文件")
    parser.add_argument("--error-text", help="直接传入报错文本")
    parser.add_argument("--max-debug-steps", type=int, default=5)
    parser.add_argument("--patch-dir", default="patches", help="patch 保存目录")
    parser.add_argument("--apply", action="store_true", help="审查通过后自动应用 patch")
    parser.add_argument("--yes", action="store_true", help="确认允许自动应用 patch")
    args = parser.parse_args()

    error_text = load_error_text(args.error_file, args.error_text)

    result = run_fix_agent(
        project_dir=args.project,
        error_text=error_text,
        max_debug_steps=args.max_debug_steps,
        patch_dir=args.patch_dir,
        apply=args.apply,
        yes=args.yes,
    )

    print("\n========== Fix Agent 最终结果 ==========")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
