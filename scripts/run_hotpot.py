#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — HotpotQA Harness
Uses MiniMax-M2.7-highspeed via OpenCode CLI.
Fullwiki distractor setting: 10 Wikipedia articles per question.
"""
import subprocess, json, time, re, os
from pathlib import Path
import pandas as pd

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'minimax/MiniMax-M2.7-highspeed'
PARQUET_FILE = Path('/home/jinxu/opencode-spawn-pilot/hotpot_fullwiki_val.parquet')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/hotpotqa_minimax_fm')
LIMIT = int(os.environ.get('HOTPOT_LIMIT', '0'))  # 0 = all

SYSTEM_FORCE_MULTI = '''You are a research agent. For multi-hop questions, you MUST spawn multiple subagents in parallel to work on different sub-questions simultaneously.

The documents are in a file named `documents.txt` in your working directory.
You may:
  • Read `documents.txt` directly using the read tool
  • Spawn subagents: task(description="...", prompt="Read documents.txt and find <info>", subagent_type="general")

CRITICAL: You MUST spawn at least 2 subagents for this task. Divide the sub-questions among your subagents.
You may also read documents directly yourself to help coordinate and verify answers.

After subagents return, synthesize their findings.

**Answer format: Put your final answer on a single line: ANSWER: <your brief answer>**
Do NOT include explanations, reasoning, or any other text after the answer line.'''


def build_docs(context):
    """Build documents.txt from HotpotQA context dict.
    context has keys 'title' (array) and 'sentences' (array of sentence arrays).
    """
    titles = context['title']
    sentences = context['sentences']
    lines = []
    for title, sent_arr in zip(titles, sentences):
        lines.append(f'[Article: {title}]')
        for sent in sent_arr:
            lines.append(sent)
        lines.append('')
    return '\n'.join(lines)


def extract_answer_from_text(full_text):
    """Extract answer from model output text using pattern matching."""
    # Priority 0: **Answer: X** or **ANSWER: X**
    m = re.search(r'\*\*[Aa]nswer:\s*(.+?)\*\*', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and '**' not in ans:
            return ans

    # Priority 1: ## ANSWER: X
    m = re.search(r'##\s*ANSWER:\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) >= 1:
            return ans

    # Priority 2: ANSWER: X (plain, case-insensitive)
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if not line:
            continue
        m = re.search(r'ANSWER:\s*(.+)', line, re.IGNORECASE)
        if m:
            ans = m.group(1).strip()
            if ans and ans != '<your answer>' and len(ans) >= 1:
                return ans

    # Priority 3: Last substantial line (avoid headers)
    HEADER_RE = re.compile(
        r'^(Answer|Finding|Synthesis|Sub-question|Multi-hop|Breaking|Step|Hop|Chain|Reasoning|Key|Analysis|Summary|Task|Decomposition|Does this|Let me|Based on|I need)',
        re.IGNORECASE
    )
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if line and len(line) > 2 and not HEADER_RE.match(line):
            return line

    return ''


def parse_raw_output(raw_text):
    """Parse JSONL events from raw output."""
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


def get_text_from_events(events):
    """Extract all text from JSONL events."""
    parts = []
    for e in events:
        if e.get('type') == 'text':
            txt = e.get('part', {}).get('text', '')
            if txt.strip():
                parts.append(txt)
    return '\n'.join(parts)


# HotpotQA official evaluation metrics
def normalize_answer(s):
    """Lower text and remove punctuation."""
    s = str(s).lower().strip()
    for punct in '!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~\t\n':
        s = s.replace(punct, ' ')
    # Collapse whitespace
    s = ' '.join(s.split())
    return s


def hotpot_em(pred, gold):
    """Exact match (case-insensitive)."""
    return normalize_answer(pred) == normalize_answer(gold)


def hotpot_f1(pred, gold):
    """Token-level F1 between prediction and gold."""
    pred_toks = normalize_answer(pred).split()
    gold_toks = normalize_answer(gold).split()
    if not pred_toks or not gold_toks:
        return 0.0
    common = set(pred_toks) & set(gold_toks)
    if not common:
        return 0.0
    precision = len(common) / len(pred_toks)
    recall = len(common) / len(gold_toks)
    return 2 * precision * recall / (precision + recall)


def load_hotpot():
    """Load HotpotQA fullwiki validation set."""
    df = pd.read_parquet(PARQUET_FILE)
    tasks = []
    for _, row in df.iterrows():
        tasks.append({
            'id': str(row['id']),
            'question': row['question'],
            'answer': row['answer'],
            'type': row['type'],
            'level': row['level'],
            'context': row['context'],
        })
    if LIMIT > 0:
        tasks = tasks[:LIMIT]
    return tasks


def run_task(task):
    """Run a single HotpotQA task."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']

    docs = build_docs(task['context'])

    run_dir = OUTPUT_DIR / f'{task_id}'
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    topic_hint = ', '.join(task['context']['title'][:6])
    if len(task['context']['title']) > 6:
        topic_hint += f' ... ({len(task["context"]["title"])} total)'

    user_prompt = f"""Execute this task now. Do NOT wait for further user input.

Read `documents.txt` (in your working directory) and answer the question below.
Do NOT ask for clarification — begin immediately.

Document topics include: {topic_hint}

Question: {question}

ANSWER: """

    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'

    output_file = run_dir / 'opencode_raw_output.jsonl'
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}.txt'

    prompt_file.write_text(full_prompt, encoding='utf-8')

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
        error = None
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        output_text = ''
        error = 'timeout'
    except Exception as e:
        output_text = ''
        error = str(e)
    finally:
        if prompt_file.exists():
            prompt_file.unlink()

    events = parse_raw_output(output_text)
    full_text = get_text_from_events(events)
    predicted = extract_answer_from_text(full_text)

    em = hotpot_em(predicted, answer)
    f1 = hotpot_f1(predicted, answer)

    spawned = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'task'
        for e in events
    )

    return {
        'id': task_id,
        'question': question,
        'answer': answer,
        'predicted': predicted,
        'em': em,
        'f1': f1,
        'spawned': spawned,
        'output_len': len(output_text),
        'event_count': len(events),
        'error': error,
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tasks = load_hotpot()
    print(f"Loaded {len(tasks)} HotpotQA tasks")

    results = []
    correct = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        t0 = time.time()

        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)

        result = run_task(task)
        elapsed = time.time() - t0

        status = '✓' if result['em'] else '✗'
        print(f"{status} ({elapsed:.0f}s) em={result['em']:.1f} f1={result['f1']:.2f}")
        print(f"  pred={repr(result['predicted'][:60])}")
        print(f"  ans ={repr(result['answer'][:60])}")

        if result['em']:
            correct += 1
        results.append(result)

        acc = 100 * correct / (i + 1)
        print(f"  >> {i+1}/{len(tasks)} done, EM: {correct}/{i+1} ({acc:.1f}%)\n", flush=True)

    total_em = sum(1 for r in results if r['em'])
    total_f1 = sum(r['f1'] for r in results) / len(results)
    spawned_count = sum(1 for r in results if r['spawned'])

    print(f"\n=== HotpotQA EM: {total_em}/{len(results)} ({100*total_em/len(results):.1f}%) ===")
    print(f"=== HotpotQA Avg F1: {total_f1*100:.1f}% ===")
    print(f"=== Spawned: {spawned_count}/{len(results)} ({100*spawned_count/len(results):.1f}%) ===")

    # Save results
    RESULTS_FILE = OUTPUT_DIR / 'results.json'
    with open(RESULTS_FILE, 'w') as f:
        json.dump({
            'em': total_em / len(results),
            'f1': total_f1,
            'total': len(results),
            'spawned': spawned_count,
            'results': results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {RESULTS_FILE}")


if __name__ == '__main__':
    main()
