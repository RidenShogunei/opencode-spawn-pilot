#!/usr/bin/env python3
"""
OpenCode Spawn Pilot v6.1 — Parallel Runner
每个任务独立进程，结果写入 JSONL，实时 tail 可见。
"""
import subprocess, json, time, sys, re, os, tempfile, shlex
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
CONFIG_FILE = Path('/home/jinxu/.config/opencode/opencode.json')
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/comparison_v6_parallel')
RESULTS_FILE = OUTPUT_DIR / 'results_v6_parallel.jsonl'

SYSTEM_SINGLE = '''You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

RULES:
- Use the read, grep, and bash tools to search through documents
- Base your answer ONLY on information found in the documents
- Do not guess or use your own knowledge

Output your final answer on its own line:
ANSWER: <your answer>'''

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

def run_single_task(mode, task, run_id):
    """在一个独立进程中跑单个任务，结果写入 run_dir/result.json"""
    task_id = task['id']
    question = task['question']
    answer = task['answer']
    run_dir = OUTPUT_DIR / mode / f'{task_id}__{run_id}'
    run_dir.mkdir(parents=True, exist_ok=True)
    docs_path = run_dir / 'documents.txt'
    docs_path.write_text(build_docs(task))

    user_prompt = f'Use the documents provided in {docs_path}.\n\nQuestion: {question}\n\nSearch the documents to find the answer. Output your final answer on its own line:\nANSWER: <your answer>'

    config = build_config(SYSTEM_SINGLE if mode == 'single' else SYSTEM_FORCE_MULTI)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    out_path = run_dir / 'opencode_raw_output.jsonl'
    log_path = run_dir / 'opencode.log'

    # 将 prompt 写入临时文件，opencode 命令通过 -- 从 stdin 读取
    prompt_path = run_dir / '.prompt.txt'
    prompt_path.write_text(user_prompt)

    # script -c 包装，opencode -- 从 stdin 读取 prompt
    cmd = ['script', '-q', '-c',
           f'{OPENCODE} run --agent build --model {MODEL} --format json --title musique-{task_id} < {shlex.quote(str(prompt_path))}',
           '/dev/null']

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
            if not line.strip(): continue
            try:
                obj = json.loads(line)
                if obj.get('type') == 'text':
                    text = obj.get('part', {}).get('text', '')
                    # 容忍 ANSWER/ANNWER/ANSWERA 等常见打字错误
                    m = re.search(r'AN[NS]WER[A-Z]*:\s*(.+)', text, re.MULTILINE | re.IGNORECASE)
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
            'exit_code': exitcode, 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
    except subprocess.TimeoutExpired:
        os.kill(proc.pid, 9)
        return {
            'run_id': f'{task_id}__{run_id}', 'task_id': task_id, 'mode': mode,
            'question': question, 'predicted': None, 'correct': False, 'answer': answer,
            'task_tool_calls': 0, 'subagent_spawned': False, 'subagent_returned': False,
            'elapsed': 300, 'exit_code': -1, 'error': 'timeout',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
    except Exception as e:
        return {
            'run_id': f'{task_id}__{run_id}', 'task_id': task_id, 'mode': mode,
            'question': question, 'predicted': None, 'correct': False, 'answer': answer,
            'task_tool_calls': 0, 'subagent_spawned': False, 'subagent_returned': False,
            'elapsed': 0, 'exit_code': -1, 'error': str(e),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }

def main():
    tasks = load_tasks()
    run_id = f'v6-{int(time.time())}'
    all_results = []

    for mode in ['single', 'force_multi']:
        print(f'=== {mode.upper()} ===', flush=True)
        mode_results = []
        for i, task in enumerate(tasks):
            tid = task['id']
            print(f'  [{i+1}/{len(tasks)}] {tid} ... ', end='', flush=True, file=sys.stderr)

            result = run_single_task(mode, task, run_id)
            status = '✓' if result['correct'] else '✗'
            spawn = f'[spawn×{result["task_tool_calls"]}]' if result['subagent_spawned'] else '[NO_SPAWN]'
            ret = '[returned]' if result['subagent_returned'] else ''
            err = f'[{result.get("error","")}]' if result.get("error") else ''
            print(f'{status} {spawn}{ret} {err} ({result["elapsed"]}s)', flush=True, file=sys.stderr)

            if not result['correct'] and result.get('predicted'):
                print(f'    Predicted: {result["predicted"][:80]}', flush=True, file=sys.stderr)
                print(f'    Answer:    {result["answer"][:80]}', flush=True, file=sys.stderr)

            mode_results.append(result)
            # 随时写入
            with open(RESULTS_FILE, 'a') as f:
                f.write(json.dumps(result) + '\n')

        n = len(mode_results)
        c = sum(1 for r in mode_results if r['correct'])
        sp = sum(r.get('task_tool_calls', 0) for r in mode_results)
        ret = sum(1 for r in mode_results if r.get('subagent_returned', False))
        print(f'  → {c}/{n} correct ({100*c/n:.0f}%), spawns={sp}, returned={ret}\n', flush=True, file=sys.stderr)
        all_results.extend(mode_results)

    # Summary
    print('=== OVERALL ===', flush=True)
    for mode in ['single', 'force_multi']:
        mr = [r for r in all_results if r['mode'] == mode]
        n = len(mr)
        c = sum(1 for r in mr if r['correct'])
        sp = sum(r.get('task_tool_calls', 0) for r in mr)
        print(f'{mode}: {c}/{n} ({100*c/n:.0f}%), spawns={sp}', flush=True)

if __name__ == '__main__':
    main()
