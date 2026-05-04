#!/usr/bin/env python3
"""
Expand task dataset from 55 to ~100 tasks.
Samples from MuSiQue dev (seed=44, different from seeds 42/43).
Keeps same JSON format as task_data_v2.
"""
import json, random
from pathlib import Path

MUSIQUE_DEV = "/home/jinxu/.cache/huggingface/hub/datasets--dgslibisey--MuSiQue/snapshots/c8f4f8c9465fb69d31a8eae894c3fd509c4ca321/musique_ans_v1.0_dev.jsonl"
OLD_DIR = Path("/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2")
NEW_DIR = Path("/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v3")

def convert_paragraph(p):
    return {
        "idx": p["idx"],
        "title": p.get("title", ""),
        "text": p.get("paragraph_text", p.get("text", "")),
    }

def main():
    random.seed(44)

    # Step 1: Copy all 55 existing tasks
    NEW_DIR.mkdir(parents=True, exist_ok=True)
    existing_ids = set()
    for tf in sorted(OLD_DIR.glob("task_*.json")):
        with open(tf) as f:
            t = json.load(f)
        existing_ids.add(t["id"])
        # Copy to new dir
        with open(NEW_DIR / tf.name, "w") as f:
            json.dump(t, f, indent=2, ensure_ascii=False)

    print(f"Copied {len(existing_ids)} existing tasks")
    
    # Get MuSiQue IDs already used (strip musique_ prefix)
    used_musique = {tid.replace("musique_", "") for tid in existing_ids if tid.startswith("musique_")}

    # Step 2: Count current hop distribution
    hop_counts = {"2hop": 0, "3hop": 0, "4hop": 0}
    for tid in existing_ids:
        for h in ["4hop", "3hop", "2hop"]:
            if h in tid:
                hop_counts[h] += 1
                break
    print(f"Current distribution: {hop_counts}")

    # Step 3: Load MuSiQue dev
    print("Loading MuSiQue dev...")
    with open(MUSIQUE_DEV) as f:
        all_dev = [json.loads(line) for line in f]

    by_hop = {}
    for d in all_dev:
        h = d["id"].split("__")[0]
        by_hop.setdefault(h, []).append(d)

    # Target: +45 tasks (to reach 100), proportional distribution
    # Current: 2hop~20, 3hop~15, 4hop~14, hotpot~6
    # Target: 2hop~40, 3hop~30, 4hop~25, hotpot~5 → 100
    TARGETS = [
        ("2hop", 20),    # +20 → 40 total
        ("3hop1", 6),    # +6  → ~21 3hop
        ("3hop2", 6),    # +6  → ~27 3hop
        ("3hop3", 3),    # +3  → ~30 3hop
        ("4hop1", 4),    # +4  → ~19 4hop
        ("4hop2", 3),    # +3  → ~22 4hop
        ("4hop3", 3),    # +3  → ~25 4hop
    ]

    chosen = []
    for htype, n in TARGETS:
        pool = [
            d for d in by_hop.get(htype, [])
            if d["id"] not in used_musique
            and d.get("answerable", True)
            and 5 <= len(d["paragraphs"]) <= 30
        ]
        n_actual = min(n, len(pool))
        sel = random.sample(pool, n_actual)
        chosen.extend(sel)
        print(f"  {htype}: sampled {n_actual}/{n} from {len(pool)} candidates")

    print(f"\nNew tasks: {len(chosen)}")

    # Write new tasks
    for d in chosen:
        task_id = d["id"]
        new_id = f"musique_{task_id}" if not task_id.startswith("musique_") else task_id
        if new_id in existing_ids:
            print(f"  SKIP duplicate: {new_id}")
            continue
        
        task = {
            "id": new_id,
            "question": d["question"],
            "answer": d["answer"],
            "answer_aliases": d.get("answer_aliases", []),
            "paragraphs": [convert_paragraph(p) for p in d["paragraphs"]],
            "source": "musique",
            "type": task_id.split("__")[0],
            "level": "dev",
        }
        out_path = NEW_DIR / f"task_{new_id}.json"
        with open(out_path, "w") as f:
            json.dump(task, f, indent=2, ensure_ascii=False)

    all_tasks = list(NEW_DIR.glob("task_*.json"))
    
    # Final distribution
    final_hop = {"2hop": 0, "3hop": 0, "4hop": 0}
    for tf in all_tasks:
        tid = tf.stem.replace("task_", "")
        for h in ["4hop", "3hop", "2hop"]:
            if h in tid:
                final_hop[h] += 1
                break
    
    print(f"\nTotal tasks: {len(all_tasks)}")
    print(f"Final distribution: {final_hop}")
    print(f"Done! New tasks in {NEW_DIR}/")

if __name__ == "__main__":
    main()
