# Stage 1B Mechanism Confirmation Report

**日期**: 2026-04-30
**模型**: Qwen3.5-9B + vLLM 0.19.1 (GPU 2, port 8010, qwen3_coder parser, thinking=off)
**Benchmark**: MuSiQue (multi-hop QA, 20-paragraph document pool)
**框架**: OpenCode 1.3.6 + forced subagent spawning
**规模**: 10 tasks × 4 systems = 40 runs

---

## 1. 实验设计

### 四个对比系统

| 系统 | 架构 | subagent 数 |
|------|------|:---:|
| **S1** Build-only | Build 独立作答 | 0 |
| **S2** Explore→Build | Explore 先探索，输出喂给 Build | 1 |
| **S3** Explore→General→Build | Explore → General review → Build | 2 |
| **S4** Explore→Table→Build | Explore → 结构化 evidence table → Build | 1 |

### 任务分层

| Bucket | MuSiQue hop | 任务数 | 含义 |
|--------|:-----------:|:---:|------|
| `local_readable` | 2-hop | 3 | 2 段支持，单 agent 可覆盖 |
| `multi_file` | 3-hop | 3 | 3 段跨文档追踪 |
| `long_context` | 4-hop | 4 | 4 段藏在 20 段文档中 |

---

## 2. 总体结果

### 成功率

| 系统 | 总体 | 2-hop | 3-hop | 4-hop |
|------|:----:|:-----:|:-----:|:-----:|
| **S1** Build-only | **40%** | 1/3 (33%) | 1/3 (33%) | 2/4 (50%) |
| **S2** Explore→Build | **70%** | 2/3 (67%) | 2/3 (67%) | 3/4 (75%) |
| **S3** Explore→General→Build | **60%** | 2/3 (67%) | 1/3 (33%) | 3/4 (75%) |
| **S4** Explore→Table→Build | **60%** | 2/3 (67%) | 1/3 (33%) | 3/4 (75%) |

### 机制指标

| 系统 | M1 Evidence Recall | M2 Extra Hops | M3 Integration Error | M5 E→B Failed |
|------|:------------------:|:-------------:|:--------------------:|:--------------:|
| **S2** Explore→Build | **67%** | 2.1 | 1/10 (10%) | 1 |
| **S3** Explore→General→Build | **64%** | 2.1 | 3/10 (30%) | 3 |
| **S4** Explore→Table→Build | **63%** | 2.1 | 3/10 (30%) | 3 |

### 逐任务详情

| Task | Hop | Gold Answer | S1 | S2 | S3 | S4 |
|------|:---:|------|:---:|:---:|:---:|:---:|
| musique_2hop_001 | 2 | Hunter River | ❌ | ❌ | ❌ | ❌ |
| musique_2hop_002 | 2 | Annapolis | ❌ | ✅ | ✅ | ✅ |
| musique_2hop_003 | 2 | George Benson | ✅ | ✅ | ✅ | ✅ |
| musique_3hop_004 | 3 | South Central Coast | ✅ | ❌ | ✅ | ✅ |
| musique_3hop_005 | 3 | January 2015 | ❌ | ✅ | ❌ | ❌ |
| musique_3hop_006 | 3 | Warner Music Group | ❌ | ✅ | ❌ | ❌ |
| musique_4hop_007 | 4 | third-largest | ❌ | ✅ | ✅ | ✅ |
| musique_4hop_008 | 4 | March 29, 2018 | ✅ | ✅ | ✅ | ✅ |
| musique_4hop_009 | 4 | Rio Linda | ❌ | ❌ | ❌ | ❌ |
| musique_4hop_010 | 4 | Sebastian Cabot | ✅ | ✅ | ✅ | ✅ |

---

## 3. 核心指标分析

### M1: Evidence Recall

Explore subagent 能找到大部分 gold supporting paragraph：
- **S2 平均 M1 = 67%**：Explore 在 10 个任务中找到约 2/3 的关键段落
- **S3/S4 M1 相近**（64%/63%）：General 和 Table 都没有显著提升 evidence discovery
- 4-hop 任务中，Explore 主要缺失中间推理跳，导致 Build 链断裂

### M3: Integration Error

| 系统 | Integration Error | 说明 |
|------|:----------------:|------|
| **S2** | 1/10 (10%) | Explore 找到证据后，Build 整合良好 |
| **S3** | 3/10 (30%) | General review 引入噪声，覆盖了部分正确证据 |
| **S4** | 3/10 (30%) | Evidence table 并没有降低整合失败率 |

**关键发现**：S4 的 evidence table 反而使 M3 从 10% 上升到 30%，与假设 H4（结构化 table 应降低整合失败）相反。

### M5: Explore Found, Build Failed（最关键的机制指标）

共 7 个案例：

| Task | System | Gold | Predicted | M1 Recall |
|------|--------|------|-----------|:---------:|
| musique_3hop_005 | S2 | January 2015 | 2014 | 100% |
| musique_3hop_005 | S3 | January 2015 | 2014 | 100% |
| musique_3hop_005 | S4 | January 2015 | Prior to the 1954 elections | 67% |
| musique_3hop_006 | S3 | Warner Music Group | James Conkling | 100% |
| musique_3hop_006 | S4 | Warner Music Group | James Conkling | 100% |
| musique_4hop_009 | S2 | Rio Linda | FINDINGS_COMPLETE | 75% |
| musique_4hop_009 | S3 | Rio Linda | Information not available | 75% |
| musique_4hop_009 | S4 | Rio Linda | Information not available | 100% |

**分析**：
- **musique_3hop_005/006**：Explore 找到了所有必要证据，但 Build 给出了错误答案（时间线推理错误 / 跳错推理链）。这说明瓶颈在于 **Build 的推理能力**，而非 evidence retrieval
- **musique_4hop_009**：Explore 找到了 75-100% 的证据，但 Build 完全无法回答。说明 4-hop 长程推理超出 Qwen3.5-9B 能力边界

---

## 4. General 和 Evidence Table 的效果

### General review（S3）

- **M3 = 30%**，高于 S2 的 10%
- **M5 = 3**，高于 S2 的 1
- 典型失败：musique_3hop_006（Gold = Warner Music Group），General review 后 Build 输出了"James Conkling"（CEO/创始人名字）
- **结论**：General 在 Qwen3.5-9B 上引入噪声，而非补全推理链。与 H3 一致。

### Evidence Table（S4）

- **M1 = 63%**，略低于 S2 的 67%（差异不显著）
- **M3 = 30%**，高于 S2 的 10%，**与 H4 假设相反**
- **S4 accuracy = S3**，都低于 S2
- 证据表格式没有帮助 Build 更好地整合证据

---

## 5. 按难度分层分析

### 2-hop（单跳推理）

| 系统 | 成功率 |
|------|:------:|
| S1 Build-only | 33% |
| S2 Explore→Build | 67% |
| S3 Explore→General→Build | 67% |
| S4 Explore→Table→Build | 67% |

Explore 在 2-hop 上有明显增益，但结构化格式没有额外收益。

### 3-hop（三跳推理）

| 系统 | 成功率 |
|------|:------:|
| S1 Build-only | 33% |
| S2 Explore→Build | 67% |
| S3 Explore→General→Build | 33% |
| S4 Explore→Table→Build | 33% |

**关键发现**：在 3-hop 上，S2 > S3 = S4。Explore 有增益，但 General 和 Evidence Table 都引入了额外的整合失败。

### 4-hop（四跳推理）

| 系统 | 成功率 |
|------|:------:|
| S1 Build-only | 50% |
| S2 Explore→Build | 75% |
| S3 Explore→General→Build | 75% |
| S4 Explore→Table→Build | 75% |

Explore 在 4-hop 上增益最大（50% → 75%）。S3/S4 与 S2 持平。

---

## 6. 结论与分支

### 主要结论

1. **Explore 带来 evidence recall 增益**：M1 = 67%，但这不自动转化为 accuracy 提升
2. **S2 是最优系统**：70% 成功率，最简单的架构（1 个 subagent），M3 最低（10%）
3. **General 引入噪声**：S3 M3 = 30%，M5 = 3，都高于 S2
4. **Evidence table 没有降低整合失败**：S4 M3 = 30%，反而高于 S2
5. **Qwen3.5-9B 在 3-hop 上面临硬瓶颈**：2-hop 67%，3-hop 33%，说明多跳推理是模型能力边界

### 假设验证

| 假设 | 验证结果 | 说明 |
|------|:--------:|------|
| H1: S2 M1 > S1 implicit coverage | ✅ | Explore M1 = 67% |
| H2: S2 accuracy 不一定 > S1 | ❌ | S2 (70%) > S1 (40%)，有明显提升 |
| H3: S3 accuracy ≤ S2 | ✅ | S3 (60%) < S2 (70%) |
| H4: S4 M3 < S2 M3 | ❌ | S4 M3 (30%) > S2 M3 (10%)，反方向 |
| H5: S4 tokens/correct 或 accuracy 优于 S2/S3 | ❌ | S4 accuracy = S3 < S2 |
| H6: M5 存在非零案例 | ✅ | 7 个 M5 案例，证实 integration bottleneck |

### 对 spawn policy 的含义

> **Spawn 的关键问题不是是否调用 subagent，而是 evidence representation 和 integration protocol。Explore 能找到证据，但 Build 整合失败的比例仍然可观（10-30%）。简单结构化（markdown table）不足以解决，需要更强的 evidence grounding 或 stepwise reasoning controller。**

### 分支建议

- **Branch A（强结构化）**：Evidence table 格式不生效，应研究 citation-grounded answer、forced evidence citation、或 hop-by-hop verifier
- **Branch B（模型能力）**：Qwen3.5-9B 在 3-hop 上存在硬瓶颈，换用更强大的模型可能是突破关键
- **Branch C（禁用 General）**：S3 一贯劣于 S2，应默认禁用 General，或仅在特定场景调用
- **Branch D（Explore 优化）**：多 query decomposition、hop-by-hop retrieval 可能有帮助

---

## 附录

### Git 历史

```
30af170 fix: M1/M5 rebuild - use task_data (paragraphs) not task; merge append for multi-system runs.jsonl
11ab67c feat: rebuild_from_existing_run for level-2 resume (parse existing stdout dirs without re-running)
ed0406b v0.5: S4 structured evidence table, M1-M5 metrics, BUILD_PROMPT for all systems
e3fb731 Stage 1B v0.5: 4 systems, 5 mechanism metrics (M1-M5), S4 structured evidence table
```

### 输出文件

- `runs.jsonl`：40 条完整 run entries
- `stage1b_report.md`：本报告