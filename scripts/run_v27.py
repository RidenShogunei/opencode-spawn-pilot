#!/usr/bin/env python3
"""
v27 — Clean comparison: prompt quality vs model strategy.
Three arms, all with improved prompts:
  v27-embed: documents embedded in prompt (baseline, no spawn mention)
  v27-file:  documents in file, model reads freely (improved prompt)
  v27-spawn: documents in file, MUST spawn subagents (improved prompt)

Key improvements vs v26:
  1. Explicit "answer IS in documents" assertion → prevents "not found"
  2. Strict ANSWER: format → prevents extraction failures
  3. Concise, directive instructions → prevents "what to do?" confusion
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
DEFAULT_MODEL = 'local/qwen35-9b'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v4')
BASE_OUT = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot')

# ── Prompts ──────────────────────────────────────────────────

SYSTEM_EMBED = '''You are a precise research assistant. Answer multi-hop questions using the documents provided below.
Read all paragraphs carefully. The answer is guaranteed to be in these documents.
After analyzing, give your final answer.'''

SYSTEM_FILE = '''You are a precise research assistant. You have access to tools: read, bash, grep, task.
Answer multi-hop questions by reading the documents file.'''

SYSTEM_SPAWN = '''You are a precise research agent. You have access to tools including: task (spawn subagents), read, grep.
You MUST use the task tool to spawn subagents before answering.'''

# User prompts
USER_EMBED = '''Documents:
{docs}

Question: {question}

You MUST output exactly one line:
ANSWER: <your answer>'''

USER_FILE = '''Read the file `documents.txt` COMPLETELY now.
The answer to the question below is GUARANTEED to be found in that file.
After reading all paragraphs carefully, answer the question.

Question: {question}

You MUST output exactly one line:
ANSWER: <your answer>'''

USER_SPAWN = '''Read `documents.txt` using a subagent: task(description="find answer", prompt="Read documents.txt and find: {question}", subagent_type="general")
The answer is GUARANTEED to be in the file.
After the subagent returns, verify and give your answer.

Question: {question}

You MUST output exactly one line:
ANSWER: <your answer>'''


# ── Core functions ───────────────────────────────────────────

def load_tasks():
    tasks = []
    for tf in sorted(DATA_DIR.glob('task_*.json')):
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
    all_texts = []
    for event in events:
        if event.get('type') == 'text':
            all_texts.append(event['part'].get('text', ''))
    full_text = '\n'.join(all_texts)
    full_text = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL)
    full_text = re.sub(r'<think>.*', '', full_text, flags=re.DOTALL)

    # Priority 1: ANSWER: X (the new enforced format)
    for line in full_text.split('\n'):
        line = line.strip()
        m = re.search(r'ANSWER:\s*(.+)', line, re.IGNORECASE)
        if m:
            ans = m.group(1).strip().rstrip('.')
            if ans and len(ans) > 1:
                return ans, full_text

    # Priority 2: ## ANSWER: **X**
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m: return m.group(1).strip(), full_text

    # Priority 3: Last substantial line
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if line and len(line) > 2 and not re.search(r'ANN?SWER|Based on|I need|Let me', line, re.I):
            return line, full_text

    return '', full_text


def parse_raw_output(raw_text):
    if not raw_text: return []
    m = re.search(r'Script started on[^\n]*\n(.*?)\nScript done', raw_text, re.DOTALL)
    content = m.group(1) if m else raw_text
    events = []
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line: continue
        try: events.append(json.loads(line))
        except: pass
    return events


_NUM_WORDS = {str(i): w for i, w in enumerate([
    'zero','one','two','three','four','five','six','seven','eight','nine',
    'ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen',
    'seventeen','eighteen','nineteen','twenty'
])}

def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—', '*']:
        s = s.replace(x, '')
    words = [_NUM_WORDS.get(w, w) for w in s.split()]
    return ' '.join(words).strip()


STOPWORDS = set(['the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to', 'for',
    'and', 'or', 'on', 'at', 'by', 'with', 'as', 'from', 'that', 'this', 'it', 'be',
    'has', 'have', 'had', 'not', 'but', 'if', 'they', 'we', 'you', 'he', 'she'])

def is_correct(pred, answer, aliases=None):
    if not pred: return False
    p, a = normalize(pred), normalize(answer)
    if p == a: return True
    if aliases:
        for alias in aliases:
            if p == normalize(alias): return True
    # substring match
    a_words = a.split()
    for i in range(len(a_words)):
        if a_words[i] not in STOPWORDS:
            suffix = ' '.join(a_words[i:])
            if len(suffix) >= 4 and suffix in p: return True
            break
    p_words = p.split()
    for i in range(len(p_words)):
        if p_words[i] not in STOPWORDS:
            suffix = ' '.join(p_words[i:])
            if len(suffix) >= 4 and suffix in a: return True
            break
    # content word overlap
    words_a = [w for w in a.split() if len(w) >= 2 and w not in STOPWORDS]
    if words_a:
        matched = sum(1 for w in words_a if (' ' + w + ' ') in (' ' + p + ' '))
        if matched == len(words_a): return True
    return False


# ── Run functions ────────────────────────────────────────────

def run_embed(task, run_id):
    """Documents embedded in prompt — baseline."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = BASE_OUT / f'comparison_v27_embed/{task_id}__v27-embed'
    run_dir.mkdir(parents=True, exist_ok=True)

    user_prompt = USER_EMBED.format(docs=docs, question=question)
    full_prompt = f'{SYSTEM_EMBED}\n\n{user_prompt}'
    prompt_file = run_dir / '.prompt.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    output_file = run_dir / 'opencode_raw_output.jsonl'
    opencode_cmd = ' '.join([
        OPENCODE, 'run', '--model', MODEL,
        '--format', 'json', '--title', task_id,
        '--message', f'@{prompt_file.absolute()}'
    ])
    cmd = ['script', '-q', '-c', opencode_cmd, '/dev/null']

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(run_dir))
        output_bytes, _ = proc.communicate(timeout=600)
        output_file.write_bytes(output_bytes)
        output_text = output_bytes.decode('utf-8', errors='replace')
    except (subprocess.TimeoutExpired, Exception):
        output_text = ''
    finally:
        if prompt_file.exists(): prompt_file.unlink()

    events = parse_raw_output(output_text)
    predicted, _ = extract_answer_from_jsonl_events(events)
    correct = is_correct(predicted, answer, aliases)

    return {
        'task_id': task_id, 'correct': correct, 'predicted': predicted, 'answer': answer,
        'output_len': len(output_text), 'event_count': len(events)
    }


def run_file_based(task, run_id, mode='file'):
    """Documents in file, model reads them. mode='file' or 'spawn'."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    arm = 'file' if mode == 'file' else 'spawn'
    run_dir = BASE_OUT / f'comparison_v27_{arm}/{task_id}__v27-{arm}'
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write documents to file
    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    # Build prompt
    if mode == 'file':
        system = SYSTEM_FILE
        user_prompt = USER_FILE.format(question=question)
    else:
        system = SYSTEM_SPAWN
        user_prompt = USER_SPAWN.format(question=question)

    full_prompt = f'{system}\n\n{user_prompt}'
    prompt_file = run_dir / '.prompt.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    output_file = run_dir / 'opencode_raw_output.jsonl'
    opencode_cmd = ' '.join([
        OPENCODE, 'run', '--model', MODEL,
        '--format', 'json', '--title', task_id,
        '--message', f'@{prompt_file.absolute()}'
    ])
    cmd = ['script', '-q', '-c', opencode_cmd, '/dev/null']

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(run_dir))
        output_bytes, _ = proc.communicate(timeout=600)
        output_file.write_bytes(output_bytes)
        output_text = output_bytes.decode('utf-8', errors='replace')
    except (subprocess.TimeoutExpired, Exception):
        output_text = ''
    finally:
        if prompt_file.exists(): prompt_file.unlink()

    events = parse_raw_output(output_text)
    spawned = any(e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'task' for e in events)
    used_read = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'read' and
        'documents.txt' in str(e.get('part', {}).get('state', {}).get('input', {}).get('filePath', ''))
        for e in events
    )

    predicted, _ = extract_answer_from_jsonl_events(events)
    correct = is_correct(predicted, answer, aliases)

    return {
        'task_id': task_id, 'correct': correct, 'predicted': predicted, 'answer': answer,
        'spawned': spawned, 'used_read_directly': used_read,
        'output_len': len(output_text), 'event_count': len(events)
    }


# ── Main ─────────────────────────────────────────────────────

def main():
    arm = sys.argv[1] if len(sys.argv) > 1 else 'embed'
    if arm not in ('embed', 'file', 'spawn'):
        print(f"Usage: python {sys.argv[0]} [embed|file|spawn]")
        sys.exit(1)

    global MODEL
    arm_dirs = {'embed': 'comparison_v27_embed', 'file': 'comparison_v27_file', 'spawn': 'comparison_v27_spawn'}
    out_dir = BASE_OUT / arm_dirs[arm]

    results_file = out_dir / f'results_v27_{arm}.jsonl'
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = load_tasks()
    existing = set()
    if results_file.exists():
        existing = {json.loads(l)['task_id'] for l in open(results_file)}

    print(f"v27-{arm}: {len(tasks)} tasks, {len(existing)} already done")
    print(f"Model: {MODEL}")

    correct = 0
    total = 0
    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            continue

        t0 = time.time()
        if arm == 'embed':
            result = run_embed(task, 0)
        else:
            result = run_file_based(task, 0, mode=arm)

        elapsed = time.time() - t0
        s = '✓' if result['correct'] else '✗'
        extra = ''
        if 'spawned' in result:
            extra = f" spawn={result['spawned']} read={result.get('used_read_directly',False)}"
        print(f"[{i+1}/{len(tasks)}] {task_id} {s} ({elapsed:.0f}s){extra} | pred={result['predicted'][:50]}")

        with open(results_file, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']: correct += 1
        total += 1

        done = len(existing) + total
        print(f"    >> {done}/{len(tasks)} {correct}/{total} ({100*correct/total:.0f}%)", flush=True)

    print(f"\n=== v27-{arm}: {correct}/{total} ({100*correct/total:.0f}%) ===")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('arm', nargs='?', default='embed', choices=['embed', 'file', 'spawn'])
    ap.add_argument('--model', default=DEFAULT_MODEL)
    args = ap.parse_args()

    MODEL = args.model
    sys.argv = [sys.argv[0], args.arm]  # for main() to parse
    main()
