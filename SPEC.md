# OpenCode Spawn Pilot — Research Specification

**Version: final-cleanup**  
**Summary**: Qwen3.5-9B can be induced to spawn subagents (92% rate after fix), but spawn helps only with information extraction — not chain reasoning. The 9B model's reasoning bottleneck is the limiting factor.

---

## 1. Key Findings

### Finding 1: `opencode run` Does NOT Read Config File
**Critical.** `opencode run --format json` ignores `~/.config/opencode/opencode.json` system prompt entirely. Only the `--message` command-line argument delivers the prompt.

**Proof**: Write `BANANA_TEST` marker to config → model output contains no marker. Pass marker via `--message` → marker appears in output.

**Impact**: All experiments before the fix (v0–v6.1 batch runs) had 0% spawn rate not because the model chose not to spawn, but because the prompt was never delivered.

**Fix**: Pass full prompt as `json.dumps(system_prompt + user_prompt)` via `--message` argument.

### Finding 2: After Fix, Spawn Rate = 92%
With correct prompt delivery, 11/12 force-multi tasks spawned subagents.

### Finding 3: Spawn Solves Search, Not Reasoning
The most important research finding:

| Task Type | Spawn Helps? | Example |
|-----------|-------------|---------|
| Direct extraction (numbers, names, facts) | ✅ Yes | BBC Staff → 35,402 ✓ |
| Chain spatial/temporal reasoning | ❌ No | "A 25mi north of B" → B 25mi south of A ✗ |

Even when subagent finds correct info, Build Agent often fails to synthesize chains (3-hop, 4-hop tasks).

### Finding 4: Model Never Spawns When Truly Optional
v5.0 (22 tasks, 3 tiers, 0 spawns): Model never voluntarily spawned even for 100-paragraph documents where parallel search could help.

---

## 2. Experiment Results

### v6.1 Paired Comparison (10 tasks, both modes on same tasks)

| Mode | Accuracy | Spawn Rate |
|------|----------|------------|
| **Single** (no spawn allowed) | 4/10 (40%) | 0% |
| **Force-Multi** (forced spawn) | 7/10 (70%) | 6/10 |

**Spawn helped 3 tasks**: BBC Staff (35,402), Rachel Nevada, Maria Shvetsova  
**Spawn hurt 0 tasks**: none  
**Net gain**: +30% from forced spawn

### v10 Force-Multi (12 tasks, final prompt)

| Mode | Accuracy | Spawn Rate |
|------|----------|------------|
| Force-Multi v10 | 7/12 (58%) | 11/12 (92%) |

**v10 prompt**: `subagent_type="general"` + "After the subagent completes, synthesize the findings and give your answer." No SPAWN_REASON requirement.

### Failure Analysis (v10, 5 wrong)
| Task | Problem |
|------|---------|
| train termini (3 vs two) | Model output `3` not `two` — format/commonsense issue |
| large_2hop Knock | Model named movie instead of actor (reasoning error) |
| large_3hop1 1853 | Subagent found info but Build Agent chose wrong country |
| large_3hop1 Casa Loma | Birthplace not found (search gap) |
| large_4hop1 Rio Linda | TIMEOUT — task too complex |

---

## 3. Environment

```
vLLM:      0.19.1, Qwen3.5-9B, GPU 1, port 8010
OpenCode:  1.3.6, local openai-compatible
Base URL:  http://localhost:8010/v1
Model:     local/qwen35-9b
```

Startup: `bash scripts/start_vllm.sh`

---

## 4. Task Set (12 tasks, task_data_v2/)

| Task ID | Question (truncated) | Difficulty |
|---------|---------------------|------------|
| hotpot_5a722a68 | Anna Leonidovna Kovalchuk law festival prize | 2-hop |
| hotpot_5a85a37d | train C&M subdivision termini | 2-hop |
| hotpot_5a87bd4e | minister at First Church Springfield | 2-hop |
| hotpot_5a8bf083 | mockingbird mascot UT Chattanooga | 2-hop |
| hotpot_5adfa226 | BBC HyperNormalisation staff count | 2-hop |
| hotpot_5adfff075 | 25 miles south of Groom Lake | 2-hop |
| large_2hop__591435 | Oscar for Knock on Any Door cast | 2-hop |
| large_2hop__736167 | Paul McCartney song for Cynthia's kid | 2-hop |
| large_3hop1__17192 | Lower Burma annexation date | 3-hop |
| large_3hop1__862117 | castle in birthplace of Speckless Sky performer | 3-hop |
| large_4hop1__28352 | shares border with Rio Linda | 4-hop |
| large_4hop1__726675 | child of Italian navigator | 4-hop |

---

## 5. Two Prompt Variants

### Single (no spawn allowed)
```
You are a research agent solving multi-hop questions using ONLY document search.

CRITICAL RESTRICTIONS:
1. You MUST NOT use the 'task' tool under any circumstances
2. You MUST NOT spawn any subagents
3. Use ONLY read, grep, and bash tools to search documents
4. Do NOT answer from your own knowledge — search the documents

Output format:
ANSWER: <your answer>
```

### Force-Multi (must spawn subagent)
```
You are a research agent solving multi-hop questions. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- You MUST spawn at least one subagent using task(description="<topic>", prompt="Read <FILEPATH> and find <info>", subagent_type="general")
- If you decide NOT to spawn a subagent, you MUST output the exact reason: SPAWN_REASON: <explain>

After the subagent completes, synthesize the findings and give your answer.

ANSWER: <your answer>
```

---

## 6. Run Commands

```bash
# Start vLLM
bash scripts/start_vllm.sh

# Run comparison (single vs force-multi paired)
python3 scripts/run_v6_parallel.py

# Run force-multi only (v10 prompt, 12 tasks)
python3 scripts/run_fm_v10.py
```

---

## 7. Metrics

| Metric | Definition |
|--------|------------|
| `subagent_spawned` | model called task tool |
| `subagent_returned` | subagent result returned to Build |
| `task_tool_calls` | total task tool invocations |
| `accuracy` | final answer correct / total |

---

## 8. Files

| File/Dir | Description |
|----------|-------------|
| `scripts/run_v6_parallel.py` | Main harness: task loading, run_single_task(), prompt delivery, JSON parsing |
| `scripts/run_fm_v10.py` | Force-multi batch launcher (uses run_v6_parallel) |
| `scripts/start_vllm.sh` | vLLM startup script |
| `outputs/.../task_data_v2/` | 12 task JSON files |
| `outputs/.../comparison_v6_parallel/results_v6_parallel.jsonl` | v6.1 paired results (single + force-multi, 10 tasks) |
| `outputs/.../comparison_v10/results_fm_v10.jsonl` | v10 force-multi results (12 tasks) |
| `outputs/.../comparison/` | Legacy results (v3/v4, ignore) |

---

## 9. Open Questions

1. **Does a larger model (14B/32B) eliminate the reasoning bottleneck?** Not yet tested.
2. **Does CoT prompting help Build Agent synthesize chains?** Worth exploring.
3. **Sample size**: 10–12 tasks is too small for statistical significance. Need 30–50 tasks.
4. **4-hop timeout**: large_4hop1 tasks timeout even with spawn — task complexity is the limit.
