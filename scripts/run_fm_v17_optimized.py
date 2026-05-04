#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v16 — Optimized Prompt Experiment.

Changes from v15:
1. ADDED mandatory planning step BEFORE spawning:
   - Agent must identify target paragraphs and search terms before spawning
   - Agent must self-check: do I already have enough info to answer?
   - This prevents "premature delegation" (spawning before thinking)
2. IMPROVED subagent prompt: specifies paragraph IDs + structured output format
   - This prevents "tool blindness" (subagent doesn't know where to search)
3. ENHANCED verification: cross-validation with second paragraph
   - Agent must verify findings with a second source
   - This prevents "integration failure" (misrepresenting subagent findings)
4. CONSTRAINED answer output: must be exact facts only, no模糊 language
   - This prevents "hallucinated elaboration" (adding "typically", "likely", etc.)

v15 problems identified:
- Agent spawns without planning (premature delegation)
- Subagent prompt is too vague (tool blindness)
- No cross-validation step (integration failure)
- No constraint on answer output format (hallucination)
"""
import subprocess, json, time, sys, re, os
from pathlib import Path
import threading

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v17')
RESULTS_FILE = OUTPUT_DIR / 'results_fm_v17.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'v17_stdout.log'
RUN_META = OUTPUT_DIR / 'run_meta_v17.json'

# v17: Based on v15 structure + improved subagent prompt (structured return format) + cross-verification
# Key changes vs v15:
#   - Subagent prompt uses structured return (FOUND/SOURCE/CONFIDENCE)
#   - Verification step enhanced with cross-paragraph check (4 questions instead of 3)
SYSTEM_FORCE_MULTI = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL WORKFLOW:
1. Read the question and identify what you need to find
2. Spawn a subagent using: task(description="<topic>", prompt="Search the provided documents for <specific info>. Return: FOUND: <exact fact> | SOURCE: <paragraph ID> | CONFIDENCE: high/medium/low", subagent_type="general")
3. IMPORTANT: After the subagent completes, verify BEFORE answering:
   a) What did the subagent find? Quote the key facts
   b) Does this COMPLETELY answer the question? (Yes/No)
   c) Can you verify with a SECOND paragraph? (Yes/No)
   d) Based on verified facts only → FINAL ANSWER: <exact answer>
   Never skip verification. Never give an answer without first writing it.

ANSWER: <your verified answer>'''


def load_tasks():
    tasks = []
    for tf in sorted(DATA_DIR.glob('*.json')):
        tasks.append(json.loads(tf.read_text()))
    return tasks


def build_docs(task):
    lines = []
    for p in task['paragraphs']:
        lines.append(f'[Paragraph {p["idx"]}] {p["title"]}')
        lines.append(p['text'])
        lines.append('')
    return '\n'.join(lines)


def extract_answer_from_jsonl_events(events):
    """Extract answer from parsed JSONL events.
    
    v17 format: Agent writes "ANSWER: <verified answer>" with verification steps.
    We prioritize ## ANSWER patterns, then ANSWER:, then structured fallbacks.
    """
    # Collect all text content
    all_texts = []
    for event in events:
        if event.get('type') == 'text':
            all_texts.append(event['part'].get('text', ''))

    full_text = '\n'.join(all_texts)

    # Priority 0: ## ANSWER: **X** (bold, highest priority)
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        # Filter garbage: "** 2", "UNABLE TO", "UNABLE TO ANSWER"
        if ans and len(ans) > 2 and not re.match(r'^[\s*]*$', ans) and 'UNABLE TO' not in ans.upper():
            return ans, full_text

    # Priority 1: ## ANSWER: X (plain)
    m = re.search(r'##\s*ANSWER:\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 2: FINAL ANSWER: X
    m = re.search(r'FINAL\s+ANSWER:\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 3: ANSWER: **X** (bold)
    m = re.search(r'ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 4: ANSWER: X (simple, reversed scan for last occurrence)
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if not line:
            continue
        line_clean = line.replace('**', '')
        m = re.search(r'ANSWER:\s*(.+)', line_clean, re.IGNORECASE)
        if m:
            ans = m.group(1).strip()
            if ans and ans != '<your answer>' and len(ans) > 1:
                return ans, full_text

    # Priority 5: **The answer is X.**
    m = re.search(r'\*\*[Tt]he\s+[^\*]+is\s+([^.]+)\.', full_text)
    if m:
        return m.group(1).strip(), full_text

    # Priority 6: **Answer: X.**
    m = re.search(r'\*\*[Aa]nswer:\s*(.+?)\*\*', full_text)
    if m:
        return m.group(1).strip(), full_text

    # Priority 7: Last substantial line — avoid planning/verification step text
    skip_patterns = [
        r'ANN?SWER', r'VERIFICATION', r'PHASE', r'planning',
        r'BEFORE YOU SPAWN', r'PLAN:', r'do i (already )?have',
        r'can you verify', r'does this completely',
        r'subagent failed', r'unable to verify',
        r'what am i looking', r'which paragraph',
        r'found:', r'source:', r'confidence:',
        r'based on', r'i (can|cannot)', r'the subagent',
    ]
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if not line or len(line) < 3 or line == '<your answer>':
            continue
        skip = False
        for pat in skip_patterns:
            if re.search(pat, line, re.I):
                skip = True
                break
        if not skip:
            return line, full_text

    return '', full_text


def parse_raw_output(raw_text):
    """Parse raw output file (may have Script started/Script done wrappers).
    Returns list of JSONL lines (stripped of terminal markers)."""
    if not raw_text:
        return []

    # Extract content between 'Script started' and 'Script done'
    m = re.search(r'Script started on[^\n]*\n(.*?)\nScript done', raw_text, re.DOTALL)
    if m:
        content = m.group(1)
    else:
        content = raw_text

    # Parse into events
    events = []
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except:
            pass
    return events


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    return s.strip()

STOPWORDS = set(['the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to', 'for',
    'and', 'or', 'on', 'at', 'by', 'with', 'as', 'from', 'that', 'this', 'it', 'be',
    'has', 'have', 'had', 'not', 'but', 'if', 'they', 'we', 'you', 'he', 'she', 'his',
    'her', 'its', 'our', 'their', 'will', 'would', 'could', 'should', 'may', 'might'])


def is_correct(pred, answer, aliases=None):
    if not pred:
        return False
    p = normalize(pred)
    a = normalize(answer)
    if p == a:
        return True
    if aliases:
        for alias in aliases:
            if p == normalize(alias):
                return True
    # Answer as substring of prediction
    a_words = a.split()
    for i in range(len(a_words)):
        if a_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(a_words[i:])
            if len(suffix) >= 4 and suffix in p:
                return True
            break
    # Prediction as substring of answer
    p_words = p.split()
    for i in range(len(p_words)):
        if p_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(p_words[i:])
            if len(suffix) >= 4 and suffix in a:
                return True
            break
    # All non-stopword content words from answer appear in prediction
    words_a = [w for w in a.split() if len(w) >= 2 and w.lower() not in STOPWORDS]
    if words_a:
        def word_in_text(w, t):
            t = ' ' + t + ' '
            return (' ' + w + ' ') in t or (' ' + w + ',') in t or (' ' + w + '.') in t
        matched = sum(1 for w in words_a if word_in_text(w, p))
        if matched == len(words_a):
            return True
    return False


def run_fm_task(task, run_id):
    """Run a single force-multi task with v17 optimized prompt."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__fm-v17-{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    user_prompt = f"""Answer this multi-hop question using ONLY the provided documents.

Question: {question}

Documents:
{docs}

Follow the complete workflow in the system prompt above."""

    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'

    output_file = run_dir / 'opencode_raw_output.jsonl'
    log_file = run_dir / 'opencode.log'
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}_{run_id}.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    cmd = [
        OPENCODE, 'run',
        '--model', MODEL,
        '--format', 'json',
        '--title', task_id,
        '--message', f'@{prompt_file.absolute()}'
    ]

    try:
        # Write output file directly (bypass Python buffering)
        output_file = run_dir / 'opencode_raw_output.jsonl'
        with open(output_file, 'wb') as fh:
            import sys
            sys.stderr.write(f'DEBUG: starting opencode in {run_dir}\n')
            sys.stderr.flush()
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(run_dir),
                start_new_session=True
            )
            sys.stderr.write(f'DEBUG: proc started pid={proc.pid}\n')
            sys.stderr.flush()
            # Wait with polling to detect if proc finishes quickly
            import time
            for _ in range(60):  # max 60 seconds
                ret = proc.poll()
                if ret is not None:
                    sys.stderr.write(f'DEBUG: proc finished quickly ret={ret}\n')
                    break
                time.sleep(1)
            else:
                sys.stderr.write(f'DEBUG: proc still running after 60s, continuing anyway\n')
        # Read back the file
        output_bytes = output_file.read_bytes()
        output_text = output_bytes.decode('utf-8', errors='replace')
    except Exception as e:
        import traceback
        sys.stderr.write(f'DEBUG: exception: {e}\n{traceback.format_exc()}\n')
        output_text = ''
    finally:
        if prompt_file.exists():
            prompt_file.unlink()

    # Parse events
    events = parse_raw_output(output_text)

    spawned = False
    subagent_returned = False
    all_text_parts = []
    task_event_index = None

    for i, event in enumerate(events):
        etype = event.get('type', '')
        part = event.get('part', {})

        if etype == 'tool_use':
            tool_name = part.get('tool', '')
            if tool_name == 'task':
                spawned = True
                task_event_index = i

        elif etype == 'text':
            content = part.get('text', '')
            all_text_parts.append(content)

    # If a task tool was used and there are events after it, subagent returned
    if task_event_index is not None and task_event_index < len(events) - 1:
        subagent_returned = True

    full_text = '\n'.join(all_text_parts)
    predicted, _ = extract_answer_from_jsonl_events(events)
    correct = is_correct(predicted, answer, aliases)

    return {
        'task_id': task_id,
        'correct': correct,
        'predicted': predicted,
        'answer': answer,
        'spawned': spawned,
        'subagent_returned': subagent_returned,
        'output_len': len(output_text),
        'event_count': len(events),
        'text_parts': len(all_text_parts),
    }


def main():
    if len(sys.argv) > 1:
        run_id = int(sys.argv[1])
        print(f"Resuming run_id={run_id}")
    else:
        run_id = int(time.time())
        print(f"Starting new run_id={run_id}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Save run metadata
    with open(RUN_META, 'w') as f:
        json.dump({'run_id': run_id, 'started': time.time()}, f)

    # Check for existing results
    existing = set()
    if RESULTS_FILE.exists():
        existing = {json.loads(l)['task_id'] for l in open(RESULTS_FILE)}
        print(f"Found {len(existing)} existing results, will skip those tasks.")

    tasks = load_tasks()
    print(f"Loaded {len(tasks)} tasks, {len(existing)} already done.")

    PROGRESS_FILE = OUTPUT_DIR / 'progress_v17.txt'
    log_file = open(STDOUT_LOG, 'w')
    correct = 0
    total = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            print(f"[{i+1}/{len(tasks)}] {task_id} ... SKIP")
            continue

        t0 = time.time()
        # Write progress BEFORE subprocess so we can monitor
        done = len(existing) + total
        with open(PROGRESS_FILE, 'w') as pf:
            pf.write(f"{done}/{len(tasks)} | acc: {correct}/{total} | RUNNING: {task_id}\n")
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)
        result = run_fm_task(task, run_id)
        elapsed = time.time() - t0

        status = '✓' if result['correct'] else '✗'
        spawn_info = f"spawn={result['spawned']}, subagent={result['subagent_returned']}"
        msg = f"{status} ({elapsed:.0f}s) {spawn_info}"
        print(msg)
        log_file.write(f"[{i+1}/{len(tasks)}] {task_id} ... {msg}\n")
        log_file.write(f"    Predicted: {result['predicted'][:80]}\n")
        log_file.write(f"    Answer:    {result['answer']}\n")
        log_file.flush()

        with open(RESULTS_FILE, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        total += 1

        done = len(existing) + total
        acc = 100 * correct / total if total > 0 else 0
        progress_msg = f"    >> {done}/{len(tasks)} done, current acc: {correct}/{total} ({acc:.0f}%)"
        print(progress_msg, flush=True)
        log_file.write(progress_msg + '\n')
        log_file.flush()

        # Also write to progress file for easy monitoring
        with open(PROGRESS_FILE, 'w') as pf:
            pf.write(f"{done}/{len(tasks)} | acc: {correct}/{total} ({acc:.0f}%) | last: {task_id} | {status} | {elapsed:.0f}s\n")

    log_file.close()
    print(f"\n=== FM v17 (optimized): {correct}/{total} ({100*correct/total:.0f}%) ===")


if __name__ == '__main__':
    main()
