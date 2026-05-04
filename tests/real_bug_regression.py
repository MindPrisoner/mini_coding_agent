import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run_cmd(command: list[str], cwd: Path, timeout: int = 180) -> dict:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"command timeout after {timeout}s",
        }


def copy_project(source_project: Path, target_project: Path):
    if target_project.exists():
        shutil.rmtree(target_project)

    ignored = shutil.ignore_patterns(
        ".git",
        "__pycache__",
        "logs",
        ".gradio",
        "*.pyc",
    )

    shutil.copytree(source_project, target_project, ignore=ignored)


def ensure_env(project_dir: Path):
    env_path = project_dir / ".env"

    if env_path.exists():
        return

    env_path.write_text(
        "\n".join([
            "BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
            "MODEL_NAME=qwen3.5-flash",
            "TOP_K=3",
            "CHUNK_SIZE=180",
            "CHUNK_OVERLAP=40",
            "KNOWLEDGE_DIR=knowledge",
            "",
        ]),
        encoding="utf-8",
    )


def make_thread_id_bug(project_dir: Path):
    path = project_dir / "agent_stage" / "agent_cli.py"
    text = path.read_text(encoding="utf-8")

    good = """    result = graph.invoke(
        {
            "user_query": args.query,
            "knowledge_dir": args.dir,
        },
        config=config,
    )
"""

    bad = """    result = graph.invoke(
        {
            "user_query": args.query,
            "knowledge_dir": args.dir,
        }
    )
"""

    if good in text:
        text = text.replace(good, bad, 1)
        path.write_text(text, encoding="utf-8")
        return

    if bad in text:
        return

    raise RuntimeError("无法制造 thread_id bug：没有找到 graph.invoke 目标代码块")


def reproduce_thread_id_error(project_dir: Path):
    cmd = [
        sys.executable,
        "-m",
        "agent_stage.agent_cli",
        "--dir",
        "knowledge",
        "--query",
        "RAG 的核心流程是什么？",
    ]

    result = run_cmd(cmd, cwd=project_dir, timeout=120)
    error_file = project_dir / "bug_thread_id.txt"
    error_file.write_text(result["stdout"] + result["stderr"], encoding="utf-8")

    text = error_file.read_text(encoding="utf-8")
    if "Checkpointer requires" not in text:
        raise RuntimeError("未成功复现 thread_id bug")

    return error_file


def verify_thread_id_fixed(project_dir: Path):
    cmd = [
        sys.executable,
        "-m",
        "agent_stage.agent_cli",
        "--dir",
        "knowledge",
        "--query",
        "RAG 的核心流程是什么？",
    ]

    result = run_cmd(cmd, cwd=project_dir, timeout=180)
    output = result["stdout"] + result["stderr"]

    if "Checkpointer requires" in output:
        return False, output

    return True, output


def make_schema_bug(project_dir: Path):
    path = project_dir / "schemas.py"
    text = path.read_text(encoding="utf-8")

    lines = text.splitlines()
    new_lines = []
    changed = False

    for line in lines:
        if not changed and "retrieved" in line and ":" in line:
            prefix = line.split(":", 1)[0]
            new_lines.append(f"{prefix}: str")
            changed = True
        else:
            new_lines.append(line)

    if not changed:
        raise RuntimeError("无法制造 schema bug：没有找到 retrieved 字段")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def reproduce_schema_error(project_dir: Path):
    script = """
from schemas import QueryResponse, RetrievedChunk

QueryResponse(
    query="test",
    retrieved=[
        RetrievedChunk(
            rank=1,
            score=3.6,
            source="doc.txt",
            chunk_id=1,
            text="test",
        )
    ],
    answer="answer",
    answer_status="success",
    elapsed_ms=100,
    timestamp="2024-01-01T00:00:00",
)
print("should not reach here")
"""

    cmd = [sys.executable, "-c", script]
    result = run_cmd(cmd, cwd=project_dir, timeout=60)

    error_file = project_dir / "bug_schema.txt"
    error_file.write_text(result["stdout"] + result["stderr"], encoding="utf-8")

    text = error_file.read_text(encoding="utf-8")
    if "ValidationError" not in text and "Input should be a valid string" not in text:
        raise RuntimeError("未成功复现 schema bug")

    return error_file


def verify_schema_fixed(project_dir: Path):
    script = """
from schemas import QueryResponse, RetrievedChunk

r = QueryResponse(
    query="test",
    retrieved=[
        RetrievedChunk(
            rank=1,
            score=3.6,
            source="doc.txt",
            chunk_id=1,
            text="test",
        )
    ],
    answer="answer",
    answer_status="success",
    elapsed_ms=100,
    timestamp="2024-01-01T00:00:00",
)
print("schema validation ok")
print(type(r.retrieved))
print(len(r.retrieved))
"""

    cmd = [sys.executable, "-c", script]
    result = run_cmd(cmd, cwd=project_dir, timeout=60)

    output = result["stdout"] + result["stderr"]
    ok = "schema validation ok" in output and result["returncode"] == 0

    return ok, output


def run_fix_agent(mini_agent_dir: Path, project_dir: Path, error_file: Path):
    cmd = [
        sys.executable,
        "fix_agent.py",
        "--project",
        str(project_dir),
        "--error-file",
        str(error_file),
        "--max-debug-steps",
        "8",
        "--apply",
        "--yes",
    ]

    return run_cmd(cmd, cwd=mini_agent_dir, timeout=300)


def run_thread_id_case(mini_agent_dir: Path, source_project: Path, work_dir: Path):
    print("\n========== Case 1: LangGraph thread_id bug ==========")

    case_dir = work_dir / "rag_lite_thread_case"
    copy_project(source_project, case_dir)
    ensure_env(case_dir)

    make_thread_id_bug(case_dir)
    error_file = reproduce_thread_id_error(case_dir)

    fix_result = run_fix_agent(mini_agent_dir, case_dir, error_file)
    print(fix_result["stdout"])
    print(fix_result["stderr"])

    ok, verify_output = verify_thread_id_fixed(case_dir)

    print("verify output:")
    print(verify_output)

    return ok


def run_schema_case(mini_agent_dir: Path, source_project: Path, work_dir: Path):
    print("\n========== Case 2: Pydantic schema bug ==========")

    case_dir = work_dir / "rag_lite_schema_case"
    copy_project(source_project, case_dir)
    ensure_env(case_dir)

    make_schema_bug(case_dir)
    error_file = reproduce_schema_error(case_dir)

    fix_result = run_fix_agent(mini_agent_dir, case_dir, error_file)
    print(fix_result["stdout"])
    print(fix_result["stderr"])

    ok, verify_output = verify_schema_fixed(case_dir)

    print("verify output:")
    print(verify_output)

    return ok


def main():
    parser = argparse.ArgumentParser(description="Mini Coding Agent 真实 bug 回归测试")
    parser.add_argument(
        "--source-project",
        default="../rag_lite_buglab",
        help="作为模板复制的项目路径",
    )
    parser.add_argument(
        "--work-dir",
        default="tmp_regression",
        help="测试副本目录",
    )
    args = parser.parse_args()

    mini_agent_dir = Path.cwd().resolve()
    source_project = Path(args.source_project).resolve()
    work_dir = (mini_agent_dir / args.work_dir).resolve()

    if not source_project.exists():
        raise RuntimeError(f"source_project 不存在: {source_project}")

    work_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "thread_id_case": run_thread_id_case(mini_agent_dir, source_project, work_dir),
        "schema_case": run_schema_case(mini_agent_dir, source_project, work_dir),
    }

    print("\n========== Regression Summary ==========")
    for name, ok in results.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")

    if all(results.values()):
        print("ALL PASSED")
        return

    raise SystemExit("Some regression cases failed")


if __name__ == "__main__":
    main()
