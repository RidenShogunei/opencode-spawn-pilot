#!/usr/bin/env python3
"""
Test prompt variants for spawn behavior with validated spawn metrics.
v2.0: Uses --format json to capture structured tool events.

Key insight: OpenCode translates 'agent=' (from model) to 'subagent_type=' (in actual call).
Both agent="general" and agent="explore" work for spawning subagents.

Metrics:
- model_emitted_task_call: model attempted task tool
- spawn_executed: subagent was actually spawned by OpenCode
- subagent_returned: subagent result returned to Build agent
- malformed_call: subagent_type was undefined (bad parameter)
- accuracy: final answer correct
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
OUTPUT_DIR = WORKDIR / "outputs/opencode_spawn_pilot/prompt_variants_v2"
RUNS_FILE = OUTPUT_DIR / "results.jsonl"

# Prompt v1: 强制 — 必须用 task，禁止其他搜索
PROMPT_V1 = """You are a research agent solving multi-hop questions using ONLY the task tool.

CRITICAL:
1. You MUST use the 'task' tool to search documents — this is your ONLY search method
2. Do NOT use bash, grep, read, or any other tool to search documents
3. Do NOT answer from your own knowledge — always search via task subagent first
4. Wait for subagent results before answering

TASK TOOL FORMAT:
  task(description="<what to search>", prompt="<instructions>", subagent_type="explore")

Valid fields: description, prompt, subagent_type (in that order).
DO NOT use: agent, agent_type, type, sub_type.

Example:
  task(description="Lostock Dam river", prompt="In <filepath>, find what river Lostock Dam is on", subagent_type="explore")

Answer format:
ANSWER: <your answer>"""

# Prompt v2: 可选 task tool，不禁止其他
PROMPT_V2 = """You are a research agent solving multi-hop questions.

You have access to the 'task' tool to spawn explore subagents for searching.

TASK TOOL FORMAT:
  task(description="<topic>", prompt="<instructions>", subagent_type="explore")

Valid fields: description, prompt, subagent_type.
DO NOT use: agent, agent_type, type.

Example: task(description="Lostock Dam river", prompt="Find what river Lostock Dam is on", subagent_type="explore")

Output format:
ANSWER: <your answer>"""

# Prompt v3: agent=general 作为对照
PROMPT_V3 = """You are a research agent solving multi-hop questions.

Use the 'task' tool to spawn subagents for parallel searching.

TASK TOOL FORMAT:
  task(description="<topic>", prompt="<instructions>", subagent_type="general")

Valid fields: description, prompt, subagent_type.
DO NOT use: agent, agent_type, type.

Example: task(description="Lostock Dam", prompt="Find what river Lostock Dam is on", subagent_type="general")

Output format:
ANSWER: <your answer>"""

# Prompt v4: Baseline — no task tool mention
PROMPT_V4 = """You are a research agent solving multi-hop questions.

Read the documents and answer based on the information in the documents.
Do not use any subagents.

Output format:
ANSWER: <your answer>"""

VARIANTS = {
    "v1_forced_explore": PROMPT_V1,       # Mode B: forced task, agent=explore
    "v2_task_only_explore": PROMPT_V2,    # Mode B: optional task, agent=explore
    "v3_task_only_general": PROMPT_V3,    # Mode C: optional task, agent=general
    "v4_baseline_no_task": PROMPT_V4,     # Mode A: no task tool
}


def get_tasks(n=5):
    """Get n tasks from task_data, stratified by hop count."""
    tasks = list(DATA_DIR.glob("*.json"))
    by_hop = {"2hop": [], "3hop": [], "4hop": []}
    for t in tasks:
        if "2hop" in t.stem:
            by_hop["2hop"].append(t)
        elif "3hop" in t.stem:
            by_hop["3hop"].append(t)
        else:
            by_hop["4hop"].append(t)

    selected = []
    for hop, count in [("2hop", 2), ("3hop", 1), ("4hop", 2)]:
        selected.extend(by_hop[hop][:count])
    return selected[:n]


def run_opencode(prompt, task_file, run_dir):
    """Run opencode with given prompt on task, return raw stdout+stderr."""
    with open(task_file) as f:
        task_data = json.load(f)

    question = task_data.get("question", "")
    paragraphs = task_data.get("paragraphs", [])

    docs_path = run_dir / "documents.txt"
    with open(docs_path, "w") as f:
        for i, p in enumerate(paragraphs):
            f.write(f"[Paragraph {i}] {p}\n")

    user_msg = f"""{prompt}

Question: {question}

Documents are in {docs_path}. Answer using task subagents to search."""

    env = {
        "HOME": "/home/jinxu",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LD_LIBRARY_PATH": "/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu",
    }

    cmd = [
        OPENCODE, "run",
        "--agent", "build",
        "--model", MODEL,
        "--format", "json",
        "--log-level", "INFO",
        "--", user_msg
    ]

    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(WORKDIR),
    )
    elapsed = time.time() - start

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed": elapsed,
    }


def parse_spawn_events_from_jsonl(raw_output):
    """
    Parse --format json output to extract spawn validation metrics.

    Each line is a JSON object with type: step_start, text, tool_use, tool_result, step_finish, error.
    """
    result = {
        "model_emitted_task_call": False,
        "task_calls": [],          # list of {description, prompt, subagent_type, status}
        "spawn_executed": False,   # at least one subagent was launched
        "subagent_returned": False, # at least one subagent result returned
        "malformed_calls": 0,      # calls where subagent_type was undefined
        "subagent_outputs": [],    # raw outputs from subagents
        "final_text": "",          # last text output
        "token_count": 0,
    }

    for line in raw_output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except:
            continue

        t = obj.get("type", "")

        if t == "tool_use":
            part = obj.get("part", {})
            if part.get("tool") == "task":
                result["model_emitted_task_call"] = True
                inp = part.get("state", {}).get("input", {})
                status = part.get("state", {}).get("status", "")

                call_info = {
                    "description": inp.get("description", ""),
                    "prompt": inp.get("prompt", ""),
                    "subagent_type": inp.get("subagent_type"),
                    "status": status,
                }
                result["task_calls"].append(call_info)

                if status == "completed":
                    output = part.get("state", {}).get("output", "")
                    if "<task_result>" in output:
                        result["subagent_returned"] = True
                        result["spawn_executed"] = True
                        # Extract task_result content
                        m = re.search(r'<task_result>(.*?)</task_result>', output, re.DOTALL)
                        if m:
                            result["subagent_outputs"].append(m.group(1).strip()[:200])

        elif t == "text":
            txt = obj.get("part", {}).get("text", "")
            if txt.strip():
                result["final_text"] = txt.strip()

        elif t == "error":
            err = obj.get("part", {}).get("error", "")
            if "subagent_type" in err and "undefined" in err:
                result["malformed_calls"] += 1

        elif t == "step_finish":
            tokens = obj.get("part", {}).get("tokens", {})
            result["token_count"] += tokens.get("total", 0)

    # Malformed: subagent_type is None or empty string
    for call in result["task_calls"]:
        st = call.get("subagent_type")
        if not st:  # None or empty string
            result["malformed_calls"] += 1

    return result


def extract_answer(output_text):
    """Extract ANSWER line from output text."""
    # Match ANSWER: <content> at start of line
    match = re.search(r'^ANSWER:\s*(.+)$', output_text, re.MULTILINE)
    if match:
        return match.group(1).strip()

    # Fallback: look for any line containing ANSWER
    for line in output_text.split('\n'):
        if 'ANSWER' in line.upper():
            m = re.search(r'ANSWER:\s*(.+)', line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Test spawn behavior with validated metrics")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--variant", choices=list(VARIANTS.keys()) + ["all"], default="all")
    parser.add_argument("--smoke", action="store_true", help="Single smoke test")
    parser.add_argument("--mode", choices=["no-subagent", "with-subagent", "all"], default="all")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mode_filter = {
        "no-subagent": ["v4_baseline_no_task"],
        "with-subagent": ["v1_forced_explore", "v2_task_only_explore", "v3_task_only_general"],
        "all": list(VARIANTS.keys()),
    }[args.mode]

    if args.variant != "all":
        mode_filter = [args.variant]

    tasks = get_tasks(args.limit)
    if args.smoke:
        tasks = tasks[:1]

    results = []

    for variant_name in mode_filter:
        prompt = VARIANTS[variant_name]
        print(f"\n{'='*60}")
        print(f"Variant: {variant_name}")
        print(f"{'='*60}")

        for i, task_file in enumerate(tasks):
            run_id = f"{variant_name}_{task_file.stem}_{int(time.time())}"
            run_dir = OUTPUT_DIR / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n[{i+1}/{len(tasks)}] {task_file.stem}...", end=" ", flush=True)

            try:
                res = run_opencode(prompt, task_file, run_dir)
                raw_output = res["stdout"] + res["stderr"]

                # Save raw output
                with open(run_dir / "opencode_output.txt", "w") as f:
                    f.write(raw_output)

                # Parse structured events
                spawn_info = parse_spawn_events_from_jsonl(raw_output)
                answer = extract_answer(spawn_info["final_text"])

                # Load ground truth
                with open(task_file) as f:
                    task_data = json.load(f)
                ground_truth = task_data.get("answer", "").strip()

                correct = bool(answer and ground_truth and (
                    answer.lower() in ground_truth.lower() or
                    ground_truth.lower() in answer.lower()
                ))

                entry = {
                    "variant": variant_name,
                    "task": task_file.stem,
                    "run_id": run_id,
                    "question": task_data.get("question", "")[:100],
                    "ground_truth": ground_truth,
                    "answer": answer,
                    "correct": correct,
                    "elapsed": res["elapsed"],
                    # Spawn validation
                    "model_emitted_task_call": spawn_info["model_emitted_task_call"],
                    "task_calls": spawn_info["task_calls"],
                    "spawn_executed": spawn_info["spawn_executed"],
                    "subagent_returned": spawn_info["subagent_returned"],
                    "malformed_calls": spawn_info["malformed_calls"],
                    "subagent_outputs": spawn_info["subagent_outputs"],
                    "final_text_preview": spawn_info["final_text"][:200],
                    "token_count": spawn_info["token_count"],
                }

                results.append(entry)

                # Print status
                status_parts = []
                if spawn_info["model_emitted_task_call"]:
                    status_parts.append("task_call")
                    if spawn_info["malformed_calls"] > 0:
                        status_parts.append(f"malformed({spawn_info['malformed_calls']})")
                else:
                    status_parts.append("no_task_call")
                if spawn_info["spawn_executed"]:
                    status_parts.append("spawned")
                if spawn_info["subagent_returned"]:
                    status_parts.append("returned")
                status_parts.append("✓" if correct else "✗")
                print(" | ".join(status_parts))

                if spawn_info["subagent_outputs"]:
                    print(f"  subagent outputs: {len(spawn_info['subagent_outputs'])} result(s)")
                    for out in spawn_info["subagent_outputs"]:
                        print(f"    {out[:100]}")

            except subprocess.TimeoutExpired:
                print("TIMEOUT")
                results.append({
                    "variant": variant_name,
                    "task": task_file.stem,
                    "run_id": run_id,
                    "error": "timeout",
                    "correct": False,
                    "model_emitted_task_call": False,
                    "spawn_executed": False,
                    "subagent_returned": False,
                })
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    "variant": variant_name,
                    "task": task_file.stem,
                    "run_id": run_id,
                    "error": str(e),
                    "correct": False,
                    "model_emitted_task_call": False,
                    "spawn_executed": False,
                    "subagent_returned": False,
                })

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    by_variant = {}
    for r in results:
        v = r["variant"]
        if v not in by_variant:
            by_variant[v] = []
        by_variant[v].append(r)

    print(f"\n{'Variant':<25} {'N':<3} {'TaskCall':<9} {'Malform':<9} {'SpawnExec':<10} {'Returned':<9} {'Acc':<5}")
    print("-" * 75)

    for variant_name, runs in by_variant.items():
        n = len(runs)
        task_calls = sum(1 for r in runs if r.get("model_emitted_task_call"))
        malformed = sum(1 for r in runs if r.get("malformed_calls", 0) > 0)
        spawned = sum(1 for r in runs if r.get("spawn_executed"))
        returned = sum(1 for r in runs if r.get("subagent_returned"))
        correct = sum(1 for r in runs if r.get("correct"))
        print(f"{variant_name:<25} {n:<3} {task_calls}/{n:<8} {malformed}/{n:<8} {spawned}/{n:<9} {returned}/{n:<8} {correct}/{n}")

    # Save results
    with open(RUNS_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {RUNS_FILE}")


if __name__ == "__main__":
    main()
