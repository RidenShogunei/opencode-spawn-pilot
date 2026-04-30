#!/usr/bin/env python3
"""
Test 4 prompt variants for spawn behavior.
Each variant tested on 5 tasks.
"""

import argparse
import json
import subprocess
import sys
import time
import re
from pathlib import Path
from datetime import datetime

OPENCODE = "/home/jinxu/.opencode/bin/opencode"
MODEL = "local/qwen35-9b"
WORKDIR = Path("/home/jinxu/opencode-spawn-pilot")
DATA_DIR = WORKDIR / "outputs/opencode_spawn_pilot/task_data"
OUTPUT_DIR = WORKDIR / "outputs/opencode_spawn_pilot/prompt_variants"
RUNS_FILE = OUTPUT_DIR / "results.jsonl"

# Variant 1: 强制要求 — 必须用 task，禁止 grep
PROMPT_V1 = """You are a research agent solving multi-hop questions.

CRITICAL: You MUST use the 'task' tool to spawn explore subagents for searching.
- Use 'task' with agent='explore' to search paragraphs in parallel
- Do NOT use bash/grep yourself
- Do NOT read files directly

For each search, spawn a task like this:
  task(description="<what to search>", prompt="<search instructions>", agent="explore")

Example: task(description="find Lostock Dam info", prompt="Search paragraphs for Lostock Dam and related rivers", agent="explore")

After subagent results, synthesize the answer.

Output format:
ANSWER: <your answer>"""

# Variant 2: 只给 task 工具，不提 read/grep
PROMPT_V2 = """You are a research agent solving multi-hop questions.

You have access to the 'task' tool to spawn explore subagents for parallel searching.

To search paragraphs:
  task(description="<what to search>", prompt="<search instructions>", agent="explore")

The explore subagent will search paragraphs and return results.

Example: task(description="find Lostock Dam", prompt="Search paragraphs for Lostock Dam and its river connections", agent="explore")

Output format:
ANSWER: <your answer>"""

# Variant 3: 更明确的格式示例
PROMPT_V3 = """You are a research agent solving multi-hop questions.

Use the 'task' tool to spawn explore subagents. Format:

  task(
    description="search for <topic>",
    prompt="Search all paragraphs for <specific query>. Return the relevant paragraph numbers and content.",
    agent="explore"
  )

Example workflow:
1. task(description="find Lostock Dam", prompt="Search paragraphs for Lostock Dam location and river", agent="explore")
2. task(description="find Paterson River", prompt="Search paragraphs for Paterson River and its connections", agent="explore")  
3. Combine results to answer

Output format:
ANSWER: <your answer>"""

# Variant 4: 解释并行价值
PROMPT_V4 = """You are a research agent solving multi-hop questions.

Multi-hop questions require finding connections across multiple paragraphs. To do this efficiently, you should use parallel exploration via the 'task' tool.

When a question has multiple parts or requires tracing relationships:
1. Spawn explore subagents in parallel for each aspect
2. Combine the results to synthesize the answer

Task format:
  task(description="<aspect to explore>", prompt="<what to search for in paragraphs>", agent="explore")

Example: "Lostock Dam → ?" involves tracing: Dam location → River → Larger catchment
  - task(description="Lostock Dam", prompt="Find Lostock Dam and what river it's on", agent="explore")
  - task(description="Paterson River", prompt="Find Paterson River and what it flows into", agent="explore")

Output format:
ANSWER: <your answer>"""

VARIANTS = {
    "v1_forced": PROMPT_V1,
    "v2_task_only": PROMPT_V2,
    "v3_detailed_format": PROMPT_V3,
    "v4_parallel_value": PROMPT_V4,
}

def get_tasks(n=5):
    """Get n tasks from task_data, stratified by hop count."""
    tasks = list(DATA_DIR.glob("*.json"))
    # Stratify: 2-hop, 3-hop, 4-hop
    by_hop = {"2hop": [], "3hop": [], "4hop": []}
    for t in tasks:
        if "2hop" in t.stem:
            by_hop["2hop"].append(t)
        elif "3hop" in t.stem:
            by_hop["3hop"].append(t)
        else:
            by_hop["4hop"].append(t)
    
    selected = []
    # 2-hop × 2, 3-hop × 1, 4-hop × 2
    for hop, count in [("2hop", 2), ("3hop", 1), ("4hop", 2)]:
        selected.extend(by_hop[hop][:count])
    return selected[:n]

def run_opencode(prompt, task_file, run_dir):
    """Run opencode with given prompt on task, return results."""
    with open(task_file) as f:
        task_data = json.load(f)
    
    question = task_data.get("question", "")
    paragraphs = task_data.get("paragraphs", [])
    
    # Write paragraphs to documents.txt
    docs_path = run_dir / "documents.txt"
    with open(docs_path, "w") as f:
        for i, p in enumerate(paragraphs):
            f.write(f"[Paragraph {i}] {p}\n")
    
    # Build user message with system instruction embedded
    user_msg = f"""{prompt}

Question: {question}

Documents are in {docs_path}. Answer using task subagents to search."""

    env = {
        "HOME": "/home/jinxu",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LD_LIBRARY_PATH": "/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu",
    }
    
    cmd = [
        OPENCODE, "run",
        "--agent", "build",
        "--model", MODEL,
        "--", user_msg
    ]
    
    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(WORKDIR),
    )
    elapsed = time.time() - start
    
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed": elapsed,
    }

def check_spawn(output):
    """Check if subagent was spawned based on output."""
    # Look for explore agent markers
    spawn_indicators = [
        "Explore Agent",
        "explore agent",
        "TOOL: task",  # model tried to call task
        "✓",  # checkmark often appears after subagent completes
    ]
    
    # Also check if output has structured task format
    has_spawn = any(ind in output for ind in spawn_indicators)
    
    # More reliable: check if "•" bullet appears (subagent spawned)
    # In our earlier tests, spawn shows as "• Explore Agent" or "✓ ... Explore Agent"
    if "Explore Agent" in output or "explore" in output.lower():
        return True
    return False

def extract_answer(output):
    """Extract answer from output."""
    match = re.search(r'ANSWER:\s*(.+)', output, re.MULTILINE)
    return match.group(1).strip() if match else None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5, help="Tasks per variant")
    parser.add_argument("--variant", choices=list(VARIANTS.keys()) + ["all"], default="all")
    args = parser.parse_args()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tasks = get_tasks(args.limit)
    
    results = {}
    
    variants_to_test = VARIANTS.keys() if args.variant == "all" else [args.variant]
    
    for variant_name in variants_to_test:
        prompt = VARIANTS[variant_name]
        print(f"\n{'='*60}")
        print(f"Testing variant: {variant_name}")
        print(f"{'='*60}")
        
        variant_results = []
        
        for i, task_file in enumerate(tasks):
            run_id = f"{variant_name}_{task_file.stem}_{int(time.time())}"
            run_dir = OUTPUT_DIR / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            
            print(f"\n[{i+1}/{len(tasks)}] {task_file.stem}...", end=" ", flush=True)
            
            try:
                res = run_opencode(prompt, task_file, run_dir)
                output = res["stdout"] + res["stderr"]
                
                spawned = check_spawn(output)
                answer = extract_answer(output)
                
                # Load ground truth
                with open(task_file) as f:
                    task_data = json.load(f)
                ground_truth = task_data.get("answer", "").strip()
                
                correct = answer and ground_truth and answer.lower() in ground_truth.lower()
                
                result_entry = {
                    "variant": variant_name,
                    "task": task_file.stem,
                    "run_id": run_id,
                    "spawned": spawned,
                    "answer": answer,
                    "ground_truth": ground_truth,
                    "correct": correct,
                    "elapsed": res["elapsed"],
                    "output_preview": output[:500],
                }
                
                variant_results.append(result_entry)
                
                status = "SPAWN" if spawned else "no-spawn"
                acc = "✓" if correct else "✗"
                print(f"{status} | {acc}")
                
                # Save raw output
                with open(run_dir / "opencode_output.txt", "w") as f:
                    f.write(output)
                
            except subprocess.TimeoutExpired:
                print("TIMEOUT")
                variant_results.append({
                    "variant": variant_name,
                    "task": task_file.stem,
                    "run_id": run_id,
                    "spawned": False,
                    "error": "timeout",
                })
            except Exception as e:
                print(f"ERROR: {e}")
                variant_results.append({
                    "variant": variant_name,
                    "task": task_file.stem,
                    "run_id": run_id,
                    "spawned": False,
                    "error": str(e),
                })
        
        results[variant_name] = variant_results
        
        # Print summary
        spawned_count = sum(1 for r in variant_results if r.get("spawned"))
        correct_count = sum(1 for r in variant_results if r.get("correct"))
        print(f"\n{variant_name} summary: {spawned_count}/{len(variant_results)} spawned, {correct_count}/{len(variant_results)} correct")
    
    # Save results
    with open(RUNS_FILE, "w") as f:
        json.dump(results, f, indent=2)
    
    # Print final summary
    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print(f"{'='*60}")
    print(f"{'Variant':<20} {'Spawned':<10} {'Correct':<10}")
    print("-" * 40)
    for name, res_list in results.items():
        spawned = sum(1 for r in res_list if r.get("spawned"))
        correct = sum(1 for r in res_list if r.get("correct"))
        print(f"{name:<20} {spawned}/{len(res_list):<10} {correct}/{len(res_list):<10}")

if __name__ == "__main__":
    main()