#!/usr/bin/env python3
"""
MiniMax API Probe — Test spawn behavior with MiniMax M2.7-highspeed.
Clone of v26-must: documents in file, MUST spawn trigger.
Runs 5 mixed-hop tasks to quickly assess spawn propensity + accuracy.
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'minimax/MiniMax-M2.7-highspeed'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v4')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/minimax_probe_v1')
RESULTS_FILE = OUTPUT_DIR / 'results_probe.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'probe_stdout.log'

SYSTEM_FORCE_MULTI = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for document searches.

The documents are in a file named `documents.txt` in your working directory.
You may:
  • Spawn a subagent: task(description="...", prompt="Read documents.txt and find <info>", subagent_type="general")
  • Read `documents.txt` directly using the read tool

CRITICAL: You MUST spawn at least one subagent before answering.
For complex multi-hop questions, spawn subagents for each sub-question.

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


def extract_answer_from_events(events):
    """Extract answer from events, stripping think tags."""
    full_text = '\n'.join(
        e['part'].get('text', '') for e in events if e.get('type') == 'text'
    )
    # Strip  ️<｜end▁of▁thinking｜> tags
    clean = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL)
    clean = re.sub(r'<think>.*', '', clean, flags=re.DOTALL)  # unclosed think

    # Priority 1: ## ANSWER: **X**
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', clean, re.DOTALL)
    if m and len(m.group(1).strip()) > 1:
        return m.group(1).strip(), clean

    # Priority 2: ## ANSWER: X
    m = re.search(r'##\s*ANSWER:\s*(.+?)(?:\n|$)', clean, re.IGNORECASE)
    if m and len(m.group(1).strip()) > 1:
        return m.group(1).strip(), clean

    # Priority 3: ANSWER: **X**
    m = re.search(r'ANSWER:\s*\*\*(.+?)\*\*', clean, re.DOTALL)
    if m and len(m.group(1).strip()) > 1:
        return m.group(1).strip(), clean

    # Priority 4: ANSWER: X (last occurrence)
    for line in reversed(clean.split('\n')):
        line = line.strip()
        if not line:
            continue
        line_clean = line.replace('**', '')
        m = re.search(r'ANSWER:\s*(.+)', line_clean, re.IGNORECASE)
        if m:
            ans = m.group(1).strip()
            if ans and ans != '<your answer>' and len(ans) > 1:
                return ans, clean

    # Priority 5: **The answer is X.**
    m = re.search(r'\*\*[Tt]he\s+[^*]+is\s+([^.]+)\.', clean)
    if m:
        return m.group(1).strip(), clean

    # Priority 6: **Answer: X.**
    m = re.search(r'\*\*[Aa]nswer:\s*(.+?)\*\*', clean)
    if m:
        return m.group(1).strip(), clean

    # Priority 7: Last substantial line
    for line in reversed(clean.split('\n')):
        line = line.strip()
        if (line and len(line) > 2 and
            not re.search(r'ANNSWER|VERIFICATION|Based on|I need to|Let me', line, re.I) and
            not line.startswith('Does this information') and
            line != '<your answer>'):
            return line, clean

    return '', clean


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


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—', '*']:
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


def run_probe_task(task, run_id):
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__mm-probe'
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    topics = [p['title'] for p in task['paragraphs']]
    topic_hint = ', '.join(topics[:6])
    if len(topics) > 6:
        topic_hint += f' ... ({len(topics)} total)'

    user_prompt = f"""Answer this multi-hop question. Documents are in `documents.txt`.

Document topics include: {topic_hint}

Question: {question}

ANSWER: """

    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    output_file = run_dir / 'opencode_raw_output.jsonl'

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
                inp = part.get('state', {}).get('input', {})
                fpath = inp.get('filePath', '')
                if 'documents.txt' in fpath or 'documents' in fpath:
                    used_read_directly = True

        elif etype == 'text':
            content = part.get('text', '')
            all_text_parts.append(content)

    if task_event_index is not None and task_event_index < len(events) - 1:
        subagent_returned = True

    predicted, clean_text = extract_answer_from_events(events)
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
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = int(time.time())

    tasks = load_tasks()
    # Probe: pick 2 × 2hop, 2 × 3hop, 1 × 4hop
    probe_tasks = []
    hop_counts = {'2hop': 0, '3hop': 0, '4hop': 0}
    targets = {'2hop': 2, '3hop': 2, '4hop': 1}

    for task in tasks:
        hop = task['id'].split('__')[0]
        if hop in targets and hop_counts.get(hop, 0) < targets[hop]:
            probe_tasks.append(task)
            hop_counts[hop] = hop_counts.get(hop, 0) + 1
        if all(hop_counts.get(h, 0) >= targets[h] for h in targets):
            break

    print(f"Probe: {len(probe_tasks)} tasks ({hop_counts})")
    print(f"Model: {MODEL}")
    print(f"Output: {OUTPUT_DIR}\n")

    log_file = open(STDOUT_LOG, 'w')
    correct = 0
    total = 0
    spawned_count = 0

    for i, task in enumerate(probe_tasks):
        task_id = task['id']
        t0 = time.time()
        print(f"[{i+1}/{len(probe_tasks)}] {task_id} ... ", end='', flush=True)
        result = run_probe_task(task, run_id)
        elapsed = time.time() - t0

        status = '✓' if result['correct'] else '✗'
        strategy = 'spawn' if result['spawned'] else ('read' if result['used_read_directly'] else 'none')
        print(f"{status} ({elapsed:.0f}s) strategy={strategy} | pred={result['predicted'][:60]}")
        print(f"  Answer: {result['answer']}", file=log_file)
        log_file.flush()

        with open(RESULTS_FILE, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        if result['spawned']:
            spawned_count += 1
        total += 1

    log_file.close()

    acc = 100 * correct / total if total > 0 else 0
    spawn_rate = 100 * spawned_count / total if total > 0 else 0
    print(f"\n=== Probe Results ===")
    print(f"Accuracy: {correct}/{total} ({acc:.0f}%)")
    print(f"Spawn rate: {spawned_count}/{total} ({spawn_rate:.0f}%)")
    print(f"Results saved to: {RESULTS_FILE}")


if __name__ == '__main__':
    main()
