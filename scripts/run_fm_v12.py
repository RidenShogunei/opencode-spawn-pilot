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

    output_file = run_dir / 'opencode_raw_output.jsonl'
    output_file_abs = output_file.absolute()
    log_file = run_dir / 'opencode.log'

    # Write prompt to a file OUTSIDE the run_dir (model can't read parent dir files)
    prompt_file = OUTPUT_DIR / f'.prompt_{task_id}_{run_id}.txt'
    prompt_file.write_text(full_prompt, encoding='utf-8')

    cmd = (
        f'script -q -c '
        f'"{OPENCODE} run --model {MODEL} --format json --title {task_id} --message @/{prompt_file.absolute()}" '
        f'{output_file_abs}'
    )

    try:
        with open(log_file, 'wb') as flog:
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=flog,
                cwd=str(run_dir)
            )
            _, _ = proc.communicate(timeout=600)
        output_text = output_file.read_text(errors='replace') if output_file.exists() else ''
    except subprocess.TimeoutExpired:
        proc.kill()
        output_text = ''
    except Exception as e:
        output_text = ''
    finally:
        if prompt_file.exists():
            prompt_file.unlink()

    # Parse output
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
                tool_name = entry.get('part', {}).get('tool', '')
                if tool_name == 'task':
                    spawned = True
            elif entry.get('type') == 'tool_result':
                content = str(entry.get('part', {}).get('result', ''))
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

        done = len(existing) + total
        acc = 100*correct/total if total > 0 else 0
        print(f"    >> {done}/{len(tasks)} done, current acc: {correct}/{total} ({acc:.0f}%)", flush=True)

    log_file.close()
    print(f"\n=== FM v12: {correct}/{total} ({100*correct/total:.0f}%) ===")


if __name__ == '__main__':
    main()
