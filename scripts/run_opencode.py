#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — Two-mode experiment
  --mode no-subagent     baseline: build agent with read-only
  --mode with-subagent   build agent + task tool hint to spawn explore

Key finding: build agent has 'task' tool (can spawn explore), but model
needs explicit hint to use it. Without hint, model only uses read.
"""

import argparse
import json
import subprocess
import sys
import time
import re
from pathlib import Path
from datetime import datetime

OPENCODE = "/home/jinxu/.opencode/bin/opencode"
MODEL = "local/qwen35-9b"
WORKDIR = Path("/home/jinxu/opencode-spawn-pilot")
DATA_DIR = WORKDIR / "outputs/opencode_spawn_pilot/task_data"
RUNS_FILE = WORKDIR / "outputs/opencode_spawn_pilot/runs.jsonl"

# Mode A: baseline - no mention of task/spawn tools
SYSTEM_NO_SUBAGENT = """You are a helpful coding and research agent.

You have access to a 'read' tool to read files.

Answer the user's question by reading the provided documents.
Search documents using the read tool, then output your answer as:
ANSWER: <your answer>"""

# Mode B: with subagent - explicit hint about task tool
SYSTEM_WITH_SUBAGENT = """You are a helpful coding and research agent.

You have access to:
  - a 'read' tool to read files
  - a 'task' tool to spawn subagents for parallel exploration

When the question requires searching multiple paragraphs, use the 'task' tool
to spawn an 'explore' subagent to search in parallel, then synthesize results.

Task tool format:
  tool: task
  input: {description, prompt, subagent_type: "explore"}

Example: If you need to search for "Lostock Dam" across documents, spawn a subagent:
  task(description="search Lostock Dam", prompt="Search paragraphs for Lostock Dam", subagent_type="explore")

Answer the user's question by searching the documents.
Output your final answer as:
ANSWER: <your answer>"""


def load_tasks():
    tasks = []
    for tf in sorted(DATA_DIR.glob("*.json")):
        with open(tf) as f:
            tasks.append(json.load(f))
    return tasks


def build_documents_txt(task):
    lines = []
    for p in task["paragraphs"]:
        lines.append(f"[Paragraph {p['idx']}] {p['title']}")
        lines.append(p["text"])
        lines.append("")
    return "\n".join(lines)


def run_opencode(mode, task, run_id):
    task_id = task["id"]
    question = task["question"]
    answer = task["answer"]

    run_dir = WORKDIR / "outputs/opencode_spawn_pilot/runs" / f"{task_id}__{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_txt = build_documents_txt(task)
    docs_path = run_dir / "documents.txt"
    docs_path.write_text(docs_txt)

    prompt = f"""Use the documents provided in {docs_path}.

Question: {question}

Search the documents to find the answer. Output your final answer on its own line:
ANSWER: <your answer>"""

    system = SYSTEM_WITH_SUBAGENT if mode == "with-subagent" else SYSTEM_NO_SUBAGENT

    cmd = [
        OPENCODE, "run",
        "--agent", "build",
        "--model", MODEL,
        "--format", "json",
        "--title", f"musique-{task_id}",
        "--", prompt
    ]

    start = time.time()

    # Use clean environment - need HOME and PATH for binary to work
    env = {
        "HOME": "/home/jinxu",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(run_dir),
    )
    elapsed = time.time() - start
    result_text = proc.stdout

    # Write raw output for debugging
    (run_dir / "opencode_raw_output.jsonl").write_text(result_text)

    # Parse output
    predicted = None
    spawn_detected = False
    task_tool_calls = 0

    for line in result_text.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "text":
                text = obj.get("part", {}).get("text", "")
                m = re.search(r"^ANSWER:\s*(.+)$", text, re.MULTILINE)
                if m:
                    predicted = m.group(1).strip()
            elif obj.get("type") == "tool_use":
                tool = obj.get("part", {}).get("tool", "")
                if tool == "task":
                    task_tool_calls += 1
                    spawn_detected = True
        except json.JSONDecodeError:
            continue

    # Evaluate
    if predicted:
        pl = predicted.lower()
        al = answer.lower()
        correct = al in pl or pl in al or any(a.lower() in pl for a in task.get("answer_aliases", []))
    else:
        correct = False

    return {
        "run_id": f"{task_id}__{run_id}",
        "task_id": task_id,
        "mode": mode,
        "question": question,
        "predicted": predicted,
        "correct": correct,
        "answer": answer,
        "spawn_detected": spawn_detected,
        "task_tool_calls": task_tool_calls,
        "elapsed": round(elapsed, 1),
        "exit_code": proc.returncode,
        "timestamp": datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["no-subagent", "with-subagent"], required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    tasks = load_tasks()
    if args.limit:
        tasks = tasks[:args.limit]

    print(f"Running {len(tasks)} tasks in mode: {args.mode}")
    print(f"Model: {MODEL}  OpenCode: {OPENCODE}")
    print()

    results = []
    for i, task in enumerate(tasks):
        task_id = task["id"]
        run_id = f"{args.mode}-{int(time.time())}"
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end="", flush=True)
        try:
            result = run_opencode(args.mode, task, run_id)
            status = "✓" if result["correct"] else "✗"
            spawn = f" [spawn×{result['task_tool_calls']}]" if result["spawn_detected"] else ""
            print(f"{status}{spawn} ({result['elapsed']}s)")
            if not result["correct"] and result["predicted"]:
                print(f"   Predicted: {result['predicted'][:80]}")
                print(f"   Answer:    {result['answer'][:80]}")
            results.append(result)
        except subprocess.TimeoutExpired:
            print("TIMEOUT (300s)")
            results.append({"run_id": f"{task_id}__{run_id}", "task_id": task_id, "mode": args.mode,
                            "correct": False, "spawn_detected": False, "task_tool_calls": 0,
                            "elapsed": 300, "exit_code": -1, "error": "timeout"})
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"run_id": f"{task_id}__{run_id}", "task_id": task_id, "mode": args.mode,
                            "correct": False, "spawn_detected": False, "task_tool_calls": 0,
                            "elapsed": 0, "exit_code": -1, "error": str(e)})

    # Summary
    n = len(results)
    correct = sum(1 for r in results if r["correct"])
    spawns = sum(r.get("task_tool_calls", 0) for r in results)
    print()
    print("=" * 50)
    print(f"Mode: {args.mode}")
    print(f"Accuracy: {correct}/{n} = {100*correct/n:.0f}%")
    print(f"Total task tool calls: {spawns}")
    print()

    # Append to runs file
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS_FILE, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results saved to {RUNS_FILE}")


if __name__ == "__main__":
    main()
