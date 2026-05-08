#!/usr/bin/env python3
"""
v27 — Clean spawn comparison: documents always embedded in prompt.
Only variable: does the model route through a subagent, or answer directly?

  v27-direct:  model reads embedded docs and answers directly  (baseline)
  v27-spawn:   model MUST spawn subagent; subagent gets embedded docs;
               parent verifies and answers

Both arms see the same total tokens (docs + question + instructions).
No external files to read — zero additional tool calls required for either arm.
"""
import subprocess, json, time, sys, re, os, argparse
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
DEFAULT_MODEL = 'local/qwen35-9b'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v4')
BASE_OUT = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot')

# ── Prompts ──────────────────────────────────────────────────
# Both get docs embedded. Only difference: spawn instruction.

SYSTEM_DIRECT = 'You are a precise research assistant. You will be given documents followed by a question. The EXACT answer IS in the documents. Read carefully and extract it.'

SYSTEM_SPAWN = '''You are a precise research agent with access to the 'task' tool for spawning subagents.
You MUST spawn at least one subagent to analyze the documents before answering.'''

USER_DIRECT = '''===== DOCUMENTS =====
{docs}

===== QUESTION =====
{question}

===== CRITICAL INSTRUCTIONS =====
1. The answer IS guaranteed to be in the documents above. Do NOT say "not found".
2. Read ALL paragraphs relevant to each entity mentioned in the question.
3. Output EXACTLY one line in this format:
ANSWER: <your concisest possible answer, just the key fact>'''

USER_SPAWN = '''Question: {question}

You MUST spawn a subagent using the task tool. Give it this exact prompt:

===== DOCUMENTS =====
{docs}

===== QUESTION =====
{question}
Find the exact answer in the documents. Output ONE line: ANSWER: <answer>

After the subagent returns, verify and output:
ANSWER: <answer>

FALLBACK: If the task tool is unavailable, read the documents above directly and answer.'''


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

    # Priority 1: ANSWER: X or answer: X or answer is: X
    for line in full_text.split('\n'):
        line = line.strip()
        m = re.search(r'(?:^|\b)(?:ANSWER|answer)\s*(?:is\s*)?:\s*(.+)', line, re.IGNORECASE)
        if m:
            ans = m.group(1).strip().rstrip('.')
            if ans and len(ans) > 1:
                return ans, full_text

    # Priority 2: ## ANSWER: **X**
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        return m.group(1).strip(), full_text

    # Priority 3: last line that looks like an answer (not a meta/action line)
    meta_pattern = re.compile(r"(?:^|\b)(?:I need|Let me|I will|I'll|Read the|Search|The documents|To answer|First|Task:)")
    for line in reversed(full_text.split("\n")):
        line = line.strip()
        if line and len(line) > 2 and not meta_pattern.search(line):
            return line, full_text

    return '', full_text


def parse_raw_output(raw_text):
    if not raw_text:
        return []
    m = re.search(r'Script started on[^\n]*\n(.*?)\nScript done', raw_text, re.DOTALL)
    content = m.group(1) if m else raw_text
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


_NUM_WORDS = {str(i): w for i, w in enumerate([
    'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
    'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
    'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'
])}


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '\u2013', '\u2014', '*']:
        s = s.replace(x, '')
    words = [_NUM_WORDS.get(w, w) for w in s.split()]
    return ' '.join(words).strip()


STOPWORDS = set(['the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to', 'for',
    'and', 'or', 'on', 'at', 'by', 'with', 'as', 'from', 'that', 'this', 'it', 'be',
    'has', 'have', 'had', 'not', 'but', 'if', 'they', 'we', 'you', 'he', 'she'])


def is_correct(pred, answer, aliases=None):
    if not pred:
        return False
    p, a = normalize(pred), normalize(answer)
    if p == a:
        return True
    if aliases:
        for alias in aliases:
            if p == normalize(alias):
                return True
    # substring match (skip leading stopwords)
    a_words = a.split()
    for i in range(len(a_words)):
        if a_words[i] not in STOPWORDS:
            suffix = ' '.join(a_words[i:])
            if len(suffix) >= 3 and suffix in p:
                return True
            break
    p_words = p.split()
    for i in range(len(p_words)):
        if p_words[i] not in STOPWORDS:
            suffix = ' '.join(p_words[i:])
            if len(suffix) >= 3 and suffix in a:
                return True
            break
    # content word overlap — all answer words in prediction
    words_a = [w for w in a.split() if len(w) >= 2 and w not in STOPWORDS]
    if words_a:
        matched = sum(1 for w in words_a if (' ' + w + ' ') in (' ' + p + ' '))
        if matched == len(words_a):
            return True
        # partial: at least half content words matched (min 1)
        if matched >= max(1, len(words_a) // 2):
            return True
    # substring: if one normalized text fully contains the other
    if len(p) >= 2 and p in a:
        return True
    if len(a) >= 2 and a in p:
        return True
    return False


# ── Run ──────────────────────────────────────────────────────

def run_one(task, arm, model):
    """Run a single task. Returns result dict."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = BASE_OUT / f'comparison_v27_{arm}/{task_id}__v27-{arm}'
    run_dir.mkdir(parents=True, exist_ok=True)

    if arm == 'direct':
        system = SYSTEM_DIRECT
        user = USER_DIRECT.format(docs=docs, question=question)
    else:  # spawn
        system = SYSTEM_SPAWN
        user = USER_SPAWN.format(docs=docs, question=question)

    full_prompt = f'{system}\n\n{user}'
    prompt_file = run_dir / '.prompt.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    output_file = run_dir / 'opencode_raw_output.jsonl'
    opencode_cmd = ' '.join([
        OPENCODE, 'run', '--model', model,
        '--format', 'json', '--title', task_id,
        '--message', f'@{prompt_file.absolute()}'
    ])
    cmd = ['script', '-q', '-c', opencode_cmd, '/dev/null']

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(run_dir))
        output_bytes, _ = proc.communicate(timeout=180)
        output_file.write_bytes(output_bytes)
        output_text = output_bytes.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            leftover = proc.stdout.read() if proc.stdout else b''
            output_file.write_bytes(leftover)
            output_text = leftover.decode('utf-8', errors='replace')
        except:
            output_text = ''
    except Exception:
        output_text = ''
    finally:
        if prompt_file.exists() and output_text.strip():
            prompt_file.unlink()  # keep prompt for debugging failures

    events = parse_raw_output(output_text)

    # Track tool usage
    spawned = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'task'
        for e in events
    )
    used_read = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'read'
        for e in events
    )

    predicted, _ = extract_answer_from_jsonl_events(events)
    correct = is_correct(predicted, answer, aliases)

    return {
        'task_id': task_id,
        'correct': correct,
        'predicted': predicted,
        'answer': answer,
        'spawned': spawned,
        'used_read': used_read,
        'output_len': len(output_text),
        'event_count': len(events),
    }


# ── Main ─────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('arm', nargs='?', default='direct', choices=['direct', 'spawn'])
    ap.add_argument('--model', default=DEFAULT_MODEL)
    args = ap.parse_args()
    model = args.model
    arm = args.arm

    out_dir = BASE_OUT / f'comparison_v27_{arm}'
    results_file = out_dir / f'results_v27_{arm}.jsonl'
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = load_tasks()
    existing = set()
    if results_file.exists():
        existing = {json.loads(l)['task_id'] for l in open(results_file)}

    print(f"v27-{arm}: {len(tasks)} tasks, {len(existing)} already done")
    print(f"Model: {model}")

    correct = 0
    total = 0
    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            continue

        t0 = time.time()
        result = run_one(task, arm, model)
        elapsed = time.time() - t0

        s = '✓' if result['correct'] else '✗'
        extra = f" spawn={result['spawned']} read={result['used_read']}"
        pred_preview = result['predicted'][:60].replace('\n', ' ')
        print(f"[{i+1}/{len(tasks)}] {task_id} {s} ({elapsed:.0f}s){extra} | pred={pred_preview}")

        with open(results_file, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        total += 1

        done = len(existing) + total
        print(f"    >> {done}/{len(tasks)} {correct}/{total} ({100*correct//total if total else 0}%)", flush=True)

    if total:
        print(f"\n=== v27-{arm}: {correct}/{total} ({100*correct//total}%) ===")
    else:
        print(f"\n=== v27-{arm}: all {len(existing)} already done ===")


if __name__ == '__main__':
    main()
