#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — Activation Probe Harness

M0 (spawn_closed):          Build agent alone. No knowledge of subagent capability.
M1 (spawn_affordance):      Build knows it CAN call wrapper script, but not forced.
M2 (spawn_decision_required): Build MUST output SPAWN_DECISION yes/no before acting.
                              If yes → must call wrapper script. If no → proceeds alone.

Spawn ground truth: spawn_events.jsonl (written by wrapper script).
No stdout regex detection.
"""

import json, os, subprocess, time, re, sys
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
        "description": "Build-only. No subagent affordance.",
        "can_spawn": False,
    },
    "M1_spawn_affordance": {
        "description": "Build knows it CAN call spawn_explore.sh.",
        "can_spawn": True,
    },
    "M2_spawn_decision_required": {
        "description": "Build must output SPAWN_DECISION yes/no, then act accordingly.",
        "can_spawn": True,
    },
}


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
            proc.wait()
            exit_code = proc.returncode
            timeout_flag = True
        finally:
            proc.stdout.close()
        stdout = "".join(stdout_chunks)
    elapsed = time.time() - start
    with open(stdout_file, "w") as f:
        f.write(stdout)
    with open(stderr_file) as f:
        stderr = f.read()
    return {
        "stdout": stdout, "stderr": stderr,
        "exit_code": exit_code, "runtime_sec": elapsed,
        "timeout": timeout_flag,
        "stdout_file": stdout_file, "stderr_file": stderr_file,
    }


# ─── Output Extraction ───────────────────────────────────────────────────────

def extract_answer(stdout, stderr):
    """Extract final ANSWER: line from OpenCode output."""
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


def extract_text_events(stdout):
    """Extract all text events from OpenCode JSON output."""
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
    return texts


def parse_metrics(stdout):
    """Parse token usage and step count from stdout JSON logs."""
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
            tokens["input"]  += tok.get("input", 0)
            tokens["output"] += tok.get("output", 0)
            tokens["total"]  += tok.get("total", 0)
    return tokens, steps


# ─── Evaluation ──────────────────────────────────────────────────────────────

def evaluate(answer, gold_answer, aliases):
    """Evaluate predicted answer against gold answer."""
    answer_clean = re.sub(r'\*+', '', answer).strip().lower().rstrip('.').rstrip(',')
    gold_clean   = gold_answer.strip().lower().rstrip('.')

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
        if len(overlap) / max(len(ans_tokens), len(gold_tokens)) > 0.6:
            return True, "partial_match"

    return False, "mismatch"


# ─── Spawn Ground Truth (from wrapper log) ──────────────────────────────────

def load_spawn_events():
    """Load all spawn events from spawn_events.jsonl."""
    if not os.path.exists(SPAWN_EVENTS):
        return []
    events = []
    with open(SPAWN_EVENTS) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def count_spawn_calls_for_run(run_id):
    """Return list of spawn event dicts for a given run_id."""
    events = load_spawn_events()
    return [e for e in events if e.get("run_id") == run_id]


def detect_echoed_instructions(stdout):
    """
    Detect cases where the model echoed the spawn instruction but did NOT
    actually call the wrapper script. Looks for the prompt text appearing
    verbatim in text output.
    """
    texts = extract_text_events(stdout)
    echoed = []
    for t in texts:
        if "opencode run --agent explore" in t and "Decide for yourself" in t:
            echoed.append(t[:300])
    return echoed


def parse_spawn_decision(stdout):
    """
    For M2: extract SPAWN_DECISION yes/no from Build output.
    Returns ('yes', 'no', or None, and the line containing it).
    """
    texts = extract_text_events(stdout)
    for t in texts:
        t_upper = t.upper()
        if "SPAWN_DECISION" in t_upper:
            m = re.search(r'SPAWN_DECISION\s*:\s*(yes|no)', t_upper, re.IGNORECASE)
            if m:
                return m.group(1).lower(), t[:500]
    return None, None


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

SPAWN_DECISION_PROMPT = (
    "Before doing anything else, you must decide whether to delegate document "
    "exploration to a subagent.\n\n"
    "Output exactly ONE of the following on its own line:\n"
    "   SPAWN_DECISION: yes\n"
    "   SPAWN_DECISION: no\n\n"
    "If you choose yes, you MUST call the wrapper script immediately:\n"
    "   bash /home/jinxu/opencode-spawn-pilot/scripts/spawn_explore.sh "
    "<task_id> <run_id> <workdir> <your exploration task>\n"
    "If you choose no, proceed alone using grep and read.\n"
    "You must output a SPAWN_DECISION line first, then act accordingly."
)


def build_prompt(task, mode):
    """Build the prompt for a given mode."""
    base = (
        f"TASK: Answer a multi-hop question by searching and reading documents.\n\n"
        f"QUESTION: {task['question']}\n\n"
        f"RESOURCES:\n"
        f"- documents.txt: {task['num_paragraphs']} Wikipedia-style paragraphs. "
        f"Each starts with \"--- PARAGRAPH N ---\".\n\n"
        f"PROCESS:\n"
        f"1. Use `grep` to search for keywords in documents.txt\n"
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

    elif mode == "M2_spawn_decision_required":
        return base + SPAWN_DECISION_PROMPT + "\n\nBegin now."


# ─── Per-Run Logic ───────────────────────────────────────────────────────────

def run_single(task, mode_key):
    """Run a single task in the specified mode."""
    run_id   = f"{task['task_id']}__{mode_key}"
    workdir, _ = prepare_workdir(run_id, task)

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

    # ── Spawn ground truth from wrapper log ──
    spawn_events = count_spawn_calls_for_run(run_id)
    actual_spawn_count = len(spawn_events)

    # ── M2-specific fields ──
    spawn_decision    = None
    spawn_decision_line = None
    if mode_key == "M2_spawn_decision_required":
        spawn_decision, spawn_decision_line = parse_spawn_decision(stdout)

    # ── Echo detection (M1/M2) ──
    echoed = detect_echoed_instructions(stdout) if mode_key != "M0_spawn_closed" else []

    entry = {
        "run_id":                run_id,
        "task_id":               task["task_id"],
        "mode":                  mode_key,
        "model":                 "qwen35-9b",
        "difficulty_bucket":      task["difficulty_bucket"],
        "num_hops":              task["num_hops"],
        "question":              task["question"],
        "gold_answer":           task["answer"],
        "predicted_answer":      answer,
        "success":               success,
        "eval_detail":           eval_detail,
        "token_usage": {
            "input_tokens":      tokens["input"],
            "output_tokens":      tokens["output"],
            "total_tokens":       tokens["total"],
        },
        "runtime_sec":            elapsed,
        "steps":                 steps,
        # Spawn metrics (ground truth from wrapper log)
        "actual_spawn_count":    actual_spawn_count,  # 0 = no spawn
        # M2 only
        "spawn_decision":        spawn_decision,
        "spawn_decision_line":   spawn_decision_line,
        # Echo detection
        "echoed_instructions":    len(echoed) > 0,
        "echoed_preview":         echoed[0] if echoed else None,
        # Meta
        "exit_code":             result.get("exit_code", -1),
        "timeout":               result.get("timeout", False),
    }

    _save_run_log(entry)
    return entry


def _save_run_log(entry):
    log_file = f"{OUTPUT_ROOT}/runs.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── Summary Reporter ─────────────────────────────────────────────────────────

def summarize_results(results, modes_to_run):
    """Print the activation probe results table."""
    by_mode = {}
    for r in results:
        by_mode.setdefault(r["mode"], []).append(r)

    print(f"\n{'='*70}")
    print("ACTIVATION PROBE RESULTS — Spawn Affordance Prompt Study")
    print(f"{'='*70}\n")

    # Per-mode table
    header = f"{'Mode':<35} {'N':>3} {'Acc':>6} {'Spawn':>6} {'Echo':>5} {'Tok/ans':>10}"
    print(header)
    print("-" * 70)
    for mk in modes_to_run:
        runs = by_mode.get(mk, [])
        if not runs:
            continue
        n    = len(runs)
        acc  = sum(1 for r in runs if r["success"]) / n
        spn  = sum(r.get("actual_spawn_count", 0) for r in runs)
        ech  = sum(1 for r in runs if r.get("echoed_instructions", False))
        print(f"{mk:<35} {n:>3} {acc:>6.0%} {spn:>6} {ech:>5} {'—':>10}")

    # Per-hop breakdown
    print("\nPer-hop breakdown (accuracy / spawn_count):")
    for hops in [2, 3, 4]:
        hop_runs = [r for r in results if r["num_hops"] == hops]
        if not hop_runs:
            continue
        print(f"  {hops}-hop ({len(hop_runs)} runs):", end="")
        for mk in modes_to_run:
            mk_hops = [r for r in hop_runs if r["mode"] == mk]
            if not mk_hops:
                continue
            acc  = sum(1 for r in mk_hops if r["success"]) / len(mk_hops)
            spn  = sum(r.get("actual_spawn_count", 0) for r in mk_hops)
            print(f"  {mk.split('_')[1]}={acc:.0%}({spn}s)", end="")
        print()

    # M2 spawn decision analysis
    m2_runs = by_mode.get("M2_spawn_decision_required", [])
    if m2_runs:
        print("\nM2 Spawn Decision Analysis:")
        decisions = [r.get("spawn_decision") for r in m2_runs]
        yes_count = decisions.count("yes")
        no_count  = decisions.count("no")
        none_count= decisions.count(None)
        print(f"  SPAWN_DECISION yes:  {yes_count}/{len(m2_runs)}")
        print(f"  SPAWN_DECISION no:   {no_count}/{len(m2_runs)}")
        print(f"  SPAWN_DECISION none: {none_count}/{len(m2_runs)}  (malformed output)")

        yes_runs = [r for r in m2_runs if r.get("spawn_decision") == "yes"]
        no_runs  = [r for r in m2_runs if r.get("spawn_decision") == "no"]
        if yes_runs:
            yes_acc = sum(1 for r in yes_runs if r["success"]) / len(yes_runs)
            yes_spawn = sum(r.get("actual_spawn_count", 0) for r in yes_runs)
            print(f"  Decision=yes accuracy:  {yes_acc:.0%}  (spawn_count={yes_spawn})")
        if no_runs:
            no_acc = sum(1 for r in no_runs if r["success"]) / len(no_runs)
            no_spawn = sum(r.get("actual_spawn_count", 0) for r in no_runs)
            print(f"  Decision=no accuracy:  {no_acc:.0%}  (spawn_count={no_spawn})")


# ─── Main ────────────────────────────────────────────────────────────────────

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
        elif subset == "m0":
            modes_to_run = ["M0_spawn_closed"]
        elif subset == "m1":
            modes_to_run = ["M1_spawn_affordance"]
        elif subset == "m2":
            modes_to_run = ["M2_spawn_decision_required"]
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

    # Resume: skip already-completed runs
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
            idx   = i * len(modes_to_run) + j + 1
            total = len(tasks) * len(modes_to_run)
            key   = (task["task_id"], mode_key)
            print(f"\n[{idx}/{total}] {task['task_id']} | {mode_key} | {task['num_hops']}hop")
            sys.stdout.flush()

            if resume_mode and key in existing_entries:
                entry = existing_entries[key]
                status = "✅" if entry["success"] else "❌"
                spn    = f"[{entry.get('actual_spawn_count', 0)}s]"
                print(f"  ⏭️  SKIP | {status}{spn} "
                      f"Answer: '{str(entry.get('predicted_answer',''))[:50]}' | "
                      f"{entry['token_usage']['total_tokens']} tok")
                results.append(entry)
                continue

            entry = run_single(task, mode_key)

            status = "✅" if entry["success"] else "❌"
            spn    = f"[{entry.get('actual_spawn_count', 0)}s]"
            echo   = "[ECHO]" if entry.get("echoed_instructions") else ""
            print(f"  {status}{spn}{echo} "
                  f"Answer: '{entry['predicted_answer'][:50]}' (gold: '{entry['gold_answer']}') | "
                  f"{entry['token_usage']['total_tokens']} tok | {entry['runtime_sec']:.0f}s")

            results.append(entry)

    summarize_results(results, modes_to_run)


if __name__ == "__main__":
    main()
