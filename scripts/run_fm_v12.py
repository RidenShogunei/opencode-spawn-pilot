#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v12 — Force-Multi on 55 tasks (expanded dataset).
Runs all 55 tasks in task_data_v2/ sequentially with force_multi mode.
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
DATA_DIR = Path('outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('outputs/opencode_spawn_pilot/comparison_v12')
RESULTS_FILE = OUTPUT_DIR / 'results_fm_v12.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'v12_fm_stdout.log'

# v10 prompt
SYSTEM_FORCE_MULTI = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- You MUST spawn at least one subagent using task(...) to search documents before answering
- task(description="<topic>", prompt="Read <FILEPATH> and find <info>", subagent_type="general")

After the subagent completes, synthesize the findings and give your answer.

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
    # Strip trailing quotes/whitespace
    m = re.search(r'ANN?SWER:\s*(.+?)(?:\s|$)', text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip('"\' \t')
    # Fallback
    for line in reversed(text.split('\n')):
        line = line.strip()
        if line and not line.startswith('#') and len(line) > 1:
            return line.strip('"\' \t')
    return text.strip()[:200]


def normalize(s):
    s = str(s).lower().strip()
    for x in [',', '.', '!', '?', "'", '"']:
        s = s.replace(x, '')
    return s.strip()


def is_correct(pred, answer, aliases=None):
    p = normalize(pred)
    a = normalize(answer)
    if p == a:
        return True
    if aliases:
        for alias in aliases:
            if p == normalize(alias):
                return True
    return False


def run_fm_task(task, run_id):
    """Run a single force-multi task."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__fm-v12-{run_id}'
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
    prompt_file = run_dir / '.prompt.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    output_file = run_dir / 'opencode_raw_output.jsonl'
    log_file = run_dir / 'opencode.log'

    cmd = [
        'script', '-q', '-c',
        f'{OPENCODE} run --model {MODEL} --message "$(cat {prompt_file})" --no-input --no-auto-continue --output-format jsonl --output {output_file}',
        '/bin/bash'
    ]

    try:
        result = subprocess.run(
            ' '.join(cmd),
            shell=True, capture_output=True, timeout=600,
            cwd=str(run_dir)
        )
        log_file.write_text(result.stderr.decode('utf-8', errors='replace'), encoding='utf-8')
    except subprocess.TimeoutExpired:
        return {'task_id': task_id, 'correct': False, 'predicted': 'TIMEOUT', 'answer': answer, 'error': 'timeout', 'spawned': False, 'subagent_returned': False}
    except Exception as e:
        return {'task_id': task_id, 'correct': False, 'predicted': f'ERROR: {e}', 'answer': answer, 'error': str(e), 'spawned': False, 'subagent_returned': False}

    # Parse output
    output_text = ''
    spawned = False
    subagent_returned = False

    if output_file.exists():
        with open(output_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    content = entry.get('content', entry.get('text', ''))
                    output_text += str(content)
                    # Check for spawn/return signals
                    if 'subagent' in str(content).lower() or 'task(' in str(content).lower():
                        spawned = True
                    if 'return' in str(content).lower() and ('result' in str(content).lower() or 'found' in str(content).lower()):
                        subagent_returned = True
                except:
                    pass

    predicted = extract_answer(output_text)
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

    # Open log file for tee
    log_file = open(STDOUT_LOG, 'w')

    run_id = int(time.time())
    correct = 0
    total = 0

    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            print(f"[{i+1}/{len(tasks)}] {task_id} ... SKIP (already done)")
            continue

        t0 = time.time()
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)
        result = run_fm_task(task, run_id)
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
        total += 1

        # Quick progress
        done = len(existing) + total
        acc = 100*correct/total if total > 0 else 0
        print(f"    >> {done}/{len(tasks)} done, current acc: {correct}/{total} ({acc:.0f}%)", flush=True)

    log_file.close()
    print(f"\n=== FM v12: {correct}/{total} ({100*correct/total:.0f}%) ===")


if __name__ == '__main__':
    main()
