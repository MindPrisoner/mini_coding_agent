import argparse
import json
from pathlib import Path
from typing import Any

from llm_client import call_llm
from debug_prompts import DEBUG_PLANNER_SYSTEM_PROMPT, build_debug_prompt
from agent_tools.tool_registry import execute_tool, AVAILABLE_TOOLS


def safe_json_loads(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    return json.loads(text)


def compact_result(result: Any, max_chars: int = 2500) -> str:
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + "\n... [结果过长，已截断]"
    return text


def validate_action(action: dict) -> tuple[bool, str]:
    if "is_final" not in action:
        return False, "缺少 is_final 字段"

    if action["is_final"]:
        final_answer = action.get("final_answer")
        if not isinstance(final_answer, dict):
            return False, "最终答案 final_answer 必须是 dict"

        required_keys = [
            "error_type",
            "root_cause",
            "evidence",
            "fix_suggestion",
            "verify_command",
        ]

        missing = [key for key in required_keys if key not in final_answer]
        if missing:
            return False, f"最终答案缺少字段: {missing}"

        return True, ""

    tool_name = action.get("tool_name")
    arguments = action.get("arguments")

    if tool_name not in AVAILABLE_TOOLS:
        return False, f"未知工具: {tool_name}"

    if not isinstance(arguments, dict):
        return False, "arguments 必须是 dict"

    return True, ""


def load_error_text(error_file: str | None, error_text: str | None) -> str:
    if error_file:
        return Path(error_file).read_text(encoding="utf-8")
    if error_text:
        return error_text
    raise ValueError("必须提供 --error-file 或 --error-text")


def fallback_debug_answer(error_text: str, observations: list[dict]) -> dict:
    joined_obs = json.dumps(observations, ensure_ascii=False, default=str)

    if "ModuleNotFoundError" in error_text and "No module named 'agent_stage'" in error_text:
        if "agent_stage/__init__.py" in joined_obs or "agent_stage" in joined_obs:
            return {
                "error_type": "ModuleNotFoundError",
                "root_cause": (
                    "项目中存在 agent_stage 包，但运行时 Python 没有在模块搜索路径中找到它。"
                    "这通常是因为没有从项目根目录使用 python -m agent_stage.xxx 的方式运行，"
                    "或者当前工作目录 / PYTHONPATH 不正确。"
                ),
                "evidence": [
                    "报错信息为 No module named 'agent_stage'",
                    "工具观察结果显示项目中存在 agent_stage 目录或相关文件",
                    "这类错误通常和 Python 模块搜索路径有关"
                ],
                "fix_suggestion": [
                    "进入项目根目录后使用 python -m agent_stage.agent_cli 运行",
                    "确认当前工作目录是 rag_lite 项目根目录",
                    "必要时设置 PYTHONPATH 指向项目根目录"
                ],
                "verify_command": "python -m agent_stage.agent_cli --help"
            }

    return {
        "error_type": "Unknown",
        "root_cause": "达到最大步数，未能完成明确判断。",
        "evidence": [],
        "fix_suggestion": [],
        "verify_command": "",
    }




def run_debug_agent(project_dir: str, error_text: str, max_steps: int = 6) -> dict:
    observations = []

    for step in range(1, max_steps + 1):
        print(f"\n========== Debug Agent Step {step}/{max_steps} ==========")

        user_prompt = build_debug_prompt(error_text, observations)

        raw_output = call_llm(
            system_prompt=DEBUG_PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
        )

        print("\n[模型决策]")
        print(raw_output)

        try:
            action = safe_json_loads(raw_output)
        except json.JSONDecodeError as e:
            observations.append({
                "tool_name": "planner_error",
                "arguments": {},
                "result": f"模型输出不是合法 JSON: {e}",
            })
            continue

        ok, error = validate_action(action)
        if not ok:
            observations.append({
                "tool_name": "planner_error",
                "arguments": {},
                "result": error,
            })
            continue

        if action["is_final"]:
            return {
                "ok": True,
                "final_answer": action["final_answer"],
                "steps": observations,
            }

        tool_name = action["tool_name"]
        arguments = action["arguments"]
        arguments["project_dir"] = project_dir

        print(f"\n[执行工具] {tool_name}")
        print(json.dumps(arguments, indent=2, ensure_ascii=False))

        tool_result = execute_tool(tool_name, arguments)

        print("\n[工具结果]")
        print(compact_result(tool_result))

        observations.append({
            "tool_name": tool_name,
            "arguments": arguments,
            "result": compact_result(tool_result),
        })
        fallback_answer = fallback_debug_answer(error_text, observations)

        return {
            "ok": fallback_answer["error_type"] != "Unknown",
            "final_answer": fallback_answer,
            "steps": observations,
}





def main():
    parser = argparse.ArgumentParser(description="Mini Coding Debug Agent")
    parser.add_argument("--project", required=True, help="要分析的项目目录")
    parser.add_argument("--error-file", help="报错文本文件")
    parser.add_argument("--error-text", help="直接传入报错文本")
    parser.add_argument("--max-steps", type=int, default=6)
    args = parser.parse_args()

    error_text = load_error_text(args.error_file, args.error_text)

    result = run_debug_agent(
        project_dir=args.project,
        error_text=error_text,
        max_steps=args.max_steps,
    )

    print("\n========== 最终分析 ==========")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
