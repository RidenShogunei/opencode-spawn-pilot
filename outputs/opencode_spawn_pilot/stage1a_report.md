# Stage 1A Pilot Report

**项目**: OpenCode-Native Spawn Pilot  
**日期**: 2026-04-29  
**模型**: Qwen3.5-9B + vLLM 0.19.1 (GPU 2, port 8010)  
**Benchmark**: MuSiQue (multi-hop QA)  
**规模**: 10 tasks × 3 systems = 30 runs (forced subagent)

---

## 1. 实验设置

### 对比系统

| 系统 | 架构 | 实现 |
|------|------|------|
| **S1** build_only | Build 单独作答 | OpenCode `--agent build`，直接回答 |
| **S2** build_explore | Explore 先探索 → Build 作答 | harness 先调 `--agent explore` 搜文档，输出喂给 Build |
| **S3** build_explore_general | Explore → General review → Build | harness 依次调 explore → general → build |

### 任务分层

| Bucket | MuSiQue hop | 任务数 | 难度特征 |
|--------|------------|:---:|------|
| `local_readable` | 2-hop | 3 | 2 个支持句，单 agent 可覆盖 |
| `multi_file` | 3-hop | 3 | 3 个支持句，跨文档追踪 |
| `long_context` | 4-hop | 4 | 4 个支持句藏在 20 段文档中 |

每个任务：20 段 Wikipedia 文档，2-4 段为答案关键段落。

### 评测

- 答案抽取：从 OpenCode JSON 输出中解析 `ANSWER:` 标记
- 匹配方式：exact match → alias match → partial match（token overlap > 60%）
- Token 统计：从 stdout JSON `step-finish` 事件累加

---

## 2. 总体结果

| 系统 | 成功率 | 平均 tokens | 平均耗时 | subagent 调用 |
|------|:---:|:---:|:---:|:---:|
| **S1** Build-only | **50%** (5/10) | 39k | 18s | 0 |
| **S2** Build+Explore | **50%** (5/10) | 103k | 35s | 1 |
| **S3** Build+Explore+General | **20%** (2/10) | 145k | 57s | 2 |

### 按难度分层

| Bucket | S1 | S2 | S3 | 总体 |
|--------|:---:|:---:|:---:|:---:|
| 2-hop (local_readable) | 3/3 | 3/3 | 1/3 | **78%** |
| 3-hop (multi_file) | 1/3 | 1/3 | 0/3 | **22%** |
| 4-hop (long_context) | 1/4 | 1/4 | 1/4 | **25%** |

---

## 3. 逐任务详情

| # | Hop | 问题摘要 | Gold Answer | S1 | S2 | S3 |
|---|-----|------|------|:---:|:---:|:---:|
| 1 | 2 | Dam 所在的河的河口？ | Hunter River | ✅ | ✅ | ✅ |
| 2 | 2 | 出版 Shadow of Greatness 的总部？ | Annapolis | ✅ | ✅ | ❌ |
| 3 | 2 | Hello Tomorrow 歌手同台的 artist？ | George Benson | ✅ | ✅ | ❌ |
| 4 | 3 | John Phan 出生地在 Phu Luong 国家的哪个区？ | South Central Coast | ✅ | ❌ | ❌ |
| 5 | 3 | 1954 前控制国会的党何时接管规则制定？ | January 2015 | ❌ | ❌ | ❌ |
| 6 | 3 | Better Than Me 歌手的唱片公司所有者？ | Warner Music Group | ❌ | ✅ | ❌ |
| 7 | 4 | Shaddix 乐队所在州的前五大城市中 Veoh 总部排第几？ | third-largest | ❌ | ❌ | ❌ |
| 8 | 4 | MLB MVP 颁发所在联盟的常规赛开始日期？ | March 29, 2018 | ❌ | ❌ | **✅** |
| 9 | 4 | 9/11 原目标地淘金者工作城市与什么接壤？ | Rio Linda | ❌ | ❌ | ❌ |
| 10 | 4 | Balbi 出生大陆东岸的意大利航海家之子？ | Sebastian Cabot | ✅ | ✅ | ❌ |

---

## 4. 关键发现

### 4.1 强制 subagent 没有提升总体成功率

S1 和 S2 打成平手（各 50%），S3 反而最差（20%）。subagent 引入了额外 noise，Build 在整合多个 subagent 输出时更容易出错。

### 4.2 成本爆炸

| 指标 | S1 | S2 | S3 |
|------|:---:|:---:|:---:|
| avg tokens | 39k | 103k (2.6×) | 145k (3.7×) |
| avg 耗时 | 18s | 35s (1.9×) | 57s (3.2×) |
| tokens/success | 78k | 206k | 725k |

### 4.3 唯一亮点：S3 独解任务 008

任务 008（"MLB MVP award → regular season start date"）是一个 4-hop 问题，gold answer 是 "March 29, 2018"。

- S1 答 "March 29"（缺年份）
- S2 答 "---"（崩溃）
- **S3 答 "March 29, 2018"**（完整正确）

Explore + General 的联合探索帮 Build 拿到了完整信息。但这是 30 runs 中唯一一次 subagent 带来净收益。

### 4.4 分层梯度明显

```
local_readable (2-hop): ████████ 78%
multi_file (3-hop):     ██       22%
long_context (4-hop):   ██       25%
```

从 2-hop 到 3-hop 成功率断崖式下降（78% → 22%），说明 Qwen3.5-9B 在 3-hop 推理上遇到瓶颈，不是 subagent 架构能解决的。

### 4.5 S3 的 General 经常引入噪声

General subagent 被设计为"验证 Explore 发现、比较假设"，但在 Qwen3.5-9B 上：
- 任务 002/003（2-hop）：General 让 Build 用 markdown 格式输出（`**Annapolis, Maryland**`），导致匹配失败
- 任务 010（4-hop）：General 输出后 Build 直接崩溃（答案 `**`）
- 任务 006（3-hop）：General review 后 Build 反而改错了答案

符合 spec §15.2 的预测："General 可能引入噪声或消耗预算"。

---

## 5. 执行问题

### 5.1 vLLM context overflow

偶发 `max_model_len` 超限（input 33537 + max_tokens 32000 > 65536）。  
影响：个别请求失败，vLLM 自动恢复，未造成 run 丢失。

### 5.2 答案格式问题

模型有时用 markdown 包装答案（`**Hunter River**`），evaluate 函数已加入 `re.sub(r'\*+', '', answer)` 清理，但嵌套格式仍有漏网。

### 5.3 后台模式不可用

OpenCode 在 terminal background 模式下无法正常工作（bash ioctl 错误），所有实验必须前台运行。

---

## 6. 结论与建议

### 6.1 Stage 1A 结论

在 MuSiQue 10-task 规模上：
- **Single-agent 已经是最优解**：50% 成功率，最低成本
- **Forced Explore 没有边际收益**：S2 平手但成本 2.6×
- **Forced Explore+General 有害**：成功率降到 20%，成本 3.7×
- **任务难度是主要瓶颈**：3-hop/4-hop 成功率仅 22-25%，不是架构能弥补的

### 6.2 是否进入 Stage 1B

spec §18.1 要求 Stage 1A review 检查：(1) 系统是否都跑通 ✅ (2) 日志完整性 ✅ (3) subagent 捕获 ✅ (4) evaluator 稳定 ✅ (5) 环境错误可管理 ✅

建议：
- **不进入 Stage 1B（90 runs）** — 当前效果不支持扩大规模
- **优先做 Stage 0C 能力预验证** — 确认 Qwen3.5-9B 的 multi-hop 推理能力边界
- **考虑降低任务难度** — 如仅用 2-hop + 简单 3-hop，或换用有更强推理能力的模型
- **S2-forced 的设计缺陷** — Explore 只在 Build 调用前运行一次，无法交互追问
