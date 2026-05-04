import argparse
import json

from agent_tools.tool_registry import execute_tool


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Agent 工具测试 CLI")
    parser.add_argument("--project", required=True, help="项目目录")
    parser.add_argument("--tool", required=True, help="工具名")
    parser.add_argument("--args", default="{}", help="工具参数 JSON")
    args = parser.parse_args()

    tool_args = json.loads(args.args)
    tool_args["project_dir"] = args.project

    result = execute_tool(args.tool, tool_args)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
