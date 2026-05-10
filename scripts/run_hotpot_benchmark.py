#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — HotpotQA Standard Benchmark Harness
Reads directly from HuggingFace Arrow file (standard distractor format).
Supports both MiniMax API and local vLLM models.
Usage:
  HOTPOT_LIMIT=200 python3 scripts/run_hotpot_benchmark.py --model minimax/MiniMax-M2.7-highspeed
  HOTPOT_LIMIT=200 python3 scripts/run_hotpot_benchmark.py --model local/qwen35-9b
"""
import subprocess, json, time, re, os, argparse
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
DEFAULT_MODEL = 'minimax/MiniMax-M2.7-highspeed'
ARROW_FILE = '/home/jinxu/.cache/huggingface/datasets/distractor/default/0.0.0/9caa7cb3112ee384/distractor-validation.arrow'

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


def build_docs(task):
    lines = []
    for p in task['paragraphs']:
        lines.append(f'[Paragraph {p["idx"]}] {p["title"]}')
        lines.append(p['text'])
        lines.append('')
    return '\n'.join(lines)


def load_hotpot(limit=0):
    """Load HotpotQA distractor validation from Arrow file."""
    import pyarrow as pa
    with pa.memory_map(ARROW_FILE, 'r') as source:
        reader = pa.ipc.open_stream(source)
        table = reader.read_all()
    rows = table.to_pydict()
    tasks = []
    for i in range(len(rows['id'])):
        ctx = rows['context'][i]
        titles = ctx['title']
        sents = ctx['sentences']

        paragraphs = []
        seen = set()
        for j, (t, sa) in enumerate(zip(titles, sents)):
            if t in seen:
                continue
            seen.add(t)
            combined = ' '.join(sa)
            paragraphs.append({'idx': j, 'title': t, 'text': combined})

        tasks.append({
            'id': str(rows['id'][i]),
            'question': rows['question'][i],
            'answer': rows['answer'][i],
            'paragraphs': paragraphs,
        })
    if limit > 0:
        tasks = tasks[:limit]
    return tasks


def extract_answer_from_jsonl_events(events):
    """Extract answer from JSONL events."""
    all_texts = []
    for event in events:
        if event.get('type') == 'text':
            txt = event.get('part', {}).get('text', '')
            if txt.strip():
                all_texts.append(txt)
    full_text = '\n'.join(all_texts)

    # Priority 0: ## ANSWER: **X**
    m = re.search(r'##\s*ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 1: ## ANSWER: X
    m = re.search(r'##\s*ANSWER:\s*(.+?)(?:\n|$)', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 2: **Answer: X**
    m = re.search(r'\*\*[Aa]nswer:\s*(.+?)\*\*', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) >= 1 and '**' not in ans:
            return ans, full_text

    # Priority 3: **X** (bare bold)
    HEADER_PATTERNS = re.compile(
        r'^\s*(Answer|Finding|Synthesis|Sub-question|Multi-hop|Breaking|Step|Hop|Chain|Reasoning|Key|Analysis|Summary|Task|Decomposition)\s*[:\d]*\s*$',
        re.IGNORECASE
    )
    BARE_ANSWER_HEADER = re.compile(r'^\s*[Aa]nswer\s*:\s*$')
    for m in re.finditer(r'\*\*([^\*]+)\*\*', full_text, re.DOTALL):
        ans = m.group(1).strip()
        if BARE_ANSWER_HEADER.match(ans):
            continue
        if ans and len(ans) > 1 and not HEADER_PATTERNS.match(ans):
            return ans, full_text

    # Priority 4: ANSWER: **X**
    m = re.search(r'ANSWER:\s*\*\*(.+?)\*\*', full_text, re.DOTALL)
    if m:
        ans = m.group(1).strip()
        if ans and len(ans) > 1:
            return ans, full_text

    # Priority 5: ANSWER: X
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

    # Priority 6: Last substantial line
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if (line and len(line) > 2 and
            not re.search(r'ANN?SWER|VERIFICATION|Based on|I need to|Let me', line, re.I) and
            line != '<your answer>'):
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


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    return ' '.join(s.split())


def is_correct(pred, gold):
    """EM check."""
    return normalize(pred) == normalize(gold)


def run_task(task, model, output_dir):
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    docs = build_docs(task)

    run_dir = output_dir / f'{task_id}'
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    topics = [p['title'] for p in task['paragraphs']]
    topic_hint = ', '.join(topics[:6])
    if len(topics) > 6:
        topic_hint += f' ... ({len(topics)} total)'

    user_prompt = f"""Execute this task now. Do NOT wait for further user input.

Read `documents.txt` (in your working directory) and answer the question below.
Do NOT ask for clarification — begin immediately.

Document topics include: {topic_hint}

Question: {question}

ANSWER: """

    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'

    output_file = run_dir / 'opencode_raw_output.jsonl'
    prompt_file = output_dir / f'.prompt_{task_id}.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    opencode_cmd = ' '.join([
        OPENCODE, 'run',
        '--model', model,
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

    spawned = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'task'
        for e in events
    )
    subagent_returned = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'task' and
        e.get('part', {}).get('state', {}).get('output', '')
        for e in events
    )
    used_read_directly = any(
        e.get('type') == 'tool_use' and e.get('part', {}).get('tool') == 'read'
        for e in events
    )

    predicted, _ = extract_answer_from_jsonl_events(events)
    correct = is_correct(predicted, answer)

    return {
        'task_id': task_id,
        'question': question,
        'answer': answer,
        'predicted': predicted,
        'correct': correct,
        'spawned': spawned,
        'subagent_returned': subagent_returned,
        'used_read_directly': used_read_directly,
        'event_count': len(events),
        'output_len': len(output_text),
        'error': error,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    model_name = args.model.replace('/', '_')
    output_dir = Path(args.output) if args.output else \
        Path(f'/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/benchmark_{model_name}')
    output_dir.mkdir(parents=True, exist_ok=True)

    limit = int(os.environ.get('HOTPOT_LIMIT', '0'))
    tasks = load_hotpot(limit)
    print(f"Loaded {len(tasks)} HotpotQA tasks (limit={limit})")

    RESULTS_FILE = output_dir / 'results.jsonl'
    TASK_LOG = output_dir / 'tasks.jsonl'
    STDOUT_LOG = output_dir / 'stdout.log'

    correct = 0
    total = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        t0 = time.time()

        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)

        result = run_task(task, args.model, output_dir)
        elapsed = time.time() - t0

        status = '✓' if result['correct'] else '✗'
        spawn_info = f"spawn={result['spawned']},subagent={result['subagent_returned']}"
        if result['used_read_directly']:
            spawn_info += ',direct_read'

        print(f"{status} ({elapsed:.0f}s) {spawn_info}")
        print(f"  pred={repr(result['predicted'][:60])}")
        print(f"  ans ={repr(result['answer'][:60])}")

        with open(TASK_LOG, 'a') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')

        with open(RESULTS_FILE, 'a') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        total += 1

        acc = 100 * correct / total if total > 0 else 0
        print(f"  >> {total}/{len(tasks)} done, acc: {correct}/{total} ({acc:.0f}%)\n", flush=True)

    spawned_count = sum(1 for r in [json.loads(l) for l in open(TASK_LOG)] if r['spawned'])
    print(f"\n=== HotpotQA EM: {correct}/{total} ({100*correct/total:.1f}%) ===")
    print(f"=== Spawned: {spawned_count}/{total} ({100*spawned_count/total:.1f}%) ===")


if __name__ == '__main__':
    main()
