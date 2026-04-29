#!/usr/bin/env python3
"""Stage 1A Harness: Multi-hop QA with OpenCode + vLLM
Fixed: properly handle OpenCode JSON output format."""

import json
import os
import subprocess
import time
import re
import sys
from pathlib import Path

PROJECT_ROOT = "/home/jinxu/opencode-spawn-pilot"
OUTPUT_ROOT = f"{PROJECT_ROOT}/outputs/opencode_spawn_pilot"
TASKS_FILE = f"{OUTPUT_ROOT}/tasks.jsonl"
TASK_DATA_DIR = f"{OUTPUT_ROOT}/task_data"
OPENCODE_BIN = "/home/jinxu/.opencode/bin/opencode"

SYSTEMS = {
    "build_only": {
        "agent": "build",
        "subagent_instruction": (
            "IMPORTANT: Do NOT use any subagents (no Explore, no General, no Task). "
            "Complete this task yourself using only direct tool calls (grep, read)."
        ),
        "constraint_note": "no_subagents"
    },
    "build_explore": {
        "agent": "build",
        "subagent_instruction": (
            "You MAY use the Explore subagent to search for and read relevant documents. "
            "Explore is read-only and helps find relevant paragraphs. "
            "You are responsible for integrating all information and providing the final answer."
        ),
        "constraint_note": "explore_allowed"
    },
    "build_explore_general": {
        "agent": "build",
        "subagent_instruction": (
            "You MAY use Explore and General subagents. "
            "Explore: find relevant documents (read-only). "
            "General: verify findings, compare hypotheses, or check reasoning. "
            "You are responsible for integrating all findings and providing the final answer."
        ),
        "constraint_note": "all_subagents_allowed"
    }
}

PROMPT_TEMPLATE = """TASK: Answer a multi-hop question by searching and reading documents.

QUESTION: {question}

RESOURCES:
- documents.txt: {num_paras} Wikipedia-style paragraphs. Each starts with "--- PARAGRAPH N ---".

PROCESS:
1. Use `grep` to search for keywords in documents.txt
2. Use `read` to read documents.txt to find specific paragraphs
3. Chain information across paragraphs ({num_hops}-hop question)
4. When ready, output exactly on its own line: ANSWER: <your answer>

{subagent_instruction}

Begin now."""


def load_tasks():
    tasks = []
    with open(TASKS_FILE) as f:
        for line in f:
            if line.strip():
                tasks.append(json.loads(line))
    return tasks


def prepare_workdir(run_id, task):
    workdir = f"{OUTPUT_ROOT}/runs/{run_id}"
    os.makedirs(workdir, exist_ok=True)

    task_data_file = f"{TASK_DATA_DIR}/{task['task_id']}.json"
    with open(task_data_file) as f:
        task_data = json.load(f)

    with open(f"{workdir}/documents.txt", "w") as f:
        for para in task_data["paragraphs"]:
            f.write(f"--- PARAGRAPH {para['idx']} ---\n")
            f.write(f"Title: {para['title']}\n")
            f.write(f"{para['text']}\n\n")

    with open(f"{workdir}/task_info.json", "w") as f:
        json.dump({
            "run_id": run_id, "task_id": task["task_id"],
            "question": task["question"], "answer": task["answer"],
            "answer_aliases": task["answer_aliases"],
            "num_hops": task["num_hops"],
            "difficulty_bucket": task["difficulty_bucket"]
        }, f, indent=2)

    return workdir, task_data


def make_prompt(task, system_key):
    sys_cfg = SYSTEMS[system_key]
    return PROMPT_TEMPLATE.format(
        question=task["question"],
        num_paras=task["num_paragraphs"],
        num_hops=task["num_hops"],
        subagent_instruction=sys_cfg["subagent_instruction"]
    )


def run_opencode(workdir, prompt, timeout=300):
    """Run OpenCode, writing stderr to file to avoid pipe deadlock"""
    cmd = [
        OPENCODE_BIN, "run",
        "--agent", "build",
        "--dir", workdir,
        "--format", "json",
        "--print-logs",
        prompt
    ]

    stderr_file = f"{workdir}/opencode_stderr_raw.txt"

    start = time.time()
    with open(stderr_file, "w") as stderr_f:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=stderr_f,
            text=True, cwd=workdir
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            elapsed = timeout
            timeout_flag = True
        else:
            elapsed = time.time() - start
            timeout_flag = False

    # Read stderr from file
    with open(stderr_file) as f:
        stderr = f.read()

    return {"stdout": stdout or "", "stderr": stderr or "",
            "exit_code": proc.returncode, "runtime_sec": elapsed,
            "timeout": timeout_flag}


def extract_answer(stdout, stderr):
    """Extract final answer from OpenCode output. Checks JSON text parts in both stdout and stderr."""
    # Check both stdout and stderr JSON lines for text parts containing ANSWER:
    combined = (stdout or "") + "\n" + (stderr or "")
    for line in combined.split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            # Also check raw lines (for non-JSON output)
            m = re.match(r'^ANSWER:\s*(.+?)\s*$', line, re.IGNORECASE)
            if m:
                return m.group(1).strip().rstrip('.')
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, KeyError):
            continue
        if data.get("type") == "text":
            text = data.get("text") or data.get("part", {}).get("text", "")
            m = re.search(r'ANSWER:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
            if m:
                return m.group(1).strip().rstrip('.')

    # Fallback: last text part from either stream
    last_text = ""
    for line in combined.split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, KeyError):
            continue
        if data.get("type") == "text":
            last_text = data.get("text") or data.get("part", {}).get("text", "")
    if last_text:
        return last_text.strip().split('\n')[-1].rstrip('.')

    return ""


def evaluate(answer, gold_answer, aliases):
    answer_clean = answer.strip().lower().rstrip('.')
    gold_clean = gold_answer.strip().lower().rstrip('.')

    if answer_clean == gold_clean:
        return True, "exact_match"

    for alias in aliases:
        if answer_clean == alias.strip().lower().rstrip('.'):
            return True, "alias_match"

    ans_tokens = set(answer_clean.split())
    gold_tokens = set(gold_clean.split())
    if ans_tokens and gold_tokens:
        overlap = ans_tokens & gold_tokens
        if len(overlap) / max(len(ans_tokens), len(gold_tokens)) > 0.6:
            return True, "partial_match"

    return False, "mismatch"


def parse_metrics(stdout):
    """Parse token usage and subagent calls from stdout JSON logs"""
    tokens = {"input": 0, "output": 0, "total": 0}
    subagent_calls = 0
    steps = 0

    for line in (stdout or "").split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = data.get("type", "")
        if t == "step_finish":
            steps += 1
            tok = data.get("part", {}).get("tokens", {})
            tokens["input"] += tok.get("input", 0)
            tokens["output"] += tok.get("output", 0)
            tokens["total"] += tok.get("total", 0)

        if t == "step_start":
            agent_info = data.get("part", {}).get("agent", "")
            if agent_info in ("explore", "general"):
                subagent_calls += 1
                tokens["output"] += 46  # approximate per-step overhead

    return tokens, subagent_calls, steps


def save_run_log(run_entry):
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(run_entry, ensure_ascii=False) + "\n")


def run_single(task, system_key):
    """Run a single task+system combo, return result"""
    run_id = f"{task['task_id']}__{system_key}__s0"
    workdir, task_data = prepare_workdir(run_id, task)
    prompt = make_prompt(task, system_key)

    with open(f"{workdir}/prompt.txt", "w") as f:
        f.write(prompt)

    result = run_opencode(workdir, prompt, timeout=300)

    with open(f"{workdir}/stdout.txt", "w") as f:
        f.write(result["stdout"])
    with open(f"{workdir}/stderr.txt", "w") as f:
        f.write(result["stderr"])

    answer = extract_answer(result["stdout"], result["stderr"])
    success, eval_detail = evaluate(answer, task["answer"], task["answer_aliases"])

    tokens, subagent_calls, steps = parse_metrics(result["stdout"])

    entry = {
        "run_id": run_id,
        "task_id": task["task_id"],
        "system": system_key,
        "budget_setting": "equal_generation",
        "seed": 0,
        "model": "qwen35-9b",
        "difficulty_bucket": task["difficulty_bucket"],
        "num_hops": task["num_hops"],
        "question": task["question"],
        "gold_answer": task["answer"],
        "predicted_answer": answer,
        "success": success,
        "eval_detail": eval_detail,
        "token_usage": {
            "input_tokens": {"total": tokens["input"]},
            "output_tokens": {"total": tokens["output"]},
            "total_tokens": tokens["total"]
        },
        "runtime_sec": result["runtime_sec"],
        "steps": steps,
        "subagent_calls_total": subagent_calls,
        "exit_code": result.get("exit_code", -1),
        "timeout": result.get("timeout", False),
        "failure_analysis": {
            "failed": not success,
            "primary_failure_type": "none" if success else "other",
            "failure_tags": [],
            "notes": ""
        }
    }

    save_run_log(entry)
    return entry


def main():
    tasks = load_tasks()

    # Allow running a subset for quick test
    if len(sys.argv) > 1:
        subset = sys.argv[1]
        if subset == "test":
            tasks = tasks[:1]  # just first task
            print("TEST MODE: 1 task")
        elif subset == "s1":
            # Just build_only for quick test
            systems_to_run = ["build_only"]
        else:
            tasks = [t for t in tasks if t["task_id"] == subset]

    systems_to_run = ["build_only", "build_explore", "build_explore_general"]

    print(f"Harness: {len(tasks)} tasks × {len(systems_to_run)} systems = {len(tasks)*len(systems_to_run)} runs")
    sys.stdout.flush()

    results = []
    for i, task in enumerate(tasks):
        for j, system_key in enumerate(systems_to_run):
            idx = i * len(systems_to_run) + j + 1
            total = len(tasks) * len(systems_to_run)
            print(f"\n[{idx}/{total}] {task['task_id']} | {system_key} | {task['num_hops']}hop")
            sys.stdout.flush()

            entry = run_single(task, system_key)

            status = "✅" if entry["success"] else "❌"
            print(f"  {status} Answer: '{entry['predicted_answer'][:60]}' (gold: '{entry['gold_answer']}') | "
                  f"{entry['token_usage']['total_tokens']} tok | {entry['runtime_sec']:.0f}s")
            sys.stdout.flush()
            results.append(entry)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(results)} runs")
    for sk in systems_to_run:
        rr = [r for r in results if r["system"] == sk]
        sr = sum(1 for r in rr if r["success"]) / len(rr) if rr else 0
        avg_tok = sum(r["token_usage"]["total_tokens"] for r in rr) / len(rr) if rr else 0
        avg_time = sum(r["runtime_sec"] for r in rr) / len(rr) if rr else 0
        avg_sub = sum(r["subagent_calls_total"] for r in rr) / len(rr) if rr else 0
        print(f"  {sk}: {sr:.0%} | avg {avg_tok:.0f} tok | {avg_time:.0f}s | {avg_sub:.1f} subagents")


if __name__ == "__main__":
    main()
