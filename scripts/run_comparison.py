#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — Controlled Experiment v2
Compares single-agent vs forced-multi-agent using custom system prompts.

Key insight: OpenCode's default system prompt includes spawn instructions.
By configuring agent.build.prompt via ~/.config/opencode/opencode.json, we override the default.

Mode A (single-agent): System prompt says "MUST NOT use task tool"
Mode B (forced-multi): System prompt says "MUST use task tool to spawn subagents"

IMPORTANT: Config is written to ~/.config/opencode/opencode.json directly (OPENCODE_CONFIG env var does not work).
"""

import argparse
import json
import subprocess
import sys
import time
import re
import shutil
from pathlib import Path
from datetime import datetime

OPENCODE = "/home/jinxu/.opencode/bin/opencode"
MODEL = "local/qwen35-9b"
WORKDIR = Path("/home/jinxu/opencode-spawn-pilot")
CONFIG_FILE = Path("/home/jinxu/.config/opencode/opencode.json")
DATA_DIR = WORKDIR / "outputs/opencode_spawn_pilot/task_data"
OUTPUT_DIR = WORKDIR / "outputs/opencode_spawn_pilot/comparison"
RUNS_FILE = OUTPUT_DIR / "results.jsonl"

# Single agent system prompt (no spawn)
SYSTEM_SINGLE = """You are a research agent solving multi-hop questions using ONLY document search.

CRITICAL RESTRICTIONS:
1. You MUST NOT use the 'task' tool under any circumstances
2. You MUST NOT spawn any subagents
3. Use ONLY read, grep, and bash tools to search documents
4. Do NOT answer from your own knowledge — search the documents

Output format:
ANSWER: <your answer>"""

# Forced multi-agent system prompt (must spawn)
SYSTEM_MULTI = """You are a research agent solving multi-hop questions. You MUST use the 'task' tool to spawn subagents for document search.

CRITICAL:
1. You MUST use task(description="<search>", prompt="Read the file <FILEPATH> and find <INFO>", subagent_type="explore") to search documents
2. Do NOT use read or grep to search - only use task tool
3. Wait for subagent results before answering

Output format:
ANSWER: <your answer>"""

MODES = {
    "single": SYSTEM_SINGLE,
    "multi": SYSTEM_MULTI,
}


def build_config(system_prompt: str) -> dict:
    """Build OpenCode config JSON for a given system prompt."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "provider": {
            "local": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Local vLLM",
                "options": {"baseURL": "http://127.0.0.1:8010/v1"},
                "models": {
                    "qwen35-9b": {"name": "qwen35-9b", "maxOutputTokens": 8192}
                },
            }
        },
        "agent": {"build": {"prompt": system_prompt}},
    }


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


def run_opencode(mode, task, run_id, config):
    task_id = task["id"]
    question = task["question"]
    answer = task["answer"]

    run_dir = OUTPUT_DIR / f"{mode}" / f"{task_id}__{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_txt = build_documents_txt(task)
    docs_path = run_dir / "documents.txt"
    docs_path.write_text(docs_txt)

    user_prompt = f"""Use the documents provided in {docs_path}.

Question: {question}

Search the documents to find the answer. Output your final answer on its own line:
ANSWER: <your answer>"""

    cmd = [
        OPENCODE, "run",
        "--agent", "build",
        "--model", MODEL,
        "--format", "json",
        "--title", f"musique-{task_id}",
        "--", user_prompt
    ]

    start = time.time()

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(run_dir),
    )
    elapsed = time.time() - start
    result_text = proc.stdout

    # Write raw output for debugging
    (run_dir / "opencode_raw_output.jsonl").write_text(result_text)

    # Parse output
    predicted = None
    task_tool_calls = 0
    subagent_spawned = False
    subagent_returned = False

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
                    subagent_spawned = True
                    state = obj.get("part", {}).get("state", {})
                    output = state.get("output", "")
                    if "<task_result>" in output:
                        subagent_returned = True
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
        "task_tool_calls": task_tool_calls,
        "subagent_spawned": subagent_spawned,
        "subagent_returned": subagent_returned,
        "elapsed": round(elapsed, 1),
        "exit_code": proc.returncode,
        "timestamp": datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Compare single vs multi-agent on MuSiQue")
    parser.add_argument("--mode", choices=["single", "multi", "both"], default="both",
                        help="Which mode to run")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of tasks")
    parser.add_argument("--tasks", type=str, default=None,
                        help="Comma-separated task IDs to run")
    args = parser.parse_args()

    tasks = load_tasks()
    if args.tasks:
        task_ids = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in task_ids]
    if args.limit:
        tasks = tasks[:args.limit]

    print(f"Model: {MODEL}  OpenCode: {OPENCODE}")
    print(f"Data: {len(tasks)} tasks")
    print()

    modes_to_run = ["single", "multi"] if args.mode == "both" else [args.mode]

    # Backup existing config
    backup_content = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else None

    all_results = []

    try:
        for mode in modes_to_run:
            print(f"{'='*50}")
            print(f"Mode: {mode.upper()}")
            print(f"{'='*50}")

            # Build and write config for this mode
            config = build_config(MODES[mode])
            CONFIG_FILE.write_text(json.dumps(config, indent=2))
            print(f"Config written to {CONFIG_FILE}")

            run_id = f"{mode}-{int(time.time())}"

            for i, task in enumerate(tasks):
                task_id = task["id"]
                print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end="", flush=True)

                try:
                    result = run_opencode(mode, task, run_id, config)
                    status = "✓" if result["correct"] else "✗"
                    spawn = f" [spawn×{result['task_tool_calls']}]" if result["subagent_spawned"] else ""
                    ret = f" [returned]" if result["subagent_returned"] else ""
                    print(f"{status}{spawn}{ret} ({result['elapsed']}s)")

                    if not result["correct"] and result["predicted"]:
                        print(f"   Predicted: {result['predicted'][:80]}")
                        print(f"   Answer:    {result['answer'][:80]}")

                    all_results.append(result)
                except subprocess.TimeoutExpired:
                    print("TIMEOUT (300s)")
                    all_results.append({
                        "run_id": f"{task_id}__{run_id}",
                        "task_id": task_id,
                        "mode": mode,
                        "correct": False,
                        "subagent_spawned": False,
                        "subagent_returned": False,
                        "task_tool_calls": 0,
                        "elapsed": 300,
                        "exit_code": -1,
                        "error": "timeout"
                    })
                except Exception as e:
                    print(f"ERROR: {e}")
                    all_results.append({
                        "run_id": f"{task_id}__{run_id}",
                        "task_id": task_id,
                        "mode": mode,
                        "correct": False,
                        "subagent_spawned": False,
                        "subagent_returned": False,
                        "task_tool_calls": 0,
                        "elapsed": 0,
                        "exit_code": -1,
                        "error": str(e)
                    })

            # Per-mode summary
            mode_results = [r for r in all_results if r["mode"] == mode]
            n = len(mode_results)
            correct = sum(1 for r in mode_results if r["correct"])
            spawns = sum(r.get("task_tool_calls", 0) for r in mode_results)
            print(f"\nMode {mode}: {correct}/{n} correct ({100*correct/n:.0f}%), {spawns} total spawns")
            print()

    finally:
        # Restore original config
        if backup_content:
            CONFIG_FILE.write_text(backup_content)
        else:
            CONFIG_FILE.unlink()
        print(f"Config restored to original state")

    # Overall summary
    print(f"{'='*50}")
    print("OVERALL SUMMARY")
    print(f"{'='*50}")

    for mode in modes_to_run:
        mode_results = [r for r in all_results if r["mode"] == mode]
        n = len(mode_results)
        correct = sum(1 for r in mode_results if r["correct"])
        spawns = sum(r.get("task_tool_calls", 0) for r in mode_results)
        returned = sum(1 for r in mode_results if r.get("subagent_returned", False))
        print(f"Mode {mode}: accuracy={correct}/{n} ({100*correct/n:.0f}%), spawns={spawns}, returned={returned}")

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUNS_FILE, "a") as f:
        for r in all_results:
            f.write(json.dumps(r) + "\n")

    print(f"\nResults saved to {RUNS_FILE}")


if __name__ == "__main__":
    main()
