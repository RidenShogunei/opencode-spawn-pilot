#!/usr/bin/env python3
"""
Expand task_data_v2/ from 12 tasks to 30 tasks.
Samples from MuSiQue dev (2417 tasks), preserving the same JSON format.
"""
import json, random, shutil
from pathlib import Path

MUSIQUE_DEV = "/home/jinxu/.cache/huggingface/hub/datasets--dgslibisey--MuSiQue/snapshots/c8f4f8c9465fb69d31a8eae894c3fd509c4ca321/musique_ans_v1.0_dev.jsonl"

OUTPUT_DIR = Path("outputs/opencode_spawn_pilot/task_data_v2")

# Existing IDs to skip
existing_ids = {
    "hotpot_5a722a6855429971e9dc9320", "hotpot_5a85a37d5542997175ce1fe5",
    "hotpot_5a87bd4e5542996432c57279", "hotpot_5a8bf0835542995d1e6f146b",
    "hotpot_5adfa22655429942ec259ac4", "hotpot_5adfff0755429925eb1afbce",
    "large_2hop__591435_51329", "large_2hop__736167_74735",
    "large_3hop1__17192_78396_157843", "large_3hop1__862117_792411_51423",
    "large_4hop1__28352_53706_795904_580996", "large_4hop1__726675_508773_85832_745702",
}

# Target: 8 more 2hop, 3 more 3hop1, 2 more 3hop2, 2 more 4hop1, 2 more 4hop3, 1 more 4hop2
TARGETS = [("2hop", 8), ("3hop1", 3), ("3hop2", 2), ("4hop1", 2), ("4hop3", 2), ("4hop2", 1)]

def convert_paragraph(p):
    """Convert MuSiQue paragraph format to our format."""
    return {
        "idx": p["idx"],
        "title": p.get("title", ""),
        "text": p.get("paragraph_text", p.get("text", "")),
    }

def main():
    random.seed(42)

    # Load all dev
    print("Loading MuSiQue dev...")
    with open(MUSIQUE_DEV) as f:
        all_dev = [json.loads(line) for line in f]

    # Group by hop type
    by_hop = {}
    for d in all_dev:
        h = d["id"].split("__")[0]
        by_hop.setdefault(h, []).append(d)

    # Sample per hop type
    chosen = []
    for htype, n in TARGETS:
        pool = [
            d for d in by_hop.get(htype, [])
            if d["id"] not in existing_ids
            and d.get("answerable", True)
            and 5 <= len(d["paragraphs"]) <= 30
        ]
        if len(pool) < n:
            print(f"  WARNING: {htype} only has {len(pool)} candidates, need {n}")
        n实际 = min(n, len(pool))
        sel = random.sample(pool, n实际)
        chosen.extend(sel)
        print(f"  {htype}: sampled {n实际}/{n} from {len(pool)} candidates")

    print(f"\nTotal new tasks: {len(chosen)}")

    # Write task files
    new_tasks = []
    for d in chosen:
        task_id = d["id"]
        # Prefix with "musique_" to avoid collision
        new_id = f"musique_{task_id}"
        task = {
            "id": new_id,
            "question": d["question"],
            "answer": d["answer"],
            "answer_aliases": d.get("answer_aliases", []),
            "paragraphs": [convert_paragraph(p) for p in d["paragraphs"]],
        }
        out_path = OUTPUT_DIR / f"task_{new_id}.json"
        with open(out_path, "w") as f:
            json.dump(task, f, indent=2, ensure_ascii=False)
        new_tasks.append((new_id, d["question"][:60], d["answer"][:30], d["id"]))

    print(f"\nWritten {len(new_tasks)} new tasks to {OUTPUT_DIR}/")
    print(f"\nNew task list:")
    print(f"{'New ID':<35} {'Question':<63} {'Answer':<32} {'Orig ID'}")
    print("-" * 140)
    for tid, q, ans, oid in new_tasks:
        print(f"{tid:<35} {q:<63} {ans:<32} {oid}")

    all_tasks = list(OUTPUT_DIR.glob("task_*.json"))
    print(f"\nTotal tasks in task_data_v2/: {len(all_tasks)}")

if __name__ == "__main__":
    main()
