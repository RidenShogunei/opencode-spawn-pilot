#!/usr/bin/env python3
"""Stage 1B Harness: Multi-hop QA with OpenCode + vLLM
Mechanism Confirmation — 4 systems, 5 diagnostic metrics (M1-M5).
v3: Added S4 (structured evidence table), M1-M5 metric computation."""

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
        "use_table": False,
    },
    "build_explore": {
        "use_explore": True,
        "use_general": False,
        "use_table": False,
    },
    "build_explore_general": {
        "use_explore": True,
        "use_general": True,
        "use_table": False,
    },
    "build_explore_table": {
        "use_explore": True,
        "use_general": False,
        "use_table": True,
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

# S4: Structured evidence table generation
TABLE_PROMPT = """You are a Table generation subagent. Convert the Explore findings into a structured evidence table.

ORIGINAL QUESTION: {question}
NUM HOPS: {num_hops}

EXPLORE FINDINGS:
{explore_output}

INSTRUCTIONS:
1. Review the Explore findings above
2. Create a markdown table with these columns:
   | Paragraph ID | Key Fact | Connects To |
3. Each row = one supporting paragraph used in the chain
4. "Connects To" column: describe which hop this paragraph contributes to
5. Mark the final ANSWER row with "→ ANSWER"
6. End your response with: TABLE_COMPLETE

Example format:
| Para ID | Key Fact | Connects To |
|---------|----------|-------------|
| 7 | Phu Luang is in Vietnam | hop 1 |
| 12 | John Phan birthplace = Vietnam | hop 2 → ANSWER |

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
            "run_id": run_id,
            "task_id": task["task_id"],
            "question": task["question"],
            "answer": task["answer"],
            "answer_aliases": task["answer_aliases"],
            "num_hops": task["num_hops"],
            "difficulty_bucket": task["difficulty_bucket"],
        }, f, indent=2)

    return workdir, task_data


def run_opencode(workdir, prompt, agent="build", timeout=300, log_suffix=""):
    """Run OpenCode with specified agent. stdout/stderr written to files, returned as content."""
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

    return {"stdout": stdout, "stderr": stderr,
            "exit_code": exit_code, "runtime_sec": elapsed,
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
    """Evaluate predicted answer against gold answer."""
    answer_clean = re.sub(r'\*+', '', answer).strip().lower().rstrip('.').rstrip(',')
    gold_clean = gold_answer.strip().lower().rstrip('.')

    if answer_clean == gold_clean:
        return True, "exact_match"

    for alias in aliases:
        if answer_clean == alias.strip().lower().rstrip('.'):
            return True, "alias_match"

    # Numeric/ordinal matching
    ordinal_map = {"1": "first", "2": "second", "3": "third", "4": "fourth", "5": "fifth",
                   "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth"}
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


# ------------------------------------------------------------------
# M1-M5 Metric Computation
# ------------------------------------------------------------------

def compute_m1_evidence_recall(explore_text, task_data):
    """M1: fraction of gold supporting paragraphs mentioned in Explore output."""
    gold_paras = set(task_data.get("post_hoc_features", {}).get("supporting_paragraph_indices", []))
    if not gold_paras:
        return 0.0, []

    found = []
    for para_idx in gold_paras:
        # Check if this paragraph ID is mentioned in explore output
        # Patterns: "paragraph 7", "para 7", "7", "[7]", "PARAGRAPH 7"
        para_str = str(para_idx)
        patterns = [
            rf'\bpara(?:graph)?\s*{re.escape(para_str)}\b',
            rf'\bPARA(?:GRAPH)?\s*{re.escape(para_str)}\b',
            rf'\[?\b{re.escape(para_str)}\b\]?',
        ]
        for pat in patterns:
            if re.search(pat, explore_text, re.IGNORECASE):
                found.append(para_idx)
                break

    recall = len(found) / len(gold_paras)
    return recall, found


def compute_m2_missing_hop_coverage(run_entry, task_data, all_runs_for_task):
    """M2: how many additional hops does this system cover vs S1 baseline.
    Returns list of gold supporting paragraph IDs covered by subagent that S1 missed.
    """
    gold_paras = set(task_data.get("post_hoc_features", {}).get("supporting_paragraph_indices", []))
    if not gold_paras:
        return []

    # Find S1 run for this task to know what S1 already covered
    s1_run = next(
        (r for r in all_runs_for_task if r["system"] == "build_only"),
        None
    )
    s1_covered = s1_run.get("m1_found_paras", []) if s1_run else []

    current_covered = run_entry.get("m1_found_paras", [])

    # Missing hops = gold paras S1 missed but this system found
    s1_set = set(s1_covered)
    extra = [p for p in current_covered if p not in s1_set and p in gold_paras]
    return extra


def compute_m3_integration_error_rate(run_entry):
    """M3: Did Explore find gold paragraphs but Build still failed?"""
    if not run_entry.get("used_subagents", False):
        return None  # Not applicable for S1
    explore_found = run_entry.get("m1_found_paras", [])
    success = run_entry.get("success", False)
    if explore_found and not success:
        return True
    return False


def compute_m4_tokens_per_correct(runs_for_system):
    """M4: average total tokens for successful runs."""
    successful = [r for r in runs_for_system if r.get("success", False)]
    if not successful:
        return float('inf')
    total_tok = sum(r["token_usage"]["total_tokens"] for r in successful)
    return total_tok / len(successful)


def compute_m5_explore_found_build_failed(run_entry):
    """M5: Explore found gold evidence but Build failed."""
    if not run_entry.get("used_subagents", False):
        return False
    explore_found = run_entry.get("m1_found_paras", [])
    success = run_entry.get("success", False)
    return bool(explore_found) and not success


def save_run_log(run_entry):
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(run_entry, ensure_ascii=False) + "\n")


def run_single(task, system_key, all_runs_for_task=None):
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

        subagent_outputs["explore"] = explore_text[:4000]

    # ---- Phase 2a: Run General subagent (S3) ----
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

    # ---- Phase 2b: Run Table generation (S4) ----
    if sys_cfg["use_table"]:
        table_prompt = TABLE_PROMPT.format(
            question=task["question"],
            num_hops=task["num_hops"],
            explore_output=subagent_outputs.get("explore", "(Explore not run)")
        )
        with open(f"{workdir}/prompt_table.txt", "w") as f:
            f.write(table_prompt)

        result = run_opencode(workdir, table_prompt, agent="general",
                              timeout=120, log_suffix="table")
        total_runtime += result["runtime_sec"]

        table_text = extract_text_output(result["stdout"])

        with open(f"{workdir}/stdout_table.txt", "w") as f:
            f.write(result["stdout"])
        with open(f"{workdir}/stderr_table.txt", "w") as f:
            f.write(result["stderr"])

        tokens_t, steps_t = parse_metrics(result["stdout"])
        all_tokens["input"] += tokens_t["input"]
        all_tokens["output"] += tokens_t["output"]
        all_tokens["total"] += tokens_t["total"]
        all_steps += steps_t
        all_stdout.append(result["stdout"])

        subagent_outputs["table"] = table_text[:2000]

    # ---- Phase 3: Run Build with subagent context ----
    context_parts = []
    if "explore" in subagent_outputs:
        context_parts.append(f"=== EXPLORE SUBAGENT FINDINGS ===\n{subagent_outputs['explore']}")
    if "general" in subagent_outputs:
        context_parts.append(f"=== GENERAL SUBAGENT REVIEW ===\n{subagent_outputs['general']}")
    if "table" in subagent_outputs:
        context_parts.append(f"=== STRUCTURED EVIDENCE TABLE ===\n{subagent_outputs['table']}")

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

    # ---- Compute M1: evidence recall ----
    explore_text = subagent_outputs.get("explore", "")
    m1_recall, m1_found_paras = compute_m1_evidence_recall(explore_text, task_data)

    # ---- Compute M2: missing-hop coverage (needs all runs, filled in main) ----
    m2_extra_paras = []

    # ---- Compute M5 early ----
    used_subagents = sys_cfg["use_explore"] or sys_cfg["use_general"] or sys_cfg["use_table"]
    m5_explore_found_build_failed = compute_m5_explore_found_build_failed({
        "used_subagents": used_subagents,
        "m1_found_paras": m1_found_paras,
        "success": success,
    })

    subagent_count = sum(1 for v in [sys_cfg["use_explore"], sys_cfg["use_general"], sys_cfg["use_table"]] if v)

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
            "total_tokens": all_tokens["total"],
        },
        "runtime_sec": total_runtime,
        "steps": all_steps,
        "subagent_calls_total": subagent_count,
        "used_subagents": used_subagents,
        "exit_code": result.get("exit_code", -1),
        "timeout": result.get("timeout", False),

        # ---- M1: Evidence Recall ----
        "m1_evidence_recall": m1_recall,
        "m1_found_paras": m1_found_paras,
        "m1_gold_paras": task_data.get("post_hoc_features", {}).get("supporting_paragraph_indices", []),

        # ---- M2: Missing-hop coverage (filled post-hoc) ----
        "m2_extra_paras": m2_extra_paras,

        # ---- M3: Integration error (filled post-hoc per-system) ----
        "m3_integration_error": None,  # Computed per-system across all runs

        # ---- M4: Tokens per correct answer (filled post-hoc) ----
        "m4_tokens_per_correct": None,  # Computed per-system

        # ---- M5: Explore-found / Build-failed ----
        "m5_explore_found_build_failed": m5_explore_found_build_failed,

        "failure_analysis": {
            "failed": not success,
            "primary_failure_type": "none" if success else "other",
            "failure_tags": [],
            "notes": ""
        }
    }

    save_run_log(entry)
    return entry


def compute_post_hoc_metrics(all_runs):
    """Compute M2, M3, M4 after all runs complete.
    M2 needs S1 baseline to compare against.
    M3 and M4 are per-system aggregates.
    """
    # Group by task_id
    by_task = {}
    for r in all_runs:
        by_task.setdefault(r["task_id"], []).append(r)

    # M2: missing-hop coverage for each run
    for task_id, runs in by_task.items():
        s1_run = next((r for r in runs if r["system"] == "build_only"), None)
        s1_covered = set(s1_run["m1_found_paras"]) if s1_run else set()
        for r in runs:
            if r["system"] == "build_only":
                r["m2_extra_paras"] = []
            else:
                gold_paras = set(r["m1_gold_paras"])
                extra = [p for p in r["m1_found_paras"] if p not in s1_covered and p in gold_paras]
                r["m2_extra_paras"] = extra

    # M3: integration error rate per system
    by_system = {}
    for r in all_runs:
        by_system.setdefault(r["system"], []).append(r)

    m3_by_system = {}
    for sk, runs in by_system.items():
        if sk == "build_only":
            m3_by_system[sk] = None
            continue
        # Integration error = Explore found gold para but Build failed
        total_with_subagent = len([r for r in runs if r.get("used_subagents", False)])
        errors = sum(1 for r in runs if r.get("used_subagents", False) and
                     r["m1_found_paras"] and not r["success"])
        m3_by_system[sk] = errors / total_with_subagent if total_with_subagent else None

    # M4: tokens per correct answer per system
    m4_by_system = {}
    for sk, runs in by_system.items():
        m4_by_system[sk] = compute_m4_tokens_per_correct(runs)

    # Update runs with post-hoc metrics
    for r in all_runs:
        r["m3_integration_error"] = m3_by_system.get(r["system"])
        r["m4_tokens_per_correct"] = m4_by_system.get(r["system"])

    return m3_by_system, m4_by_system


def print_metrics_summary(all_runs, m3_by_system, m4_by_system):
    """Print M1-M5 summary table."""
    by_system = {}
    for r in all_runs:
        by_system.setdefault(r["system"], []).append(r)

    print(f"\n{'='*70}")
    print("STAGE 1B METRICS SUMMARY (M1-M5)")
    print(f"{'='*70}")

    systems_order = ["build_only", "build_explore", "build_explore_general", "build_explore_table"]
    systems_display = {
        "build_only": "S1 Build-only",
        "build_explore": "S2 Explore→Build",
        "build_explore_general": "S3 Explore→General→Build",
        "build_explore_table": "S4 Explore→Table→Build",
    }

    # Header
    print(f"{'System':<28} {'Succ':>5} {'M1 Rec':>7} {'M2 Ex':>5} {'M3 IntErr':>9} {'M4 tok/correct':>14} {'M5 E→B fail':>11}")
    print("-" * 85)

    for sk in systems_order:
        runs = by_system.get(sk, [])
        if not runs:
            continue

        sr = sum(1 for r in runs if r["success"]) / len(runs)
        avg_m1 = sum(r["m1_evidence_recall"] for r in runs) / len(runs)
        avg_m2 = sum(len(r["m2_extra_paras"]) for r in runs) / len(runs)

        m3_val = m3_by_system.get(sk)
        m4_val = m4_by_system.get(sk)

        m3_str = f"{m3_val:.0%}" if m3_val is not None else "  N/A  "
        m4_str = f"{m4_val:,.0f}" if m4_val and m4_val != float('inf') else "   N/A"
        m5_count = sum(1 for r in runs if r.get("m5_explore_found_build_failed", False))

        print(f"{systems_display[sk]:<28} {sr:>5.0%} {avg_m1:>7.0%} {avg_m2:>5.1f} {m3_str:>9} {m4_str:>14} {m5_count:>11}")

    print()
    # Per-hop breakdown
    print("Per-hop breakdown:")
    for hops in [2, 3, 4]:
        hop_runs = [r for r in all_runs if r["num_hops"] == hops]
        if not hop_runs:
            continue
        print(f"  {hops}-hop ({len(hop_runs)} runs): ", end="")
        for sk in systems_order:
            sk_hops = [r for r in hop_runs if r["system"] == sk]
            if not sk_hops:
                continue
            sr = sum(1 for r in sk_hops if r["success"]) / len(sk_hops)
            print(f"  {systems_display[sk].split()[0]}={sr:.0%}", end="")
        print()


def main():
    tasks = load_tasks()
    resume_mode = "--resume" in sys.argv
    if resume_mode:
        sys.argv.remove("--resume")

    # Allow running a subset
    systems_to_run = list(SYSTEMS.keys())
    if len(sys.argv) > 1:
        subset = sys.argv[1]
        if subset == "test":
            tasks = tasks[:1]
            print("TEST MODE: 1 task")
        elif subset == "s1":
            systems_to_run = ["build_only"]
        elif subset == "s2":
            systems_to_run = ["build_explore"]
        elif subset == "s3":
            systems_to_run = ["build_explore_general"]
        elif subset == "s4":
            systems_to_run = ["build_explore_table"]
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

    # Load existing runs for resume deduplication
    existing_runs = {}
    if resume_mode:
        log_file = f"{OUTPUT_ROOT}/runs.jsonl"
        if os.path.exists(log_file):
            with open(log_file) as f:
                for line in f:
                    if line.strip():
                        r = json.loads(line)
                        existing_runs[(r["task_id"], r["system"])] = r
            existing_count = len(existing_runs)
            print(f"RESUME MODE: found {existing_count} existing runs, will skip completed ones")

    results = []
    for i, task in enumerate(tasks):
        for j, system_key in enumerate(systems_to_run):
            idx = i * len(systems_to_run) + j + 1
            total = len(tasks) * len(systems_to_run)
            print(f"\n[{idx}/{total}] {task['task_id']} | {system_key} | {task['num_hops']}hop")
            sys.stdout.flush()

            # Resume: skip if already exists
            if resume_mode and (task["task_id"], system_key) in existing_runs:
                entry = existing_runs[(task["task_id"], system_key)]
                status = "✅" if entry["success"] else "❌"
                print(f"  ⏭️  SKIP (already exists) | {status} Answer: '{entry['predicted_answer'][:60]}' | "
                      f"{entry['token_usage']['total_tokens']} tok")
                results.append(entry)
                continue

            entry = run_single(task, system_key)

            status = "✅" if entry["success"] else "❌"
            m1_pct = entry["m1_evidence_recall"]
            print(f"  {status} Answer: '{entry['predicted_answer'][:60]}' (gold: '{entry['gold_answer']}') | "
                  f"M1={m1_pct:.0%} | {entry['token_usage']['total_tokens']} tok | {entry['runtime_sec']:.0f}s | "
                  f"{entry['subagent_calls_total']} subagents")
            sys.stdout.flush()
            results.append(entry)

    # Post-hoc M2, M3, M4
    m3_by_system, m4_by_system = compute_post_hoc_metrics(results)

    # Overwrite runs.jsonl with enriched entries
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Print summary
    print_metrics_summary(results, m3_by_system, m4_by_system)


if __name__ == "__main__":
    main()
