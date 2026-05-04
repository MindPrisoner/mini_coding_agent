import subprocess
from pathlib import Path


ALLOWED_COMMAND_PREFIXES = [
    "python",
    "pytest",
    "tree",
    "ls",
    "cat",
    "pwd",
]


def is_allowed_command(command: str) -> bool:
    command = command.strip()

    dangerous_tokens = [
        "rm ",
        "rm -",
        "sudo",
        "chmod",
        "chown",
        "mv ",
        "cp ",
        ">",
        ">>",
        "|",
        "&&",
        ";",
    ]

    if any(token in command for token in dangerous_tokens):
        return False

    return any(command.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES)


def run_command(project_dir: str, command: str, timeout: int = 30) -> dict:
    root = Path(project_dir).resolve()

    if not root.exists():
        return {
            "ok": False,
            "error": f"项目目录不存在: {root}"
        }

    if not is_allowed_command(command):
        return {
            "ok": False,
            "error": f"命令被安全策略拒绝: {command}"
        }

    try:
        result = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "ok": result.returncode == 0,
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[-5000:],
            "stderr": result.stderr[-5000:],
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "command": command,
            "error": f"命令超时: {timeout}s"
        }
