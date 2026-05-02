"""
OpenCode Spawn Pilot v13 — Deliberation Prompt Experiment.

v12 task template:
  task(description="<topic>", prompt="Read the provided documents and find <info>", subagent_type="general")

v13 change: Keep task template EXACTLY the same, but add a system-level
  "Plan before you delegate" instruction to encourage structured thinking.

v12 had: "You MUST spawn at least one subagent"
v13 adds: "First think about what to search, then delegate"

Hypothesis: v12 spawns impulsively; forcing a planning step first will produce
better subagent prompts and fewer redundant/wrong spawns.
"""
import subprocess, json, time, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v13')
RESULTS_FILE = OUTPUT_DIR / 'results_fm_v13.jsonl'
STDOUT_LOG = OUTPUT_DIR / 'v13_fm_stdout.log'

# v13: IDENTICAL task template to v12, only adds deliberation instruction
SYSTEM_FORCE_MULTI = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- First, read the question and think about what information is needed and where to find it
- Then spawn at least one subagent using task(...) to search documents before answering
- task(description="<topic>", prompt="Read the provided documents and find <info>", subagent_type="general")

After the subagent completes, synthesize the findings and give your answer.

ANSWER: <your answer>'''

MODEL = 'local/qwen35-9b'
STOPWORDS = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to', 'for', 'on', 'by', 'with', 'at', 'from', 'and', 'or', 'but', 'as', 'it', 'be'}

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

def normalize(s):
    return re.sub(r'[^a-z0-9 ]', '', s.lower())

def check_correct(predicted, answer, aliases):
    if not predicted:
        return False
    p, a = normalize(predicted), normalize(answer)
    if p == a:
        return True
    for alias in aliases:
        if p == normalize(alias):
            return True
    a_words = a.split()
    for i in range(len(a_words)):
        if a_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(a_words[i:])
            if len(suffix) >= 4 and suffix in p:
                return True
            break
    p_words = p.split()
    for i in range(len(p_words)):
        if p_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(p_words[i:])
            if len(suffix) >= 4 and suffix in a:
                return True
            break
    words_a = [w for w in a.split() if len(w) >= 2 and w.lower() not in STOPWORDS]
    if words_a:
        def word_in_text(w, t):
            t = ' ' + t + ' '
            return (' ' + w + ' ') in t or (' ' + w + ',') in t or (' ' + w + '.') in t
        matched = sum(1 for w in words_a if word_in_text(w, p))
        if matched == len(words_a):
            return True
    return False

def extract_answer(text):
    text = text.replace('**', '').replace('*', '').replace('__', '')
    lines = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('Script ')]
    for line in reversed(lines):
        line = line.strip()
        if re.search(r'ANN?SWER', line, re.IGNORECASE):
            m = re.search(r'ANN?WER:\s*(.+?)(?:\s*$)', line, re.IGNORECASE)
            if m:
                ans = m.group(1).strip('"\': \t')
                if ans == '<your answer>' or not ans:
                    continue
                if len(ans) > 0:
                    return ans

def run_fm_task(task, run_id):
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    aliases = task.get('answer_aliases', [])
    docs = build_docs(task)

    run_dir = OUTPUT_DIR / f'{task_id}__fm-v13-{run_id}'
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
    except subprocess.TimeoutExpired:
        proc.kill()
        return {'task_id': task_id, 'correct': False, 'predicted': '', 'answer': answer, 'spawned': False, 'subagent_returned': False, 'output_len': 0, 'error': 'timeout'}

    spawned = False
    subagent_returned = False
    output_text = ""

    if output_file.exists():
        with open(output_file) as f:
            content = f.read()

        for line in content.strip().split('\n'):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                part = event.get('part', {})
                if part.get('type') == 'tool' and part.get('tool') == 'task':
                    spawned = True
            except:
                pass

        all_text = []
        for line in content.strip().split('\n'):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                part = event.get('part', {})
                if part.get('type') == 'text':
                    t = part.get('text', '')
                    all_text.append(t)
                    if 'subagent' in t.lower():
                        subagent_returned = True
            except:
                pass

        output_text = '\n'.join(all_text)

    predicted = extract_answer(output_text) if output_text else ""
    correct = check_correct(predicted, answer, aliases)

    return {
        'task_id': task_id,
        'correct': correct,
        'predicted': predicted,
        'answer': answer,
        'spawned': spawned,
        'subagent_returned': subagent_returned,
        'output_len': len(output_text)
    }


OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
tasks = load_tasks()
print(f"Loaded {len(tasks)} tasks")

existing = set()
if RESULTS_FILE.exists():
    existing = {json.loads(l)['task_id'] for l in open(RESULTS_FILE)}
    print(f"Found {len(existing)} existing results, will skip those tasks.")

with open(STDOUT_LOG, 'w') as log:
    for i, task in enumerate(tasks):
        task_id = task['id']
        if task_id in existing:
            print(f"[{i+1}/{len(tasks)}] {task_id} ... SKIP")
            continue

        t0 = time.time()
        print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end='', flush=True)

        run_id = int(time.time())
        result = run_fm_task(task, run_id)

        elapsed = time.time() - t0
        log.write(f"\n{'='*60}\n")
        log.write(f"TASK: {task_id}\n")
        log.write(f"ELAPSED: {elapsed:.1f}s\n")
        log.write(f"RESULT: {json.dumps(result)}\n")

        with open(RESULTS_FILE, 'a') as rf:
            rf.write(json.dumps(result) + '\n')

        status = "✓" if result['correct'] else "✗"
        spawn_s = "✓" if result['spawned'] else "✗"
        ret_s = "✓" if result['subagent_returned'] else "-"
        err = " [TIMEOUT]" if result.get('error') == 'timeout' else ""
        print(f"{status} spawn={spawn_s} ret={ret_s} ({elapsed:.0f}s){err}")

        time.sleep(1)

print(f"\nDone. Results in {RESULTS_FILE}")
