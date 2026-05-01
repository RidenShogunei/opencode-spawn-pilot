#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v13 — Agent-Decides mode on 55 tasks.
Model decides whether to spawn subagents based on task complexity.
Uses PTY for proper TTY handling in background mode.
"""
import subprocess, json, time, re, os, pty, select
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
DATA_DIR = Path('outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('outputs/opencode_spawn_pilot/comparison_v13_agent_decides')
RESULTS_FILE = OUTPUT_DIR / 'results_agent_decides_v13.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'v13_agent_decides_stdout.log'

# Agent-Decides prompt: informs model about task tool, lets it decide
SYSTEM_AGENT_DECIDES = '''You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

You have access to two approaches for searching documents:
1. Direct search: Use read and grep tools to search documents yourself
2. Delegation: Use the 'task' tool to spawn a research subagent that searches on your behalf

WHEN TO DELEGATE (use task tool):
- When the question requires finding MULTIPLE pieces of information from different parts of the documents
- When you would need to run several separate searches and cross-reference results
- When the documents are large and parallel search would be more efficient

WHEN TO SEARCH DIRECTLY (use read/grep):
- When the question can be answered with a single search
- When you can quickly locate the answer yourself

DELEGATION FORMAT:
  task(description="<short topic>", prompt="Read <FILEPATH> and find <INFO>", subagent_type="general")

After any subagent completes, review its findings and give your final answer.

Output your final answer on its own line:
ANSWER: <your answer>'''


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


def extract_answer(text):
    # Strip markdown bold/italic markers
    text = text.replace('**', '').replace('*', '').replace('__', '')
    # Strip script wrapper lines
    lines = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('Script ')]
    # Find last ANSWER line
    for line in reversed(lines):
        line = line.strip()
        if re.search(r'ANN?SWER', line, re.IGNORECASE):
            m = re.search(r'ANN?WER:\s*(.+?)(?:\s*$)', line, re.IGNORECASE)
            if m:
                ans = m.group(1).strip('"\' \t')
                if ans == '<your answer>' or not ans:
                    continue
                if len(ans) > 0:
                    return ans
    # Fallback: last substantial non-ANSWER, non-placeholder line
    for line in reversed(lines):
        line = line.strip().strip('"\' \t')
        if (line and
            not re.search(r'ANN?SWER', line, re.IGNORECASE) and
            line != '<your answer>' and
            len(line) > 1 and
            not line.startswith('Based on the provided')):
            return line
    return text.strip()[:200]


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"', '-', '–', '—']:
        s = s.replace(x, '')
    return s.strip()

STOPWORDS = set(['the','a','an','is','are','was','were','of','in','to','for','and','or','on','at','by','with','as','from','that','this','it','be','has','have','had','not','but','if','they','we','you','he','she','his','her','its','our','their'])

def is_correct(pred, answer, aliases=None):
    p = normalize(pred)
    a = normalize(answer)
    if p == a:
        return True
    if aliases:
        for alias in aliases:
            if p == normalize(alias):
                return True
    # Fuzzy: answer (minus leading stopwords) as contiguous substring of prediction
    a_words = a.split()
    for i in range(len(a_words)):
        if a_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(a_words[i:])
            if len(suffix) >= 4 and suffix in p:
                return True
            break
    # Prediction (minus leading stopwords) as contiguous substring of answer
    p_words = p.split()
    for i in range(len(p_words)):
        if p_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(p_words[i:])
            if len(suffix) >= 4 and suffix in a:
                return True
            break
    # All non-stopword content words from answer appear as complete words in prediction
    words_a = [w for w in a.split() if len(w) >= 2 and w.lower() not in STOPWORDS]
    if words_a:
        def word_in_text(w, t):
            t = ' ' + t + ' '
            return (' ' + w + ' ') in t or (' ' + w + ',') in t or (' ' + w + '.') in t
        matched = sum(1 for w in words_a if word_in_text(w, p))
        if matched == len(words_a):
            return True
    return False


def run_opencode_with_pty(cmd, timeout=600):
    """Run OpenCode with a pseudo-terminal to satisfy TTY requirements."""
    master_fd, slave_fd = pty.openpty()
    
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        cwd=str(Path.cwd())
    )
    os.close(slave_fd)
    
    output = b''
    start_time = time.time()
    
    while proc.poll() is None and time.time() - start_time < timeout:
        ready, _, _ = select.select([master_fd], [], [], 1.0)
        if ready:
            try:
                data = os.read(master_fd, 65536)
                if data:
                    output += data
            except (OSError, IOError):
                break
    
    # Wait for process to finish
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    
    # Drain remaining output
    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.5)
            if ready:
                data = os.read(master_fd, 65536)
                if data:
                    output += data
                else:
                    break
            else:
                break
    except (OSError, IOError):
        pass
    
    os.close(master_fd)
    
    return output.decode('utf-8', errors='replace')


def run_agent_decides_task(task, run_id):
    """Run a single task in agent-decides mode."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__agent-decides-v13-{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)

    docs_file = run_dir / 'documents.txt'
    docs_file.write_text(docs, encoding='utf-8')

    user_prompt = f"""Answer this multi-hop question using ONLY the provided documents.

Question: {question}

Documents:
{docs}

Use whichever approach (direct search or delegation via task tool) you think is most appropriate for this question.

ANSWER: """

    full_prompt = f'{SYSTEM_AGENT_DECIDES}\n\n---\n\n{user_prompt}'

    # Write prompt to a file OUTSIDE the run_dir so the model can't read it
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}_{run_id}.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    cmd = (
        f'{OPENCODE} run --model {MODEL} --format json --title {task_id} '
        f'--message @/{prompt_file.absolute()}'
    )

    try:
        output_text = run_opencode_with_pty(cmd, timeout=600)
    except Exception as e:
        output_text = ''
    finally:
        if prompt_file.exists():
            prompt_file.unlink()

    # Save raw output for debugging
    raw_output_file = run_dir / 'opencode_raw_output.jsonl'
    raw_output_file.write_text(output_text, encoding='utf-8')

    # Parse output: extract text and detect spawn events
    spawned = False
    subagent_returned = False
    output_text_parsed = ''

    for line in output_text.split('\n'):
        line = line.strip()
        if not line or line.startswith('Script '):
            continue
        try:
            entry = json.loads(line)
            content = ''
            if entry.get('type') == 'text':
                content = entry.get('part', {}).get('text', '')
            elif entry.get('type') == 'tool_use':
                state = entry.get('part', {}).get('state', {})
                content = str(state.get('output', ''))
                # Check if this is a task tool call (spawn)
                tool_name = entry.get('part', {}).get('tool', '')
                if tool_name == 'task':
                    spawned = True
            elif entry.get('type') == 'tool_result':
                content = str(entry.get('part', {}).get('result', ''))
                # Check if this is a task result (subagent returned)
                tool_name = entry.get('part', {}).get('tool', '')
                if tool_name == 'task':
                    subagent_returned = True
            output_text_parsed += content + '\n'
        except:
            pass

    predicted = extract_answer(output_text_parsed)
    correct = is_correct(predicted, answer, aliases)

    return {
        'task_id': task_id,
        'correct': correct,
        'predicted': predicted,
        'answer': answer,
        'spawned': spawned,
        'subagent_returned': subagent_returned,
        'output_len': len(output_text),
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if RESULTS_FILE.exists():
        existing = {json.loads(l)['task_id'] for l in open(RESULTS_FILE)}
        print(f"Found {len(existing)} existing results, will skip those tasks.")
    else:
        existing = set()

    tasks = load_tasks()
    print(f"Loaded {len(tasks)} tasks, {len(existing)} already done.")

    log_file = open(STDOUT_LOG, 'w')

    run_id = int(time.time())
    correct = 0
    total = 0
    spawned_count = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            print(f"[{i+1}/{len(tasks)}] {task_id} ... SKIP (already done)")
            continue

        t0 = time.time()
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)
        result = run_agent_decides_task(task, run_id)
        elapsed = time.time() - t0

        status = '✓' if result['correct'] else '✗'
        spawn_info = f"(spawn={result.get('spawned')}, subagent={result.get('subagent_returned')})" if result.get('spawned') else ''
        print(f"{status} ({elapsed:.1f}s) {spawn_info}")
        print(f"    Predicted: {result['predicted'][:80]}", file=log_file)
        print(f"    Answer:    {result['answer']}", file=log_file)

        with open(RESULTS_FILE, 'a') as rf:
            rf.write(json.dumps(result, ensure_ascii=False) + '\n')

        if result['correct']:
            correct += 1
        if result.get('spawned'):
            spawned_count += 1
        total += 1

        done = len(existing) + total
        acc = 100*correct/total if total > 0 else 0
        spawn_rate = 100*spawned_count/total if total > 0 else 0
        print(f"    >> {done}/{len(tasks)} done, acc: {correct}/{total} ({acc:.0f}%), spawn_rate: {spawned_count}/{total} ({spawn_rate:.0f}%)", flush=True)

    log_file.close()
    print(f"\n=== Agent-Decides v13: {correct}/{total} ({100*correct/total:.0f}%) ===")
    print(f"=== Spawn rate: {spawned_count}/{total} ({100*spawned_count/total:.0f}%) ===")


if __name__ == '__main__':
    main()
