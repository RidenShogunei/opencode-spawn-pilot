#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — Simplified Runner
Two modes:
  --mode no-subagent    Build agent alone (baseline)
  --mode with-subagent  Build agent + explore subagent available
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
WORKDIR = "/home/jinxu/opencode-spawn-pilot"
DATA_DIR = Path(WORKDIR) / "outputs/opencode_spawn_pilot/task_data"
TASKS_FILE = Path(WORKDIR) / "outputs/opencode_spawn_pilot/tasks.jsonl"
RUNS_FILE = Path(WORKDIR) / "outputs/opencode_spawn_pilot/runs.jsonl"


SYSTEM_PROMPT_NO_SUBAGENT = """You are a helpful coding and research agent.

You have access to tools: bash, grep, read, write, glob, etc.

Answer the user's question by searching the provided documents.
Use grep to search and read to inspect specific sections.
When you find the answer, output it directly.
"""

SYSTEM_PROMPT_WITH_SUBAGENT = """You are a helpful coding and research agent.

You have access to tools: bash, grep, read, write, glob, and you can also spawn subagents.

AVAILABLE SUBAGENT:
  bash /home/jinxu/opencode-spawn-pilot/scripts/spawn_explore.sh <task_id> <run_id> <workdir> "<exploration_task>"

The explore subagent can search documents and return findings. Use it when the question requires searching multiple paragraphs.

Answer the user's question by searching the provided documents.
Use grep to search and read to inspect specific sections.
When you find the answer, output it directly.
"""


def load_tasks():
    """Load task list from task_data/*.json files."""
    tasks = []
    task_files = sorted(DATA_DIR.glob("*.json"))
    for tf in task_files:
        with open(tf) as f:
            d = json.load(f)
            d["_file"] = tf.name  # track source file
            tasks.append(d)
    return tasks


def build_documents_txt(task):
    """Build a documents.txt from task paragraphs."""
    lines = []
    for p in task["paragraphs"]:
        lines.append(f"[Paragraph {p['idx']}] {p['title']}")
        lines.append(p["text"])
        lines.append("")
    return "\n".join(lines)


def run_opencode(mode, task, run_id):
    """Run OpenCode on a single task."""
    task_id = task["id"]
    question = task["question"]
    answer = task["answer"]

    # Build workdir for this run
    run_dir = Path(WORKDIR) / "outputs/opencode_spawn_pilot/runs" / f"{task_id}__{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write documents.txt
    docs_txt = build_documents_txt(task)
    docs_path = run_dir / "documents.txt"
    docs_path.write_text(docs_txt)

    # Write task prompt
    prompt = f"""Use the documents provided in {docs_path}.

Question: {question}

Search the documents to find the answer. Output your final answer on its own line:
ANSWER: <your answer>"""

    # System prompt
    system_prompt = SYSTEM_PROMPT_WITH_SUBAGENT if mode == "with-subagent" else SYSTEM_PROMPT_NO_SUBAGENT

    # Build command
    cmd = [
        OPENCODE,
        "run",
        "--agent", "build",
        "--model", MODEL,
        "--format", "json",
        "--title", f"musique-{task_id}",
        "--",
        prompt
    ]

    start = time.time()
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

    # Parse output
    result_text = proc.stdout
    error_text = proc.stderr

    # Extract answer
    predicted = None
    spawn_detected = False

    for line in result_text.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "text":
                text = obj.get("part", {}).get("text", "")
                # Look for ANSWER line
                m = re.search(r"^ANSWER:\s*(.+)$", text, re.MULTILINE)
                if m:
                    predicted = m.group(1).strip()
                # Check for spawn call
                if "spawn_explore.sh" in text or "spawn explore" in text.lower():
                    spawn_detected = True
            elif obj.get("type") == "tool_use":
                tool = obj.get("part", {}).get("tool", "")
                if tool == "bash":
                    cmd_input = obj.get("part", {}).get("state", {}).get("input", {}).get("command", "")
                    if "spawn_explore.sh" in cmd_input:
                        spawn_detected = True
        except json.JSONDecodeError:
            continue

    # Check stderr for spawn
    if "spawn" in error_text.lower() and "explore" in error_text.lower():
        spawn_detected = True

    # Evaluate
    correct = None
    if predicted:
        # Simple contains check
        predicted_lower = predicted.lower()
        answer_lower = answer.lower()
        # Check if answer is contained in prediction or vice versa
        if answer_lower in predicted_lower or predicted_lower in answer_lower:
            correct = True
        else:
            # Check aliases
            aliases = task.get("answer_aliases", [])
            for alias in aliases:
                if alias.lower() in predicted_lower:
                    correct = True
                    break
            if correct is None:
                correct = False
    else:
        correct = False  # No answer extracted

    return {
        "run_id": f"{task_id}__{run_id}",
        "task_id": task_id,
        "mode": mode,
        "question": question,
        "predicted": predicted,
        "correct": correct,
        "answer": answer,
        "spawn_detected": spawn_detected,
        "elapsed": round(elapsed, 1),
        "exit_code": proc.returncode,
        "timestamp": datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["no-subagent", "with-subagent"], required=True)
    parser.add_argument("--task", type=str, default=None, help="Run specific task_id")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    args = parser.parse_args()

    tasks = load_tasks()
    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]
        if not tasks:
            print(f"Task {args.task} not found")
            sys.exit(1)
    if args.limit:
        tasks = tasks[:args.limit]

    print(f"Running {len(tasks)} tasks in mode: {args.mode}")
    print(f"Model: {MODEL}")
    print(f"OpenCode: {OPENCODE}")
    print()

    results = []
    for i, task in enumerate(tasks):
        task_id = task["id"]
        run_id = f"{args.mode}-{int(time.time())}"
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end="", flush=True)

        try:
            result = run_opencode(args.mode, task, run_id)
            status = "✓" if result["correct"] else "✗"
            spawn = " [SPAWNED]" if result["spawn_detected"] else ""
            print(f"{status}{spawn} ({result['elapsed']}s)")
            if result["predicted"]:
                print(f"   Predicted: {result['predicted'][:80]}")
                print(f"   Answer:    {result['answer'][:80]}")
            results.append(result)
        except subprocess.TimeoutExpired:
            print(f"TIMEOUT (300s)")
            results.append({
                "run_id": f"{task_id}__{run_id}",
                "task_id": task_id,
                "mode": args.mode,
                "correct": False,
                "spawn_detected": False,
                "elapsed": 300,
                "exit_code": -1,
                "error": "timeout",
            })
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "run_id": f"{task_id}__{run_id}",
                "task_id": task_id,
                "mode": args.mode,
                "correct": False,
                "spawn_detected": False,
                "elapsed": 0,
                "exit_code": -1,
                "error": str(e),
            })

    # Summary
    print()
    print("=" * 50)
    n = len(results)
    correct = sum(1 for r in results if r["correct"])
    spawns = sum(1 for r in results if r["spawn_detected"])
    print(f"Mode: {args.mode}")
    print(f"Accuracy: {correct}/{n} = {100*correct/n:.0f}%")
    print(f"Spawn detected: {spawns}/{n}")
    print()

    # Append results to runs file
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS_FILE, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print(f"Results saved to {RUNS_FILE}")


if __name__ == "__main__":
    main()
