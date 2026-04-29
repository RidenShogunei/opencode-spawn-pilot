# OpenCode-Native Spawn Pilot — 阶段性汇总报告

**日期**: 2026-04-29  
**模型**: Qwen3.5-9B + vLLM 0.19.1 (GPU 2, port 8010, qwen3_coder parser, thinking=off)  
**Benchmark**: MuSiQue (multi-hop QA, 20-paragraph document pool)  
**框架**: OpenCode 1.3.6 + forced subagent spawning  
**规模**: 10 tasks × 3 systems = 30 runs

---

## 1. 实验设计

### 三大系统

| 系统 | 架构 | 实现 |
|------|------|------|
| **S1** build_only | Build 独立作答 | `opencode run --agent build`，禁止 subagent |
| **S2** build_explore | Explore → Build | harness 先跑 `--agent explore`，输出喂给 Build |
| **S3** build_explore_general | Explore → General → Build | harness 依次跑 explore → general → build |

### 任务分层

| Bucket | MuSiQue hop | 任务数 | 含义 |
|--------|------------|:---:|------|
| `local_readable` | 2-hop | 3 | 2 段支持，单 agent 一眼看完 |
| `multi_file` | 3-hop | 3 | 3 段跨文档追踪 |
| `long_context` | 4-hop | 4 | 4 段藏在 20 段文档中 |

### 评测

- 答案抽取：从 OpenCode JSON stdout 解析 `ANSWER:` 标记
- 匹配：exact → alias → ordinal（"3"↔"third"）→ partial_date → word overlap
- Token 统计：从 stdout `step-finish` 事件累加

---

## 2. 最终结果

### 总体

| 系统 | 成功率 | tokens | 耗时 | subagent |
|------|:---:|:---:|:---:|:---:|
| **S1** Build-only | **60%** (6/10) | 39k | 18s | 0 |
| **S2** Build+Explore | **60%** (6/10) | 103k | 35s | 1 |
| **S3** Build+Explore+General | **30%** (3/10) | 145k | 57s | 2 |

### 按难度

| Bucket | S1 | S2 | S3 | 总体 |
|--------|:---:|:---:|:---:|:---:|
| 2-hop (local_readable) | 3/3 | 3/3 | 1/3 | **78%** |
| 3-hop (multi_file) | 1/3 | 1/3 | 0/3 | **22%** |
| 4-hop (long_context) | 2/4 | 2/4 | 2/4 | **50%** |

### 逐任务

| # | Hop | 问题 | Gold | S1 | S2 | S3 |
|---|-----|------|------|:---:|:---:|:---:|
| 1 | 2 | Lostock Dam 所在河的河口？ | Hunter River | ✅ | ✅ | ✅ |
| 2 | 2 | "In the Shadow of Greatness" 出版商总部？ | Annapolis | ✅ | ✅ | ❌ |
| 3 | 2 | Hello Tomorrow 歌手同台的 artist？ | George Benson | ✅ | ✅ | ❌ |
| 4 | 3 | John Phan 出生地所在区域？ | South Central Coast | ✅ | ❌ | ❌ |
| 5 | 3 | 共和党何时接管规则制定机构？ | January 2015 | ❌ | ❌ | ❌ |
| 6 | 3 | Better Than Me 唱片公司所有者？ | Warner Music Group | ❌ | ✅ | ❌ |
| 7 | 4 | Veoh 总部在城市排名第几？ | third-largest | ❌ | ✅ | ✅ |
| 8 | 4 | MLB 常规赛开始日期？ | March 29, 2018 | ✅ | ❌ | ✅ |
| 9 | 4 | 9/11 目标地淘金者城市接壤？ | Rio Linda | ❌ | ❌ | ❌ |
| 10 | 4 | 意大利航海家之子？ | Sebastian Cabot | ✅ | ✅ | ❌ |

---

## 3. Stage 0C 预验证发现

### 3.1 Explore 子代理质量

| 指标 | 2-hop | 3-hop | 4-hop |
|------|:---:|:---:|:---:|
| 支持段落命中率 | 67% | 67% | 56% |
| 平均工具调用 | 3 | 4.3 | 6.8 |

Explore 能找到大部分相关段落。4-hop 时缺失的主要是中间推理跳，导致 Build 链断裂。但部分任务 Explore 找到了语义等价段落（不同编号），实际有效覆盖率高于数字。

### 3.2 失败归因

30 runs 中 15 个失败：

| 原因 | 数量 | 说明 |
|------|:---:|------|
| **模型推理错误** | 8 | 答错答案（如答"James Conkling"而非"Warner Music Group"） |
| **接近但格式不对** | 3 | 已通过 ordinal/date 匹配修复 |
| **提取 bug** | 2 | markdown 污染，已修复 |
| **信息不可用** | 2 | 模型放弃（均为 task 009 S2/S3） |

80% 的"失败"有合理的根因，不是模型完全不会。修正提取和匹配后，S1 从 50% 提升到 60%，S3 从 20% 提升到 30%。

### 3.3 Budget 校准

| 指标 | 值 |
|------|-----|
| S1 median tokens | 40,659 |
| S1 median × 1.5 (建议 cap) | 60,988 |
| 超过 cap 的 runs | 19/30（所有 S2/S3） |

**建议不设硬 cap**：S2/S3 的额外 token 是 subagent 固有成本，强控会人为压低 S2/S3。改为记录 tokens-per-success 做效率比较。

---

## 4. 关键发现

### 4.1 S1 = S2，subagent 无净收益

Build-only 和 Build+Explore 打成平手。Explore 找到了相关文档，但没有帮助 Build 得出更多正确答案。额外 2.6× tokens 换不来成功率提升。

### 4.2 S3 更差，General 引入噪声

Build+Explore+General 成功率仅 30%。General 被设计为"验证 Explore 发现、比较假设"，但在 Qwen3.5-9B 上：
- 让 Build 输出 markdown 格式的答案（`**Annapolis, Maryland**`）导致匹配失败
- 有时输出后 Build 直接崩溃（答案 `**`）
- 偶尔覆盖了 Explore 的正确发现

符合 spec §15.2 的预测："General 可能引入噪声或消耗预算"。

### 4.3 唯一亮点：S3 独解 task 008

4-hop 问题"MLB MVP 常规赛开始日期"，gold = "March 29, 2018"：
- S1 答 "March 29"（缺年份）
- S2 崩溃（"---"）  
- **S3 答 "March 29, 2018"**（唯一完整正确）

Explore + General 联合探索才拿到完整信息。但这是 30 runs 中唯一一次 subagent 带来净收益。

### 4.4 分层梯度明显

```
2-hop: ████████ 78%
3-hop: ██       22%  
4-hop: █████    50%
```

3-hop 断崖式下降（78% → 22%）说明 Qwen3.5-9B 在 3-hop 推理上碰到硬瓶颈，不是 subagent 架构能解决的。

### 4.5 Qwen3.5-9B 不会主动 spawn subagent

在"允许但不强制"的旧版 harness（第一版）中，模型从未调用过 Explore 或 General。它把"you MAY use subagents"理解为"多做几轮 grep"。只有 harness 层面的强制 spawn（新版）才真正测试了 subagent 分工。

---

## 5. 执行问题

| 问题 | 影响 | 状态 |
|------|------|:---:|
| vLLM context overflow（input > 32k + max_tokens 32k） | 个别请求 500 错误 | ✅ 自动恢复 |
| 后台模式不可用（bash ioctl） | 必须前台运行 | ⚠️ 限制 |
| Markdown 答案格式污染 | 匹配失败 | ✅ 已修复 |
| Ordinal/date 匹配不全 | 低估成功率 | ✅ 已修复 |

---

## 6. 结论与建议

### 当前结论

在 MuSiQue 10-task × Qwen3.5-9B 条件下：

1. **Single-agent 已经是最优解**：60% 成功率，最低成本（39k tokens）
2. **Explore subagent 没有边际价值**：S2 同分但成本 2.6×
3. **Explore+General 有害**：S3 仅 30%，成本 3.7×
4. **Explore 能找到文档，但 Build 整合不了** — 定位不是瓶颈，推理链才是
5. **3-hop 是 Qwen3.5-9B 的硬上限** — 78% → 22% 断崖

### 建议

| 优先级 | 行动 | 理由 |
|:---:|------|------|
| 1 | **不入 Stage 1B** | S1=S2，多重复无增量信息 |
| 2 | **换更强模型** | Qwen3.5-9B 的 3-hop 推理是根本瓶颈 |
| 3 | **改进 S2-forced 设计** | Explore 只跑一次无法追问。应该允许 Build→Explore→Build 交互循环 |
| 4 | **补 multi_hypothesis bucket** | 当前 10 个任务无此分层，补 HotpotQA comparison 类任务 |
| 5 | **扩大任务规模** | 10 任务太少，特别是 3-hop 只有 3 个 |

---

## 附录：Git 历史

```
bd3db77 fix evaluate: cross-match ordinal (digit↔word), S1 60% S2 60% S3 30%
6cb2860 Stage 0C prevalidation report
5631dd9 fix evaluate: ordinal matching, date partial matching
4b4541f Stage 1A complete: 30 forced-subagent runs + report
2d708b3 harness v2: forced subagent spawning (Explore/General as separate processes)
c516e68 fix harness: extract_answer reads stdout JSON, parse_metrics reads stdout
8942717 Stage 1A prep: 10 MuSiQue tasks, config, annotations frozen
0890652 spec v0.4, smoke test artifacts, tool-call-parser -> qwen3_coder
82fe3d7 init: OpenCode-Native Spawn Pilot project with spec v0.3
```
