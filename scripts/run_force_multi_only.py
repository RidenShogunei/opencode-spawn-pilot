#!/usr/bin/env python3
"""只跑 force_multi 模式，验证 spawn 修复"""
import sys, time, json
sys.path.insert(0, 'scripts')
import run_v6_parallel as r

RESULTS_FILE = 'outputs/opencode_spawn_pilot/comparison_v6_parallel/results_v6_parallel.jsonl'
open(RESULTS_FILE, 'w').close()

tasks = r.load_tasks()
run_id = f'v7-{int(time.time())}'
results = []

print(f'Running {len(tasks)} tasks in force_multi mode...', flush=True)

for i, task in enumerate(tasks):
    tid = task['id']
    print(f'  [{i+1}/{len(tasks)}] {tid} ... ', end='', flush=True)

    result = r.run_single_task('force_multi', task, run_id)
    status = '✓' if result['correct'] else '✗'
    spawn = f'[spawn×{result["task_tool_calls"]}]' if result['subagent_spawned'] else '[NO_SPAWN]'
    ret = '[returned]' if result['subagent_returned'] else ''
    err = f'[{result.get("error","")}]' if result.get('error') else ''
    print(f'{status} {spawn}{ret} {err} ({result["elapsed"]}s)', flush=True)

    if not result['correct'] and result.get('predicted'):
        print(f'    Predicted: {result["predicted"][:80]}', flush=True)
        print(f'    Answer:    {result["answer"][:80]}', flush=True)

    with open(RESULTS_FILE, 'a') as f:
        f.write(json.dumps(result) + '\n')
    results.append(result)

n = len(results)
c = sum(1 for r_ in results if r_['correct'])
sp = sum(r_['task_tool_calls'] for r_ in results)
ret = sum(1 for r_ in results if r_.get('subagent_returned'))
spawned = sum(1 for r_ in results if r_['subagent_spawned'])
print(f'\n=== FORCE_MULTI SUMMARY ===')
print(f'Correct: {c}/{n} ({100*c/n:.0f}%)')
print(f'Spawned: {spawned}/{n} ({100*spawned/n:.0f}%)')
print(f'Total spawn calls: {sp}')
print(f'Subagent returned: {ret}')
