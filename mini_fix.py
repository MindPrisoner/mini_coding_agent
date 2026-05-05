import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fix_agent import run_fix_agent, load_error_text


CONFIG_FILE_NAME = ".mini-fix.json"


def load_config(config_path: str | None = None) -> dict[str, Any]:
    path = Path(config_path or CONFIG_FILE_NAME)

    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_config(config: dict[str, Any], config_path: str = CONFIG_FILE_NAME) -> str:
    path = Path(config_path)
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def make_default_config() -> dict[str, Any]:
    return {
        "max_debug_steps": 8,
        "patch_dir": "patches",
        "report_dir": "reports",
        "auto_apply_requires_yes": True,
        "default_apply": False,
        "default_yes": False,
    }


def make_report_filename(project_dir: str) -> str:
    project_name = Path(project_dir).name or "project"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{project_name}_{timestamp}.json"


def save_report(result: dict[str, Any], report_dir: str, report_name: str | None = None) -> str:
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if report_name is None:
        report_name = make_report_filename(result.get("project_dir", "project"))

    report_path = output_dir / report_name
    report_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    return str(report_path)


def get_nested(data: dict[str, Any], path: list[str], default=None):
    current = data

    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)

    return current if current is not None else default


def build_summary(result: dict[str, Any], report_path: str | None = None) -> str:
    lines = []

    lines.append("========== Mini Fix Summary ==========")
    lines.append(f"ok: {result.get('ok')}")
    lines.append(f"stage: {result.get('stage')}")
    lines.append(f"project_dir: {result.get('project_dir')}")
    lines.append(f"saved_patch_path: {result.get('saved_patch_path')}")

    edit_need_patch = get_nested(result, ["edit_result", "need_patch"])
    patch_need_patch = get_nested(result, ["patch_result", "need_patch"])
    target_files = get_nested(result, ["patch_result", "target_files"], [])

    lines.append(f"edit_need_patch: {edit_need_patch}")
    lines.append(f"patch_need_patch: {patch_need_patch}")
    lines.append(f"target_files: {target_files}")

    safe_to_apply = get_nested(result, ["review_result", "safe_to_apply"])
    risk_level = get_nested(result, ["review_result", "risk_level"])

    lines.append(f"safe_to_apply: {safe_to_apply}")
    lines.append(f"risk_level: {risk_level}")

    apply_ok = get_nested(result, ["auto_apply", "apply_result", "ok"])
    verify_ok = get_nested(result, ["auto_apply", "verify_result", "ok"])
    verify_stdout = get_nested(result, ["auto_apply", "verify_result", "stdout"], "")

    lines.append(f"auto_apply_ok: {apply_ok}")
    lines.append(f"verify_ok: {verify_ok}")

    next_status = get_nested(result, ["next_steps", "status"])
    next_message = get_nested(result, ["next_steps", "message"])

    lines.append(f"next_status: {next_status}")
    lines.append(f"next_message: {next_message}")

    if verify_stdout:
        lines.append("")
        lines.append("---------- Verify stdout ----------")
        lines.append(str(verify_stdout).strip())

    manual_commands = result.get("manual_commands") or get_nested(result, ["next_steps", "commands"], {})

    if manual_commands:
        lines.append("")
        lines.append("---------- Manual commands ----------")
        for name, command in manual_commands.items():
            lines.append(f"{name}: {command}")

    if report_path:
        lines.append("")
        lines.append(f"report_path: {report_path}")

    return "\n".join(lines)


def infer_exit_code(result: dict[str, Any]) -> int:
    if not result.get("ok"):
        return 1

    auto_apply = result.get("auto_apply") or {}

    apply_result = auto_apply.get("apply_result")
    verify_result = auto_apply.get("verify_result")

    if apply_result is not None and not apply_result.get("ok"):
        return 1

    if verify_result is not None and not verify_result.get("ok"):
        return 1

    return 0


def apply_config_defaults(args, config: dict[str, Any]):
    if getattr(args, "max_debug_steps", None) is None:
        args.max_debug_steps = config.get("max_debug_steps", 8)

    if getattr(args, "patch_dir", None) is None:
        args.patch_dir = config.get("patch_dir", "patches")

    if getattr(args, "report_dir", None) is None:
        args.report_dir = config.get("report_dir", "reports")

    if not getattr(args, "apply", False):
        args.apply = bool(config.get("default_apply", False))

    if not getattr(args, "yes", False):
        args.yes = bool(config.get("default_yes", False))

    return args


def run_command_init(args) -> int:
    config_path = args.config or CONFIG_FILE_NAME
    path = Path(config_path)

    if path.exists() and not args.force:
        print(f"配置文件已存在: {path}")
        print("如需覆盖，请使用: mini-fix init --force")
        return 0

    config = make_default_config()
    saved_path = save_config(config, config_path=config_path)

    Path(config["patch_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["report_dir"]).mkdir(parents=True, exist_ok=True)

    patch_keep = Path(config["patch_dir"]) / ".gitkeep"
    report_keep = Path(config["report_dir"]) / ".gitkeep"

    patch_keep.touch(exist_ok=True)
    report_keep.touch(exist_ok=True)

    print("Mini Fix 初始化完成")
    print(f"config: {saved_path}")
    print(f"patch_dir: {config['patch_dir']}")
    print(f"report_dir: {config['report_dir']}")
    return 0


def check_bool(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "detail": detail,
    }


def run_command_diagnose(args) -> int:
    config = load_config(args.config)
    args = apply_config_defaults(args, config)

    checks = []

    project_dir = Path(args.project).resolve() if args.project else None

    checks.append(
        check_bool(
            "python_version",
            sys.version_info >= (3, 11),
            sys.version.replace("\n", " "),
        )
    )

    checks.append(
        check_bool(
            "api_key",
            bool(os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY")),
            "found DASHSCOPE_API_KEY/API_KEY" if (os.getenv("DASHSCOPE_API_KEY") or os.getenv("API_KEY")) else "missing API key",
        )
    )

    checks.append(
        check_bool(
            "git_available",
            shutil.which("git") is not None,
            shutil.which("git") or "git not found",
        )
    )

    checks.append(
        check_bool(
            "project_exists",
            project_dir.exists() if project_dir else False,
            str(project_dir) if project_dir else "no project provided",
        )
    )

    if project_dir and project_dir.exists():
        checks.append(
            check_bool(
                "project_has_python_files",
                any(project_dir.rglob("*.py")),
                "found python files" if any(project_dir.rglob("*.py")) else "no python files",
            )
        )

    if args.error_file:
        error_file = Path(args.error_file).resolve()
        checks.append(
            check_bool(
                "error_file_exists",
                error_file.exists(),
                str(error_file),
            )
        )

    patch_dir = Path(args.patch_dir)
    report_dir = Path(args.report_dir)

    checks.append(
        check_bool(
            "patch_dir_ready",
            patch_dir.exists() or patch_dir.parent.exists(),
            str(patch_dir),
        )
    )

    checks.append(
        check_bool(
            "report_dir_ready",
            report_dir.exists() or report_dir.parent.exists(),
            str(report_dir),
        )
    )

    print("========== Mini Fix Diagnose ==========")
    for item in checks:
        status = "PASS" if item["ok"] else "FAIL"
        print(f"{status} | {item['name']} | {item['detail']}")

    all_ok = all(item["ok"] for item in checks)

    if all_ok:
        print("diagnose: PASS")
        return 0

    print("diagnose: FAIL")
    return 1


def run_command_run(args) -> int:
    config = load_config(args.config)
    args = apply_config_defaults(args, config)

    error_text = load_error_text(args.error_file, args.error_text)

    result = run_fix_agent(
        project_dir=args.project,
        error_text=error_text,
        max_debug_steps=args.max_debug_steps,
        patch_dir=args.patch_dir,
        apply=args.apply,
        yes=args.yes,
    )

    report_path = None

    if not args.no_report:
        report_path = save_report(
            result=result,
            report_dir=args.report_dir,
        )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(build_summary(result, report_path=report_path))

    return infer_exit_code(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mini Fix: local coding agent for debug, structured edit, patch review, apply and verify."
    )

    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="初始化 Mini Fix 配置")
    init_parser.add_argument("--config", default=CONFIG_FILE_NAME, help="配置文件路径")
    init_parser.add_argument("--force", action="store_true", help="覆盖已有配置")
    init_parser.set_defaults(func=run_command_init)

    diagnose_parser = subparsers.add_parser("diagnose", help="检查 Mini Fix 运行环境")
    diagnose_parser.add_argument("--project", required=True, help="要检查的项目目录")
    diagnose_parser.add_argument("--error-file", help="报错文本文件")
    diagnose_parser.add_argument("--config", default=CONFIG_FILE_NAME)
    diagnose_parser.add_argument("--patch-dir", default=None)
    diagnose_parser.add_argument("--report-dir", default=None)
    diagnose_parser.add_argument("--max-debug-steps", type=int, default=None)
    diagnose_parser.set_defaults(func=run_command_diagnose)

    run_parser = subparsers.add_parser("run", help="运行完整修复流程")
    add_run_arguments(run_parser)
    run_parser.set_defaults(func=run_command_run)

    add_run_arguments(parser)
    parser.set_defaults(func=run_command_run)

    return parser


def add_run_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--project", help="要分析和修复的项目目录")
    parser.add_argument("--error-file", help="报错文本文件")
    parser.add_argument("--error-text", help="直接传入报错文本")
    parser.add_argument("--max-debug-steps", type=int, default=None, help="Debug Agent 最大步骤数")
    parser.add_argument("--patch-dir", default=None, help="patch 保存目录")
    parser.add_argument("--apply", action="store_true", help="审查通过后自动应用 patch")
    parser.add_argument("--yes", action="store_true", help="确认允许自动应用 patch")
    parser.add_argument("--report-dir", default=None, help="运行报告保存目录")
    parser.add_argument("--no-report", action="store_true", help="不保存运行报告")
    parser.add_argument("--json", action="store_true", help="只输出完整 JSON，不输出摘要")
    parser.add_argument("--config", default=CONFIG_FILE_NAME, help="配置文件路径")


def normalize_legacy_args(argv: list[str]) -> list[str]:
    """
    兼容旧写法：
    mini-fix --project xxx --error-file yyy

    如果第一个参数不是 init/diagnose/run，就自动当成 run。
    """
    if not argv:
        return argv

    known_commands = {"init", "diagnose", "run", "-h", "--help"}

    if argv[0] in known_commands:
        return argv

    return ["run"] + argv


def main():
    argv = normalize_legacy_args(sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "func", None) == run_command_run:
        if not args.project:
            parser.error("run 需要提供 --project")
        if not args.error_file and not args.error_text:
            parser.error("run 需要提供 --error-file 或 --error-text")

    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
