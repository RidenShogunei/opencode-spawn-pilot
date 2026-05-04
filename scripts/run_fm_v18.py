#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v18 — Fixed subagent prompt format.

v17 problem: Structured "FOUND: ... | SOURCE: ... | CONFIDENCE: ..." template
caused main model to NOT include document excerpts in subagent prompts.
Subagents received empty prompts → 21% error rate → 18% accuracy.

v18 fix: Revert to v15's natural-language subagent prompt guidance.
Same verification structure (3 questions + re-spawn) that achieved 40%.
Also fix prompt_file creation and subprocess handling bugs from v17.
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v18')
RESULTS_FILE = OUTPUT_DIR / 'results_fm_v18.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'v18_stdout.log'
RUN_META = OUTPUT_DIR / 'run_meta_v18.json'
PROGRESS_FILE = OUTPUT_DIR / 'progress_v18.txt'

# v18: v15's proven natural-language subagent prompt (identical to v15)
SYSTEM_FORCE_MULTI = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL WORKFLOW:
1. Read the question and identify what you need to find
2. Spawn a subagent using: task(description="<topic>", prompt="Read the provided documents and find <specific info>", subagent_type="general")
3. IMPORTANT: After the subagent completes, you MUST do the following BEFORE giving your answer:

   VERIFICATION STEP (required):
   Write your answers to these three questions:
   a) What did the subagent find? (Quote the key facts from the subagent output)
   b) Does this information directly and COMPLETELY answer the question? (Yes/No, and explain why)
   c) If YES → give the answer. If NO → spawn another subagent with a MORE SPECIFIC question.

   Never skip the verification step. Never give an answer without first writing it.

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
    """Extract answer from parsed JSONL events (v15's proven function)."""
    all_texts = []
    for event in events:
        etype = event.get('type', '')
        part = event.get('part', {})
        if etype == 'text':
            all_texts.append(part.get('text', ''))

    full_text = '\n'.join(all_texts)

    # Priority 0: ## ANSWER: **X** (bold, highest priority)
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 1: ## ANSWER: X (plain)
    m = re.search(r'##\s*ANSWER:\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 2: ANSWER: **X** (bold)
    m = re.search(r'ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 3: ANSWER: X (simple)
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

    # Priority 4: **The answer is X.**
    m = re.search(r'\*\*[Tt]he\s+[^\*]+is\s+([^.]+)\.', full_text)
    if m:
        return m.group(1).strip(), full_text

    # Priority 5: **Answer: X.**
    m = re.search(r'\*\*[Aa]nswer:\s*(.+?)\*\*', full_text)
    if m:
        return m.group(1).strip(), full_text

    # Priority 6: Last substantial line
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if (line and len(line) > 2 and
            not re.search(r'ANN?SWER|VERIFICATION|Based on|I need to|Let me', line, re.I) and
            not line.startswith('Does this information') and
            line != '<your answer>'):
            return line, full_text

    return '', full_text


def parse_raw_output(raw_text):
    """Parse raw output file, handling Script markers."""
    if not raw_text:
        return []

    m = re.search(r'Script started on[^\n]*\n(.*?)\nScript done', raw_text, re.DOTALL)
    if m:
        content = m.group(1)
    else:
        content = raw_text

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


STOPWORDS = set(['the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to', 'for',
    'and', 'or', 'on', 'at', 'by', 'with', 'as', 'from', 'that', 'this', 'it', 'be',
    'has', 'have', 'had', 'not', 'but', 'if', 'they', 'we', 'you', 'he', 'she', 'his',
    'her', 'its', 'our', 'their', 'will', 'would', 'could', 'should', 'may', 'might'])


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    return s.strip()


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
    """Run a single force-multi task with v18 prompt."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__fm-v18-{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    user_prompt = f"""Answer this multi-hop question using ONLY the provided documents.

Question: {question}

Documents:
{docs}

After the subagent completes, synthesize the findings and give your answer.

ANSWER: """

    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'

    # Write prompt to file (v15 approach: @filepath works with script PTY)
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}_{run_id}.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    # Use script -q -c for PTY (v15 proven: model spawns correctly with this)
    opencode_cmd = ' '.join([
        OPENCODE, 'run',
        '--model', MODEL,
        '--format', 'json',
        '--title', task_id,
        '--message', f'@{prompt_file.absolute()}'
    ])
    wrapped_cmd = ['script', '-q', '-c', opencode_cmd, '/dev/null']

    output_text = ''
    try:
        output_file = run_dir / 'opencode_raw_output.jsonl'
        proc = subprocess.Popen(
            wrapped_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(run_dir)
        )
        try:
            output_bytes, _ = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            output_bytes, _ = proc.communicate()
        output_file.write_bytes(output_bytes)
        output_text = output_bytes.decode('utf-8', errors='replace')
    except Exception:
        pass
    finally:
        if prompt_file.exists():
            prompt_file.unlink()

    if not output_text:
        return {
            'task_id': task_id,
            'correct': False,
            'predicted': '',
            'answer': answer,
            'spawned': False,
            'subagent_returned': False,
            'output_len': 0,
            'event_count': 0,
            'text_parts': 0,
            'error': 'empty_output' if output_text == '' else 'timeout',
        }

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

    if task_event_index is not None and task_event_index < len(events) - 1:
        subagent_returned = True

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

    with open(RUN_META, 'w') as f:
        json.dump({'run_id': run_id, 'started': time.time()}, f)

    existing = set()
    if RESULTS_FILE.exists():
        existing = {json.loads(l)['task_id'] for l in open(RESULTS_FILE)}
        print(f"Found {len(existing)} existing results, will skip those tasks.")

    tasks = load_tasks()
    print(f"Loaded {len(tasks)} tasks, {len(existing)} already done.")

    log_file = open(STDOUT_LOG, 'w')
    correct = 0
    total = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            print(f"[{i+1}/{len(tasks)}] {task_id} ... SKIP")
            continue

        t0 = time.time()
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)
        result = run_fm_task(task, run_id)
        elapsed = time.time() - t0

        status = '✓' if result['correct'] else '✗'
        spawn_info = f"spawn={result['spawned']}, subagent={result['subagent_returned']}"
        print(f"{status} ({elapsed:.0f}s) {spawn_info}")
        print(f"    Predicted: {result['predicted'][:80]}", file=log_file)
        print(f"    Answer:    {result['answer']}", file=log_file)
        log_file.flush()

        with open(RESULTS_FILE, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        total += 1

        done = len(existing) + total
        acc = 100 * correct / total if total > 0 else 0

        progress_msg = f"{done}/{len(tasks)} | acc: {correct}/{total} | last: {task_id} | {status} | {elapsed:.0f}s"
        with open(PROGRESS_FILE, 'w') as pf:
            pf.write(progress_msg)
        print(f"    >> {done}/{len(tasks)} done, current acc: {correct}/{total} ({acc:.0f}%)", flush=True)

    log_file.close()
    print(f"\n=== FM v18: {correct}/{total} ({100*correct/total if total else 0:.0f}%) ===")


if __name__ == '__main__':
    main()
