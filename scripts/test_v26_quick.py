#!/usr/bin/env python3
"""Quick 5-task test for v26: documents NOT embedded in prompt."""
import subprocess, json, time, sys, re
from pathlib import Path

# Import v26 functions
sys.path.insert(0, str(Path(__file__).parent))
from run_fm_v26 import (
    OPENCODE, MODEL, DATA_DIR, OUTPUT_DIR, RESULTS_FILE, SYSTEM_FORCE_MULTI,
    load_tasks, build_docs, extract_answer_from_jsonl_events,
    parse_raw_output, is_correct
)

TEST_TASKS = [
    "musique_2hop__96096_78606",
    "musique_2hop__20256_71302",
    "large_3hop1__862117_792411_51423",
    "musique_3hop2__89854_38738_76291",
    "musique_4hop1__638988_17130_70784_61381",
]

def run_test():
    run_id = int(time.time())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_tasks = load_tasks()
    task_map = {t['id']: t for t in all_tasks}

    for i, tid in enumerate(TEST_TASKS):
        task = task_map.get(tid)
        if not task:
            print(f"Task {tid} not found!")
            continue

        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(TEST_TASKS)}] {tid} ... ", flush=True)
        t0 = time.time()

        # Inline run_fm_task logic (adapted from v26)
        question = task['question']
        answer = task['answer']
        aliases = task.get('answer_aliases', [])
        docs = build_docs(task)

        run_dir = OUTPUT_DIR / f'{tid}__fm-v26-test-{run_id}'
        run_dir.mkdir(parents=True, exist_ok=True)

        docs_file = run_dir / 'documents.txt'
        docs_file.write_text(docs, encoding='utf-8')

        user_prompt = f"""Answer this multi-hop question. The documents are stored in `documents.txt` — you MUST spawn a subagent to read them.

Question: {question}

After subagent(s) complete, synthesize the findings and give your answer.

ANSWER: """

        full_prompt = f'{SYSTEM_FORCE_MULTI}\n\n---\n\n{user_prompt}'
        prompt_file = OUTPUT_DIR / f'.prompt_{tid}_{run_id}.txt'
        prompt_file.write_text(full_prompt, encoding='utf-8')

        opencode_cmd = ' '.join([
            OPENCODE, 'run',
            '--model', MODEL,
            '--format', 'json',
            '--title', tid,
            '--message', f'@{prompt_file.absolute()}'
        ])
        cmd = ['script', '-q', '-c', opencode_cmd, '/dev/null']

        output_file = run_dir / 'opencode_raw_output.jsonl'
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(run_dir)
            )
            output_bytes, _ = proc.communicate(timeout=600)
            output_file.write_bytes(output_bytes)
            output_text = output_bytes.decode('utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            output_text = ''
        except Exception as e:
            output_text = ''
        finally:
            if prompt_file.exists():
                prompt_file.unlink()

        events = parse_raw_output(output_text)

        spawned = False
        subagent_returned = False
        used_read_directly = False
        task_event_index = None

        for j, event in enumerate(events):
            etype = event.get('type', '')
            part = event.get('part', {})
            if etype == 'tool_use':
                tool_name = part.get('tool', '')
                if tool_name == 'task':
                    spawned = True
                    task_event_index = j
                elif tool_name == 'read':
                    inp = part.get('state', {}).get('input', {})
                    if 'documents' in str(inp.get('filePath', '')):
                        used_read_directly = True
            elif etype == 'text':
                pass

        if task_event_index is not None and task_event_index < len(events) - 1:
            subagent_returned = True

        all_text_parts = [e['part'].get('text', '') for e in events if e.get('type')=='text']
        full_text = '\n'.join(all_text_parts)
        predicted, _ = extract_answer_from_jsonl_events(events)
        correct = is_correct(predicted, answer, aliases)

        elapsed = time.time() - t0
        status = '✓' if correct else '✗'
        read_flag = ' 📖direct_read' if used_read_directly else ''
        spawn_flag = ' 🚀spawn' if spawned else ''
        sub_flag = ' ✅returned' if subagent_returned else ''
        print(f"{status} ({elapsed:.0f}s){spawn_flag}{sub_flag}{read_flag}")
        print(f"  Predicted: {predicted[:100]}")
        print(f"  Answer:    {answer}")

        # Show model's text output (first 500 chars)
        text_summary = ' '.join(all_text_parts)[:300].replace('\n', ' | ')
        print(f"  Text:      {text_summary}")

    print(f"\n{'='*60}")
    print("Test complete. Check logs in:", OUTPUT_DIR)

if __name__ == '__main__':
    run_test()
