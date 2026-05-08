#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v26-single — Single Agent baseline for v26.
Documents in file (like v26), NO spawn instruction.
Model reads documents.txt directly to answer.
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v4')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v26_single_single')
RESULTS_FILE = OUTPUT_DIR / 'results_single_v26.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'v26_single_stdout.log'
RUN_META = OUTPUT_DIR / 'run_meta_v26_single_single.json'

# v26-single: Documents in file, no spawn — model reads directly to answer
SYSTEM_FORCE_MULTI = '''You are a research agent. Answer multi-hop questions using the provided documents.

The documents are in a file named `documents.txt` in your working directory.
Read the file using the read tool to find the information you need.

After gathering information, give your verified answer.

ANSWER:'''


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
    
    v15 format: Agent writes "## ANSWER: **X**" or "## ANSWER: X" with verification steps.
    We prioritize ## ANSWER patterns over bold-text fallbacks.
    """
    # Collect all text content
    all_texts = []
    for event in events:
        if event.get('type') == 'text':
            all_texts.append(event['part'].get('text', ''))

    full_text = '\n'.join(all_texts)

    # Priority 0: ## ANSWER: **X** (v15 bold, highest priority)
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 1: ## ANSWER: X (v15 plain)
    m = re.search(r'##\s*ANSWER:\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 2: ANSWER: **X** (old format bold)
    m = re.search(r'ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 3: ANSWER: X (simple, reversed scan for last occurrence)
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

    # Priority 6: Last substantial line — avoid verification step text
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if (line and len(line) > 2 and
            not re.search(r'ANN?SWER|VERIFICATION|Based on|I need to|Let me', line, re.I) and
            not line.startswith('Does this information') and
            line != '<your answer>'):
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
        # No markers — assume raw JSONL
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


_NUM_WORDS = {
    '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
    '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine',
    '10': 'ten', '11': 'eleven', '12': 'twelve', '13': 'thirteen',
    '14': 'fourteen', '15': 'fifteen', '16': 'sixteen', '17': 'seventeen',
    '18': 'eighteen', '19': 'nineteen', '20': 'twenty',
}
_WORD_NUMS = {v: k for k, v in _NUM_WORDS.items()}

_NUM_WORDS = {
    '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
    '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine',
    '10': 'ten', '11': 'eleven', '12': 'twelve', '13': 'thirteen',
    '14': 'fourteen', '15': 'fifteen', '16': 'sixteen', '17': 'seventeen',
    '18': 'eighteen', '19': 'nineteen', '20': 'twenty',
}
_WORD_NUMS = {v: k for k, v in _NUM_WORDS.items()}

def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    # Convert digit tokens to word equivalents
    words = []
    for w in s.split():
        words.append(_NUM_WORDS.get(w, w))
    return ' '.join(words).strip()
    words = []
    for w in s.split():
        words.append(_NUM_WORDS.get(w, w))
    return ' '.join(words).strip()


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
    """Run a single force-multi task — v26: documents NOT in prompt."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__single-v26-{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write documents to file — subagent will read this
    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    # v26-single: Documents NOT in prompt — model decides strategy.
    # Give topic hints so model knows what to search for.
    topics = [p['title'] for p in task['paragraphs']]
    topic_hint = ', '.join(topics[:6])
    if len(topics) > 6:
        topic_hint += f' ... ({len(topics)} total)'

    user_prompt = f"""Answer this multi-hop question. Documents are in `documents.txt`.

Document topics include: {topic_hint}

Question: {question}

ANSWER: """

    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'

    output_file = run_dir / 'opencode_raw_output.jsonl'
    log_file = run_dir / 'opencode.log'
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}_{run_id}.txt'

    # Write prompt to file (model reads via @/path syntax)
    prompt_file.write_text(full_prompt, encoding='utf-8')

    # Use 'script' for PTY (correct model behavior) + communicate() for complete capture
    opencode_cmd = ' '.join([
        OPENCODE, 'run',
        '--model', MODEL,
        '--format', 'json',
        '--title', task_id,
        '--message', f'@{prompt_file.absolute()}'
    ])
    cmd = ['script', '-q', '-c', opencode_cmd, '/dev/null']

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(run_dir)
        )
        output_bytes, _ = proc.communicate(timeout=600)
        output_file.write_bytes(output_bytes)
        output_text = output_bytes.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        output_text = ''
    except Exception as e:
        output_text = ''
    finally:
        if prompt_file.exists():
            prompt_file.unlink()

    # Parse events
    events = parse_raw_output(output_text)

    spawned = False
    subagent_returned = False
    used_read_directly = False
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
            elif tool_name == 'read':
                # Main agent read documents directly — track this
                inp = part.get('state', {}).get('input', {})
                fpath = inp.get('filePath', '')
                if 'documents.txt' in fpath or 'documents' in fpath:
                    used_read_directly = True

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
        'used_read_directly': used_read_directly,
        'output_len': len(output_text),
        'event_count': len(events),
        'text_parts': len(all_text_parts),
    }


def main():
    # run_id: optional positional (resume from a specific run_id)
    try:
        run_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
    except (ValueError, IndexError):
        run_id = None
    
    if run_id:
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
        read_info = ', direct_read' if result['used_read_directly'] else ''
        spawn_info = f"spawn={result['spawned']}, subagent={result['subagent_returned']}{read_info}"
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
        print(f"    >> {done}/{len(tasks)} done, current acc: {correct}/{total} ({acc:.0f}%)", flush=True)

    log_file.close()
    print(f"\n=== FM v26: {correct}/{total} ({100*correct/total:.0f}%) ===")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('model', nargs='?', default='qwen', choices=['qwen', 'minimax'],
                    help='Model: qwen (local vLLM) or minimax (API)')
    args = ap.parse_args()

    if args.model == 'minimax':
        MODEL = 'minimax/MiniMax-M2.7-highspeed'
        OUTPUT_DIR = Path(str(OUTPUT_DIR).replace('comparison_v26_single', 'comparison_v26_single_minimax'))
        RESULTS_FILE = OUTPUT_DIR / 'results_single_v26_minimax.jsonl'
        STDOUT_LOG = OUTPUT_DIR / 'v26_minimax_single_stdout.log'
        RUN_META = OUTPUT_DIR / 'run_meta_v26_single_minimax.json'
    main()
