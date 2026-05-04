from agent_tools.file_tools import list_files, read_file, search_text
from agent_tools.shell_tools import run_command


AVAILABLE_TOOLS = {
    "list_files": "列出项目目录下的文件",
    "read_file": "读取项目中的指定文件",
    "search_text": "在项目中搜索关键词",
    "run_command": "运行安全命令，例如 python / pytest / tree / ls / pwd",
}


TOOL_ALLOWED_ARGS = {
    "list_files": {"project_dir", "max_files"},
    "read_file": {"project_dir", "file_path", "max_chars"},
    "search_text": {"project_dir", "keyword", "max_results"},
    "run_command": {"project_dir", "command", "timeout"},
}


def normalize_arguments(tool_name: str, arguments: dict) -> dict:
    """
    统一清洗 LLM 生成的工具参数。

    作用：
    1. 丢弃工具不支持的参数
    2. 兼容常见别名
    3. 避免 TypeError 直接把程序打崩
    """
    if not isinstance(arguments, dict):
        return {}

    args = dict(arguments)

    # 常见别名兼容：模型有时会把 read_file 的 file_path 写成 path
    if tool_name == "read_file":
        if "file_path" not in args and "path" in args:
            args["file_path"] = args["path"]

    # 常见别名兼容：模型有时会把 run_command 的 command 写成 cmd
    if tool_name == "run_command":
        if "command" not in args and "cmd" in args:
            args["command"] = args["cmd"]

    # list_files 不需要 path。project_dir 会由 agent 主程序统一注入。
    if tool_name == "list_files":
        args.pop("path", None)

    allowed = TOOL_ALLOWED_ARGS.get(tool_name, set())

    cleaned = {
        key: value
        for key, value in args.items()
        if key in allowed
    }

    return cleaned


def execute_tool(tool_name: str, arguments: dict) -> dict:
    """
    工具统一执行入口。

    所有 Agent 都应该通过这个函数调用工具，而不是直接调用 list_files/read_file。
    这样可以统一做参数清洗、异常保护和错误返回。
    """
    if tool_name not in AVAILABLE_TOOLS:
        return {
            "ok": False,
            "error": f"未知工具: {tool_name}",
        }

    cleaned_args = normalize_arguments(tool_name, arguments)

    try:
        if tool_name == "list_files":
            return list_files(**cleaned_args)

        if tool_name == "read_file":
            return read_file(**cleaned_args)

        if tool_name == "search_text":
            return search_text(**cleaned_args)

        if tool_name == "run_command":
            return run_command(**cleaned_args)

        return {
            "ok": False,
            "error": f"未知工具: {tool_name}",
        }

    except TypeError as e:
        return {
            "ok": False,
            "error": f"工具参数错误: {type(e).__name__}: {e}",
            "tool_name": tool_name,
            "raw_arguments": arguments,
            "cleaned_arguments": cleaned_args,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"工具执行异常: {type(e).__name__}: {e}",
            "tool_name": tool_name,
            "raw_arguments": arguments,
            "cleaned_arguments": cleaned_args,
        }
