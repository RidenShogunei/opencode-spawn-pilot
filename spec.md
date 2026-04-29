# OpenCode-Native Spawn Mechanism Spec

版本：v0.5
上一版本：v0.4
变更类型：研究对象与机制假设重构
主题：用 Qwen3.5-9B + vLLM + OpenCode 在 MuSiQue multi-hop QA 上验证 forced subagent spawning 的机制链条。

---

## 0. 当前定位

v0.5 不再是 SWE-bench coding-agent pilot。

当前实验定位为：

> Stage 1B Mechanism Confirmation：在 MuSiQue multi-hop QA 上，用 forced subagent spawning 验证 subagent 是否带来 evidence discovery 增益、primary agent 是否存在 integration bottleneck，以及 structured evidence table 是否能降低整合失败。

也就是说，本阶段不试图证明 multi-agent workflow 直接优于 single-agent，而是验证更细的机制链：

```text
spawn Explore
→ evidence discovery / coverage gain
→ Build integration bottleneck
→ General may introduce noise
→ structured evidence table may reduce integration failure
```

---

## 1. 研究目标

本实验研究 forced subagent spawning 在 multi-hop QA 任务中的机制性影响。

核心问题：

1. Explore 是否能比 Build-only 找到更多支持证据？
2. Explore 找到的证据是否能被 Build 正确整合？
3. General review 是否帮助整合，还是引入额外噪声？
4. 将 Explore 输出改为结构化 evidence table，是否能降低 integration error？
5. 如果 subagent 没有提升最终 accuracy，失败主要发生在 evidence discovery、evidence representation、reasoning integration 还是 answer formatting？

本阶段目标不是训练 RL，也不是优化最终 benchmark 分数，而是确认后续 spawn policy 研究所需的机制信号。

---

## 2. 背景与动机

v0.4 原始设想是 SWE-bench coding tasks。但 Stage 0C 的 MuSiQue forced-decomposition probe 已经暴露出一个关键现象：

```text
Explore 能找到部分支持证据，但 Build 未必能利用这些证据得出正确答案；General 有时进一步引入噪声。
```

因此，v0.5 暂时将任务域切换为 MuSiQue multi-hop QA，原因是：

1. MuSiQue 的支持段落和 hop 结构更容易量化 evidence discovery。
2. QA 任务比 coding task 更快，适合验证机制链。
3. 可以直接区分 evidence recall 与 final answer accuracy。
4. 可以构造 structured evidence table，测试输出表示形式对 integration 的影响。
5. Stage 0C 已经跑通 OpenCode + vLLM + forced subagent pipeline。

本阶段的研究问题从：

```text
Does spawning subagents improve task success?
```

转为：

```text
When subagents discover useful evidence, why does that evidence fail to improve final answers, and can structured integration fix it?
```

---

## 3. 已验证环境

### 3.1 vLLM / 模型配置

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

### 3.2 OpenCode 配置

```yaml
framework: OpenCode 1.3.6
provider: openai_compatible
base_url: http://localhost:8010/v1
model: qwen35-9b
mode: foreground only
```

注意：OpenCode background mode 存在 bash ioctl 问题，当前实验必须前台运行。

### 3.3 Tool parser 配置

当前报告中出现过 `qwen3_coder parser` 表述。如果实际 vLLM 启动仍使用 Hermes parser，则本 spec 统一记录为：

```yaml
tool_call_parser: hermes
```

如果实际启动已切换为 `qwen3_coder parser`，必须同步更新 launch command、config 和 report。禁止在不同文档中混用 parser 名称。

### 3.4 Context overflow 风险

已观察到偶发：

```text
input tokens + max_tokens > max_model_len
```

v0.5 不把该问题作为阻塞项，但必须记录每次 context overflow。建议在 harness 或 proxy 层限制 per-request `max_tokens`：

```yaml
build_max_tokens: 8192
explore_max_tokens: 4096
general_max_tokens: 4096
```

如果 OpenCode 仍固定发送约 32000 `max_tokens`，必须在 report 中标记为 unresolved configuration limitation。

---

## 4. 任务域

### 4.1 Benchmark

```yaml
benchmark: MuSiQue
format: multi-hop QA
context: 20-paragraph document pool per question
supporting_evidence: 2–4 support paragraphs per question
answer_type: short answer
```

### 4.2 任务规模

当前 Stage 1B 使用：

```text
10 tasks × 4 systems = 40 runs
```

不做 seed 扩展。

原因：

1. 本阶段是机制确认，不是统计显著性主实验。
2. Stage 0C 已经显示多重复 forced pipeline 的边际信息有限。
3. 当前重点是比较系统间机制指标 M1–M5，而不是估计低方差 accuracy。

### 4.3 任务分层

| Bucket           | MuSiQue hop | 任务数 | 含义                       |
| ---------------- | ----------: | --: | ------------------------ |
| `local_readable` |       2-hop |   3 | 2 段支持，Build-only 有机会直接覆盖 |
| `multi_file`     |       3-hop |   3 | 3 段跨文档追踪                 |
| `long_context`   |       4-hop |   4 | 4 段支持藏在 20 段文档中          |

当前 10 个任务没有 `multi_hypothesis` bucket。后续如扩展，可补 HotpotQA comparison 或 MuSiQue 中多候选实体干扰更强的任务。

---

## 5. 对比系统

v0.5 使用四个系统。

### 5.1 S1: Build-only

```text
system = build_only
```

实现：

```bash
opencode run --agent build
```

约束：

* 禁止 subagent。
* Build 直接阅读问题和 20 段上下文。
* Build 输出最终答案。

作用：single-agent baseline。

### 5.2 S2: Build + Explore

```text
system = build_explore
```

实现：

```text
Explore → Build
```

流程：

1. Harness 先调用 Explore。
2. Explore 搜索 / 阅读文档并输出相关证据或推理线索。
3. Harness 将 Explore 输出喂给 Build。
4. Build 输出最终答案。

作用：测试非结构化 Explore 输出是否能提升 evidence coverage 和 final accuracy。

### 5.3 S3: Build + Explore + General

```text
system = build_explore_general
```

实现：

```text
Explore → General → Build
```

流程：

1. Explore 找证据。
2. General review Explore 的发现、比较假设、尝试补全推理链。
3. Build 基于 Explore + General 输出最终答案。

作用：测试 General review 是否帮助整合，或是否引入噪声。

### 5.4 S4: Build + Explore Evidence Table

```text
system = build_explore_table
```

实现：

```text
Explore → structured evidence table → Build
```

流程：

1. Harness 调用 Explore。
2. Explore 必须输出 markdown evidence table。
3. Build 只能基于结构化证据表和原问题作答。
4. Build 输出最终答案。

作用：测试 structured output representation 是否降低 integration error。

---

## 6. S4 Evidence Table 规范

### 6.1 TABLE_PROMPT 目标

Explore 在 S4 中不允许输出长篇自由分析。它必须输出结构化表格。

推荐格式：

```markdown
| Evidence ID | Hop | Paragraph ID | Key Entity | Evidence Sentence | Supports |
|---|---:|---|---|---|---|
| E1 | 1 | P03 | ... | ... | identifies starting entity |
| E2 | 2 | P11 | ... | ... | links entity to next hop |
| E3 | 3 | P07 | ... | ... | supports final answer |
```

### 6.2 表格字段定义

| 字段                | 含义              |
| ----------------- | --------------- |
| Evidence ID       | `E1`, `E2`, ... |
| Hop               | 该证据服务于第几跳推理     |
| Paragraph ID      | 段落编号，例如 `P03`   |
| Key Entity        | 该证据中的关键实体       |
| Evidence Sentence | 原文中的关键句，尽量短     |
| Supports          | 该证据支持的推理作用      |

### 6.3 Build 使用约束

Build 收到 evidence table 后必须：

1. 优先基于 evidence table 作答。
2. 如果表格证据不足，明确说明 missing hop。
3. 最终答案必须用以下格式输出：

```text
ANSWER: <short answer>
```

不要输出 markdown 粗体、项目符号或解释性长段作为最终答案。

---

## 7. 评测与答案解析

### 7.1 答案抽取

从 OpenCode JSON stdout 中解析：

```text
ANSWER:
```

后面的短答案。

如果不存在 `ANSWER:`，则尝试 fallback extraction，但必须记录：

```json
"answer_extraction_mode": "fallback"
```

### 7.2 答案 normalization

必须执行：

1. 去除 markdown 标记，例如 `**answer**`。
2. 去除反引号、引号、项目符号。
3. lowercase。
4. collapse whitespace。
5. normalize punctuation。
6. normalize articles。
7. 支持 alias match。
8. 支持 ordinal match，例如 `3` ↔ `third`。
9. 支持 partial date，但日期关键字段不能缺失。

日期规则：

* Gold 为 `March 29, 2018` 时，`March 29` 不算正确。
* Gold 只要求月份或年份时，按 gold granularity 判断。

地点规则：

* `Annapolis, Maryland` 可以匹配 `Annapolis`，如果 gold alias 允许或 contains relation 无歧义。

### 7.3 成功判定顺序

```text
exact match
→ alias match
→ ordinal match
→ date-aware match
→ token overlap / word overlap
```

Word overlap 只能作为最后 fallback，且必须记录。

---

## 8. 成本与预算

### 8.1 当前阶段预算策略

Stage 1B mechanism confirmation 使用 natural budget。

理由：

1. 当前重点是观察 forced subagent 的自然成本和机制指标。
2. S2/S3/S4 的额外 token 是 delegation / representation 的固有成本。
3. 本阶段不追求严格 equal-compute 架构对比。

### 8.2 必须报告的成本指标

每个 run 必须记录：

* input tokens；
* output tokens；
* total tokens；
* wall-clock time；
* subagent call count；
* tokens per correct answer。

### 8.3 正式比较时的补充说明

如果后续要做 architecture efficiency comparison，必须重新引入：

```text
equal generation budget
```

或报告 capped / uncapped 两套结果。

本阶段 natural budget 的结论不能直接外推为 equal-budget 下的架构优劣。

---

## 9. 核心指标 M1–M5

v0.5 的主指标不是单纯 accuracy，而是机制指标。

### M1: Evidence Recall

```text
M1 = Explore 找到的 gold supporting paragraph 比例
```

实现：

* 从 Explore stdout 解析 paragraph IDs。
* 与任务 gold support paragraph IDs 对比。
* 对 S2/S3/S4 计算。

报告：

* overall evidence recall；
* per-hop evidence recall；
* per-bucket evidence recall。

### M2: Missing Hop Coverage

```text
M2 = Explore 是否覆盖了 S1 baseline 未覆盖 / 未使用的关键 hop
```

用途：判断 subagent 是否提供了 Build-only 缺失的信息。

实现：

* 以 S1 baseline 为参照。
* 如果 S1 错误，而 Explore 找到 gold support paragraph，则计为 potential missing-hop coverage。
* 进一步标记该证据是否进入 Build 输入。

### M3: Integration Error Rate

```text
M3 = Explore 找到必要证据，但 Build 最终仍答错的比例
```

定义：

一个 run 满足以下条件时，计为 integration error：

1. Explore 找到至少一个关键 gold support paragraph；
2. Explore 输出被传给 Build；
3. Build 最终答案错误；
4. 错误不是由 answer extraction bug 或 evaluator bug 导致。

对 S2/S3/S4 分别计算。

### M4: Tokens Per Correct

```text
M4 = total tokens / number of correct answers
```

如果某系统 correct = 0，则报告为 undefined 或 inf。

作用：衡量机制收益是否抵消 delegation 成本。

### M5: Explore Found, Build Failed

```text
M5 = Explore 找到关键证据但 Build 失败的 case 数和比例
```

这是本研究最关键的 case-study 指标。

报告时必须列出代表性样例：

* task id；
* found evidence；
* Build final answer；
* gold answer；
* failure note。

---

## 10. 预注册假设

| 假设 | 预期方向                                          | 解释                             |
| -- | --------------------------------------------- | ------------------------------ |
| H1 | S2 的 M1 > S1 implicit evidence coverage       | Explore 应提升 evidence discovery |
| H2 | S2 accuracy 不一定 > S1                          | Evidence discovery 不自动转化为答案正确  |
| H3 | S3 accuracy ≤ S2                              | General 可能引入噪声或覆盖正确线索          |
| H4 | S4 M3 < S2 M3                                 | 结构化 evidence table 应降低整合失败     |
| H5 | S4 tokens per correct < S2/S3 或 accuracy > S2 | 如果结构化有效，应改善效率或正确率              |
| H6 | M5 存在非零案例                                     | 存在“找到证据但 Build 失败”的机制性断层       |

强结果模式：

```text
S2/S4 evidence recall 高
+ S2 final accuracy 无提升
+ S4 integration error 低于 S2
```

这将支持：

```text
spawn 的关键问题不是是否调用 subagent，而是如何表示、压缩、验证和整合 subagent 输出。
```

---

## 11. 失败归因

### 11.1 Failure Types

```text
answer_wrong
missing_evidence
integration_error
general_noise
format_error
extraction_bug
context_overflow
model_refusal_or_abstention
environment_error
```

### 11.2 标签定义

#### answer_wrong

模型给出明确答案，但答案与 gold 不匹配，且不是 evidence 缺失或格式问题。

#### missing_evidence

Explore / Build 没有找到关键 supporting paragraph。

#### integration_error

Explore 找到了关键 evidence，但 Build 没有正确使用。

#### general_noise

General 输出导致正确 evidence 被覆盖、推理方向改变或最终答案恶化。

#### format_error

答案语义正确但格式导致解析失败，例如 markdown 包装、额外解释、答案粒度错误。

#### extraction_bug

Harness 未能正确抽取答案。

#### context_overflow

vLLM 因 context length 或 max_tokens 问题报错。

#### model_refusal_or_abstention

模型输出 `---`、空答案、放弃或无法回答。

#### environment_error

OpenCode、vLLM、文件、stdout 解析等基础设施异常。

---

## 12. Harness v3 要求

### 12.1 系统支持

`harness.py` 必须支持：

```bash
python scripts/harness.py s1
python scripts/harness.py s2
python scripts/harness.py s3
python scripts/harness.py s4
python scripts/harness.py
```

其中：

* `s1` 只跑 Build-only。
* `s2` 只跑 Build+Explore。
* `s3` 只跑 Build+Explore+General。
* `s4` 只跑 Build+Explore Table。
* 不带参数时跑全部系统。

### 12.2 Resume / 去重

Harness 必须按 `(task_id, system)` 去重。

如果已有 run 结果，默认 skip，除非显式传入：

```bash
--force
```

如当前尚未实现 `--resume` / `--force`，则运行前必须手动备份 outputs，避免覆盖。

### 12.3 Metrics functions

Harness v3 已实现或必须实现：

```python
compute_m1_evidence_recall()
compute_m2_missing_hop_coverage()
compute_m3_integration_error_rate()
compute_m4_tokens_per_correct()
compute_m5_explore_found_build_failed()
compute_post_hoc_metrics()
print_metrics_summary()
```

### 12.4 Post-hoc metrics

`compute_post_hoc_metrics()` 在所有 runs 完成后运行。

它应填充：

* M2；
* M3；
* M4；
* M5；
* per-hop breakdown；
* per-system summary。

如果只完成 S1，则 post-hoc metrics 应 graceful skip S2/S3/S4 相关指标，而不是报错。

---

## 13. 输出文件结构

建议输出：

```text
outputs/musique_spawn_mechanism/
├── spec.md
├── config.json
├── tasks.jsonl
├── runs.jsonl
├── raw_logs/
│   └── <task_id>_<system>.log
├── parsed_answers.jsonl
├── evidence_tables/
│   └── <task_id>_s4.md
├── metrics_summary.json
├── metrics_summary.md
├── per_hop_breakdown.csv
├── failure_cases.jsonl
└── report.md
```

### 13.1 Run-level schema

```json
{
  "run_id": "...",
  "task_id": "...",
  "system": "build_only | build_explore | build_explore_general | build_explore_table",
  "hop": 2,
  "bucket": "local_readable | multi_file | long_context",
  "question": "...",
  "gold_answer": "...",
  "predicted_answer": "...",
  "correct": true,
  "answer_extraction_mode": "answer_marker | fallback | failed",
  "match_type": "exact | alias | ordinal | date | overlap | none",
  "token_usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0
  },
  "runtime_sec": 0,
  "subagent_calls": 0,
  "explore_found_paragraph_ids": [],
  "gold_support_paragraph_ids": [],
  "m1_evidence_recall": 0.0,
  "m2_missing_hop_coverage": false,
  "m3_integration_error": false,
  "m5_explore_found_build_failed": false,
  "failure_type": "none | answer_wrong | missing_evidence | integration_error | general_noise | format_error | extraction_bug | context_overflow | model_refusal_or_abstention | environment_error",
  "notes": ""
}
```

---

## 14. 执行计划

v0.5 从 Stage 1B 开始。原 v0.4 中针对 coding harness 的 Step 1–8 不再适用。

### Step 1: 跑 S1 baseline

```bash
python scripts/harness.py s1
```

检查：

1. 10/10 runs 是否完成；
2. 每个 task 是否抽取到答案；
3. S1 accuracy 是否接近 Stage 0C 的 60%；
4. token 统计是否非空；
5. runs.jsonl 是否写入正常；
6. 只有 S1 时 post-hoc metrics 是否 graceful skip。

### Step 2: 跑 S2

```bash
python scripts/harness.py s2
```

检查：

1. Explore stdout 是否可解析 paragraph IDs；
2. M1 evidence recall 是否可计算；
3. S2 accuracy 是否复现 Stage 0C 的大致趋势；
4. M5 是否出现非零案例。

### Step 3: 跑 S3

```bash
python scripts/harness.py s3
```

检查：

1. General 是否引入答案变化；
2. 是否出现 General 覆盖 Explore 正确信息；
3. general_noise case 是否可识别。

### Step 4: 跑 S4

```bash
python scripts/harness.py s4
```

检查：

1. Evidence table 是否按 markdown 表格输出；
2. Build 是否使用 evidence table；
3. S4 的 M3 是否低于 S2；
4. S4 的 tokens per correct 是否优于 S2/S3，或 accuracy 是否更高。

### Step 5: 汇总 M1–M5

运行：

```bash
python scripts/harness.py --metrics-only
```

如果尚未实现 `--metrics-only`，则直接调用 metrics summary 函数或通过全量 harness 触发，但必须避免重复跑已有结果。

### Step 6: 写 Stage 1B report

报告必须包含：

1. S1/S2/S3/S4 accuracy；
2. tokens per correct；
3. M1 evidence recall；
4. M2 missing-hop coverage；
5. M3 integration error rate；
6. M5 case list；
7. per-hop breakdown；
8. S4 是否降低 integration error；
9. 对后续 spawn policy 的含义。

---

## 15. 成功标准

Stage 1B 成功不要求 S4 一定显著提升 accuracy。

满足以下任意组合即可认为机制实验有价值：

### 强成功

```text
S4 accuracy > S2
and S4 M3 < S2 M3
and M5 > 0
```

解释：结构化证据表降低 integration failure，并转化为最终准确率提升。

### 中等成功

```text
S4 M3 < S2 M3
but S4 accuracy ≈ S2
```

解释：结构化整合减少部分错误，但可能受模型推理能力或答案抽取限制。

### 机制成功

```text
S2/S4 M1 高
and M5 > 0
```

解释：确认 evidence discovery 与 final answer 之间存在断层，即 spawn 的优化重点应包括 integration protocol。

### 负结果但有价值

```text
S2/S4 M1 不高
```

解释：Explore 本身没有稳定找到证据，问题主要在 evidence discovery，而不是 integration。

或：

```text
S4 M3 ≥ S2 M3
```

解释：简单 evidence table 不足以解决 integration，需要更强的 verifier、citation-grounded answer 或 stepwise reasoning controller。

---

## 16. 后续研究分支

根据 Stage 1B 结果，后续分支如下。

### Branch A: S4 明显优于 S2

继续研究 structured delegation：

* evidence table；
* citation-grounded answer；
* verifier；
* forced evidence citation；
* Build answer must cite evidence IDs。

### Branch B: S4 降低 M3 但不提高 accuracy

研究模型推理瓶颈：

* 更强本地模型；
* stepwise chain construction；
* hop-by-hop controller；
* answer verifier。

### Branch C: S3 明显有害

默认禁用 General，只在封闭任务中调用：

* hypothesis comparison；
* answer verification；
* contradiction detection。

### Branch D: Explore evidence recall 不足

优化 Explore：

* 多 query decomposition；
* hop-by-hop retrieval；
* paragraph reranking；
* support sentence extraction。

---

## 17. 当前主线表述

v0.5 的主线不是 “multi-agent beats single-agent”。

更准确的主线是：

> Forced spawning does not automatically improve final performance. The important question is whether spawned agents increase evidence coverage, and whether primary agents can reliably integrate that evidence. Structured subagent outputs may be necessary for spawn to become useful.

中文表述：

> 强制 spawn subagent 不会自动提升最终准确率。真正值得优化的是：subagent 是否带来证据覆盖增益、这些证据是否能被主 agent 稳定整合，以及结构化输出能否降低整合失败。

一句话总结：

**用 Qwen3.5-9B + vLLM + OpenCode 在 MuSiQue multi-hop QA 上比较 Build-only、Explore→Build、Explore→General→Build、Explore→Evidence Table→Build 四种系统，通过 M1–M5 指标验证 evidence discovery、integration failure、General noise 和 structured integration 的机制链条，为后续 spawn policy 和 subagent output protocol 优化提供依据。**
