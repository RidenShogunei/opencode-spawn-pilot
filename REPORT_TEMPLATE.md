# OpenCode Spawn Pilot — Experiment Report

> 报告时间：2026-05-01
> 模型：Qwen3.5-9B + vLLM @ localhost:8010
> 框架：OpenCode 1.3.6
> 任务集：MuSiQue + HotpotQA 子集（12 tasks，task_data_v2/）

---

## 1. 实验概述

| 项目 | 值 |
|------|-----|
| 总任务数 | 12 |
| v6.1 配对实验 | 10 tasks，both single + force_multi |
| v10 force-multi | 12 tasks，force_multi only |
| 模型 | Qwen3.5-9B（Qwen3.5-14B 已删除） |

---

## 2. 核心发现

### Finding 1: `opencode run` 不读取配置文件
`opencode run --format json` 完全忽略 `~/.config/opencode/opencode.json`。只有 `--message` 参数能送达 prompt。

**影响**：v6.1 之前所有批量实验（0% spawn）均为 broken experiment——prompt 从未送达。

### Finding 2: 修复后 Spawn 率达 92%
正确送达 prompt 后，force_multi 模式 11/12 任务 spawn 了 subagent。

### Finding 3: Spawn 只解决搜索，不解决推理
即使 subagent 找到了正确信息，Build Agent 在链式推理任务上仍然失败。

### Finding 4: 模型从不主动 spawn
v5.0（22 tasks，3 tiers）：0 spawn。模型认为 spawn 不必要。

---

## 3. 实验结果

### 3.1 v6.1 配对实验（10 tasks，同任务两种模式）

| 模式 | 准确率 | Spawn 率 |
|------|--------|----------|
| **Single**（只许 read/grep） | 4/10（40%） | 0% |
| **Force-Multi**（强制 spawn） | 7/10（70%） | 6/10 |

**净提升**：+30%（spawn 帮助了 3 个任务：B🍊Staff 35,402、Rachel Nevada、Maria Shvetsova）

### 3.2 v10 Force-Multi（12 tasks，final prompt）

| 模式 | 准确率 | Spawn 率 |
|------|--------|----------|
| Force-Multi v10 | 7/12（58%） | 11/12（92%） |

### 3.3 失败分析（v10，5 个错误）

| 任务 | 错误类型 | 说明 |
|------|----------|------|
| train termini | 格式/常识 | 模型输出 `3`，标准答案是 `two` |
| large_2hop Knock | 推理错误 | 问演员成就的影片，模型答了影片名 |
| large_3hop1 1853 | 推理错误 | Subagent 找到正确信息，Build Agent 选错了国家 |
| large_3hop1 Casa Loma | 搜索失败 | Birthplace 信息缺失 |
| large_4hop1 Rio Linda | TIMEOUT | 任务过于复杂，300s 内未完成 |

---

## 4. 结论

| 结论 | 证据 |
|------|------|
| 模型可以被诱导 spawn | 92% spawn 率 |
| Spawn 对直接可提取任务有帮助 | BBC Staff、Rachel Nevada |
| Spawn 无法解决链式推理 | 3-hop、4-hop 任务仍然失败 |
| 真正瓶颈是 9B 推理能力 | 不是搜索能力 |

**下一步问题**：更大模型（14B/32B）能否消除推理瓶颈？

---

## 5. 数据附录

### 5.1 关键结果文件

| 文件 | 内容 |
|------|------|
| `outputs/.../comparison_v6_parallel/results_v6_parallel.jsonl` | v6.1 配对结果（single + force_multi） |
| `outputs/.../comparison_v10/results_fm_v10.jsonl` | v10 force-multi 结果（12 tasks） |
| `outputs/.../task_data_v2/` | 12 个任务 JSON 文件 |

### 5.2 任务详情

| task_id | 难度 | 标准答案 | Single | Force-Multi | Spawn |
|---------|------|---------|--------|-------------|-------|
| hotpot_5a722a68 | 2-hop | Chief Detective Maria Shvetsova | ✗ | ✓ | 1 |
| hotpot_5a85a37d | 2-hop | two termini | ✗ | ✗ | 1 |
| hotpot_5a87bd4e | 2-hop | Ned Flanders | ✓ | ✓ | 1 |
| hotpot_5a8bf083 | 2-hop | northern mockingbird | ✓ | ✓ | 1 |
| hotpot_5adfa226 | 2-hop | 35,402 | ✗ | ✓ | 2 |
| hotpot_5adfff075 | 2-hop | Rachel, Nevada | ✗ | ✓ | 2 |
| large_2hop__591435 | 2-hop | The African Queen | ✓ | ✓ | 0 |
| large_2hop__736167 | 2-hop | ``Hey Jude '' | ✓ | ✓ | 0 |
| large_3hop1__17192 | 3-hop | 1853 | ✗ | ✗ | 1 |
| large_3hop1__862117 | 3-hop | Casa Loma | ✗ | ✗ | 1 |
| large_4hop1__28352 | 4-hop | Rio Linda | ✗ | ✗ | TIMEOUT |
| large_4hop1__726675 | 4-hop | Sebastian Cabot | ✓ | ✓ | 3 |
