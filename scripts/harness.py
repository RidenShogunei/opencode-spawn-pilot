#!/usr/bin/env python3
"""
New Architecture Harness: spawn_closed vs spawn_open comparison
In spawn_closed: Build agent runs alone, subagent system is non-functional.
In spawn_open: Build agent knows it CAN call 'opencode run --agent explore' when it wants.
Both modes run only ONE OpenCode invocation per task — no forced subagent chain.
"""

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

MODES = {
    "spawn_closed": {
        "description": "Build-only. Agent has no knowledge of subagent capability.",
        "can_spawn": False,
    },
    "spawn_open": {
        "description": "Build agent CAN call explore subagent when it decides to.",
        "can_spawn": True,
    },
}


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
            "run_id": run_id,
            "task_id": task["task_id"],
            "question": task["question"],
            "answer": task["answer"],
            "answer_aliases": task["answer_aliases"],
            "num_hops": task["num_hops"],
            "difficulty_bucket": task["difficulty_bucket"],
        }, f, indent=2)

    return workdir, task_data


def run_opencode(workdir, prompt, agent="build", timeout=600, log_suffix=""):
    """Run OpenCode. stdout/stderr written to files, returned as content."""
    suffix = f"_{log_suffix}" if log_suffix else ""
    stdout_file = f"{workdir}/stdout{suffix}.txt"
    stderr_file = f"{workdir}/stderr{suffix}.txt"

    cmd = [
        OPENCODE_BIN, "run",
        "--agent", agent,
        "--dir", workdir,
        "--format", "json",
        "--print-logs",
        prompt
    ]

    start = time.time()
    with open(stdout_file, "w", buffering=1) as stdout_f, \
         open(stderr_file, "w", buffering=1) as stderr_f:
        proc = subprocess.Popen(
            cmd, stdout=stdout_f, stderr=stderr_f,
            text=True, cwd=workdir, bufsize=1
        )
        try:
            exit_code = proc.wait(timeout=timeout)
            timeout_flag = False
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            exit_code = proc.returncode
            timeout_flag = True
    elapsed = time.time() - start

    with open(stdout_file) as f:
        stdout = f.read()
    with open(stderr_file) as f:
        stderr = f.read()

    return {
        "stdout": stdout, "stderr": stderr,
        "exit_code": exit_code, "runtime_sec": elapsed,
        "timeout": timeout_flag,
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
    }


def extract_text_output(stdout):
    """Extract all text parts from OpenCode JSON output."""
    texts = []
    for line in (stdout or "").split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "text":
            text = data.get("text") or data.get("part", {}).get("text", "")
            texts.append(text)
    return "\n".join(texts)


def extract_answer(stdout, stderr):
    """Extract final answer from OpenCode output."""
    combined = (stdout or "") + "\n" + (stderr or "")
    for line in combined.split('\n'):
        line = line.strip()
        if not line.startswith('{') and line:
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
    """Evaluate predicted answer against gold answer."""
    answer_clean = re.sub(r'\*+', '', answer).strip().lower().rstrip('.').rstrip(',')
    gold_clean = gold_answer.strip().lower().rstrip('.')

    if answer_clean == gold_clean:
        return True, "exact_match"

    for alias in aliases:
        if answer_clean == alias.strip().lower().rstrip('.'):
            return True, "alias_match"

    # Numeric/ordinal matching
    ordinal_map = {
        "1": "first", "2": "second", "3": "third", "4": "fourth", "5": "fifth",
        "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth"
    }
    ans_nums = re.findall(r'\d+', answer_clean)
    gold_nums = re.findall(r'\d+', gold_clean)
    if ans_nums == gold_nums and ans_nums:
        ans_ord = any(ordinal_map.get(n, "") in answer_clean for n in ans_nums)
        gold_ord = any(ordinal_map.get(n, "") in gold_clean for n in gold_nums)
        if ans_ord or gold_ord:
            return True, "ordinal_match"
    if ans_nums and not gold_nums:
        for n in ans_nums:
            word = ordinal_map.get(n, "")
            if word and word in gold_clean:
                return True, "ordinal_match"
    if gold_nums and not ans_nums:
        for n in gold_nums:
            word = ordinal_map.get(n, "")
            if word and word in answer_clean:
                return True, "ordinal_match"

    # Date partial matching
    if re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}', gold_clean):
        gold_norm = re.sub(r',\s*\d{4}', '', gold_clean)
        ans_norm = re.sub(r',\s*\d{4}', '', answer_clean)
        if gold_norm.strip() == ans_norm.strip():
            return True, "partial_date"

    # Word overlap matching
    ans_tokens = set(re.findall(r'\w+', answer_clean))
    gold_tokens = set(re.findall(r'\w+', gold_clean))
    if ans_tokens and gold_tokens:
        overlap = ans_tokens & gold_tokens
        if len(overlap) / max(len(ans_tokens), len(gold_tokens)) > 0.6:
            return True, "partial_match"

    return False, "mismatch"


def parse_metrics(stdout):
    """Parse token usage from stdout JSON logs."""
    tokens = {"input": 0, "output": 0, "total": 0}
    steps = 0

    for line in (stdout or "").split('\n'):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if data.get("type") in ("step_finish", "step-finish"):
            steps += 1
            tok = data.get("part", {}).get("tokens", {})
            tokens["input"] += tok.get("input", 0)
            tokens["output"] += tok.get("output", 0)
            tokens["total"] += tok.get("total", 0)

    return tokens, steps


def detect_spawn_attempt(stdout):
    """Detect if Build agent attempted to spawn explore subagent.
    Returns (attempted, method) where method is 'cli' or 'direct' or None.
    """
    stdout_lower = (stdout or "").lower()

    # Method 1: CLI-style spawn (what we're teaching it)
    cli_patterns = [
        r'opencode\s+run\s+--agent\s+explore',
        r'opencode run --agent explore',
        r'!opencode run --agent explore',
    ]
    for pat in cli_patterns:
        if re.search(pat, stdout, re.IGNORECASE):
            return True, "cli"

    # Method 2: Direct mention of spawn/explore
    direct_patterns = [
        r'spawn.*explore',
        r'call.*explore',
        r'use.*explore.*subagent',
    ]
    for pat in direct_patterns:
        if re.search(pat, stdout_lower):
            return True, "direct"

    return False, None


def build_prompt_closed(task):
    """Mode A: Build agent alone, no knowledge of subagents."""
    return f"""TASK: Answer a multi-hop question by searching and reading documents.

QUESTION: {task['question']}

RESOURCES:
- documents.txt: {task['num_paragraphs']} Wikipedia-style paragraphs. Each starts with "--- PARAGRAPH N ---".

PROCESS:
1. Use `grep` to search for keywords in documents.txt
2. Use `read` to read specific paragraphs
3. Chain information across paragraphs ({task['num_hops']}-hop question)
4. When ready, output exactly on its own line: ANSWER: <your answer>

IMPORTANT: Work entirely alone. Do not call any other agents or subagents. Use only your own tool calls (grep, read).

Begin now."""


def build_prompt_open(task):
    """Mode B: Build agent knows it CAN call explore subagent when it wants."""
    return f"""TASK: Answer a multi-hop question by searching and reading documents.

QUESTION: {task['question']}

RESOURCES:
- documents.txt: {task['num_paragraphs']} Wikipedia-style paragraphs. Each starts with "--- PARAGRAPH N ---".

PROCESS:
1. You may use `grep` and `read` to search documents directly.
2. If you want to delegate document exploration to a subagent, you can call:
   opencode run --agent explore --dir <workdir> -- <your exploration task>
   The explore subagent will search documents and return findings.
3. Chain information across paragraphs ({task['num_hops']}-hop question)
4. When ready, output exactly on its own line: ANSWER: <your answer>

Decide for yourself whether to use the explore subagent or handle everything directly.

Begin now."""


def save_run_log(entry):
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_single(task, mode_key):
    """Run a single task in the specified mode.
    Only ONE OpenCode invocation — Build agent decides on spawn internally.
    """
    run_id = f"{task['task_id']}__{mode_key}"
    workdir, task_data = prepare_workdir(run_id, task)

    mode_cfg = MODES[mode_key]

    if mode_key == "spawn_closed":
        prompt = build_prompt_closed(task)
    else:
        prompt = build_prompt_open(task)

    with open(f"{workdir}/prompt_build.txt", "w") as f:
        f.write(prompt)

    # Single OpenCode run — Build decides internally
    result = run_opencode(workdir, prompt, agent="build", timeout=600, log_suffix="build")
    elapsed = result["runtime_sec"]

    tokens, steps = parse_metrics(result["stdout"])
    stdout = result["stdout"]
    stderr = result["stderr"]

    # Extract answer
    answer = extract_answer(stdout, stderr)
    success, eval_detail = evaluate(answer, task["answer"], task["answer_aliases"])

    # Detect spawn attempt
    spawn_attempted, spawn_method = detect_spawn_attempt(stdout)

    entry = {
        "run_id": run_id,
        "task_id": task["task_id"],
        "mode": mode_key,
        "model": "qwen35-9b",
        "difficulty_bucket": task["difficulty_bucket"],
        "num_hops": task["num_hops"],
        "question": task["question"],
        "gold_answer": task["answer"],
        "predicted_answer": answer,
        "success": success,
        "eval_detail": eval_detail,
        "token_usage": {
            "input_tokens": tokens["input"],
            "output_tokens": tokens["output"],
            "total_tokens": tokens["total"],
        },
        "runtime_sec": elapsed,
        "steps": steps,
        "spawn_attempted": spawn_attempted,
        "spawn_method": spawn_method,
        "exit_code": result.get("exit_code", -1),
        "timeout": result.get("timeout", False),
    }

    save_run_log(entry)
    return entry


def main():
    tasks = load_tasks()
    resume_mode = "--resume" in sys.argv
    if resume_mode:
        sys.argv.remove("--resume")

    modes_to_run = list(MODES.keys())
    if len(sys.argv) > 1:
        subset = sys.argv[1]
        if subset == "test":
            tasks = tasks[:1]
            print("TEST MODE: 1 task")
        elif subset == "closed":
            modes_to_run = ["spawn_closed"]
        elif subset == "open":
            modes_to_run = ["spawn_open"]
        elif subset == "2hop":
            tasks = [t for t in tasks if t["num_hops"] == 2]
        elif subset == "3hop":
            tasks = [t for t in tasks if t["num_hops"] == 3]
        elif subset == "4hop":
            tasks = [t for t in tasks if t["num_hops"] == 4]
        else:
            tasks = [t for t in tasks if t["task_id"] == subset]

    print(f"Harness: {len(tasks)} tasks × {len(modes_to_run)} modes = {len(tasks)*len(modes_to_run)} runs")
    sys.stdout.flush()

    existing_entries = {}
    if resume_mode:
        log_file = f"{OUTPUT_ROOT}/runs.jsonl"
        if os.path.exists(log_file):
            with open(log_file) as f:
                for line in f:
                    if line.strip():
                        r = json.loads(line)
                        existing_entries[(r["task_id"], r["mode"])] = r
            print(f"RESUME: found {len(existing_entries)} entries in runs.jsonl")

    results = []
    for i, task in enumerate(tasks):
        for j, mode_key in enumerate(modes_to_run):
            idx = i * len(modes_to_run) + j + 1
            total = len(tasks) * len(modes_to_run)
            key = (task["task_id"], mode_key)
            print(f"\n[{idx}/{total}] {task['task_id']} | {mode_key} | {task['num_hops']}hop")
            sys.stdout.flush()

            if resume_mode and key in existing_entries:
                entry = existing_entries[key]
                status = "✅" if entry["success"] else "❌"
                spawn = "🔗" if entry.get("spawn_attempted") else "  "
                print(f"  ⏭️  SKIP | {status}{spawn} Answer: '{str(entry.get('predicted_answer',''))[:50]}' | "
                      f"{entry['token_usage']['total_tokens']} tok")
                results.append(entry)
                continue

            entry = run_single(task, mode_key)

            status = "✅" if entry["success"] else "❌"
            spawn = "🔗" if entry.get("spawn_attempted") else "  "
            print(f"  {status}{spawn} Answer: '{entry['predicted_answer'][:50]}' (gold: '{entry['gold_answer']}') | "
                  f"{entry['token_usage']['total_tokens']} tok | {entry['runtime_sec']:.0f}s")

            results.append(entry)

    # ---- Print Summary ----
    print(f"\n{'='*70}")
    print("RESULTS SUMMARY")
    print(f"{'='*70}")

    by_mode = {}
    for r in results:
        by_mode.setdefault(r["mode"], []).append(r)

    print(f"\n{'Mode':<20} {'Tasks':>5} {'Success':>7} {'Spawned':>8} {'Tok/ans':>12}")
    print("-" * 55)
    for mk in modes_to_run:
        runs = by_mode.get(mk, [])
        if not runs:
            continue
        n = len(runs)
        sr = sum(1 for r in runs if r["success"]) / n
        spawned = sum(1 for r in runs if r.get("spawn_attempted", False))
        print(f"{mk:<20} {n:>5} {sr:>7.0%} {spawned:>8} {'—':>12}")

    print("\nPer-hop breakdown:")
    for hops in [2, 3, 4]:
        hop_runs = [r for r in results if r["num_hops"] == hops]
        if not hop_runs:
            continue
        print(f"  {hops}-hop ({len(hop_runs)} runs):", end="")
        for mk in modes_to_run:
            mk_hops = [r for r in hop_runs if r["mode"] == mk]
            if not mk_hops:
                continue
            sr = sum(1 for r in mk_hops if r["success"]) / len(mk_hops)
            spawned = sum(1 for r in mk_hops if r.get("spawn_attempted", False))
            print(f"  {mk.split('_')[1]}={sr:.0%}({spawned}s)", end="")
        print()


if __name__ == "__main__":
    main()
