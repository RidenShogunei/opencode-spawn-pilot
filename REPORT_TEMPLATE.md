# OpenCode Spawn Pilot — Activation Probe Report Template

> 报告时间：YYYY-MM-DD
> 模型：Qwen3.5-9B + vLLM @ localhost:8010
> 框架：OpenCode 1.3.6

---

## 1. 实验概述

| 项目 | 值 |
|------|-----|
| 总 runs | 30（10 tasks × 3 modes） |
| M0（spawn_closed） | 10 runs |
| M1（spawn_affordance） | 10 runs |
| M2（spawn_decision_required） | 10 runs |

---

## 2. 核心发现

> **摘要**：M0/M1/M2 各模式准确率、spawn 行为、echo 行为。

---

## 3. 指标详解

### 3.1 准确率（accuracy_by_mode）

| 模式 | N | 准确率 | 正确答案数 |
|------|---|--------|-----------|
| M0_spawn_closed | 10 | X% | X/10 |
| M1_spawn_affordance | 10 | X% | X/10 |
| M2_spawn_decision_required | 10 | X% | X/10 |

### 3.2 Spawn 行为（actual_spawn_count from wrapper log）

| 模式 | 总 spawn 次数 | spawn ≥1 的任务数 | 平均 spawn 次数 |
|------|-------------|-----------------|---------------|
| M0_spawn_closed | 0 | 0/10 | 0.0 |
| M1_spawn_affordance | X | X/10 | X.X |
| M2_spawn_decision_required | X | X/10 | X.X |

### 3.3 Echo 检测（echoed_instructions）

| 模式 | Echoed 次数 | 说明 |
|------|-----------|------|
| M0_spawn_closed | N/A | 无 spawn affordance |
| M1_spawn_affordance | X/10 | 模型把 spawn 提示文本 Echo 出来 |
| M2_spawn_decision_required | X/10 | 模型把 SPAWN_DECISION 格式 Echo 出来 |

### 3.4 M2 决策分析（spawn_decision_yes_rate）

| 指标 | 值 |
|------|---|
| SPAWN_DECISION: yes | X/10 |
| SPAWN_DECISION: no | X/10 |
| Malformed（无 SPAWN_DECISION） | X/10 |

**decision_call_consistency**：X/Y（M2 中决策和实际调用一致的比例）

### 3.5 invalid_spawn_attempts

| 任务 | decision | actual_spawn | 说明 |
|------|----------|-------------|------|
| task_id | yes | 0 | 说了要 spawn 但没调用 wrapper |
| ... | ... | ... | ... |

### 3.6 spawn_call_rate_by_hop

| Hop | M0 准确率 | M1 spawn 率 | M1 准确率 | M2 spawn 率 | M2 准确率 |
|-----|---------|------------|---------|------------|---------|
| 2-hop（3题） | X% | X/X | X% | X/X | X% |
| 3-hop（3题） | X% | X/X | X% | X/X | X% |
| 4-hop（4题） | X% | X/X | X% | X/X | X% |

---

## 4. 关键观察

### 4.1 M1 问题诊断：affordance 为什么不转化为 action？

- 模型是否把 spawn 提示 Echo 出来但不执行？
- 模型是否自己判断"不需要 spawn"？
- 模型是否知道如何调用 wrapper script？

### 4.2 M2 问题诊断：强制决策是否解锁 spawn？

- M2 spawn 率 vs M1 spawn 率
- 如果 M2 spawn 率 > M1 → 决策格式是瓶颈
- 如果 M2 spawn 率 = M1 → 决策本身不是问题

### 4.3 整合问题

- 即使 spawn 了，最终答案是否引用了 subagent 的输出？
- spawn 对准确率的贡献是否独立于 mode？

---

## 5. 结论

> **核心问题**：Qwen3.5-9B 不会主动 spawn（因为没有这个行为习惯），还是它不知道如何将文本 affordance 转化为可执行动作？

| 假说 | 证据 | 结论 |
|------|------|------|
| 模型不会主动 spawn | M1 spawn 率 ≈ 0 | 待验证 |
| 模型不知道如何执行 | M2 spawn 率 > M1 | 待验证 |
| 模型 spawn 了但不整合 | spawn 了仍然错 | 待验证 |

---

## 6. 数据附录

### 6.1 原始 runs.jsonl 路径
`outputs/opencode_spawn_pilot/runs.jsonl`

### 6.2 Spawn 事件日志路径
`outputs/opencode_spawn_pilot/spawn_events/spawn_events.jsonl`

### 6.3 per-task 结果

| task_id | mode | success | actual_spawn | echoed | spawn_decision | pred | gold |
|---------|------|---------|--------------|--------|----------------|------|------|
| ... | M0 | ✅/❌ | 0 | N/A | N/A | ... | ... |
| ... | M1 | ✅/❌ | X | ✅/❌ | N/A | ... | ... |
| ... | M2 | ✅/❌ | X | ✅/❌ | yes/no | ... | ... |
