#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — Activation Probe Harness v2

Modes:
  M0 (spawn_closed):               Baseline. No affordance. Build alone.
  M1 (spawn_affordance):           Build knows it CAN call spawn wrapper, self-decides.
  M2a (harness_decision):          Build outputs SPAWN_DECISION yes/no. Harness executes if yes.

M2a two-phase protocol:
  Phase 1: Build outputs only "SPAWN_DECISION: yes" or "SPAWN_DECISION: no"
  Phase 2a (decision=yes): Harness calls wrapper, injects Explore findings, Build answers
  Phase 2b (decision=no):  Harness gives follow-up prompt, Build answers alone

Spawn ground truth: spawn_events.jsonl (written by wrapper). No stdout regex.
"""

import json, os, subprocess, time, re, sys, argparse
from pathlib import Path

PROJECT_ROOT = "/home/jinxu/opencode-spawn-pilot"
OUTPUT_ROOT  = f"{PROJECT_ROOT}/outputs/opencode_spawn_pilot"
SPAWN_EVENTS = f"{OUTPUT_ROOT}/spawn_events/spawn_events.jsonl"
TASKS_FILE   = f"{OUTPUT_ROOT}/tasks.jsonl"
TASK_DATA_DIR= f"{OUTPUT_ROOT}/task_data"
OPENCODE_BIN = "/home/jinxu/.opencode/bin/opencode"
SCRIPT_DIR   = f"{PROJECT_ROOT}/scripts"

MODES = {
    "M0_spawn_closed": {
        "description": "Baseline. No subagent affordance.",
        "can_spawn": False,
    },
    "M1_spawn_affordance": {
        "description": "Build knows it CAN call wrapper, self-decides.",
        "can_spawn": True,
    },
    "M2a_harness_decision": {
        "description": "Build outputs decision. Harness executes wrapper if yes.",
        "can_spawn": True,
    },
}


# ─── Prompts ─────────────────────────────────────────────────────────────────

WRAPPER_CALL = (
    "If you want to delegate document exploration, you MUST use the wrapper script:\n"
    "   bash /home/jinxu/opencode-spawn-pilot/scripts/spawn_explore.sh "
    "<task_id> <run_id> <workdir> <your exploration task>\n"
    "   The explore subagent will search documents and return findings.\n"
    "You may only delegate via this script — do NOT run opencode directly."
)

SPAWN_AFFORDANCE_PROMPT = (
    "If you want to delegate document exploration to a subagent, you can call:\n"
    "   bash /home/jinxu/opencode-spawn-pilot/scripts/spawn_explore.sh "
    "<task_id> <run_id> <workdir> <your exploration task>\n"
    "   The explore subagent will search documents and return findings.\n"
    "Decide for yourself whether to use the subagent or handle everything directly."
)

# ── M2a: decision-only prompt ────────────────────────────────────────────────

M2A_DECISION_PROMPT = (
    "You must first decide whether to delegate document exploration to a subagent.\n\n"
    "Output exactly ONE line and nothing else:\n"
    "   SPAWN_DECISION: yes\n"
    "   or\n"
    "   SPAWN_DECISION: no\n\n"
    "Decision rule:\n"
    "- Choose yes if the question requires following 3 or more linked entities across paragraphs,\n"
    "  or if you expect multiple paragraphs must be searched.\n"
    "- Choose no only if the answer is likely contained in 1–2 obvious paragraphs.\n\n"
    "Do NOT answer the question yet.\n"
    "Do NOT run grep yet.\n"
    "Do NOT call any command yet.\n"
    "Output only the SPAWN_DECISION line."
)

M2A_NO_WRAPPER_PROMPT = (
    "Proceed alone to answer the question.\n\n"
    "Use grep to search documents.txt, then read specific paragraphs.\n"
    "Chain information across paragraphs.\n"
    "When ready, output exactly on its own line: ANSWER: <your answer>\n"
    "Do not output anything else besides the ANSWER line."
)

def m2a_build_followup_prompt(explore_findings, original_question):
    """Build the Phase 2a prompt that injects Explore results."""
    findings_block = "\n".join(
        f"  [{evt.get('task_id','?')}] {evt.get('child_stdout','')[:500]}"
        for evt in explore_findings
    )
    if not findings_block:
        findings_block = "  (Explore subagent returned no output)"
    return (
        "The Explore subagent returned the following findings:\n"
        f"{findings_block}\n\n"
        "Now answer the original question using these findings and your own grep/read if needed.\n"
        "Output exactly on its own line: ANSWER: <your answer>\n"
        "Do not output anything else besides the ANSWER line."
    )


# ─── Task Loading ────────────────────────────────────────────────────────────

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


# ─── OpenCode Runner ─────────────────────────────────────────────────────────

def run_opencode(workdir, prompt, agent="build", timeout=120, log_suffix=""):
    """Run OpenCode. stdout/stderr written to files, returned as content."""
    suffix = f"_{log_suffix}" if log_suffix else ""
    stdout_file = f"{workdir}/stdout{suffix}.txt"
    stderr_file = f"{workdir}/stderr{suffix}.txt"
    workdir_abs = os.path.abspath(workdir)
    cmd = [OPENCODE_BIN, "run",
           "--agent", agent,
           "--dir",    workdir_abs,
           "--format", "json",
           "--print-logs",
           prompt]
    start = time.time()
    with open(stderr_file, "w", buffering=1) as stderr_f:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_f,
            text=True,
            cwd=workdir_abs,
            bufsize=1,
        )
        stdout_chunks = []
        try:
            for line in iter(proc.stdout.readline, ''):
                if line:
                    stdout_chunks.append(line)
            exit_code = proc.wait(timeout=timeout)
            timeout_flag = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_chunks.append(f"[TIMEOUT after {timeout}s]")
            exit_code = -1
            timeout_flag = True
    elapsed = time.time() - start
    stdout = "".join(stdout_chunks)
    with open(stdout_file, "w") as f:
        f.write(stdout)
    return {
        "stdout": stdout,
        "stderr": open(stderr_file).read(),
        "exit_code": exit_code,
        "timeout": timeout_flag,
        "runtime_sec": elapsed,
    }


# ─── Parsing ─────────────────────────────────────────────────────────────────

def extract_answer(stdout, stderr):
    """Extract ANSWER text from OpenCode JSON output. Tries JSON text fields first."""
    # Try to find text in JSON lines
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            # Navigate nested structure: type → text / content
            if obj.get("type") in ("text", "console", "output") or True:
                part = obj.get("part", {})
                text = part.get("text") or part.get("content") or ""
                if isinstance(text, str):
                    text = text.strip()
                    # Look for ANSWER pattern
                    if re.match(r"^ANSWER\s*[:：]", text, re.IGNORECASE):
                        parts = re.split(r":\s*", text, 1)
                        if len(parts) == 2:
                            return parts[1].strip()
                    # Also check raw text field
                    if text.startswith("answer") or re.match(r"^ANSWER\s*[:：]", text, re.IGNORECASE):
                        parts = re.split(r":\s*", text, 1)
                        if len(parts) == 2:
                            return parts[1].strip()
        except (json.JSONDecodeError, ValueError):
            # Try regex on raw line
            if re.match(r"^ANSWER\s*[:：]", line, re.IGNORECASE):
                parts = re.split(r":\s*", line, 1)
                if len(parts) == 2:
                    return parts[1].strip()
    # fallback: last substantial non-empty non-JSON line
    for line in reversed(stdout.split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            # Skip JSON lines
            json.loads(line)
            continue
        except (json.JSONDecodeError, ValueError):
            pass
        if line and not line.startswith("[") and "tool" not in line.lower():
            return line
    return ""


def parse_metrics(stdout):
    """Extract token counts and steps from OpenCode JSON output."""
    tokens = {"input": 0, "output": 0, "total": 0}
    steps = 0
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            obj_type = obj.get("type", "")
            # Top-level token_usage
            if obj_type == "token_usage":
                tok = obj.get("token_usage", {})
                tokens["input"]  += tok.get("input_tokens", 0)
                tokens["output"] += tok.get("output_tokens", 0)
                tokens["total"]  += tok.get("total", 0)
            # step_finish has tokens in part.tokens
            elif obj_type == "step_finish":
                steps += 1
                part = obj.get("part", {})
                tok = part.get("tokens", {})
                if tok:
                    tokens["input"]  += tok.get("input", 0)
                    tokens["output"] += tok.get("output", 0)
                    tokens["total"]  += tok.get("total", 0)
            elif obj_type == "step":
                steps += 1
        except (json.JSONDecodeError, ValueError):
            continue
    return tokens, steps


def parse_spawn_decision(stdout):
    """Extract SPAWN_DECISION yes/no from Phase-1 output. Checks JSON text fields."""
    # First try: find JSON text lines
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            part = obj.get("part", {})
            text = part.get("text", "")
            if text and "SPAWN_DECISION" in text.upper():
                m = re.search(r"SPAWN_DECISION\s*:\s*(yes|no)", text, re.IGNORECASE)
                if m:
                    return m.group(1).lower(), text.strip()[:500]
        except (json.JSONDecodeError, ValueError):
            continue
    # Second try: raw line match
    for line in stdout.split("\n"):
        upper = line.upper().strip()
        if "SPAWN_DECISION" in upper:
            m = re.search(r"SPAWN_DECISION\s*:\s*(yes|no)", upper, re.IGNORECASE)
            if m:
                return m.group(1).lower(), line.strip()[:500]
    return None, None


def detect_echoed_instructions(stdout):
    """Detect if model echoed affordance text without acting."""
    texts = extract_text_events(stdout)
    echoed = []
    for t in texts:
        if "opencode run --agent explore" in t and "Decide for yourself" in t:
            echoed.append(t[:300])
    return echoed


def extract_text_events(stdout):
    """Pull text content from OpenCode JSON lines."""
    texts = []
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            part = obj.get("part", {})
            text = part.get("text") or part.get("content") or ""
            if text:
                texts.append(text)
        except (json.JSONDecodeError, ValueError):
            continue
    return texts


def count_spawn_calls_for_run(run_id):
    """Read spawn_events.jsonl, return events for this run_id."""
    if not os.path.exists(SPAWN_EVENTS):
        return []
    events = []
    with open(SPAWN_EVENTS) as f:
        for line in f:
            try:
                evt = json.loads(line)
                if evt.get("run_id") == run_id:
                    events.append(evt)
            except json.JSONDecodeError:
                continue
    return events


# ─── Evaluation ──────────────────────────────────────────────────────────────

def evaluate(answer, gold_answer, aliases):
    """Evaluate predicted answer against gold answer."""
    answer_clean = re.sub(r'\*+', '', answer).strip().lower().rstrip('.').rstrip(',')
    gold_clean   = gold_answer.strip().lower().rstrip('.').rstrip(',')

    if answer_clean == gold_clean:
        return True, "exact_match"
    for alias in aliases:
        if answer_clean == alias.strip().lower().rstrip('.'):
            return True, "alias_match"

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

    if re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}', gold_clean):
        gold_norm = re.sub(r',\s*\d{4}', '', gold_clean)
        ans_norm  = re.sub(r',\s*\d{4}', '', answer_clean)
        if gold_norm.strip() == ans_norm.strip():
            return True, "partial_date"

    ans_tokens  = set(re.findall(r'\w+', answer_clean))
    gold_tokens = set(re.findall(r'\w+', gold_clean))
    if ans_tokens and gold_tokens:
        overlap = ans_tokens & gold_tokens
        if len(overlap) >= 2 and len(overlap) / len(gold_tokens) >= 0.6:
            return True, "partial_match"

    return False, "mismatch"


# ─── Prompts Builder ─────────────────────────────────────────────────────────

def build_prompt(task, mode):
    """Build the user prompt for a given mode."""
    base = (
        f"TASK: Answer a multi-hop question by searching and reading documents.\n\n"
        f"QUESTION: {task['question']}\n\n"
        f"RESOURCES:\n"
        f"- documents.txt: {task['num_paragraphs']} Wikipedia-style paragraphs. "
        f"Each starts with \"--- PARAGRAPH N ---\".\n\n"
        f"PROCESS:\n"
        f"1. Use `grep` to search keywords in documents.txt\n"
        f"2. Use `read` to read specific paragraphs\n"
        f"3. Chain information across paragraphs ({task['num_hops']}-hop question)\n"
        f"4. When ready, output exactly on its own line: ANSWER: <your answer>\n\n"
    )

    if mode == "M0_spawn_closed":
        return base + (
            "IMPORTANT: Work entirely alone. Do not call any other agents or subagents. "
            "Use only your own tool calls (grep, read).\n\nBegin now."
        )

    elif mode == "M1_spawn_affordance":
        return base + SPAWN_AFFORDANCE_PROMPT + "\n\nBegin now."

    elif mode == "M2a_harness_decision":
        return base + M2A_DECISION_PROMPT

    else:
        raise ValueError(f"Unknown mode: {mode}")


# ─── Phase Logic for M2a ────────────────────────────────────────────────────

def call_spawn_wrapper(task_id, run_id, workdir, exploration_task):
    """Call spawn_explore.sh wrapper. Returns (exit_code, stdout, stderr)."""
    wrapper = f"{SCRIPT_DIR}/spawn_explore.sh"
    cmd = ["bash", wrapper, task_id, run_id, os.path.abspath(workdir), exploration_task]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.abspath(workdir),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def run_phase2_build(workdir, prompt, original_timeout=120):
    """Run Build again with Phase-2 prompt."""
    return run_opencode(workdir, prompt, agent="build", timeout=original_timeout, log_suffix="phase2")


# ─── Per-Run Logic ──────────────────────────────────────────────────────────

def run_single(task, mode_key):
    """Run a single task in the specified mode. Returns result dict."""
    run_id   = f"{task['task_id']}__{mode_key}"
    workdir, _ = prepare_workdir(run_id, task)

    # ── M2a two-phase ──────────────────────────────────────────────────────
    if mode_key == "M2a_harness_decision":
        return run_m2a(task, run_id, workdir)

    # ── M0 / M1 single-phase ───────────────────────────────────────────────
    prompt = build_prompt(task, mode_key)
    with open(f"{workdir}/prompt_build.txt", "w") as f:
        f.write(prompt)

    result = run_opencode(workdir, prompt, agent="build", timeout=120, log_suffix="build")
    elapsed = result["runtime_sec"]
    tokens, steps = parse_metrics(result["stdout"])
    stdout = result["stdout"]
    stderr = result["stderr"]

    answer = extract_answer(stdout, stderr)
    success, eval_detail = evaluate(answer, task["answer"], task["answer_aliases"])

    spawn_events  = count_spawn_calls_for_run(run_id)
    actual_spawn  = len(spawn_events)
    echoed        = detect_echoed_instructions(stdout) if mode_key != "M0_spawn_closed" else []
    decision, decision_line = parse_spawn_decision(stdout) if mode_key != "M0_spawn_closed" else (None, None)

    return make_entry(
        task, run_id, mode_key, answer, success, eval_detail,
        tokens, elapsed, steps, actual_spawn,
        decision, decision_line, echoed,
        result["exit_code"], result.get("timeout", False),
        spawn_events,
    )


def run_m2a(task, run_id, workdir):
    """Run M2a: Phase 1 (decision) → Phase 2a or 2b."""
    # ── Phase 1: Get decision ───────────────────────────────────────────────
    prompt_p1 = build_prompt(task, "M2a_harness_decision")
    with open(f"{workdir}/prompt_phase1.txt", "w") as f:
        f.write(prompt_p1)

    result_p1 = run_opencode(workdir, prompt_p1, agent="build", timeout=120, log_suffix="phase1")
    elapsed_p1 = result_p1["runtime_sec"]
    stdout_p1 = result_p1["stdout"]
    tokens_p1, steps_p1 = parse_metrics(stdout_p1)

    decision, decision_line = parse_spawn_decision(stdout_p1)

    # ── Phase 2 ────────────────────────────────────────────────────────────
    phase2_prompt = None
    if decision == "yes":
        # Harness calls wrapper
        exploration_task = (
            f"Find the paragraph chain needed to answer: {task['question']}. "
            f"Return paragraph IDs, key entities, and the final candidate answer."
        )
        wc, wout, werr = call_spawn_wrapper(task["task_id"], run_id, workdir, exploration_task)
        # Log wrapper call
        os.makedirs(os.path.dirname(SPAWN_EVENTS), exist_ok=True)
        with open(SPAWN_EVENTS, "a") as f:
            f.write(json.dumps({
                "run_id": run_id,
                "task_id": task["task_id"],
                "wrapper_exit_code": wc,
                "child_stdout": wout[:2000],
                "child_stderr": werr[:500],
            }) + "\n")

        # Phase 2a: inject findings
        explore_events = count_spawn_calls_for_run(run_id)
        phase2_prompt = m2a_build_followup_prompt(explore_events, task["question"])
        workdir_phase = workdir
        log_suffix = "phase2a"
    elif decision == "no":
        # Phase 2b: Build continues alone
        phase2_prompt = (
            f"Original question: {task['question']}\n\n"
            + M2A_NO_WRAPPER_PROMPT
        )
        workdir_phase = workdir
        log_suffix = "phase2b"
    else:
        # Malformed / no decision — treat as failure
        return make_entry(
            task, run_id, "M2a_harness_decision",
            answer="SPAWN_DECISION_MALFORMED",
            success=False, eval_detail="malformed_decision",
            tokens=tokens_p1, elapsed=elapsed_p1, steps=steps_p1,
            actual_spawn=0,
            decision=None, decision_line=None,
            echoed=[],
            exit_code=result_p1["exit_code"],
            timeout=result_p1.get("timeout", False),
            spawn_events=[],
        )

    with open(f"{workdir}/{log_suffix}_prompt.txt", "w") as f:
        f.write(phase2_prompt)

    result_p2 = run_phase2_build(workdir_phase, phase2_prompt, original_timeout=120)
    elapsed_p2 = result_p2["runtime_sec"]
    stdout_p2 = result_p2["stdout"]
    stderr_p2 = result_p2["stderr"]
    tokens_p2, steps_p2 = parse_metrics(stdout_p2)
    tokens_total = {
        "input": tokens_p1["input"] + tokens_p2["input"],
        "output": tokens_p1["output"] + tokens_p2["output"],
        "total": tokens_p1["total"] + tokens_p2["total"],
    }
    elapsed_total = elapsed_p1 + elapsed_p2
    steps_total = steps_p1 + steps_p2

    answer = extract_answer(stdout_p2, stderr_p2)
    success, eval_detail = evaluate(answer, task["answer"], task["answer_aliases"])

    spawn_events = count_spawn_calls_for_run(run_id)
    echoed = detect_echoed_instructions(stdout_p2)

    entry = make_entry(
        task, run_id, "M2a_harness_decision",
        answer, success, eval_detail,
        tokens_total, elapsed_total, steps_total,
        actual_spawn=len(spawn_events),
        decision=decision, decision_line=decision_line,
        echoed=echoed,
        exit_code=result_p2["exit_code"],
        timeout=result_p2.get("timeout", False),
        spawn_events=spawn_events,
    )
    # Extra M2a fields
    entry["wrapper_called"] = True
    entry["phase1_tokens"] = tokens_p1["total"]
    entry["phase2_tokens"] = tokens_p2["total"]
    return entry


def make_entry(task, run_id, mode_key, answer, success, eval_detail,
               tokens, elapsed, steps, actual_spawn,
               decision, decision_line, echoed,
               exit_code, timeout, spawn_events):
    entry = {
        "run_id":                run_id,
        "task_id":               task["task_id"],
        "mode":                  mode_key,
        "model":                 "qwen35-9b",
        "difficulty_bucket":     task["difficulty_bucket"],
        "num_hops":              task["num_hops"],
        "question":              task["question"],
        "gold_answer":           task["answer"],
        "predicted_answer":       answer,
        "success":               success,
        "eval_detail":           eval_detail,
        "token_usage": {
            "input_tokens":      tokens["input"],
            "output_tokens":      tokens["output"],
            "total_tokens":       tokens["total"],
        },
        "runtime_sec":            elapsed,
        "steps":                 steps,
        "actual_spawn_count":    actual_spawn,
        "spawn_decision":        decision,
        "spawn_decision_line":   decision_line,
        "echoed_instructions":   len(echoed) > 0,
        "echoed_preview":        echoed[0] if echoed else None,
        "exit_code":             exit_code,
        "timeout":               timeout,
        "harness_called_wrapper": decision == "yes" if decision else None,   # harness called wrapper
    }
    return entry


def _save_run_log(entry):
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── Summary Reporter ─────────────────────────────────────────────────────────

def summarize_results(results, modes_to_run):
    by_mode = {}
    for r in results:
        by_mode.setdefault(r["mode"], []).append(r)

    print(f"\n{'='*70}")
    print("ACTIVATION PROBE RESULTS — Spawn Affordance Prompt Study v2")
    print(f"{'='*70}\n")

    # Per-mode table
    header = f"{'Mode':<35} {'N':>4} {'Acc':>6} {'Spawn':>6} {'Echo':>5} {'Tok/ans':>10}"
    print(header)
    print("-" * 70)
    for mode in modes_to_run:
        runs = by_mode.get(mode, [])
        if not runs:
            continue
        n = len(runs)
        acc = sum(1 for r in runs if r["success"]) / n * 100
        spawn = sum(r.get("actual_spawn_count", 0) for r in runs)
        echo = sum(1 for r in runs if r.get("echoed_instructions", False))
        toks = sum(r["token_usage"]["total_tokens"] for r in runs) / n
        print(f"{mode:<35} {n:>4} {acc:>5.0f}% {spawn:>6} {echo:>5} {toks:>9.0f}")

    print()

    # Per-hop breakdown
    print("Per-hop breakdown (accuracy / spawn_count):")
    for hop in sorted(set(r.get("num_hops") for r in results)):
        hop_runs = [r for r in results if r["num_hops"] == hop]
        parts = []
        for mode in modes_to_run:
            m_runs = [r for r in hop_runs if r["mode"] == mode]
            if not m_runs:
                continue
            n = len(m_runs)
            acc = sum(1 for r in m_runs if r["success"]) / n * 100
            spawn = sum(r.get("actual_spawn_count", 0) for r in m_runs)
            parts.append(f"  spawn={spawn}({n}r)")
        if parts:
            print(f"  {hop}-hop ({len(hop_runs)} runs): {'  '.join(parts)}")

    print()

    # M2a-specific: decision breakdown
    for mode in ["M2a_harness_decision"]:
        runs = by_mode.get(mode, [])
        if not runs:
            continue
        yes = sum(1 for r in runs if r.get("spawn_decision") == "yes")
        no  = sum(1 for r in runs if r.get("spawn_decision") == "no")
        none = sum(1 for r in runs if r.get("spawn_decision") is None)
        harness_called = sum(1 for r in runs if r.get("harness_called_wrapper") == True)
        print(f"{mode} Spawn Decision Analysis:")
        print(f"  SPAWN_DECISION yes:  {yes}/{len(runs)}")
        print(f"  SPAWN_DECISION no:   {no}/{len(runs)}")
        print(f"  SPAWN_DECISION none: {none}/{len(runs)} (malformed)")
        print(f"  Harness executed wrapper: {harness_called}/{yes if yes > 0 else 'N/A'} (yes decisions)")
        yes_acc = sum(1 for r in runs if r.get("spawn_decision")=="yes" and r["success"]) / max(yes, 1) * 100
        no_acc  = sum(1 for r in runs if r.get("spawn_decision")=="no" and r["success"]) / max(no, 1) * 100
        print(f"  Decision=yes accuracy:  {yes_acc:.0f}%")
        print(f"  Decision=no accuracy:   {no_acc:.0f}%")
        print()


# ─── CLI / Main ─────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="OpenCode Spawn Pilot Harness")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "test", "report"])
    parser.add_argument("--modes", default="M0,M1,M2a",
                        help="Comma-separated modes to run (default: M0,M1,M2a)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from existing runs, skip completed ones")
    parser.add_argument("--subset", default=None,
                        choices=["test","m0","m1","m2a","2hop","3hop","4hop"],
                        help="Shortcut subset (overrides --modes)")
    parser.add_argument("task_filter", nargs="?", default=None,
                        help="Task ID to run single task")
    return parser.parse_args()

def main():
    args = parse_args()

    # Determine modes
    modes_to_run = list(MODES.keys())
    if args.subset == "m0":
        modes_to_run = ["M0_spawn_closed"]
    elif args.subset == "m1":
        modes_to_run = ["M1_spawn_affordance"]
    elif args.subset == "m2a":
        modes_to_run = ["M2a_harness_decision"]
    elif args.modes and args.modes != "M0,M1,M2a":
        # Parse comma-separated modes
        requested = [m.strip() for m in args.modes.split(",")]
        mode_map = {"M0": "M0_spawn_closed", "M1": "M1_spawn_affordance", "M2a": "M2a_harness_decision"}
        modes_to_run = [mode_map[m] for m in requested if m in mode_map]

    # Determine tasks
    tasks = load_tasks()
    if args.command == "test":
        tasks = tasks[:1]
        print("TEST MODE: 1 task")
    elif args.subset == "2hop":
        tasks = [t for t in tasks if t["num_hops"] == 2]
    elif args.subset == "3hop":
        tasks = [t for t in tasks if t["num_hops"] == 3]
    elif args.subset == "4hop":
        tasks = [t for t in tasks if t["num_hops"] == 4]
    elif args.task_filter:
        tasks = [t for t in tasks if t["task_id"] == args.task_filter]

    resume_mode = args.resume

    print(f"Harness: {len(tasks)} tasks × {len(modes_to_run)} modes = {len(tasks)*len(modes_to_run)} runs")
    sys.stdout.flush()

    # Deduplicate: skip if run_id already exists in runs.jsonl
    existing = set()
    if os.path.exists(f"{OUTPUT_ROOT}/runs.jsonl"):
        with open(f"{OUTPUT_ROOT}/runs.jsonl") as f:
            for line in f:
                try:
                    existing.add(json.loads(line)["run_id"])
                except:
                    pass

    results = []
    total = len(tasks) * len(modes_to_run)
    for i, task in enumerate(tasks):
        for mode in modes_to_run:
            run_id = f"{task['task_id']}__{mode}"
            if run_id in existing:
                print(f"[{i*len(modes_to_run)+modes_to_run.index(mode)+1}/{total}] {task['task_id']} | {mode} — SKIP (exists)")
                continue
            entry = run_single(task, mode)
            _save_run_log(entry)
            results.append(entry)
            status = "✅" if entry["success"] else "❌"
            spawn = entry.get("actual_spawn_count", 0)
            tok = entry["token_usage"]["total_tokens"]
            rt = entry["runtime_sec"]
            decision_str = ""
            if mode == "M2a_harness_decision":
                decision_str = f" decision={entry.get('spawn_decision','?')}"
            print(f"[{i*len(modes_to_run)+modes_to_run.index(mode)+1}/{total}] {task['task_id']} | {mode} | {task['num_hops']}hop\n  {status}[{spawn}s] tok={tok} rt={rt:.0f}s{decision_str}")
            sys.stdout.flush()
            time.sleep(0.5)

    if results:
        summarize_results(results, modes_to_run)


if __name__ == "__main__":
    main()
