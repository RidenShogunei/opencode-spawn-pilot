#!/usr/bin/env python3
"""
Expand task_data_v2/ from 30 to 60 tasks.
Uses seed=43 to sample 30 NEW tasks from MuSiQue dev (different from seed=42 used for first 30).
"""
import json, random
from pathlib import Path

MUSIQUE_DEV = "/home/jinxu/.cache/huggingface/hub/datasets--dgslibisey--MuSiQue/snapshots/c8f4f8c9465fb69d31a8eae894c3fd509c4ca321/musique_ans_v1.0_dev.jsonl"
OUTPUT_DIR = Path("outputs/opencode_spawn_pilot/task_data_v2")

# Existing IDs (seed=42 originals + first expansion)
existing_ids = {
    # Original 12
    "hotpot_5a722a6855429971e9dc9320", "hotpot_5a85a37d5542997175ce1fe5",
    "hotpot_5a87bd4e5542996432c57279", "hotpot_5a8bf0835542995d1e6f146b",
    "hotpot_5adfa22655429942ec259ac4", "hotpot_5adfff0755429925eb1afbce",
    "large_2hop__591435_51329", "large_2hop__736167_74735",
    "large_3hop1__17192_78396_157843", "large_3hop1__862117_792411_51423",
    "large_4hop1__28352_53706_795904_580996", "large_4hop1__726675_508773_85832_745702",
    # seed=42 expansion (18 tasks)
    "musique_2hop__252521_80650", "musique_2hop__476927_31270",
    "musique_2hop__557496_57594", "musique_2hop__623501_297043",
    "musique_2hop__628752_538661", "musique_2hop__642686_7292",
    "musique_2hop__657913_88628", "musique_2hop__825727_584042",
    "musique_3hop1__135794_87694_64412", "musique_3hop1__498954_160713_77246",
    "musique_3hop1__791757_15840_36014",
    "musique_3hop2__304722_667199_63959", "musique_3hop2__87184_90327_76291",
    "musique_4hop1__199881_378185_282674_759393", "musique_4hop1__399219_765799_282674_759393",
    "musique_4hop2__5206_14670_8987_8529",
    "musique_4hop3__193820_466199_128875_72134", "musique_4hop3__193820_466199_695123_72134",
}

# Target: 10 more 2hop, 5 more 3hop, 5 more 4hop, 5 more hotpot (5+10+5+5+5=30)
TARGETS = [("2hop", 10), ("3hop1", 5), ("3hop2", 3), ("4hop1", 3), ("4hop3", 3), ("4hop2", 1), ("hotpot", 5)]


def convert_paragraph(p):
    return {
        "idx": p["idx"],
        "title": p.get("title", ""),
        "text": p.get("paragraph_text", p.get("text", "")),
    }


def main():
    random.seed(43)  # Different from seed=42

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
            if d["id"] not in existing_ids
            and d.get("answerable", True)
            and 5 <= len(d["paragraphs"]) <= 30
        ]
        n_actual = min(n, len(pool))
        sel = random.sample(pool, n_actual)
        chosen.extend(sel)
        print(f"  {htype}: sampled {n_actual}/{n} from {len(pool)} candidates")

    print(f"\nTotal new tasks: {len(chosen)}")

    for d in chosen:
        task_id = d["id"]
        new_id = f"musique_{task_id}" if not task_id.startswith("musique_") else task_id
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

    all_tasks = list(OUTPUT_DIR.glob("task_*.json"))
    print(f"Total tasks in task_data_v2/: {len(all_tasks)}")


if __name__ == "__main__":
    main()
