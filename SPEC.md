# OpenCode Spawn Pilot — Research Specification

Version: v5.0
Summary: Expanded task set to 3 tiers (short/medium/large) with 22 total tasks. Model never spawns subagents regardless of document size or task complexity when spawn is truly optional. Single and multi modes perform comparably.

---

## 0. Key Findings (v5.0)

### Critical Design Fix

**v3 Design Flaw**: Single mode prompt said "MUST NOT use task tool" — which itself mentions the spawn mechanism and creates curiosity/confusion.

**v4 Corrected Design**:
- **single_v2**: System prompt makes NO mention of spawn, task tool, or subagents. Agent only knows about read/grep/bash.
- **multi_v2**: System prompt mentions task tool as ONE option among read/grep/bash, with model deciding when to use it.

### v5.0 Results (22 tasks across 3 tiers, 0 spawns)

| Tier | Mode | Accuracy | Spawn |
|------|------|----------|-------|
| short (~20 para) | single | 5/8 (63%) | 0 |
| short (~20 para) | multi | 6/10 (60%) | 0 |
| med (10 para, 14-16K chars) | single | 6/6 (100%) | 0 |
| med (10 para, 14-16K chars) | multi | 4/6 (67%) | 0 |
| large (100 para, 43-49K chars) | single | 3/6 (50%) | 0 |
| large (100 para, 43-49K chars) | multi | 4/6 (67%) | 0 |

**Overall: Single 14/20 (70%), Multi 14/22 (64%)**

**Critical finding: Model NEVER spawns when spawn is truly optional — across all 22 tasks, all 3 tiers, and all complexity levels. This holds even for large documents (100 paragraphs) where parallel search could help.**

### Architecture

OpenCode's default system prompt includes hardcoded spawn instructions. By configuring `~/.config/opencode/opencode.json` with custom `agent.build.prompt`, we override the default and control tool behavior.

```json
// ~/.config/opencode/opencode.json
{
  "agent": {
    "build": {
      "prompt": "Your custom system prompt here"
    }
  }
}
```

**IMPORTANT**: `OPENCODE_CONFIG` environment variable does NOT work. Must modify config file directly.

### Architecture Breakthrough

**Discovery**: OpenCode's default system prompt includes hardcoded spawn instructions. By configuring `~/.config/opencode/opencode.json` with custom `agent.build.prompt`, we override the default and control tool behavior.

### How to Control Spawn

```json
// ~/.config/opencode/opencode.json
{
  "agent": {
    "build": {
      "prompt": "Your custom system prompt here"
    }
  }
}
```

**IMPORTANT**: `OPENCODE_CONFIG` environment variable does NOT work. Must modify config file directly.

### Two Validated Configurations

| Mode | System Prompt | Behavior | Spawn Rate |
|------|--------------|----------|------------|
| **single** | "MUST NOT use task tool" | Uses only read/grep | 0% (verified) |
| **multi** | "MUST use task tool to spawn subagents" | Spawns explore subagent | >0% (verified) |

### Proof of Concept Results (2 tasks)

|| Task | Single Agent | Multi Agent |
|------|-------------|-------------|
| 2hop (Lostock Dam) | ✓ no-spawn (8.8s) | ✓ no-spawn (11.6s) |
| 2hop (publisher HQ) | ✓ no-spawn (10.1s) | ✓ spawn (11.8s) |

---

## 1. v4.0 Experimental Results

### Single Agent v2 (10 tasks, 0 spawns)

| Task | Correct | Predicted | Spawn | Time |
|------|---------|-----------|-------|------|
| 2hop (Lostock Dam) | ✓ | Hunter River | 0 | 11s |
| 2hop (publisher HQ) | ✓ | Annapolis, Maryland | 0 | 11s |
| 2hop (Smooth Jazz) | ✓ | George Benson | 0 | 12s |
| 3hop1 (Warner owner) | ✗ | James Conkling | 0 | 9s |
| 3hop2 (John Phan region) | ✓ | South Central Coast | 0 | 12s |
| 3hop2 (date) | ✓ | January 2015 | 0 | 52s |
| 4hop1 (Rio Linda) | ✗ | El Salvador | 0 | 131s |
| 4hop1 (Italian navigator) | ✓ | Sebastian Cabot | 0 | 18s |
| 4hop1 (MLB season) | ✓ | March 29, 2018 | 0 | 25s |
| 4hop3 (largest urban area) | ✗ | 3 | 0 | 18s |

**Single v2: 7/10 (70%), spawns=0**

### Multi Agent v2 (10 tasks, 0 spawns — model never chose to spawn)

| Task | Correct | Predicted | Spawn | Time |
|------|---------|-----------|-------|------|
| 2hop (Lostock Dam) | ✓ | The Hunter River | 0 | 11s |
| 2hop (publisher HQ) | ✓ | Annapolis, Maryland | 0 | 12s |
| 2hop (Smooth Jazz) | ✓ | George Benson | 0 | 20s |
| 3hop1 (Warner owner) | ✓ | Warner Music Group | 0 | 24s |
| 3hop2 (John Phan region) | ✓ | South Central Coast | 0 | 16s |
| 3hop2 (date) | ✗ | The documents state... | 0 | 22s |
| 4hop1 (Rio Linda) | ✗ | Oak Lawn, Illinois | 0 | 218s |
| 4hop1 (Italian navigator) | ✓ | Sebastian Cabot | 0 | 17s |
| 4hop1 (MLB season) | ✓ | March 29, 2018 | 0 | 26s |
| 4hop3 (largest urban area) | ✗ | 3 | 0 | 18s |

**Multi v2: 7/10 (70%), spawns=0**

### Key Observations

1. **Model never spawns when truly optional**: multi_v2 prompt includes task tool description but model chose 0 spawns across all 10 tasks
2. **Identical accuracy**: Both modes at 70% on same task set
3. **Same wrong answers**: Both modes fail on identical tasks (3hop1-Warner, 4hop1-RioLinda, 4hop3-largest)
4. **3hop2-date difference**: Single got it right (January 2015), Multi got it wrong — the only task where accuracy differed
5. **Spawn doesn't improve**: For these small documents (~60 lines), direct grep is faster and equally effective

### Prompt Comparison

**single_v2** (no mention of spawn):
```
You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

RULES:
- Use the read, grep, and bash tools to search through documents
- Base your answer ONLY on information found in the documents
- Do not guess or use your own knowledge
```

**multi_v2** (spawn available as one option):
```
You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

You have access to a subagent tool that can search documents on your behalf. When you need to find specific information, use the task tool to spawn a subagent.

TOOL USAGE:
- task(description="<search>", prompt="Read <FILEPATH> and find <INFO>", subagent_type="explore"): Spawns a search subagent
- read, grep, bash: Standard document search tools

Answer only based on information found in the documents.
```

---

## 2. Research Question

Does the model choose to spawn subagents when given the option? Does spawn improve accuracy on MuSiQue multi-hop QA?

**Answer from v5.0**: Model never spawns on any of the 22 tasks across 3 tiers. Single-agent performs marginally better (70% vs 64%). Spawn is never perceived as beneficial by the model even on large documents (100 paragraphs, 43-49K chars).

---

## 3. Environment

Does explicitly forcing the model to spawn subagents (via system prompt) improve accuracy over single-agent baseline on MuSiQue multi-hop QA?

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
config: ~/.config/opencode/opencode.json (custom agent.build.prompt)
```

---

## 3. Task Domain

MuSiQue benchmark subset (10 tasks):
- 2-hop × 3
- 3-hop × 3
- 4-hop × 4

Data: `outputs/opencode_spawn_pilot/task_data/*.json`

---

## 4. Two Prompt Variants

### single (True Single-Agent)
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

### multi (Forced Multi-Agent)
```
You are a research agent solving multi-hop questions. You MUST use the 'task' tool to spawn subagents for document search.

CRITICAL:
1. You MUST use task(description="<search>", prompt="Read the file <FILEPATH> and find <INFO>", subagent_type="explore") to search documents
2. Do NOT use read or grep to search - only use task tool
3. Wait for subagent results before answering

Output format:
ANSWER: <your answer>
```

---

## 5. Run Commands

```bash
# Run comparison experiment
python3 scripts/run_comparison.py --mode both

# Run single mode only
python3 scripts/run_comparison.py --mode single

# Run multi mode only
python3 scripts/run_comparison.py --mode multi

# Run on subset of tasks
python3 scripts/run_comparison.py --mode both --limit 5
python3 scripts/run_comparison.py --mode both --tasks "2hop__623501_297043,2hop__628752_538661"
```

---

## 6. Metrics

| Metric | Definition |
|--------|------------|
| subagent_spawned | model called task tool |
| subagent_returned | subagent result returned to Build |
| task_tool_calls | total task tool invocations |
| accuracy | final answer correct / total |

---

## 7. Results (v3.0 — Proof of Concept)

### 2-Task Proof of Concept
| Task | Single (spawn) | Multi (spawn) | Notes |
|------|-----------------|---------------|-------|
| 2hop_001 | ✓ (0) | ✓ (0) | Both answered correctly |
| 2hop_002 | ✓ (0) | ✓ (1) | Multi spawned 1 subagent |

### Summary
| Variant | N | Accuracy | Spawn Rate | Notes |
|---------|---|----------|------------|-------|
| single | 2 | 100% | 0% | No spawn as expected |
| multi | 2 | 100% | 50% | Spawn behavior controlled |

---

## 8. Technical Notes

### Config Override Discovery

OpenCode loads config from `~/.config/opencode/opencode.json`. The `agent.build.prompt` field overrides the binary's hardcoded default prompt.

**Old approach (v2)**: Layer additional prompt on top of default → Default prompt conflicted, model ignored our instructions

**New approach (v3)**: Replace default prompt entirely → Our instructions take full effect

### Why Previous Experiments Were Flawed

In v2 experiments:
- OpenCode default prompt says "use task tool to spawn subagents"
- Our v4 prompt said "do not use subagents"
- **Result**: Model followed default prompt, spawned anyway (40% spawn rate)
- **Conclusion**: Our "no-spawn" baseline was NOT actually a single-agent baseline

### Timeout Issues

Single-agent mode times out on 4-hop tasks (300s limit) because:
- Can only use read/grep (no parallel search)
- 4-hop requires 3 sequential document lookups
- Model loops or takes too long

This suggests forced-multi-agent may have accuracy advantage on complex tasks.

---

## 9. Open Questions

1. **Does multi-agent improve accuracy?** Not yet proven — need full 10-task experiment
2. **Why did multi agent not spawn on 2hop_001?** Model may have judged task simple enough without spawn
3. **Timeout on 4-hop**: Single agent struggles, multi agent may handle better with parallel subagents

---

## 10. Next Steps

1. **Run full 10-task experiment** (handle timeouts better)
2. **Analyze spawn decisions**: Why does model spawn on some tasks but not others in multi mode?
3. **Compare accuracy by task complexity**: 2-hop vs 3-hop vs 4-hop
4. **Verify subagent quality**: Does spawned subagent actually search documents vs using internal knowledge?

---

## 11. Files

- `scripts/run_comparison.py`: Main experiment script
- `configs/single_agent.json`: Single-agent config template
- `configs/multi_agent_forced.json`: Multi-agent config template
- `system_prompts/single_agent.txt`: Single-agent system prompt
- `system_prompts/multi_agent_forced.txt`: Multi-agent system prompt
- `outputs/opencode_spawn_pilot/comparison/`: Experiment results

---

## 12. Change Log

### v5.0 (2026-05-01)
- Expanded task set from 10 to 22 tasks across 3 tiers: short (MuSiQue original, ~20 para), medium (HotpotQA, 10 para, 14-16K chars), large (MuSiQue expanded, 100 para, 43-49K chars)
- Model NEVER spawns subagents across all 22 tasks — even on 100-paragraph documents where parallel search could theoretically help
- Single-agent: 14/20 (70%), Multi-agent: 14/22 (64%) — no accuracy advantage for spawn
- Medium tier (HotpotQA): single 6/6 (100%), multi 4/6 (67%) — single actually outperforms multi
- Large tier: multi 4/6 (67%) vs single 3/6 (50%) — slight multi advantage but spawn count = 0

### v4.0 (2026-04-30)
- Discovered config file approach to override default system prompt
- Proved single/multi configurations work correctly
- `OPENCODE_CONFIG` env var does NOT work — must modify config directly
- New experimental framework with clean separation between modes

### v2.0 (2026-04-29)
- Found `--format json` parsing approach
- Discovered `subagent_type` parameter name
- Proved subagent results return to Build agent
- Found prompt engineering cannot control spawn (flawed experiment design)
