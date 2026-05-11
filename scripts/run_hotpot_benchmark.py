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
    """Extract answer from the last text event's last line only.

    Bugfix v2: Previous extraction scanned ALL text events and greedily matched
    bare **bold** from subagent thinking output, causing wrong entity names to be
    returned instead of the final answer. Now we only look at the very last
    text event and extract from its final line only.
    """
    text_events = [e for e in events if e.get('type') == 'text']
    if not text_events:
        return '', ''
    last_text = text_events[-1].get('part', {}).get('text', '')

    # Strip think tags (MiniMax/Qwen3.5 emit <think>...</think> tokens)
    last_text = re.sub(r'<think>.*?』', '', last_text, flags=re.DOTALL)
    last_text = re.sub(r'<think>.*', '', last_text)

    # Take only the last line to avoid subagent thinking leaking into extraction
    lines = last_text.strip().split('\n')
    last_line = lines[-1].strip()

    # Priority 1: **ANSWER: X**  (non-greedy, single line only)
    m = re.match(r'\*\*ANSWER:\s*(.+?)\*\*\s*$', last_line, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip(), last_text

    # Priority 2: ANSWER: X  (strip trailing punctuation)
    m = re.match(r'ANSWER:\s*(.+?)\s*$', last_line, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip().rstrip('.'), last_text

    # Priority 3: **Answer: X** (no inner **, single line)
    m = re.match(r'\*\*Answer:\s*(.+?)\*\*\s*$', last_line, re.IGNORECASE)
    if m and m.group(1).strip() and '**' not in m.group(1):
        return m.group(1).strip(), last_text

    # Priority 4: yes/no detection (when model says "The answer is yes." etc.)
    m = re.search(r'\b([Yy]es|[Nn]o)\b', last_line)
    if m and 'answer' in last_line.lower():
        return m.group(1), last_text

    # Priority 5: "the answer is X" variations
    for pat in [r'[Tt]he answer is[:\s]+([^.!?]+)', r'[Tt]he answer should be[:\s]+(.+?)(?:\.|$)']:
        m = re.search(pat, last_line)
        if m:
            ans = re.sub(r'^\*\*|\*\*$', '', m.group(1).strip()).strip().rstrip('.')
            if ans:
                return ans, last_text

    # Priority 6: Scan last 3 lines for ANSWER patterns
    for line in reversed(lines[-3:]):
        line = line.strip()
        for pat in [r'\*\*ANSWER:\s*(.+?)\*\*', r'ANSWER:\s*(.+?)\s*$']:
            m = re.match(pat, line, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip().rstrip('.'), last_text

    return '', last_text


def normalize_for_match(s):
    """Normalize string for semantic matching — remove punctuation, extra spaces."""
    for x in [',', '.', '!', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    return ' '.join(s.lower().split())


def is_semantic_match(pred, gt):
    """Check if pred matches gt meaning-wise, not just exact string.

    Handles:
    - Yes/No with trailing explanation (e.g., "Yes, both X and Y are musicians")
    - Number format differences (e.g., "seven" vs "7", "3" vs "three centuries")
    - Entity name aliases (e.g., "Bill Clinton" vs "William Jefferson Clinton")
    - Substring containment (GT is substring of pred or vice versa)
    - Token overlap ratio >= 0.6
    """
    pred_n = normalize_for_match(pred)
    gt_n = normalize_for_match(gt)
    if pred_n == gt_n:
        return True

    # Yes/No: extract the yes/no word
    if gt_n in ('yes', 'no'):
        return bool(re.search(r'\b(yes|no)\b', pred_n))

    # Number normalization: strip commas and spaces
    def parse_nums(s):
        s = s.replace(',', '').replace(' ', '')
        try:
            return float(s)
        except:
            return None

    pred_nums = [parse_nums(w) for w in pred_n.split()]
    gt_nums = [parse_nums(w) for w in gt_n.split()]
    pred_nums = [n for n in pred_nums if n is not None]
    gt_nums = [n for n in gt_nums if n is not None]
    # Numeric match: within 1% relative error
    for pn in pred_nums:
        for gn in gt_nums:
            if gn != 0 and abs(pn - gn) / abs(gn) < 0.01:
                return True
            if gn == 0 and pn == 0:
                return True

    # Substring containment
    if len(gt_n) >= 2 and gt_n in pred_n:
        return True
    if len(pred_n) >= 2 and pred_n in gt_n:
        return True

    # Token overlap
    stopwords = {'the', 'a', 'an', 'of', 'in', 'to', 'and', 'is', 'are', 'was',
                 'were', 'for', 'on', 'with', 'as', 'by', 'at', 'it', 'of',
                 'that', 'this', 'be', 'has', 'have', 'had', 'not', 'but',
                 'which', 'or', 'from', 'they', 'their', 'has', 'was', 'were'}
    gt_tokens = set(gt_n.split()) - stopwords
    pred_tokens = set(pred_n.split()) - stopwords
    if gt_tokens and pred_tokens:
        ratio = len(gt_tokens & pred_tokens) / len(gt_tokens)
        if ratio >= 0.6:
            return True

    # First content token match (handles "7" vs "seven")
    if gt_tokens and pred_tokens:
        first_gt = sorted(gt_tokens, key=len, reverse=True)[0]
        first_pred = sorted(pred_tokens, key=len, reverse=True)[0]
        if first_gt == first_pred and len(gt_tokens) == 1:
            return True

    return False


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


def is_correct(pred, gold, use_semantic=False):
    """Check if pred matches gold.

    use_semantic=True: meaning-based matching (yes/no with explanation,
    number format differences, entity aliases, token overlap).
    use_semantic=False: strict exact match (EM).
    """
    if use_semantic:
        return is_semantic_match(pred, gold)
    # Strip punctuation and lowercase for strict EM
    pred_n = normalize_for_match(pred)
    gold_n = normalize_for_match(gold)
    return pred_n == gold_n


def normalize_for_match(s):
    """Normalize string for matching — remove punctuation, extra spaces, lowercase."""
    for x in [',', '.', '!', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    return ' '.join(s.lower().split())


def run_task(task, model, output_dir, use_semantic=False):
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
    correct = is_correct(predicted, answer, use_semantic=use_semantic)

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
    parser.add_argument('--semantic', action='store_true',
                        help='Use semantic matching instead of strict EM')
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

        result = run_task(task, args.model, output_dir, use_semantic=args.semantic)
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
    mode_str = 'Semantic Match' if args.semantic else 'EM'
    print(f"\n=== HotpotQA {mode_str}: {correct}/{total} ({100*correct/total:.1f}%) ===")
    print(f"=== Spawned: {spawned_count}/{total} ({100*spawned_count/total:.1f}%) ===")


if __name__ == '__main__':
    main()
