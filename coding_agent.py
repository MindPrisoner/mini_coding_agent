import argparse
import json
from typing import Any

from llm_client import call_llm
from prompts import PLANNER_SYSTEM_PROMPT, build_planner_prompt
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
        if not action.get("final_answer"):
            return False, "最终答案缺少 final_answer"
        return True, ""

    tool_name = action.get("tool_name")
    arguments = action.get("arguments")

    if tool_name not in AVAILABLE_TOOLS:
        return False, f"未知工具: {tool_name}"

    if not isinstance(arguments, dict):
        return False, "arguments 必须是 dict"

    return True, ""


def run_agent(project_dir: str, task: str, max_steps: int = 6) -> dict:
    observations = []

    for step in range(1, max_steps + 1):
        print(f"\n========== Agent Step {step}/{max_steps} ==========")

        user_prompt = build_planner_prompt(task, observations)
        raw_output = call_llm(
            system_prompt=PLANNER_SYSTEM_PROMPT,
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
                "result": f"模型输出不是合法 JSON: {e}"
            })
            continue

        ok, error = validate_action(action)
        if not ok:
            observations.append({
                "tool_name": "planner_error",
                "arguments": {},
                "result": error
            })
            continue

        if action["is_final"]:
            return {
                "ok": True,
                "task": task,
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

    return {
        "ok": False,
        "task": task,
        "final_answer": "达到最大步数，Agent 未能完成最终回答。",
        "steps": observations,
    }


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Agent")
    parser.add_argument("--project", required=True, help="要分析的项目目录")
    parser.add_argument("--task", required=True, help="用户任务")
    parser.add_argument("--max-steps", type=int, default=6, help="最大工具调用步数")
    args = parser.parse_args()

    result = run_agent(
        project_dir=args.project,
        task=args.task,
        max_steps=args.max_steps,
    )

    print("\n========== 最终结果 ==========")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
