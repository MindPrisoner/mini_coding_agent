import ast
import shlex
import subprocess
from pathlib import Path


ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "tree",
    "ls",
    "cat",
    "pwd",
}


SHELL_DANGEROUS_TOKENS = [
    "&&",
    "|",
    ">",
    ">>",
    "<<",
    "`",
    "$(",
]


PYTHON_C_BANNED_IMPORTS = {
    "os",
    "subprocess",
    "shutil",
    "pathlib",
    "socket",
    "requests",
    "httpx",
    "urllib",
    "glob",
    "pickle",
    "builtins",
}


PYTHON_C_BANNED_CALLS = {
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
}


PYTHON_C_BANNED_ATTRS = {
    "system",
    "popen",
    "remove",
    "unlink",
    "rmdir",
    "mkdir",
    "rename",
    "replace",
    "write",
    "writelines",
    "chmod",
    "chown",
}


def split_command(command: str) -> tuple[bool, list[str], str]:
    """
    使用 shlex 把命令拆成参数列表。

    好处：
    1. 不用 shell=True
    2. 可以正确处理引号
    3. 可以识别 python -c "..." 这种命令
    """
    try:
        args = shlex.split(command)
    except ValueError as e:
        return False, [], f"命令解析失败: {e}"

    if not args:
        return False, [], "命令为空"

    return True, args, ""


def is_python_inline_command(args: list[str]) -> bool:
    """
    判断是否是 python -c 形式。
    """
    if len(args) < 3:
        return False

    executable = Path(args[0]).name

    return executable in {"python", "python3"} and args[1] == "-c"


def validate_python_inline_code(code: str) -> tuple[bool, str]:
    """
    检查 python -c 里的代码是否安全。

    允许：
    - from schemas import QueryResponse
    - 创建对象
    - print(...)
    - 简单变量赋值

    禁止：
    - os / subprocess / shutil 等危险模块
    - open / eval / exec / __import__
    - .system / .remove / .write 等危险属性调用
    """
    if len(code) > 3000:
        return False, "python -c 代码过长，拒绝执行"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"python -c 代码语法错误: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split(".")[0]
                if root_name in PYTHON_C_BANNED_IMPORTS:
                    return False, f"python -c 禁止导入危险模块: {root_name}"

        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root_name = module.split(".")[0]
            if root_name in PYTHON_C_BANNED_IMPORTS:
                return False, f"python -c 禁止导入危险模块: {root_name}"

        if isinstance(node, ast.Call):
            func = node.func

            if isinstance(func, ast.Name):
                if func.id in PYTHON_C_BANNED_CALLS:
                    return False, f"python -c 禁止调用危险函数: {func.id}"

            if isinstance(func, ast.Attribute):
                if func.attr in PYTHON_C_BANNED_ATTRS:
                    return False, f"python -c 禁止调用危险方法: {func.attr}"

    return True, ""


def is_allowed_non_python_inline_command(command: str, args: list[str]) -> tuple[bool, str]:
    """
    检查普通命令是否允许。

    注意：
    python -c 不走这里，因为 python -c 可能合法包含分号。
    """
    executable = Path(args[0]).name

    if executable not in ALLOWED_EXECUTABLES:
        return False, f"命令不在白名单中: {executable}"

    if ";" in command:
        return False, "普通命令禁止使用分号"

    for token in SHELL_DANGEROUS_TOKENS:
        if token in command:
            return False, f"命令包含危险 shell 符号: {token}"

    return True, ""


def is_allowed_command(command: str) -> tuple[bool, str, list[str]]:
    """
    统一命令安全检查。

    返回：
    - 是否允许
    - 拒绝原因
    - shlex 拆分后的参数列表
    """
    command = command.strip()

    ok, args, error = split_command(command)
    if not ok:
        return False, error, []

    executable = Path(args[0]).name

    if executable not in ALLOWED_EXECUTABLES:
        return False, f"命令不在白名单中: {executable}", args

    if is_python_inline_command(args):
        code = args[2]
        code_ok, code_error = validate_python_inline_code(code)
        if not code_ok:
            return False, code_error, args
        return True, "", args

    ok, error = is_allowed_non_python_inline_command(command, args)
    if not ok:
        return False, error, args

    return True, "", args


def run_command(project_dir: str, command: str, timeout: int = 30) -> dict:
    """
    运行安全命令。

    关键设计：
    1. 不使用 shell=True
    2. 普通命令禁止 ; && | > 等 shell 组合符
    3. python -c 单独走 AST 安全检查
    """
    root = Path(project_dir).resolve()

    if not root.exists():
        return {
            "ok": False,
            "error": f"项目目录不存在: {root}"
        }

    allowed, reason, args = is_allowed_command(command)

    if not allowed:
        return {
            "ok": False,
            "error": f"命令被安全策略拒绝: {reason}",
            "command": command,
        }

    try:
        result = subprocess.run(
            args,
            cwd=str(root),
            shell=False,
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

    except Exception as e:
        return {
            "ok": False,
            "command": command,
            "error": f"命令执行异常: {type(e).__name__}: {e}"
        }
