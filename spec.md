# OpenCode Spawn Pilot — Research Specification

版本：v0.7
上一版本：v0.6
变更类型：实验设计完全重构（检测机制失效 → 新 M0/M1/M2 activation probe）

---

## 0. 核心发现（v0.6 实验）

**Prompt-level spawn affordance did not induce actual subagent calls.**

上一轮实验（spawn_closed vs spawn_open，20 runs）的 `spawn_open` 模式中：

- regex 检测到 6/10 "spawn_attempted"
- **事后确认全部为误报**：
  - `cli` 模式：regex 匹配到了 prompt 提示文本本身（模型 Echo 了提示，不是真的调用）
  - `direct` 模式：regex 匹配到了路径字符串（`spawn-pilot/.../documents.txt`）或 JSON 字段（`callID`）
- **真实调用：0/10**

这说明 Qwen3.5-9B 在文本 affordance 下不会将"你知道可以 spawn"转化为可执行动作。

---

## 1. 研究问题（新）

**Qwen3.5-9B 是不会主动 spawn，还是不会把文本 affordance 转化为可执行动作？**

具体分层：
- M0（spawn_closed）：作为能力 baseline——模型单独能达到的准确率
- M1（spawn_affordance）：给文本 affordance，但不强制——模型会主动 spawn 吗？
- M2（spawn_decision_required）：强制模型先做 SPAWN_DECISION yes/no，再执行——这能否解锁 spawn 行为？

---

## 2. 环境配置

### 2.1 vLLM / 模型

```yaml
engine: vLLM 0.19.1
model: Qwen3.5-9B
served_model_name: qwen35-9b
gpu: 2
port: 8010
base_url: http://localhost:8010/v1
thinking: off
max_model_len: 65536
```

### 2.2 OpenCode

```yaml
framework: OpenCode 1.3.6
provider: openai_compatible
base_url: http://localhost:8010/v1
model: qwen35-9b
mode: foreground only
```

### 2.3 Wrapper Script

```bash
scripts/spawn_explore.sh  # 每次真实调用写 spawn_events.jsonl
```

**Spawn 地面真值来自 wrapper 日志，不是 stdout regex。**

---

## 3. 任务域

同 v0.6：MuSiQue 10 题（2-hop × 3，3-hop × 3，4-hop × 4）。

---

## 4. 三种模式设计

### M0 — spawn_closed（Baseline）

```
Build agent 单独运行
subagent 能力：完全不知道
目的：测量无 spawn 能力下的准确率 baseline
```

### M1 — spawn_affordance（Probe 1）

```
Build agent 知道它可以通过 wrapper script 调用 explore subagent
但不是必须——它自己决定是否 spawn
```

Prompt 片段：
```
If you want to delegate document exploration to a subagent, you can call:
   bash /home/jinxu/opencode-spawn-pilot/scripts/spawn_explore.sh <task_id> <run_id> <workdir> <your exploration task>
   The explore subagent will search documents and return findings.
Decide for yourself whether to use the subagent or handle everything directly.
```

### M2 — spawn_decision_required（Probe 2）

```
Build agent 必须在做任何事情之前输出：
   SPAWN_DECISION: yes
   或
   SPAWN_DECISION: no

如果 yes：必须立即调用 wrapper script
如果 no：自己用 grep/read 完成
```

Prompt 片段：
```
Before doing anything else, you must decide whether to delegate document
exploration to a subagent.

Output exactly ONE of the following on its own line:
   SPAWN_DECISION: yes
   SPAWN_DECISION: no

If you choose yes, you MUST call the wrapper script immediately:
   bash /home/jinxu/opencode-spawn-pilot/scripts/spawn_explore.sh ...

You must output a SPAWN_DECISION line first, then act accordingly.
```

---

## 5. 关键机制

### 5.1 Wrapper Script（spawn_explore.sh）

- Build 每次真实调用 explore subagent 必须通过此 wrapper
- Wrapper 每次调用写入 `outputs/opencode_spawn_pilot/spawn_events/spawn_events.jsonl`
- 记录：timestamp, task_id, run_id, workdir, exploration_task, exit_code, child_stdout_preview, child_stderr_preview
- Wrapper 的 stdout（即 subagent 的输出）会返回给 Build 进程

### 5.2 Spawn 地面真值

**禁止用 stdout regex 作为 spawn 真值。**

Spawn count = `spawn_events.jsonl` 中对应 run_id 的记录数。

### 5.3 Echo 检测

检测模型是否把 spawn 提示文本 Echo 出来（而不是执行）：

```python
if "opencode run --agent explore" in text and "Decide for yourself" in text:
    → echoed_instructions = True
```

---

## 6. 报告指标

| 指标 | 定义 |
|------|------|
| `spawn_decision_yes_rate` | M2 中 SPAWN_DECISION: yes 的比例 |
| `valid_spawn_call_rate` | 有 SPAWN_DECISION=yes 且 actual_spawn_count ≥ 1 的比例 |
| `decision_call_consistency` | M2 中 decision 和实际调用一致的比例（yes→spawned 或 no→not spawned） |
| `spawn_call_rate_by_hop` | 各 hop 层级 actual_spawn_count ≥ 1 的比例 |
| `accuracy_by_mode` | 各模式准确率 |
| `invalid_spawn_attempts` | M2 中说了 yes 但没调用 wrapper 的次数 |
| `echoed_instructions` | 模型把 spawn 提示文本 Echo 出来但没执行的次数 |

---

## 7. 实验规模

```
10 tasks × 3 modes = 30 runs
```

不做 seed 扩展（当前阶段不需要统计显著性）。

---

## 8. 运行方式

```bash
# 启动 vLLM（如未运行）
bash /home/jinxu/opencode-spawn-pilot/scripts/start_vllm.sh

# 运行全部 30 runs
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py

# 仅 M0
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py m0

# 仅 M1
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py m1

# 仅 M2
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py m2

# 仅测试（1题）
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py test

# 断点续跑
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py --resume
```

---

## 9. 不做的事

- 不在 harness 层强制调用 Explore subagent
- 不用 stdout regex 作为 spawn 真值
- 不在 M1/M2 中预热 spawn（即不让模型提前练习 spawn）
- 不扩展 seed（单次运行）

---

## 10. 期望的观察结果类型

### 10.1 M1 vs M0 对比

- 如果 M1  spawn 率 ≈ 0 → 模型不会主动 spawn（affordance 不够）
- 如果 M1  spawn 率 > 0，但准确率没提升 → spawn 了但没整合
- 如果 M1  spawn 率 > 0 且准确率提升 → affordance 有效

### 10.2 M2 vs M1 对比

- 如果 M2 spawn 率 > M1 → 强制决策解锁了 spawn 行为
- 如果 M2 spawn 率 = M1 → 决策本身不是瓶颈
- 如果 M2 invalid_attempts > 0 → 模型说了 yes 但没调用 wrapper

### 10.3 Echo 分析

- M1 中 echoed_instructions > 0 → 模型把提示当成了系统指令而非行动指令
- M2 中 echoed_instructions = 0 → 强制决策格式减少了 Echo
