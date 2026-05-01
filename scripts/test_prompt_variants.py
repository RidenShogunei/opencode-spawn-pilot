#!/usr/bin/env python3
"""
Test 4 prompt variants to see if we can improve spawn rate.
Core hypothesis: Re-framing task tool from "document search" to "research delegation"
will make the model more willing to spawn.
"""
import subprocess, json, time, sys, re, os, tempfile
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'
CONFIG_FILE = Path('/home/jinxu/.config/opencode/opencode.json')
DATA_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
OUTPUT_DIR = Path('/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/prompt_test')
RESULTS_FILE = OUTPUT_DIR / 'results.jsonl'

# ============================================================
# PROMPT VARIANTS
# ============================================================

# Variant 1: "Research Director" — natural delegation framing
PROMPT_V1_RESEARCH_DIRECTOR = """You are a research director leading a team of research assistants to answer complex questions.

Your role: Break down the question into sub-questions, delegate each to a research assistant (subagent), then synthesize their findings into a final answer.

Each research assistant can independently search documents and return findings. Use the 'task' tool to dispatch them:

  task(description="<what to search for>", prompt="Read the file <FILEPATH> and find <specific information>", subagent_type="explore")

WORKFLOW:
1. Identify 2-3 specific sub-questions needed to answer the main question
2. For each sub-question, spawn a research assistant using the task tool above
3. Wait for all assistants to return results
4. Combine their findings and output your final answer

After collecting results, output your final answer on its own line:
ANSWER: <your answer>"""

# Variant 2: "Few-shot" — concrete examples showing the format
PROMPT_V2_FEWSHOT = """You are a research agent. To search documents, use the 'task' tool to spawn search subagents.

EXAMPLES of how to use the task tool for document search:

Example 1:
User: "What river is the Lostock Dam located on?"
You call: task(description="find Lostock Dam river", prompt="Read the file /path/to/documents.txt and search for information about which river the Lostock Dam is on. Return the river name.", subagent_type="explore")
Subagent returns: "Lostock Dam is located on the Paterson River."
You answer: ANSWER: Paterson River

Example 2:
User: "Who published the book that won the 2020 Pulitzer Prize for Fiction?"
You call: task(description="find 2020 Pulitzer Fiction winner", prompt="Read /path/to/documents.txt and find the book that won the 2020 Pulitzer Prize for Fiction. Return the book title and publisher.", subagent_type="explore")
Subagent returns: "The Nickel Boys by Colson Whitehead won the 2020 Pulitzer. Published by Doubleday."
You answer: ANSWER: Doubleday

For each search task, use: task(description="<topic>", prompt="Read the file <FILEPATH> and find <info>", subagent_type="explore")

Output your final answer on its own line:
ANSWER: <your answer>"""

# Variant 3: "Benefit Explained" — explain WHY spawn, not just command
PROMPT_V3_BENEFIT = """You are a research agent answering questions by searching through documents.

IMPORTANT: For multi-hop questions that require finding information from different parts of the documents, using the 'task' tool to spawn search subagents is MORE EFFECTIVE than searching directly. Here's why:

- Subagents search in PARALLEL, saving time on complex questions
- Each subagent focuses on ONE specific piece of information, reducing confusion
- Subagents return clean, focused results that are easier to combine

Use the task tool format:
  task(description="<what to find>", prompt="Read the file <FILEPATH> and find <specific info>", subagent_type="explore")

When to spawn subagents:
- If the question has multiple parts → spawn one subagent per part
- If you need to cross-reference different sections → spawn subagents for each section
- For direct single-fact questions → you may search directly

Output your final answer on its own line:
ANSWER: <your answer>"""

# Variant 4: "Combined" — research director + examples + benefit
PROMPT_V4_COMBINED = """You are a research director leading a team of research assistants. Each assistant (subagent) can search documents independently. Delegating search to assistants is your PRIMARY method, not a fallback.

WHY DELEGATE:
- Assistants search in parallel, much faster than sequential searching
- Each assistant focuses on one specific sub-question, producing cleaner results
- You stay free to focus on combining findings and reasoning

HOW TO DELEGATE (use the 'task' tool):
  task(description="<short topic>", prompt="Read the file <FILEPATH>. Search for and return information about: <specific question>", subagent_type="explore")

EXAMPLE — For question "Who published the book written by the person who founded Company X?":
  Step 1: task(description="find founder of Company X", prompt="Read /path/to/docs.txt. Find who founded Company X. Return the name.", subagent_type="explore")
  Step 2: task(description="find book by that person", prompt="Read /path/to/docs.txt. Find what book was written by [name from step 1]. Return book title and publisher.", subagent_type="explore")
  Step 3: Combine results and answer

WORKFLOW:
1. Break question into 2-3 sub-questions
2. Delegate each sub-question to an assistant using task(...)
3. Wait for all results
4. Combine findings into final answer

Output your final answer on its own line:
ANSWER: <your answer>"""

# Baseline: current v6.1 force_multi prompt
PROMPT_BASELINE = """You are a research agent solving multi-hop questions. You MUST use the 'task' tool to spawn subagents for document search.

CRITICAL:
1. You MUST use task(description="<search description>", prompt="Read the file <FILEPATH> and find <information to find>", subagent_type="explore") to search documents
2. Do NOT use read or grep to search documents — only use task tool to spawn subagents
3. Wait for subagent results before answering
4. You must spawn at least one subagent before giving your final answer

Output your final answer on its own line:
ANSWER: <your answer>"""

PROMPTS = {
    "baseline": PROMPT_BASELINE,
    "v1_research_director": PROMPT_V1_RESEARCH_DIRECTOR,
    "v2_fewshot": PROMPT_V2_FEWSHOT,
    "v3_benefit": PROMPT_V3_BENEFIT,
    "v4_combined": PROMPT_V4_COMBINED,
}

# ============================================================
# TASKS (6 representative tasks)
# ============================================================
TASK_IDS = [
    "hotpot_5adfa226",  # BBC Staff count — search→answer, spawn helped before
    "hotpot_5adfff075",  # Groom Lake — spatial reasoning
    "hotpot_5a722a68",  # Chief Detective — spawn helped before
    "large_2hop__591435_51329",  # The African Queen — 2-hop
    "large_3hop1__17192_78396_157843",  # Burma 1853 — 3-hop
    "large_4hop1__726675_508773_85832_745702",  # Sebastian Cabot — 4-hop
]


def build_config(sp):
    return {
        '$schema': 'https://opencode.ai/config.json',
        'model': MODEL,
        'provider': {
            'local': {
                'npm': '@ai-sdk/openai-compatible',
                'name': 'Local vLLM',
                'options': {'baseURL': 'http://127.0.0.1:8010/v1'},
                'models': {'qwen35-9b': {'name': 'qwen35-9b', 'maxOutputTokens': 8192}}
            }
        },
        'agent': {'build': {'prompt': sp}}
    }


def load_task(task_prefix):
    """Load task by ID prefix matching."""
    for tf in sorted(DATA_DIR.glob('*.json')):
        if task_prefix in tf.stem:
            return json.loads(tf.read_text())
    raise FileNotFoundError(f"No task matching {task_prefix}")


def build_docs(task):
    lines = []
    for p in task['paragraphs']:
        lines.append(f'[Paragraph {p["idx"]}] {p["title"]}')
        lines.append(p['text'])
        lines.append('')
    return '\n'.join(lines)


def run_one(prompt_name, task, run_dir):
    """Run one prompt on one task. Returns result dict."""
    task_id = task['id']
    question = task['question']
    answer = task['answer']

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    docs_path = run_dir / 'documents.txt'
    docs_path.write_text(build_docs(task))

    user_prompt = (
        f"Documents are in: {docs_path}\n\n"
        f"Question: {question}\n\n"
        f"Search the documents to find the answer."
    )

    with tempfile.NamedTemporaryFile(mode='w+', suffix='.jsonl', delete=False) as tmp:
        tmp_output = tmp.name

    try:
        cmd = [
            OPENCODE, 'run', '--agent', 'build', '--model', MODEL,
            '--format', 'json', '--title', f'prompttest-{prompt_name}-{task_id[:20]}',
            '--', user_prompt
        ]

        start = time.time()
        with open(tmp_output, 'w') as f_out:
            proc = subprocess.run(
                cmd, stdout=f_out, stderr=subprocess.DEVNULL,
                timeout=180, cwd=str(run_dir)
            )
        elapsed = time.time() - start

        result_text = Path(tmp_output).read_text()
        (run_dir / 'opencode_raw_output.jsonl').write_text(result_text)

        # Parse results
        predicted = None
        task_tool_calls = 0
        subagent_spawned = False
        subagent_returned = False
        spawn_descriptions = []

        for line in result_text.split('\n'):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if obj.get('type') == 'text':
                    text = obj.get('part', {}).get('text', '')
                    m = re.search(r'^ANSWER:\s*(.+)$', text, re.MULTILINE)
                    if m:
                        predicted = m.group(1).strip()
                elif obj.get('type') == 'tool_use':
                    tool = obj.get('part', {}).get('tool', '')
                    if tool == 'task':
                        task_tool_calls += 1
                        subagent_spawned = True
                        state = obj.get('part', {}).get('state', {})
                        output = state.get('output', '')
                        if '<task_result>' in output:
                            subagent_returned = True
                        # Extract description
                        tool_args = obj.get('part', {}).get('tool_args', {})
                        desc = tool_args.get('INPUT', {}).get('description', '?')
                        spawn_descriptions.append(desc)
            except Exception:
                pass

        # Evaluation
        if predicted:
            pl = predicted.lower().strip().strip("'\"")
            al = answer.lower().strip().strip("'\"")
            correct = al in pl or pl in al
            if not correct:
                aliases = task.get('answer_aliases', [])
                correct = any(a.lower() in pl for a in aliases)
        else:
            correct = False

        return {
            'prompt': prompt_name,
            'task_id': task_id,
            'question': question[:80],
            'predicted': predicted,
            'correct': correct,
            'answer': answer,
            'task_tool_calls': task_tool_calls,
            'subagent_spawned': subagent_spawned,
            'subagent_returned': subagent_returned,
            'spawn_descriptions': spawn_descriptions,
            'elapsed': round(elapsed, 1),
            'exit_code': proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            'prompt': prompt_name, 'task_id': task_id, 'correct': False,
            'elapsed': 180, 'error': 'timeout', 'subagent_spawned': False,
            'task_tool_calls': 0, 'question': question[:80], 'answer': answer,
        }
    except Exception as e:
        return {
            'prompt': prompt_name, 'task_id': task_id, 'correct': False,
            'elapsed': 0, 'error': str(e), 'subagent_spawned': False,
            'task_tool_calls': 0, 'question': question[:80], 'answer': answer,
        }
    finally:
        if os.path.exists(tmp_output):
            os.unlink(tmp_output)


# ============================================================
# MAIN
# ============================================================
print("=" * 70)
print("Prompt Variant Test — Can we improve spawn rate?")
print(f"Model: {MODEL} | Tasks: {len(TASK_IDS)} | Variants: {len(PROMPTS)}")
print("=" * 70)

all_results = []

for prompt_name, prompt_text in PROMPTS.items():
    print(f"\n{'='*60}")
    print(f"PROMPT: {prompt_name}")
    print(f"{'='*60}")

    # Write config
    config = build_config(prompt_text)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    run_id = f'{prompt_name}-{int(time.time())}'
    prompt_results = []

    for i, task_prefix in enumerate(TASK_IDS):
        task = load_task(task_prefix)
        task_id = task['id']
        run_dir = OUTPUT_DIR / prompt_name / f'{task_id}__{run_id}'

        print(f'  [{i+1}/{len(TASK_IDS)}] {task_prefix[:35]:35s} ... ', end='', flush=True)
        result = run_one(prompt_name, task, run_dir)

        status = '✓' if result['correct'] else '✗'
        spawn = f' [spawn×{result["task_tool_calls"]}]' if result['subagent_spawned'] else ' [no spawn]'
        ret = ' [ret]' if result.get('subagent_returned') else ''
        err = f' [{result.get("error", "")}]' if 'error' in result else ''

        print(f'{status}{spawn}{ret}{err} ({result["elapsed"]}s)', flush=True)
        if not result['correct'] and result.get('predicted'):
            print(f'        Pred: {str(result["predicted"])[:60]}', flush=True)
            print(f'        Gold: {str(result["answer"])[:60]}', flush=True)

        if result.get('spawn_descriptions'):
            for sd in result['spawn_descriptions']:
                print(f'        → {sd[:80]}', flush=True)

        prompt_results.append(result)

    n = len(prompt_results)
    c = sum(1 for r in prompt_results if r['correct'])
    sp = sum(r.get('task_tool_calls', 0) for r in prompt_results)
    s_tasks = sum(1 for r in prompt_results if r.get('subagent_spawned'))
    print(f'\n  Summary: {c}/{n} correct ({100*c/n:.0f}%), {s_tasks}/{n} spawned ({sp} total spawns)')

    all_results.extend(prompt_results)

# ============================================================
# FINAL SUMMARY
# ============================================================
print(f"\n{'='*70}")
print("FINAL COMPARISON")
print(f"{'='*70}")
print(f"{'Prompt':<25s} {'Acc':>6s} {'Spawn%':>7s} {'TotalSp':>8s}")
print("-" * 50)

for prompt_name in PROMPTS:
    pr = [r for r in all_results if r['prompt'] == prompt_name]
    n = len(pr)
    c = sum(1 for r in pr if r['correct'])
    s = sum(1 for r in pr if r.get('subagent_spawned'))
    sp_total = sum(r.get('task_tool_calls', 0) for r in pr)
    print(f"{prompt_name:<25s} {c}/{n} ({100*c/n:2.0f}%) {s}/{n} ({100*s/n:2.0f}%) {sp_total:>6d}")

# Save results
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
with open(RESULTS_FILE, 'w') as f:
    for r in all_results:
        f.write(json.dumps(r) + '\n')

print(f"\nResults saved to {RESULTS_FILE}")
