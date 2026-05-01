#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v11 — Force-Multi only, 30 tasks.
Runs all 30 tasks in task_data_v2/ sequentially with force_multi mode.
Uses v10 prompt (proven to work at 58% on 12 tasks).
"""
import subprocess, json, time, sys, re, os
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v11')
RESULTS_FILE = OUTPUT_DIR / 'results_fm_v11.jsonl'

# v10 prompt — proven to work at 58% on 12 tasks
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


def run_single_task(task, run_id):
    """Run a single task, write result to run_dir/result.json"""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    run_dir = OUTPUT_DIR / f'{task_id}__{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)
    docs_path = run_dir / 'documents.txt'
    docs_path.write_text(build_docs(task))

    user_prompt = (
        f'Use the documents provided in {docs_path}.\n\n'
        f'Question: {question}\n\n'
        f'Search the documents to find the answer.\n'
        f'Output your final answer on its own line:\n'
        f'ANSWER: <your answer>'
    )
    full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'

    out_path = run_dir / 'opencode_raw_output.jsonl'
    log_path = run_dir / 'opencode.log'

    # Build command — full prompt as JSON-encoded --message argument
    cmd = [
        'script', '-q', '-c',
        f'{OPENCODE} run --agent build --model {MODEL} --format json --title musique-{task_id} {json.dumps(full_prompt)} 2>&1',
        '/dev/null'
    ]

    start = time.time()
    try:
        with open(out_path, 'w', buffering=1) as fout, open(log_path, 'w', buffering=1) as flog:
            proc = subprocess.Popen(cmd, stdout=fout, stderr=flog, cwd=str(run_dir))
            exitcode = proc.wait(timeout=300)
        elapsed = time.time() - start
        result_text = out_path.read_text()

        predicted = None
        task_tool_calls = 0
        subagent_spawned = False
        subagent_returned = False

        for line in result_text.split('\n'):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if obj.get('type') == 'text':
                    text = obj.get('part', {}).get('text', '')
                    m = re.search(r'AN[NS]WER[A-Z]*:\s*(.+)', text, re.MULTILINE | re.IGNORECASE)
                    if m:
                        predicted = m.group(1).strip().strip('"').strip()
                elif obj.get('type') == 'tool_use':
                    tool = obj.get('part', {}).get('tool', '')
                    if tool == 'task':
                        task_tool_calls += 1
                        subagent_spawned = True
                        state = obj.get('part', {}).get('state', {})
                        output = state.get('output', '')
                        if '<task_result>' in output:
                            subagent_returned = True
            except Exception:
                pass

        if predicted:
            pl = predicted.lower()
            al = answer.lower()
            correct = (
                al in pl or pl in al
                or any(a.lower() in pl for a in task.get('answer_aliases', []))
            )
        else:
            correct = False

        return {
            'run_id': f'{task_id}__{run_id}',
            'task_id': task_id,
            'mode': 'force_multi',
            'question': question,
            'predicted': predicted,
            'correct': correct,
            'answer': answer,
            'task_tool_calls': task_tool_calls,
            'subagent_spawned': subagent_spawned,
            'subagent_returned': subagent_returned,
            'elapsed': round(elapsed, 1),
            'exit_code': exitcode,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
    except subprocess.TimeoutExpired:
        os.kill(proc.pid, 9)
        return {
            'run_id': f'{task_id}__{run_id}',
            'task_id': task_id,
            'mode': 'force_multi',
            'question': question,
            'predicted': None,
            'correct': False,
            'answer': answer,
            'task_tool_calls': 0,
            'subagent_spawned': False,
            'subagent_returned': False,
            'elapsed': 300,
            'exit_code': -1,
            'error': 'timeout',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
    except Exception as e:
        return {
            'run_id': f'{task_id}__{run_id}',
            'task_id': task_id,
            'mode': 'force_multi',
            'question': question,
            'predicted': None,
            'correct': False,
            'answer': answer,
            'task_tool_calls': 0,
            'subagent_spawned': False,
            'subagent_returned': False,
            'elapsed': 0,
            'exit_code': -1,
            'error': str(e),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }


def main():
    tasks = load_tasks()
    run_id = f'v11-{int(time.time())}'
    all_results = []

    print(f'Total tasks: {len(tasks)}')
    print(f'Output dir: {OUTPUT_DIR}')
    print(f'Results file: {RESULTS_FILE}')
    print(f'Run ID: {run_id}')
    print('=' * 60)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, task in enumerate(tasks):
        tid = task['id']
        print(f'[{i+1}/{len(tasks)}] {tid} ... ', end='', flush=True, file=sys.stderr)

        result = run_single_task(task, run_id)
        status = '✓' if result['correct'] else '✗'
        spawn = f'[spawn×{result["task_tool_calls"]}]' if result['subagent_spawned'] else '[NO_SPAWN]'
        ret = '[ret]' if result['subagent_returned'] else ''
        err = f'[{result.get("error","")}]' if result.get("error") else ''
        elapsed = result['elapsed']
        print(f'{status} {spawn}{ret} {err} ({elapsed}s)', flush=True, file=sys.stderr)

        if not result['correct'] and result.get('predicted'):
            print(f'    Predicted: {result["predicted"][:80]}', flush=True, file=sys.stderr)
            print(f'    Answer:    {result["answer"][:80]}', flush=True, file=sys.stderr)

        all_results.append(result)
        with open(RESULTS_FILE, 'a') as f:
            f.write(json.dumps(result) + '\n')

    # Summary by hop type
    print('\n=== SUMMARY ===')
    n = len(all_results)
    c = sum(1 for r in all_results if r['correct'])
    sp = sum(r.get('task_tool_calls', 0) for r in all_results)
    ret = sum(1 for r in all_results if r.get('subagent_returned', False))
    no_spawn = sum(1 for r in all_results if not r.get('subagent_spawned', False))
    print(f'Overall:  {c}/{n} correct ({100*c/n:.0f}%) | spawns={sp} | returned={ret} | no_spawn={no_spawn}')

    # Breakdown by hop type
    for prefix in ['hotpot', '2hop', '3hop', '4hop']:
        subset = [r for r in all_results if prefix in r['task_id']]
        if subset:
            sc = sum(1 for r in subset if r['correct'])
            sn = len(subset)
            print(f'  {prefix}: {sc}/{sn} ({100*sc/sn:.0f}%)')

    # Failed tasks
    failed = [r for r in all_results if not r['correct']]
    if failed:
        print(f'\nFailed tasks ({len(failed)}):')
        for r in failed:
            print(f'  {r["task_id"]}: predicted={r.get("predicted","N/A")[:50]} answer={r["answer"][:50]}')


if __name__ == '__main__':
    main()
