# OpenCode Spawn Pilot — Research Specification

Version: v2.0
Summary: Prompt engineering experiment for spawn behavior on MuSiQue multi-hop QA.

---

## 0. Key Findings (v2.0)

### Architecture

| Agent | Role | Tools | Can Spawn |
|-------|------|-------|-----------|
| build | primary | read, grep, bash, task | ✓ (has task tool) |
| explore | subagent | bash, grep, read, web*, etc | N/A |
| general | subagent | bash, grep, read, web*, etc | N/A |

### spawn_events Mechanism

```
tool: task
input: {description, prompt, subagent_type: "explore" | "general"}
```
OpenCode internally spawns the specified subagent. Task result is returned as tool output.

**Important**: Model writes `agent=` in thinking, but OpenCode framework translates it to `subagent_type=` in actual tool call. Both `"explore"` and `"general"` work for spawning.

### Core Findings

| Finding | Evidence |
|---------|----------|
| Subagent results ARE returned to Build | Confirmed via `--format json` — `tool_result` with `<task_result>` in output |
| Model spawns regardless of prompt | v4 says "Do not use subagents" but model still spawns when it wants |
| Model does NOT spawn based on difficulty | v2_task_only (optional): 20% spawn rate; v4_baseline (no mention): 40% spawn rate |
| Spawn does NOT improve accuracy | v1_forced (80% spawn): 60% accuracy; v2 (20% spawn): 40% accuracy |
| Subagent uses its own knowledge | Subagent for 9/11 question answered "Washington D.C." from training data, not documents |

### 5-Task Comparative Results

| Task | v1_forced_explore | v2_task_only | v4_baseline | Notes |
|------|-------------------|--------------|-------------|-------|
| 2hop_001 | timeout | no spawn, ✅ | spawn, ❌ | v1 timeout, v4 wrong answer |
| 2hop_003 | spawn, ✅ | no spawn, ✅ | no spawn, ✅ | All correct |
| 3hop_004 | spawn, ✅ | no spawn, ✅ | spawn, ✅ | All correct |
| 4hop_007 | spawn, ✅ | timeout | no spawn, ❌ | v1 correct, v4 wrong |
| 4hop_009 | spawn, ❌ | spawn, ❌ | no spawn, ❌ | All wrong |

### Summary Metrics

| Variant | Prompt Style | Spawn Rate | Accuracy |
|---------|-------------|-----------|---------|
| v1_forced_explore | MUST use task, no other tools | 4/5 (80%) | 3/5 (60%) |
| v2_task_only_explore | CAN use task (optional) | 1/5 (20%) | 2/5 (40%) |
| v4_baseline_no_task | No mention of task/subagent | 2/5 (40%) | 2/5 (40%) |

### Key Insight: Model Ignores Prompt Constraints

- v4 prompt: "Do not use any subagents." — model spawned twice anyway
- v2 prompt: "You have access to..." — model rarely uses it (20%)
- v1 prompt: "MUST use task tool" — highest spawn rate (80%)

**Conclusion**: Model spawns based on its own judgment, not prompt instructions. Prompt engineering cannot reliably control spawn behavior.

### Subagent Quality Issues

Case: musique_4hop_009 (4-hop, very complex)
- Subagent 1 (9/11 question): answered from internal knowledge ("Washington D.C.") instead of searching documents
- Subagent 3 (San Francisco border): correctly said "documents don't contain this information"
- Build then used grep, found "Oak Lawn, Illinois shares borders with the city" (Chicago), gave wrong answer

**Root cause**: Even when subagent results are returned, subagent may use training knowledge instead of document search, and Build may make wrong inferences from incomplete subagent output.

---

## 1. Research Question

**Does explicitly telling the model it can spawn subagents (via task tool) change accuracy or spawn behavior?**

---

## 2. Environment

### 2.1 vLLM
```
engine: vLLM 0.19.1
model: Qwen3.5-9B
port: 8010
base_url: http://localhost:8010/v1
```

### 2.2 OpenCode
```
version: 1.3.6
provider: local (openai-compatible)
model: local/qwen35-9b
agent: build (primary)
```

---

## 3. Task Domain

MuSiQue benchmark subset (10 tasks):
- 2-hop × 3
- 3-hop × 3
- 4-hop × 4

Data: `outputs/opencode_spawn_pilot/task_data/*.json`

---

## 4. Four Prompt Variants

### v1_forced_explore
```
You are a research agent solving multi-hop questions using ONLY the task tool.

CRITICAL:
1. You MUST use the 'task' tool to search documents — this is your ONLY search method
2. Do NOT use bash, grep, read, or any other tool to search documents
3. Do NOT answer from your own knowledge — always search via task subagent first
4. Wait for subagent results before answering

TASK TOOL FORMAT:
  task(description="<what to search>", prompt="<instructions>", subagent_type="explore")

Valid fields: description, prompt, subagent_type (in that order).
DO NOT use: agent, agent_type, type, sub_type.

Example:
  task(description="Lostock Dam river", prompt="In <filepath>, find what river Lostock Dam is on", subagent_type="explore")

Answer format:
ANSWER: <your answer>
```

### v2_task_only_explore
```
You are a research agent solving multi-hop questions.

You have access to the 'task' tool to spawn explore subagents for searching.

TASK TOOL FORMAT:
  task(description="<topic>", prompt="<instructions>", subagent_type="explore")

Valid fields: description, prompt, subagent_type.
DO NOT use: agent, agent_type, type.

Example: task(description="Lostock Dam river", prompt="Find what river Lostock Dam is on", subagent_type="explore")

Output format:
ANSWER: <your answer>
```

### v3_task_only_general
```
You are a research agent solving multi-hop questions.

Use the 'task' tool to spawn subagents for parallel searching.

TASK TOOL FORMAT:
  task(description="<topic>", prompt="<instructions>", subagent_type="general")

Valid fields: description, prompt, subagent_type.
DO NOT use: agent, agent_type, type.

Example: task(description="Lostock Dam", prompt="Find what river Lostock Dam is on", subagent_type="general")

Output format:
ANSWER: <your answer>
```

### v4_baseline_no_task
```
You are a research agent solving multi-hop questions.

Read the documents and answer based on the information in the documents.
Do not use any subagents.

Output format:
ANSWER: <your answer>
```

---

## 5. Run Commands

```bash
# Test all variants on 5 tasks
python3 scripts/prompt_variants.py --limit 5 --mode all

# Test specific variant
python3 scripts/prompt_variants.py --limit 5 --variant v1_forced_explore
python3 scripts/prompt_variants.py --limit 5 --variant v2_task_only_explore
python3 scripts/prompt_variants.py --limit 5 --variant v3_task_only_general
python3 scripts/prompt_variants.py --limit 5 --variant v4_baseline_no_task

# Full 10-task run
python3 scripts/prompt_variants.py --limit 10 --variant <variant_name>
```

---

## 6. Metrics

| Metric | Definition |
|--------|------------|
| model_emitted_task_call | model attempted task tool |
| spawn_executed | subagent was actually spawned by OpenCode |
| subagent_returned | subagent result returned to Build agent |
| malformed_call | subagent_type was undefined (bad parameter) |
| accuracy | final answer correct / total |

---

## 7. Results (v2.0 — Preliminary 5-task)

### Overall
| Variant | N | SpawnRate | Accuracy |
|---------|---|-----------|---------|
| v1_forced_explore | 5 | 80% | 60% |
| v2_task_only_explore | 5 | 20% | 40% |
| v4_baseline_no_task | 5 | 40% | 40% |

### Key Observation
**Prompt engineering cannot control spawn behavior.** Model spawns based on internal judgment regardless of prompt constraints.

### By hop count
| Hop | v1 Spawn | v1 Acc | v2 Spawn | v2 Acc | v4 Spawn | v4 Acc |
|-----|---------|--------|---------|--------|---------|--------|
| 2-hop | 1/2 | 1/2 | 0/2 | 2/2 | 1/2 | 1/2 |
| 3-hop | 1/1 | 1/1 | 0/1 | 1/1 | 1/1 | 1/1 |
| 4-hop | 2/2 | 1/2 | 1/2 | 0/2 | 0/2 | 0/2 |

---

## 8. Open Questions

1. **Why does model ignore prompt constraints?** v4 says "don't spawn" but model spawns anyway
2. **Why doesn't spawn improve accuracy?** v1 has highest spawn rate but same accuracy as v4
3. **Does subagent quality matter more than spawn rate?** Subagent using internal knowledge instead of documents
4. **Would removing task tool from build agent change behavior?** Current tests can't establish true no-spawn baseline

---

## 9. Next Steps

1. **Full 10-task experiment** — needed for statistically meaningful conclusions
2. **v3_task_only_general** — not yet tested
3. **Understand model spawn motivation** — why does v4 model spawn on some tasks but not others?
4. **Subagent quality improvement** — ensure subagent searches documents, not internal knowledge
5. **True no-spawn baseline** — OpenCode build agent has task tool by default; need to test without it
