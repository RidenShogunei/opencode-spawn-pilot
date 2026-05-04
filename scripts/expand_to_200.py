#!/usr/bin/env python3
"""
Expand task dataset from 97 to ~200 tasks.
Samples from MuSiQue dev (seed=45, different from 42/43/44).
Copies all existing task_data_v3, adds new tasks.
"""
import json, random
from pathlib import Path

MUSIQUE_DEV = "/home/jinxu/.cache/huggingface/hub/datasets--dgslibisey--MuSiQue/snapshots/c8f4f8c9465fb69d31a8eae894c3fd509c4ca321/musique_ans_v1.0_dev.jsonl"
OLD_DIR = Path("/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v3")
NEW_DIR = Path("/home/jinxu/opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v4")

def convert_paragraph(p):
    return {
        "idx": p["idx"],
        "title": p.get("title", ""),
        "text": p.get("paragraph_text", p.get("text", "")),
    }

def main():
    random.seed(45)
    NEW_DIR.mkdir(parents=True, exist_ok=True)

    # Copy all 97 existing tasks
    existing_ids = set()
    for tf in sorted(OLD_DIR.glob("task_*.json")):
        with open(tf) as f:
            t = json.load(f)
        existing_ids.add(t["id"])
        with open(NEW_DIR / tf.name, "w") as f:
            json.dump(t, f, indent=2, ensure_ascii=False)
    print(f"Copied {len(existing_ids)} existing tasks")

    # MuSiQue IDs already used
    used_musique = {tid.replace("musique_", "") for tid in existing_ids if tid.startswith("musique_")}

    # Current distribution
    hop_counts = {"2hop": 0, "3hop": 0, "4hop": 0}
    for tid in existing_ids:
        for h in ["4hop", "3hop", "2hop"]:
            if h in tid:
                hop_counts[h] += 1
                break
    print(f"Current: 2hop={hop_counts['2hop']}, 3hop={hop_counts['3hop']}, 4hop={hop_counts['4hop']}")

    # Target ~200: 82 2hop, 56 3hop, 56 4hop + 6 hotpot
    TARGETS = [
        ("2hop",  42),    # 40+42=82
        ("3hop1", 12),    # +12
        ("3hop2", 12),    # +12 → 27+24=51
        ("3hop3",  5),    # +5 (try) → ~56
        ("4hop1", 12),    # +12
        ("4hop2",  8),    # +8
        ("4hop3", 12),    # +12 → 24+32=56
    ]

    # Load MuSiQue dev
    print("Loading MuSiQue dev...")
    with open(MUSIQUE_DEV) as f:
        all_dev = [json.loads(line) for line in f]

    by_hop = {}
    for d in all_dev:
        h = d["id"].split("__")[0]
        by_hop.setdefault(h, []).append(d)

    chosen = []
    for htype, n in TARGETS:
        pool = [
            d for d in by_hop.get(htype, [])
            if d["id"] not in used_musique
            and d.get("answerable", True)
            and 5 <= len(d["paragraphs"]) <= 30
        ]
        n_actual = min(n, len(pool))
        if n_actual > 0:
            sel = random.sample(pool, n_actual)
            chosen.extend(sel)
        print(f"  {htype}: +{n_actual}/{n} from {len(pool)} candidates")

    print(f"\nWriting {len(chosen)} new tasks...")
    written = 0
    for d in chosen:
        task_id = d["id"]
        new_id = f"musique_{task_id}" if not task_id.startswith("musique_") else task_id
        if new_id in existing_ids:
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
        with open(NEW_DIR / f"task_{new_id}.json", "w") as f:
            json.dump(task, f, indent=2, ensure_ascii=False)
        written += 1

    all_tasks = list(NEW_DIR.glob("task_*.json"))

    final_hop = {"2hop": 0, "3hop": 0, "4hop": 0}
    for tf in all_tasks:
        tid = tf.stem.replace("task_", "")
        for h in ["4hop", "3hop", "2hop"]:
            if h in tid:
                final_hop[h] += 1
                break

    print(f"\nTotal: {len(all_tasks)} tasks")
    print(f"2hop={final_hop['2hop']}, 3hop={final_hop['3hop']}, 4hop={final_hop['4hop']}")
    print(f"Done → {NEW_DIR}/")

if __name__ == "__main__":
    main()
