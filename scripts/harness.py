#!/usr/bin/env python3
"""Stage 1A Harness: Multi-hop QA with OpenCode + vLLM
v2: Forced subagent spawning — Explore/General run as separate OpenCode processes."""

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
        "use_explore": False,
        "use_general": False,
    },
    "build_explore": {
        "use_explore": True,
        "use_general": False,
    },
    "build_explore_general": {
        "use_explore": True,
        "use_general": True,
    },
}

EXPLORE_PROMPT = """You are an Explore subagent. Your job is to find relevant information in documents.txt.

QUESTION TO ANSWER: {question}
NUM HOPS: {num_hops}

INSTRUCTIONS:
1. Use grep to search documents.txt for relevant keywords
2. Use read to read specific paragraphs
3. Report ALL relevant paragraphs with their IDs and key facts
4. Trace the chain of facts across paragraphs ({num_hops}-hop question)
5. End with: FINDINGS_COMPLETE

Begin now."""

GENERAL_PROMPT = """You are a General verification subagent. Review the Explore subagent's findings.

ORIGINAL QUESTION: {question}

EXPLORE_FINDINGS:
{explore_output}

INSTRUCTIONS:
1. Check if any relevant paragraphs were missed
2. Verify the logical chain across paragraphs
3. Identify any gaps or contradictions
4. Propose additional searches if needed
5. End with: REVIEW_COMPLETE

Begin now."""

BUILD_PROMPT = """TASK: Answer a multi-hop question using findings from subagents.

QUESTION: {question}

{subagent_context}

INSTRUCTIONS:
1. Read the subagent findings above
2. If needed, verify by reading specific paragraphs from documents.txt
3. Chain the information across paragraphs ({num_hops}-hop question)
4. When ready, output exactly on its own line: ANSWER: <your answer>

IMPORTANT: Do NOT call any subagents. Use only direct tool calls (grep, read).

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


def run_opencode(workdir, prompt, agent="build", timeout=300, log_suffix=""):
    """Run OpenCode with specified agent, return stdout and stderr."""
    suffix = f"_{log_suffix}" if log_suffix else ""
    cmd = [
        OPENCODE_BIN, "run",
        "--agent", agent,
        "--dir", workdir,
        "--format", "json",
        "--print-logs",
        prompt
    ]

    stderr_file = f"{workdir}/opencode_stderr_raw{suffix}.txt"

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

    with open(stderr_file) as f:
        stderr = f.read()

    return {"stdout": stdout or "", "stderr": stderr or "",
            "exit_code": proc.returncode, "runtime_sec": elapsed,
            "timeout": timeout_flag}


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
    # Clean markdown formatting and trailing punctuation
    answer_clean = re.sub(r'\*+', '', answer).strip().lower().rstrip('.').rstrip(',')
    gold_clean = gold_answer.strip().lower().rstrip('.')

    if answer_clean == gold_clean:
        return True, "exact_match"

    for alias in aliases:
        if answer_clean == alias.strip().lower().rstrip('.'):
            return True, "alias_match"

    # Numeric/ordinal matching: "3" = "third" = "3rd" = "third-largest"
    ordinal_map = {"1": "first", "2": "second", "3": "third", "4": "fourth", "5": "fifth",
                   "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth"}
    ans_nums = re.findall(r'\d+', answer_clean)
    gold_nums = re.findall(r'\d+', gold_clean)
    if ans_nums == gold_nums and ans_nums:
        ans_ord = any(ordinal_map.get(n, "") in answer_clean for n in ans_nums)
        gold_ord = any(ordinal_map.get(n, "") in gold_clean for n in gold_nums)
        if ans_ord or gold_ord:
            return True, "ordinal_match"
    # Cross-match: answer has digit, gold has ordinal word
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

    # Date partial matching: "March 29" in "March 29, 2018" → close
    import datetime
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


def save_run_log(run_entry):
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(run_entry, ensure_ascii=False) + "\n")


def run_single(task, system_key):
    """Run a single task+system combo with forced subagent spawning."""
    run_id = f"{task['task_id']}__{system_key}__s0"
    workdir, task_data = prepare_workdir(run_id, task)
    sys_cfg = SYSTEMS[system_key]

    total_runtime = 0
    all_stdout = []
    all_tokens = {"input": 0, "output": 0, "total": 0}
    all_steps = 0
    subagent_outputs = {}

    # ---- Phase 1: Run Explore subagent (if enabled) ----
    if sys_cfg["use_explore"]:
        explore_prompt = EXPLORE_PROMPT.format(
            question=task["question"],
            num_hops=task["num_hops"]
        )
        with open(f"{workdir}/prompt_explore.txt", "w") as f:
            f.write(explore_prompt)

        result = run_opencode(workdir, explore_prompt, agent="explore",
                              timeout=120, log_suffix="explore")
        total_runtime += result["runtime_sec"]

        explore_text = extract_text_output(result["stdout"])

        with open(f"{workdir}/stdout_explore.txt", "w") as f:
            f.write(result["stdout"])
        with open(f"{workdir}/stderr_explore.txt", "w") as f:
            f.write(result["stderr"])

        tokens_e, steps_e = parse_metrics(result["stdout"])
        all_tokens["input"] += tokens_e["input"]
        all_tokens["output"] += tokens_e["output"]
        all_tokens["total"] += tokens_e["total"]
        all_steps += steps_e
        all_stdout.append(result["stdout"])

        subagent_outputs["explore"] = explore_text[:4000]  # cap to avoid overflow

    # ---- Phase 2: Run General subagent (if enabled) ----
    if sys_cfg["use_general"]:
        general_prompt = GENERAL_PROMPT.format(
            question=task["question"],
            explore_output=subagent_outputs.get("explore", "(Explore not run)")
        )
        with open(f"{workdir}/prompt_general.txt", "w") as f:
            f.write(general_prompt)

        result = run_opencode(workdir, general_prompt, agent="general",
                              timeout=120, log_suffix="general")
        total_runtime += result["runtime_sec"]

        general_text = extract_text_output(result["stdout"])

        with open(f"{workdir}/stdout_general.txt", "w") as f:
            f.write(result["stdout"])
        with open(f"{workdir}/stderr_general.txt", "w") as f:
            f.write(result["stderr"])

        tokens_g, steps_g = parse_metrics(result["stdout"])
        all_tokens["input"] += tokens_g["input"]
        all_tokens["output"] += tokens_g["output"]
        all_tokens["total"] += tokens_g["total"]
        all_steps += steps_g
        all_stdout.append(result["stdout"])

        subagent_outputs["general"] = general_text[:2000]

    # ---- Phase 3: Run Build with subagent context ----
    context_parts = []
    if "explore" in subagent_outputs:
        context_parts.append(f"=== EXPLORE SUBAGENT FINDINGS ===\n{subagent_outputs['explore']}")
    if "general" in subagent_outputs:
        context_parts.append(f"=== GENERAL SUBAGENT REVIEW ===\n{subagent_outputs['general']}")

    subagent_context = "\n\n".join(context_parts) if context_parts else \
        "No subagent findings available. Search documents.txt yourself.\n" \
        "IMPORTANT: Do NOT use any subagents. Use only direct tool calls (grep, read)."

    build_prompt = BUILD_PROMPT.format(
        question=task["question"],
        subagent_context=subagent_context,
        num_hops=task["num_hops"]
    )

    with open(f"{workdir}/prompt_build.txt", "w") as f:
        f.write(build_prompt)

    result = run_opencode(workdir, build_prompt, agent="build",
                          timeout=180, log_suffix="build")
    total_runtime += result["runtime_sec"]

    with open(f"{workdir}/stdout_build.txt", "w") as f:
        f.write(result["stdout"])
    with open(f"{workdir}/stderr_build.txt", "w") as f:
        f.write(result["stderr"])

    tokens_b, steps_b = parse_metrics(result["stdout"])
    all_tokens["input"] += tokens_b["input"]
    all_tokens["output"] += tokens_b["output"]
    all_tokens["total"] += tokens_b["total"]
    all_steps += steps_b
    all_stdout.append(result["stdout"])

    # ---- Evaluate ----
    combined_stdout = "\n".join(all_stdout)
    answer = extract_answer(combined_stdout, result["stderr"])
    success, eval_detail = evaluate(answer, task["answer"], task["answer_aliases"])

    subagent_count = (1 if sys_cfg["use_explore"] else 0) + \
                     (1 if sys_cfg["use_general"] else 0)

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
            "input_tokens": {"total": all_tokens["input"]},
            "output_tokens": {"total": all_tokens["output"]},
            "total_tokens": all_tokens["total"]
        },
        "runtime_sec": total_runtime,
        "steps": all_steps,
        "subagent_calls_total": subagent_count,
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

    # Allow running a subset
    systems_to_run = list(SYSTEMS.keys())
    if len(sys.argv) > 1:
        subset = sys.argv[1]
        if subset == "test":
            tasks = tasks[:1]
            print("TEST MODE: 1 task")
        elif subset == "s1":
            systems_to_run = ["build_only"]
        elif subset == "2hop":
            tasks = [t for t in tasks if t["num_hops"] == 2]
        elif subset == "3hop":
            tasks = [t for t in tasks if t["num_hops"] == 3]
        elif subset == "4hop":
            tasks = [t for t in tasks if t["num_hops"] == 4]
        else:
            tasks = [t for t in tasks if t["task_id"] == subset]

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
                  f"{entry['token_usage']['total_tokens']} tok | {entry['runtime_sec']:.0f}s | "
                  f"{entry['subagent_calls_total']} subagents")
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
