# OpenCode Spawn Pilot — Research Specification

版本：v0.8
上一版本：v0.7
变更类型：M2 重构为 M2a（两阶段协议 + harness 接管执行）

---

## 0. 核心发现（v0.7 实验）

**Prompt-level spawn affordance did not induce actual subagent calls.**

v0.7 激活 probe（30 runs，M0/M1/M2）的关键数据：

| Mode | Acc | Spawn |
|------|-----|-------|
| M0 | 60% | 0 |
| M1 | 80% | 0 |
| M2 | 30% | 0 |

- actual_spawn_count = 0（wrapper 从未被调用）
- M2 强制决策严重损害能力：acc 从 60%→30%
- **M2 的 `SPAWN_DECISION: no` 被模型理解为"终止回答"而非"继续自己回答"**

### v0.7 发现的核心问题

1. **SPAWN_DECISION 语义冲突**：模型输出 `SPAWN_DECISION: no` 后就停止回答，而不是继续用 grep/read 回答
2. **Prompt 把决策和执行混在一起**：模型需要同时处理"是否 spawn"+"如何执行"+"如何回答"，导致决策压力下直接放弃
3. **OpenCode 输出 JSON 嵌套结构**：需要 `part.text` 字段才能正确提取 ANSWER 和 SPAWN_DECISION
4. **Wrapper 调用能力未知**：模型从未真正调用 wrapper，无法测出"想不想"和"能不能"的差异

---

## 1. 研究问题（v0.8）

**核心问题拆解为三个独立维度：**

1. **Policy（想不想）**：给定 affordance，模型是否会输出 `SPAWN_DECISION: yes`？
2. **Operational（能不能）**：模型能否正确构造并执行 wrapper 调用？
3. **Integration（会不会整合）**：Explore 结果能否被正确整合进最终答案？

**M2a 两阶段协议将三者分离：**
- Phase 1：模型只输出决策（纯测 policy）
- Phase 2a（decision=yes）：Harness 执行 wrapper，注入结果（纯测 integration）
- Phase 2b（decision=no）：模型单独回答（纯测 no-spawn baseline）

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

MuSiQue 10 题（2-hop × 3，3-hop × 3，4-hop × 4）。

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

### M2a — harness_decision（Probe 2 — 两阶段协议）

**Phase 1（Build）：输出决策，不执行其他动作**

```
You must first decide whether to delegate document exploration to a subagent.

Output exactly ONE line and nothing else:
   SPAWN_DECISION: yes
   or
   SPAWN_DECISION: no

Decision rule:
- Choose yes if the question requires following 3 or more linked entities across paragraphs,
  or if you expect multiple paragraphs must be searched.
- Choose no only if the answer is likely contained in 1–2 obvious paragraphs.

Do NOT answer the question yet.
Do NOT run grep yet.
Do NOT call any command yet.
Output only the SPAWN_DECISION line.
```

**Phase 2a（Harness, decision=yes）：执行 wrapper，注入结果**

Harness 检测到 `SPAWN_DECISION: yes` 后：
1. 调用 `bash spawn_explore.sh <task_id> <run_id> <workdir> "<exploration_task>"`
2. 将 Explore 输出注入 Build 的下一轮 prompt
3. Build 整合 Explore 结果，输出 ANSWER

**Phase 2b（Harness, decision=no）：模型单独继续**

```
Original question: <question>

Proceed alone to answer the question.
Use grep to search documents.txt, then read specific paragraphs.
Chain information across paragraphs.
When ready, output exactly on its own line: ANSWER: <your answer>
Do not output anything else besides the ANSWER line.
```

---

## 5. 关键机制

### 5.1 Wrapper Script（spawn_explore.sh）

- Build 每次真实调用 explore subagent 必须通过此 wrapper
- Wrapper 每次调用写入 `outputs/opencode_spawn_pilot/spawn_events/spawn_events.jsonl`
- 记录：timestamp, task_id, run_id, workdir, exploration_task, exit_code, child_stdout_preview, child_stderr_preview
- Wrapper 的 stdout（即 subagent 的输出）返回给 Harness

### 5.2 Spawn 地面真值

**禁止用 stdout regex 作为 spawn 真值。**

Spawn count = `spawn_events.jsonl` 中对应 run_id 的记录数。

### 5.3 OpenCode JSON 解析

OpenCode 输出 JSON lines，嵌套在 `part.text` 字段：

```
{"type":"text",..., "part":{"text":"SPAWN_DECISION: yes",...}}
{"type":"text",..., "part":{"text":"ANSWER: Hunter River",...}}
{"type":"step_finish",..., "part":{"tokens":{"total":10404,"input":10395,"output":9,...}}}
```

解析规则：
- `parse_spawn_decision()`：扫描 JSON，提取 `part.text` 中含 `SPAWN_DECISION` 的行
- `extract_answer()`：扫描 JSON，提取 `part.text` 中含 `ANSWER:` 的行
- `parse_metrics()`：从 `step_finish.part.tokens` 提取 input/output/total

### 5.4 Echo 检测

检测模型是否把 spawn 提示文本 Echo 出来（而不是执行）：

```python
if "opencode run --agent explore" in text and "Decide for yourself" in text:
    → echoed_instructions = True
```

---

## 6. 报告指标

| 指标 | 定义 | 测什么 |
|------|------|--------|
| `spawn_decision_yes_rate` | M2a 中 SPAWN_DECISION: yes 的比例 | Policy |
| `harness_wrapper_called` | decision=yes 时 harness 实际执行 wrapper 的比例 | Operational |
| `decision_accuracy_yes` | decision=yes 且最终成功的比例 | Integration（yes 路径）|
| `decision_accuracy_no` | decision=no 且最终成功的比例 | 单独回答能力 |
| `spawn_call_rate_by_hop` | 各 hop 层级的 yes 比例 | Hop 难度与 spawn 意愿关系 |
| `accuracy_by_mode` | 各模式最终准确率 | 整体效果 |

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

# 仅 M2a
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py m2a

# 仅测试（1题）
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py test

# 断点续跑
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py --resume
```

---

## 9. 不做的事

- 不在 harness 层预热 spawn（即不让模型提前练习 spawn）
- 不用 stdout regex 作为 spawn 真值
- 不扩展 seed（单次运行）
- 不让模型自己调用 wrapper（M2a 由 Harness 执行）

---

## 10. 期望的观察结果类型

### 10.1 M2a 解锁了什么

- 如果 M2a `spawn_decision_yes_rate` > M1 的隐式 yes 率 → 强制决策揭示了真实意愿
- 如果 M2a yes 路径 accuracy > M0 → harness-executed spawn 真正帮助了回答
- 如果 M2a yes 路径 accuracy < M0 → Explore 结果注入反而干扰了模型
- 如果 decision 分布与 hop 数相关 → 模型有合理的 spawn 策略

### 10.2 关键对比

- M0 vs M2a（no 路径）：单独回答能力是否因决策要求而下降？
- M1 vs M2a（yes 路径）：Harness 执行 vs 自我执行，哪个整合效果更好？
- 3-hop vs 4-hop 的 yes 率：hop 数越多，模型是否越倾向 spawn？
