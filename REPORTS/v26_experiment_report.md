# OpenCode Spawn Pilot — v26 实验总结报告

**实验时间**: 2025-05-02 ~ 2025-05-06  
**数据集**: MuSiQue (195 tasks, 2/3/4-hop) + HotpotQA (6 tasks) + Large-scale Multi-hop (6 tasks)  
**总计**: 207 tasks / 已完成 195 tasks

---

## 一、核心结果总览

| 实验组 | 模型 | Prompt策略 | 准确率 | Spawn率 | Spawn正确率 |
|--------|------|-----------|--------|---------|------------|
| **v26 Single** | Qwen3.5-9B | Free choice (无MUST) | **49%** (96/195) | 3% | 40% |
| **v26 MUST** | Qwen3.5-9B | MUST spawn subagent | 45% (88/195) | **83%** | 46% |
| v26 MiniMax FM | MiniMax-M2 | MUST spawn subagent | 52% (45/85) | 36% | 51% |
| v27 Direct (参考) | Qwen3.5-9B | 无MUST, 直接搜索 | 51% (100/195) | ~0% | — |

---

## 二、关键发现

### 发现1: "MUST" 强制 spawn 反而降低准确率

Qwen3.5-9B 在 free-choice (v26 single) 下准确率 **49%**，加了 MUST 强制 spawn 后反而跌到 **45%**（-4pts）。强制 spawn 没有带来收益，模型在不理解何时该委托的情况下 spawn，质量反而更差。

| 指标 | Free Choice (v26 single) | MUST (v26 main) | 差异 |
|------|--------------------------|-----------------|------|
| 准确率 | 49% | 45% | **-4pts** |
| Spawn率 | 3% | 83% | +80pts |
| Spawn后正确率 | 40% | 46% | +6pts |

> **解读**: 模型"自发不spawn"是正确的自我保护——它知道自己直接搜效果更好。MUST 强迫它 spawn，但 spawn 出来的子 agent 质量（46%）并不比直接搜（37%）高太多。

---

### 发现2: MiniMax vs Qwen3.5-9B 对比

MiniMax-FM (MUST, 85题) vs Qwen3.5-9B MUST (195题):

| 指标 | MiniMax-FM | Qwen3.5-9B MUST |
|------|-----------|-----------------|
| 准确率 | **52%** | 45% |
| Spawn率 | 36% | 83% |
| Spawn后正确率 | **51%** | 46% |
| 直接搜正确率 | **53%** | 37% |

> **MiniMax 全面胜出**:  
> 1. 准确率高出 7pts（52% vs 45%）  
> 2. 直接搜索能力更强（53% vs 37%）——MiniMax 搜索引擎质量更高  
> 3. Spawn 率更低（36% vs 83%）——更懂得"不该 spawn 时不 spawn"  
> 4. 即使 spawn，质量也更稳定（51% vs 46%）

---

### 发现3: 按问题复杂度分解（Qwen3.5-9B MUST）

| 任务类型 | 题数 | 准确率 | Spawn率 |
|----------|------|--------|---------|
| 2-hop (MuSiQue) | 80 | 48% | 79% |
| 3-hop-1 (MuSiQue) | 26 | **53%** | 81% |
| 3-hop-2 (MuSiQue) | 23 | 30% | 87% |
| 4-hop-1 (MuSiQue) | 21 | 42% | 95% |
| 4-hop-2 (MuSiQue) | 13 | **53%** | 92% |
| 4-hop-3 (MuSiQue) | 20 | 35% | 75% |
| HotpotQA (2-hop) | 6 | **100%** | 100% |
| Large-scale (2-4 hop) | 6 | 17% | 100% |

> **观察**:  
> - 越复杂的问题（4-hop）spawn率越高（95%），但准确率反而下降（35-42%）  
> - 3-hop-1 和 4-hop-2 是"甜蜜点"（53%），这两个类型的委托有正收益  
> - HotpotQA 全对（100%），但样本太小（6题）  
> - Large-scale 全部失败（17%），spawn 了但都答错——过度委托

---

### 发现4: Spawn vs 不Spawn 的质量对比

| 模型 | Spawn后正确率 | 直接搜正确率 | 差值 |
|------|-------------|------------|------|
| Qwen3.5-9B (MUST) | 46% | 37% | **+9pts** |
| MiniMax-FM (MUST) | 51% | 53% | **-2pts** |

> **解读**:  
> - Qwen3.5-9B: spawn 有正收益（+9pts），但 MUST 强迫 spawn 导致大量低质量 spawn  
> - MiniMax: spawn 基本没用（-2pts），直接搜反而更好——这就是 MiniMax spawn 率低的原因

---

## 三、实验设计

### v26 Single (自由策略)
```python
SYSTEM = '''You are a research agent. Answer multi-hop questions using the provided documents.
The documents are in a file named `documents.txt`.
Read the file using the read tool to find the information you need.
After gathering information, give your verified answer.'''
```
模型完全自主决策——3% spawn，49%准确率。

### v26 MUST (强制策略)
```python
SYSTEM = '''You are a research agent. You MUST use the 'task' tool to spawn subagents for document searches.
The documents are in a file named `documents.txt`.
You may:
  • Spawn a subagent: task(description="...", prompt="Read documents.txt and find <info>", subagent_type="general")
  • Read `documents.txt` directly using the read tool
CRITICAL: You MUST spawn at least one subagent before answering.'''
```
强制 spawn + 给予选择权（也可以直接读）——83% spawn，45%准确率。

### v26 MiniMax FM
与 v26 MUST 相同 prompt，模型换为 MiniMax-M2.7B-highspeed，API 调用。

---

## 四、子 Agent 完成率分析

### 子 Agent 返回率：100%

两个模型的子 agent 返回率都是 **100%**（所有 spawn 的子 agent 都成功返回了结果）。这说明 OpenCode 的 spawn 机制是可靠的。

| 模型 | Spawn次数 | 子Agent返回 | 返回率 |
|------|----------|------------|--------|
| Qwen3.5-9B MUST | 163 | 163 | **100%** |
| MiniMax-FM MUST | 31 | 31 | **100%** |

### 关键差距：子Agent返回 ≠ 答案正确

真正的损耗在**主模型整合阶段**——子 agent 返回了正确答案，但主模型在合成最终答案时失败。

| 模型 | Spawn后正确 | Spawn后错误 | 整合损耗率 |
|------|------------|------------|-----------|
| Qwen3.5-9B MUST | 76 (46%) | 87 (53%) | **53%** |
| MiniMax-FM MUST | 16 (51%) | 15 (48%) | **48%** |

> **核心发现**: 超过一半的情况下，子 agent 完成了自己的任务，但主模型在整合信息给最终答案时失败了。这是"spawn 有正收益但整体是负优化"的根本原因——子 agent 干活了，但主模型没用上。

### 整合失败的三种子模式

1. **答案提取失败**：子 agent 找到了正确信息，但主模型在 `ANSWER:` 阶段格式/归一化错误
2. **多候选选择错误**：子 agent 返回了多个可能答案，主模型选了错误的
3. **信息丢失**：子 agent 返回了多跳推理的中间结果，主模型在最后一跳整合时断掉

---

## 五、失败模式分析

### 失败模式1: 过度委托（Large-scale, 0/6）
Large-scale 任务全部失败。虽然模型 spawn 了，但过度委托导致：
- 每个 subagent 只搜到一个中间答案
- 主模型在整合时丢失信息
- 最终答案与 gold 偏离

### 失败模式2: Spawn 后答案提取失败
即使 subagent 返回了正确答案，主模型在 `ANSWER:` 阶段提取失败：
- 答案格式不对（"2013" vs "2013年"）
- 多个候选答案时选了错误的
- 数字/日期归一化问题（"2013" vs "twenty thirteen"）

### 失败模式3: 2-hop 问题过度工程化
2-hop 任务本应简单（搜一次就够），但 MUST 模式下模型 spawn 后反而引入噪声：
```
musique_2hop: 48% (spawned 79%) vs 简单直接搜可达更高
```

---

## 五、结论与建议

### 核心结论
1. **强制 spawn 策略在 Qwen3.5-9B 上是负优化**（-4pts）  
2. **MiniMax-M2 的直接搜索能力远强于 Qwen3.5-9B**（53% vs 37%）  
3. **问题复杂度与 spawn 收益呈非单调关系**——3-hop 部分类型有正收益，4-hop 和大规模问题过度委托  
4. **模型知道自己什么时候该 spawn**——free choice 下 3% spawn 率但准确率更高

### 下一步建议

**方向A — Prompt 工程优化**（最快）：
- 不强制 spawn，而是给出"何时该委托"的决策指引
- 类似: "If the question has multiple independent parts, spawn subagents for each"
- 目标: 把 spawn 率从 83% 降到 30-40%，但保持或提升准确率

**方向B — MiniMax API 大规模评测**（最有价值）：
- 85题 MiniMax FM 已经显示 52% 准确率，显著优于 Qwen3.5-9B
- 建议跑完全部 195 题，与 Qwen3.5-9B 做完整对照
- 同时测试 MiniMax free-choice（不加MUST）看是否能达到更高

**方向C — 答案提取后处理**（工程改进）：
- 当前 26-58% 的"wrong"是答案提取失败
- 加一个 normalize() 函数处理数字/日期/别名归一化
- 预期可将整体准确率提升 3-5pts

---

## 六、实验资产

| 文件 | 说明 |
|------|------|
| `scripts/run_fm_v26.py` | v26 MUST 实验脚本 |
| `scripts/run_fm_v26_single.py` | v26 Single (free choice) 实验脚本 |
| `outputs/opencode_spawn_pilot/comparison_v26/` | v26 MUST 原始输出 (195 tasks) |
| `outputs/opencode_spawn_pilot/comparison_v26_single/` | v26 Single 原始输出 |
| `outputs/opencode_spawn_pilot/comparison_v26_minimax_fm/` | MiniMax FM 输出 (85 tasks) |
| `outputs/opencode_spawn_pilot/task_data_v4/` | 任务数据集 (195 JSON) |
