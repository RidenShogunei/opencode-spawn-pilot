#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — MiniMax API Harness v1
Documents NOT embedded in prompt. Model freely chooses: read directly or spawn.
Uses MiniMax-M2.7-highspeed via OpenCode CLI.
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'minimax/MiniMax-M2.7-highspeed'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v5')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v27_minimax_fm')

# v1: Documents NOT embedded — must spawn multiple subagents for sub-questions,
# but main agent may also read documents directly (not forced to go through subagents).
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
    """Extract answer from JSONL events (OpenCode format)."""
    all_text_parts = []
    for event in events:
        etype = event.get('type', '')
        part = event.get('part', {})
        if etype == 'text':
            content = part.get('text', '')
            all_text_parts.append(content)
        elif etype == 'assistant':
            content = part.get('content', '')
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get('type') == 'text':
                        all_text_parts.append(c.get('text', ''))
            elif isinstance(content, str):
                all_text_parts.append(content)

    full_text = '\n'.join(all_text_parts)

    # Strip think tags (MiniMax may output these) — non-greedy to avoid consuming entire text
    full_text = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL)

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

    # Priority 2: **Answer: X** (explicit answer format) — before bare bold
    # e.g. **Answer: 2** or **Answer: some phrase**
    # Use re.IGNORECASE because [Aa]nswer alone doesn't match ANSWER:
    m = re.search(r'\*\*[Aa]nswer:\s*(.+?)\*\*', full_text, re.IGNORECASE)
    if m:
        ans = m.group(1).strip()
        # Allow single-char answers like "2" or "X"
        # Skip if answer still contains ** (malformed output like **Answer:** X **)
        if ans and len(ans) >= 1 and '**' not in ans:
            return ans, full_text

    # Priority 3: **X** (bare bold, strip markers) — model outputs **Answer** without ## ANSWER:
    # Skip headers/structural markers like **Answer:**, **Findings:**, **Synthesis:**
    HEADER_PATTERNS = re.compile(
        r'^\s*(Answer|Finding|Synthesis|Sub-question|Multi-hop|Breaking down|Step|Hop|Chain|Reasoning|Key|Analysis|Summary|Task|Decomposition)\s*[:\d]*\s*$',
        re.IGNORECASE
    )
    BARE_ANSWER_HEADER = re.compile(r'^\s*[Aa]nswer\s*:\s*$')
    for m in re.finditer(r'\*\*([^\*]+)\*\*', full_text, re.DOTALL):
        ans = m.group(1).strip()
        # Skip if it's just a header label like "Answer:" or "Answer: "
        if BARE_ANSWER_HEADER.match(ans):
            continue
        if ans and len(ans) > 1 and not HEADER_PATTERNS.match(ans):
            return ans, full_text

    # Priority 4: ANSWER: **X** (old format bold)
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

    # Priority 6: Last substantial line — avoid verification step text
    for line in reversed(full_text.split('\n')):
        line = line.strip()
        if (line and len(line) > 2 and
            not re.search(r'ANN?SWER|VERIFICATION|Based on|I need to|Let me', line, re.I) and
            not line.startswith('Does this information') and
            line != '<your answer>'):
            return line, full_text

    # Priority 7: Last isolated number or short answer (thinking=false edge case
    # where model outputs bare thinking tags with answer at end, e.g. "...assistant5")
    m = re.search(r'\d+(?:\.\d+)?$', full_text.strip())
    if m:
        return m.group(0), full_text

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


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    # Convert digit tokens to word equivalents
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
            break  # only check first non-stopword anchor point
    # Prediction as substring of answer
    p_words = p.split()
    for i in range(len(p_words)):
        if p_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(p_words[i:])
            if len(suffix) >= 4 and suffix in a:
                return True
            break  # only check first non-stopword anchor point
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


def run_fm_task(task):
    """Run a single force-multi task — v26: documents NOT in prompt."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__fm-v1'
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write documents to file — subagent will read this
    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    # v26: Documents NOT in prompt — model decides strategy.
    # Give topic hints so model knows what to search for.
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
    log_file = run_dir / 'opencode.log'
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}.txt'

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
        'error': error,
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Fixed result file for this harness version
    RESULTS_FILE = OUTPUT_DIR / 'results_fm_v1_minimax.jsonl'
    STDOUT_LOG = OUTPUT_DIR / 'v1_minimax_stdout.log'
    RUN_META = OUTPUT_DIR / 'run_meta_v1_minimax.json'

    # Save run metadata
    with open(RUN_META, 'w') as f:
        json.dump({'started': time.time()}, f)

    tasks = load_tasks()
    print(f"Loaded {len(tasks)} tasks.")

    # Structured per-task log — one JSON object per line
    TASK_LOG = OUTPUT_DIR / 'tasks_v1_minimax.jsonl'

    correct = 0
    total = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        t0 = time.time()

        stdout_msg = f"[{i+1}/{len(tasks)}] {task_id} ... "
        print(stdout_msg, end='', flush=True)

        result = run_fm_task(task)
        elapsed = time.time() - t0

        status = '✓' if result['correct'] else '✗'
        read_info = ',direct_read' if result['used_read_directly'] else ''
        spawn_info = f"spawn={result['spawned']},subagent={result['subagent_returned']}{read_info}"
        raw_output_path = str(OUTPUT_DIR / f'{task_id}__fm-v1' / 'opencode_raw_output.jsonl')

        task_record = {
            'task_id': task_id,
            'task_index': i + 1,
            'total_tasks': len(tasks),
            'elapsed_s': round(elapsed, 1),
            'correct': result['correct'],
            'status': status,
            'spawned': result['spawned'],
            'subagent_returned': result['subagent_returned'],
            'used_read_directly': result['used_read_directly'],
            'event_count': result['event_count'],
            'text_parts': result['text_parts'],
            'output_len': result['output_len'],
            'predicted': result['predicted'],
            'answer': result['answer'],
            'spawn_info': spawn_info,
            'raw_output': raw_output_path,
            'error': result.get('error', None),
        }

        # Print human-readable summary line
        print(f"{status} ({elapsed:.0f}s) {spawn_info}")
        print(f"  pred={repr(result['predicted'][:60])}")
        print(f"  ans ={repr(result['answer'][:60])}")

        # Append structured record
        with open(TASK_LOG, 'a') as lf:
            lf.write(json.dumps(task_record, ensure_ascii=False) + '\n')

        # Append results
        with open(RESULTS_FILE, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        total += 1

        acc = 100 * correct / total if total > 0 else 0
        print(f"  >> {total}/{len(tasks)} done, acc: {correct}/{total} ({acc:.0f}%)\n", flush=True)

    print(f"\n=== FM v1 MiniMax: {correct}/{total} ({100*correct/total:.0f}%) ===")


if __name__ == '__main__':
    main()
