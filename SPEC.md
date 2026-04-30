# OpenCode Spawn Pilot — Research Specification

Version: v3.0
Summary: Controlled experiment comparing single-agent vs forced-multi-agent on MuSiQue multi-hop QA using custom system prompts.

---

## 0. Key Findings (v3.0)

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

| Task | Single Agent | Multi Agent |
|------|-------------|-------------|
| 2hop (Lostock Dam) | ✓ no-spawn (8.8s) | ✓ no-spawn (11.6s) |
| 2hop (publisher HQ) | ✓ no-spawn (10.1s) | ✓ spawn (11.8s) |

### Key Insight

**Prompt engineering CAN control spawn behavior when using custom system prompt config**, unlike our previous v2 experiments where OpenCode's default prompt instructions conflicted with our overlay prompts.

---

## 1. Research Question

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

### v3.0 (2026-04-30)
- Discovered config file approach to override default system prompt
- Proved single/multi configurations work correctly
- `OPENCODE_CONFIG` env var does NOT work — must modify config directly
- New experimental framework with clean separation between modes

### v2.0 (2026-04-29)
- Found `--format json` parsing approach
- Discovered `subagent_type` parameter name
- Proved subagent results return to Build agent
- Found prompt engineering cannot control spawn (flawed experiment design)
