#!/usr/bin/env python3
"""
OpenCode Spawn Pilot — Force-Spawn Experiment v4
Compares single-agent vs FORCE-multi-agent (must spawn).
Single: no mention of spawn tool
Force-multi: MUST use task tool, CANNOT use read/grep for search
"""
import subprocess, json, time, sys, re, os, tempfile
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
CONFIG_FILE = Path('/home/jinxu/.config/opencode/opencode.json')
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v4_forced')
RESULTS_FILE = OUTPUT_DIR / 'results_v4_forced.jsonl'

# Single: no mention of spawn at all (clean baseline)
SYSTEM_SINGLE = '''You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

RULES:
- Use the read, grep, and bash tools to search through documents
- Base your answer ONLY on information found in the documents
- Do not guess or use your own knowledge

Output your final answer on its own line:
ANSWER: <your answer>'''

# Force-spawn: MUST use task tool (v6.1 latest with SPAWN_REASON)
SYSTEM_FORCE_MULTI = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- You MUST spawn at least one subagent using task(...) to search documents before answering
- task(description="<topic>", prompt="Read <FILEPATH> and find <info>", subagent_type="explore")

If you decide NOT to spawn a subagent, you MUST output the exact reason:
SPAWN_REASON: <explain why you chose not to spawn despite being required to>

Output format:
SPAWN_REASON: <reason, or "spawned as required" if you spawned>
ANSWER: <your answer>'''

def build_config(sp):
    return {
        '$schema': 'https://opencode.ai/config.json',
        'model': MODEL,
        'provider': {'local': {'npm': '@ai-sdk/openai-compatible', 'name': 'Local vLLM', 'options': {'baseURL': 'http://127.0.0.1:8010/v1'}, 'models': {'qwen35-9b': {'name': 'qwen35-9b', 'maxOutputTokens': 8192}}}},
        'agent': {'build': {'prompt': sp}}
    }

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

def run_task(mode, task, run_id):
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    run_dir = OUTPUT_DIR / mode / f'{task_id}__{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)
    docs_path = run_dir / 'documents.txt'
    docs_path.write_text(build_docs(task))

    user_prompt = f'Use the documents provided in {docs_path}.\n\nQuestion: {question}\n\nSearch the documents to find the answer. Output your final answer on its own line:\nANSWER: <your answer>'

    with tempfile.NamedTemporaryFile(mode='w+', suffix='.jsonl', delete=False) as tmp_out:
        tmp_output = tmp_out.name

    try:
        cmd = [OPENCODE, 'run', '--agent', 'build', '--model', MODEL, '--format', 'json', '--title', f'musique-{task_id}', '--', user_prompt]

        start = time.time()
        with open(tmp_output, 'w') as f_out:
            proc = subprocess.run(cmd, stdout=f_out, stderr=subprocess.DEVNULL, timeout=300, cwd=str(run_dir))
        elapsed = time.time() - start

        result_text = Path(tmp_output).read_text()
        (run_dir / 'opencode_raw_output.jsonl').write_text(result_text)

        predicted = None
        task_tool_calls = 0
        subagent_spawned = False
        subagent_returned = False

        for line in result_text.split('\n'):
            if not line.strip(): continue
            try:
                obj = json.loads(line)
                if obj.get('type') == 'text':
                    text = obj.get('part', {}).get('text', '')
                    m = re.search(r'^ANSWER:\s*(.+)$', text, re.MULTILINE)
                    if m: predicted = m.group(1).strip()
                elif obj.get('type') == 'tool_use':
                    tool = obj.get('part', {}).get('tool', '')
                    if tool == 'task':
                        task_tool_calls += 1
                        subagent_spawned = True
                        state = obj.get('part', {}).get('state', {})
                        output = state.get('output', '')
                        if '<task_result>' in output: subagent_returned = True
            except: pass

        if predicted:
            pl = predicted.lower()
            al = answer.lower()
            correct = al in pl or pl in al or any(a.lower() in pl for a in task.get('answer_aliases', []))
        else:
            correct = False

        return {
            'run_id': f'{task_id}__{run_id}', 'task_id': task_id, 'mode': mode,
            'question': question, 'predicted': predicted, 'correct': correct, 'answer': answer,
            'task_tool_calls': task_tool_calls, 'subagent_spawned': subagent_spawned,
            'subagent_returned': subagent_returned, 'elapsed': round(elapsed, 1),
            'exit_code': proc.returncode, 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
    finally:
        os.unlink(tmp_output)

tasks = load_tasks()
print(f'Tasks: {len(tasks)}', flush=True)

modes = ['single', 'force_multi']
all_results = []

for mode in modes:
    print(f'=== Mode: {mode.upper()} ===', flush=True)

    config = build_config(SYSTEM_SINGLE if mode == 'single' else SYSTEM_FORCE_MULTI)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    run_id = f'{mode}-{int(time.time())}'
    mode_results = []

    for i, task in enumerate(tasks):
        task_id = task['id']
        print(f'[{i+1}/{len(tasks)}] {task_id} ... ', end='', flush=True)

        try:
            result = run_task(mode, task, run_id)
            status = '✓' if result['correct'] else '✗'
            spawn = f' [spawn×{result["task_tool_calls"]}]' if result['subagent_spawned'] else ' [NO_SPAWN]'
            ret = ' [returned]' if result['subagent_returned'] else ''
            print(f'{status}{spawn}{ret} ({result["elapsed"]}s)', flush=True)

            if not result['correct'] and result['predicted']:
                print(f'   Predicted: {result["predicted"][:80]}', flush=True)
                print(f'   Answer:    {result["answer"][:80]}', flush=True)

            mode_results.append(result)
        except subprocess.TimeoutExpired:
            print('TIMEOUT', flush=True)
            mode_results.append({'task_id': task_id, 'mode': mode, 'correct': False, 'elapsed': 300, 'error': 'timeout'})
        except Exception as e:
            print(f'ERROR: {e}', flush=True)
            mode_results.append({'task_id': task_id, 'mode': mode, 'correct': False, 'elapsed': 0, 'error': str(e)})

    n = len(mode_results)
    correct = sum(1 for r in mode_results if r['correct'])
    spawns = sum(r.get('task_tool_calls', 0) for r in mode_results)
    print(f'Mode {mode}: {correct}/{n} correct ({100*correct/n:.0f}%), {spawns} spawns\n', flush=True)

    all_results.extend(mode_results)

print('=== OVERALL ===', flush=True)
for mode in modes:
    mr = [r for r in all_results if r['mode'] == mode]
    n = len(mr)
    c = sum(1 for r in mr if r['correct'])
    sp = sum(r.get('task_tool_calls', 0) for r in mr)
    ret = sum(1 for r in mr if r.get('subagent_returned', False))
    print(f'Mode {mode}: accuracy={c}/{n} ({100*c/n:.0f}%), spawns={sp}, returned={ret}', flush=True)

with open(RESULTS_FILE, 'w') as f:
    for r in all_results:
        f.write(json.dumps(r) + '\n')
print(f'Results saved to {RESULTS_FILE}', flush=True)