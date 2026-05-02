# OpenCode Spawn Pilot — Research Specification

**Version: v1 — Formal release**
**Status**: Complete. Single vs v14 vs v13（Deliberation）对比，55 任务
**Summary**: Multi Agent 与 Single 基本持平，净效果 -1 题。核心发现：Agent 不知道如何正确委托。

---

## 0. 实验结果（55 任务）

**完成时间**：2025-05-02

| 模式 | 准确率 | Spawn 率 | Subagent 返回率 |
|------|--------|----------|---------------|
| **Single** | **23/55 (41.8%)** | 0% | — |
| **v14 Force-Multi** | 22/55 (40.0%) | 60% | 100% |
| **v13 Force-Multi** | 25/55 (45.5%) | 60% | 100% |

> v13 准确率为人工逐题核查结果（自动评测有 bug）。

### 核心发现

1. **Multi 与 Single 基本持平**：净效果 -1 题
2. **Agent 不知道如何正确委托**：过早委托、过度委托、工具盲目、整合失败
3. **Deliberation 提示词有效**：让模型先思考再委托，可减少无效 spawn
4. **Subagent 返回率 100%**：通过事件索引法确认所有 spawn 都返回

---

## 1. 核心问题

### 1.1 Task Delegation 能力

**Agent 何时该 spawn、怎么 spawn、spawn 后怎么用结果？**

| 问题 | 描述 |
|------|------|
| **When to Delegate** | 问题太复杂时？模型自己搜不到时？ |
| **How to Delegate** | prompt 怎么写？要指定搜索范围吗？ |
| **How to Use Results** | subagent 返回纯文本，主模型怎么提取信息？ |

---

## 2. 数据集

| 数据集 | Hop | 题数 |
|--------|-----|------|
| MuSiQue | 2 | 18 |
| MuSiQue | 3 | 13 |
| MuSiQue | 4 | 12 |
| HotpotQA | 2 | 6 |
| Large-scale Multi-hop | 4 | 6 |
| **总计** | — | **55** |

---

## 3. 提示词

### 3.1 v14 Force-Multi（baseline）

```
You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- You MUST spawn at least one subagent using task(...) to search documents before answering
- task(description="<topic>", prompt="Read the provided documents and find <info>", subagent_type="general")

After the subagent completes, synthesize the findings and give your answer.

ANSWER: <your answer>
```

### 3.2 v13 Force-Multi（Deliberation）

```
You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- First, read the question and think about what information is needed and where to find it
- Then spawn at least one subagent using task(...) to search documents before answering
- task(description="<topic>", prompt="Read the provided documents and find <info>", subagent_type="general")

After the subagent completes, synthesize the findings and give your answer.

ANSWER: <your answer>
```

---

## 4. 关键脚本

| 脚本 | 用途 |
|------|------|
| `scripts/run_single_v12.py` | Single Agent 基线 |
| `scripts/run_fm_v14_baseline.py` | v14 Force-Multi（baseline） |
| `scripts/run_fm_v13.py` | v13 Force-Multi（Deliberation prompt） |
| `scripts/start_vllm.sh` | 启动 vLLM |

---

## 5. 输出目录

| 目录 | 内容 |
|------|------|
| `outputs/opencode_spawn_pilot/task_data_v2/` | 55 个任务 JSON |
| `outputs/opencode_spawn_pilot/comparison_v12_single/` | Single 结果 |
| `outputs/opencode_spawn_pilot/comparison_v14/` | v14 FM 结果 |
| `outputs/opencode_spawn_pilot/comparison_v13/` | v13 FM 结果 |

---

## 6. 历史版本

| 版本 | 说明 |
|------|------|
| v1 | 正式版本：Single vs v14 vs v13（Deliberation），55 任务 |
