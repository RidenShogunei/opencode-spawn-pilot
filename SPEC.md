# OpenCode Spawn Pilot — Research Specification

Version: v1.0
Summary: Simplified two-mode experiment comparing agent-alone vs agent-with-spawn-affordance on MuSiQue multi-hop QA.

---

## 0. Key Findings (v1.0 Experiment)

**Build agent has `task` tool (can spawn explore subagent), but model never chooses to use it.**

| Mode | Accuracy | Task Tool Calls | Spawns |
|------|----------|-----------------|---------|
| no-subagent | 6/10 = 60% | 0 | 0 |
| with-subagent | 6/10 = 60% | 0 | 0 |

Even with explicit hint about `task` tool, model uses `bash`+`grep` instead of spawning.

### OpenCode Architecture

| Agent | Role | Tools | Can Spawn |
|-------|------|-------|-----------|
| build | primary | read, grep, bash, task | ✓ (has task tool) |
| explore | subagent | bash, grep, read, web*, etc | N/A |

- **build agent has `task` tool** — confirmed by explicit test
- **build agent has `bash` and `grep`** — confirmed from raw output
- **model preference: direct bash/grep over spawn** — even when task tool is available
- **subagent mechanism**: task tool with `subagent_type="explore"` triggers OpenCode's internal spawn

### spawn_events mechanism

When build agent calls `task` tool with valid `agent` parameter:
```
tool: task
input: {description, prompt, agent: "explore" | "general"}
```
OpenCode internally spawns the specified subagent. The task result is returned as tool output.

**Note**: OpenCode task tool schema uses `agent` field (string), not `subagent_type`.

---

## 1. Research Question

**Does explicitly telling the model it can spawn subagents (via task tool) change accuracy or spawn behavior?**

- Mode A: baseline, no mention of spawn capability
- Mode B: explicit hint about `task` tool for spawning explore subagents

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

## 4. Two Modes

### Mode A — no-subagent

System prompt: no mention of task/spawn tools. Model uses only read+grep.

### Mode B — with-subagent

System prompt explicitly describes `task` tool for spawning explore subagents.

Prompt:
```
You have access to:
  - a 'read' tool to read files
  - a 'task' tool to spawn subagents for parallel exploration

When the question requires searching multiple paragraphs, use the 'task' tool
to spawn an 'explore' subagent to search in parallel.

Task tool format:
  tool: task
  input: {description, prompt, agent: "explore"}
```

---

## 5. Run Commands

```bash
# no-subagent baseline
python3 scripts/run_opencode.py --mode no-subagent

# with-subagent
python3 scripts/run_opencode.py --mode with-subagent

# single task test
python3 scripts/run_opencode.py --mode with-subagent --limit 1
```

---

## 6. Metrics

| Metric | Definition |
|--------|------------|
| accuracy | correct / total |
| task_tool_calls | number of task tool invocations |
| spawn_rate | task_tool_calls / total |
| by-hop accuracy | accuracy grouped by 2/3/4 hop |

---

## 7. Results (v1.0)

### ⚠️ Corrected Conclusion

**v1.0 used an unavailable task target (`agent="explore"` in prompts but `subagent_type` was the parameter the model attempted to use with undefined value), so it cannot conclusively measure voluntary task-tool spawning.**

All spawn attempts failed due to parameter mismatch:
- Prompt examples told model to use: `agent="explore"` / `agent="general"`
- Model actually called with: `subagent_type=undefined` (value missing/empty)
- OpenCode task tool schema expects: `agent` field, not `subagent_type`

### Overall (original, invalidated)
- no-subagent: **6/10 = 60%** (0 confirmed successful spawns)
- with-subagent: **6/10 = 60%** (0 confirmed successful spawns)

### By hop count (original)
| Hop | no-subagent | with-subagent |
|-----|-------------|---------------|
| 2-hop | 3/3 ✓ | 3/3 ✓ |
| 3-hop | 1/3 | 1/3 |
| 4-hop | 2/4 | 2/4 |

### What actually happened

1. **Model DID attempt to use task tool** — not "never uses"
2. **Parameter name mismatch**: prompts used `agent=` but model called with `subagent_type=undefined`
3. **All spawns failed silently** — errors were buried in tool-call rejection, not visible as "spawn failed"
4. **3-hop hardest** — accuracy gap between modes was artificial (same failures, not same successes)

---

## 8. Next Steps

1. **Force spawn**: Make task tool the ONLY way to search (remove bash/grep from prompt)
2. **Larger dataset**: 10 tasks too small to draw conclusions
3. **Different model**: Qwen3.5-9B may be too small to learn spawn strategy
4. **OpenCode agent customization**: Create build agent without bash/grep, forcing task tool use
