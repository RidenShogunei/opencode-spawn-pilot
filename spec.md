# OpenCode-Native Spawn Pilot Spec

版本：v0.4
上一版本：v0.3
变更类型：Stage 0 验证后执行版修订
主题：用本地 Qwen3.5-9B + vLLM 驱动 OpenCode，评测 single-agent vs subagent workflow，并为后续 spawn policy / RL 优化提供轨迹数据。

---

## 变更记录

| 版本        | 变更项           | 说明                                                               |
| --------- | ------------- | ---------------------------------------------------------------- |
| v0.3→v0.4 | §3 模型配置       | 固化 Stage 0 已验证的 vLLM / Qwen / OpenCode 配置                        |
| v0.3→v0.4 | §3.4 部署方案     | 默认从双实例方案改为已验证的单实例方案：GPU 2，port 8010                              |
| v0.3→v0.4 | §3.5 新增       | 明确 thinking 关闭、Hermes tool parser、OpenAI-compatible endpoint 配置  |
| v0.3→v0.4 | §4 Stage 0 状态 | 将 Stage 0 infrastructure validation 标记为 completed                |
| v0.3→v0.4 | §4.3          | 将模型能力预验证从 infrastructure validation 中拆出，避免概念混淆                   |
| v0.3→v0.4 | §7 预算         | 明确 Equal generation budget，不再暗示 equal compute                    |
| v0.3→v0.4 | §9 日志         | 增加 step-level trajectory 和 subagent marginal information gain 字段 |
| v0.3→v0.4 | §11 失败归因      | 区分 test_error 与 environment_error，保留双人标注与 Cohen's κ              |
| v0.3→v0.4 | §17 主线表述      | 删除“第一个”这类过强表述，改为更稳健的研究贡献表述                                       |

---

## 1. 研究目标

本实验研究真实 coding-agent 框架中的 `spawn subagent` 操作是否能提升代码修复任务的成功率与成本效率。

核心问题不是证明 subagent 一定优于 single agent，也不是证明 subagent 一定存在目标错位，而是回答：

> 在代码任务中，什么时候应该 spawn subagent，spawn 哪类 subagent，subagent 的输出是否真的帮助 primary agent，失败主要发生在探索、执行、整合还是预算控制环节？

实验聚焦 OpenCode 原生 agent 机制，优先使用 OpenCode 内置 primary agents 与 subagents，而不是一开始手搓复杂多角色系统。

本实验全部使用本地推理：

```text
Qwen3.5-9B + vLLM 0.19.1 + OpenCode
```

不依赖外部 API。这既排除了 API cost 变量，也测试本地小模型在 spawn workflow 下的行为模式。

---

## 2. 背景与动机

此前探索性实验显示，spawned-agent delegation 并不是稳定有害，也不是稳定有益。subagent 有时能通过额外上下文和分工提升任务成功率，但也可能引入 recommendation drift、过度保守、动作语义混淆或 integration error。

现有 spawn / multi-agent 研究通常更关注 GPT-4 / Claude 级别强模型。一个重要空缺是：

> 在本地可部署的小模型，例如 9B 级模型上，spawn subagent 的效果如何？小模型是否也能从任务分工中受益？还是 spawn overhead 压倒收益？

因此，本实验将 spawn agent 视为一个需要优化的操作，研究如何在真实任务中学习：

1. 是否 spawn；
2. spawn 哪类 subagent；
3. 传递什么上下文；
4. 分配多少预算；
5. 如何整合 subagent 输出。

代码任务是第一阶段的合适场景，因为 coding-agent 工作流天然包含代码库探索、bug 定位、patch 生成、测试解释和 review 等子任务。

---

## 3. 实验框架与模型配置

### 3.1 主框架

第一阶段使用 OpenCode 原生 agent/subagent 机制。

OpenCode 中的基本角色包括：

* Primary agent：用户直接交互的主 agent，例如 Build / Plan。
* Subagent：由 primary agent 调用的专门 agent，例如 Explore / General。

第一版不手搓 Explorer / Implementer / Reviewer / Tester 四类角色，而是优先使用 OpenCode 原生结构。

### 3.2 使用 OpenCode 原生机制的原因

1. 更贴近真实 coding-agent 使用方式。
2. 避免实验被质疑为“自定义多 agent workflow”，而不是框架内真实 spawn 行为。
3. 先观察默认 subagent 机制在小模型上是否有价值，再决定是否设计 custom subagents 或 RL policy。
4. OpenCode 相对开源和可控，方便插入日志、预算、subagent call 记录和失败归因。

### 3.3 Model Specification

#### 推理引擎与硬件

```yaml
engine: vLLM 0.19.1
validated_gpu: 2
validated_port: 8010
available_hardware: NVIDIA A100 40GB class GPU
parallel_instances: optional, not Stage 1 default
```

#### 模型

```yaml
model_family: Qwen3.5-9B
served_model_name: qwen35-9b
local_path: /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/
architecture: Qwen3_5ForConditionalGeneration
max_position_embeddings: 262144
validated_max_model_len: 65536
```

#### 重要上下文长度结论

Stage 0 已验证：OpenCode 可能发送约：

```text
max_tokens ≈ 32000
system_prompt ≈ 769 tokens
```

因此，`max_model_len` 不能低于约 32768。正式实验使用：

```text
max_model_len = 65536
```

不要在 Stage 1/2 中降回 32768。

### 3.4 默认 vLLM 部署方案：单实例已验证配置

Stage 1 默认使用已经验证通过的单实例配置。

```yaml
validated_default:
  gpu: 2
  port: 8010
  base_url: http://localhost:8010/v1
  served_model_name: qwen35-9b
  max_model_len: 65536
  thinking_enabled: false
  tool_call_parser: hermes
```

vLLM 启动命令：

```bash
CUDA_VISIBLE_DEVICES=2 python -m vllm.entrypoints.openai.api_server \
    --model /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/ \
    --served-model-name qwen35-9b \
    --port 8010 \
    --max-model-len 65536 \
    --gpu-memory-utilization 0.85 \
    --enforce-eager \
    --tensor-parallel-size 1 \
    --enable-prefix-caching \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --default-chat-template-kwargs '{"enable_thinking": false}' \
    --max-num-seqs 4
```

### 3.5 Thinking 与 tool call 配置

Stage 0 已验证 Qwen3.5-9B 需要显式关闭 thinking。

CLI 调用使用：

```bash
--default-chat-template-kwargs '{"enable_thinking": false}'
```

Python API 调用使用等价参数：

```python
default_chat_template_kwargs={"enable_thinking": False}
```

Tool call 兼容配置必须包含：

```bash
--enable-auto-tool-choice --tool-call-parser hermes
```

这两个参数用于解决 OpenCode tool call 与 vLLM / Qwen 的兼容问题。

### 3.6 OpenCode endpoint 配置

OpenCode 通过 OpenAI-compatible API 连接本地 vLLM。

```yaml
provider: openai_compatible
base_url: http://localhost:8010/v1
api_key: not-needed
model: qwen35-9b
```

所有 primary agent 和 subagent 默认共用同一个 endpoint。不要在 Stage 1 中引入多 endpoint，除非单实例吞吐成为明确瓶颈。

### 3.7 Primary Agent 配置

Primary agent 使用 OpenCode 内置 Build agent。

```yaml
agent: Build
model: qwen35-9b
temperature: 0.0
system_prompt: OpenCode built-in Build prompt
endpoint: http://localhost:8010/v1
```

如果 OpenCode 支持可靠覆盖 per-turn generation cap，可设置：

```yaml
primary_per_turn_max_output_tokens: 8192
```

如果 OpenCode 实际请求仍发送约 32000 `max_tokens`，则不要假设上述限制已经生效。实验预算应由外部 harness 通过累计 output token 控制。

### 3.8 Subagent 配置

Subagent 使用 OpenCode 内置 Explore / General。

```yaml
agents:
  Explore:
    model: qwen35-9b
    temperature: 0.0
    endpoint: http://localhost:8010/v1
    permission: read_only
  General:
    model: qwen35-9b
    temperature: 0.0
    endpoint: http://localhost:8010/v1
    permission: bounded_subtask
```

如果 OpenCode 支持可靠覆盖 per-turn generation cap，可设置：

```yaml
subagent_per_turn_max_output_tokens: 4096
```

同样，最终以累计 output token 预算为准。

### 3.9 可选双实例部署方案

双实例部署仅作为吞吐优化，不作为 Stage 1 默认配置。

可选方案：

```text
GPU A: primary + Explore endpoint
GPU B: General endpoint or additional shared endpoint
```

启用条件：

1. 单实例 Stage 1A 跑通；
2. 日志显示 vLLM 队列成为主要瓶颈；
3. 双实例不会改变 agent 行为或 token accounting；
4. endpoint routing 可以被完整记录。

如果启用双实例，必须在 `runs.jsonl` 中记录每次请求使用的 endpoint、GPU id 和 model name。

---

## 4. Stage 0 状态与预验证

### 4.1 Stage 0A: Infrastructure validation status

当前状态：completed。

已验证事实：

1. vLLM 0.19.1 可以用 OpenAI-compatible endpoint 服务 Qwen3.5-9B。
2. 当前服务运行在 GPU 2。
3. 当前端口为 8010。
4. 对外 model name 为 `qwen35-9b`。
5. `max_model_len=65536` 已验证可满足 OpenCode 请求。
6. thinking 已通过 `default_chat_template_kwargs` 正确关闭。
7. `--enable-auto-tool-choice --tool-call-parser hermes` 已验证解决 tool call 兼容问题。

### 4.2 Stage 0B: Pipeline debug

如果 repo checkout、测试执行、patch capture、evaluator、token tracking 已经完成验证，则标记为 completed。否则在 Stage 1 前补齐。

Pipeline debug checklist：

* [ ] repo checkout 可复现
* [ ] task loading 可复现
* [ ] baseline failing test 可运行
* [ ] OpenCode 能调用本地 vLLM endpoint
* [ ] Build-only 能完整完成一个 run
* [ ] subagent call 能被捕获
* [ ] git diff 能保存为 patch
* [ ] evaluator 能运行并返回结构化结果
* [ ] token usage 能记录到 run-level log
* [ ] step-level trajectory 能写入 jsonl

### 4.3 Stage 0C: 模型能力预验证

Qwen3.5-9B 不是 Claude / GPT-4 级模型。在投入 Stage 2 之前，必须确认它至少有能力完成目标任务分布中的一部分任务。

验证项目：

| 项目                    | 方法                                          | 通过标准                                 |
| --------------------- | ------------------------------------------- | ------------------------------------ |
| 代码理解                  | 给 issue + repo，要求 model 定位 bug 文件，不要求 patch | top-3 candidate files 命中 ≥ 60% tasks |
| 代码生成                  | 给定 bug 位置和修复方案，要求生成 patch                   | patch 语法正确且可 apply                   |
| Tool use              | 验证 search、open、edit、test 等 OpenCode tools   | 工具调用语法正确 ≥ 80%                       |
| Instruction following | 禁止 subagent 时 Build 是否遵守                    | subagent 调用数为 0                      |
| 端到端                   | 在 2 个 simple tasks 上跑完整 S1                  | 至少 1/2 生成可 apply patch               |

如果预验证失败：

1. 不切换到 API 模型。本地小模型是实验核心设置。
2. 优先降低任务难度，例如 curated Python bug-fix 或小型 failing-test tasks。
3. 如果 tool use 明显失败，考虑切换到更适合代码/工具调用的本地模型，但必须记录为模型变更。
4. 保留失败报告，因为小模型在 spawn workflow 下的能力边界本身是实验发现。

---

## 5. 实验对象

### 5.1 任务类型

第一阶段任务为代码修复任务，优先使用：

* SWE-bench Lite / Verified 小子集；
* curated Python bug-fix tasks；
* 小型真实 repo 的 failing-test 修复任务。

每个任务必须具备可自动评估的 oracle，例如：

* regression tests pass；
* issue-specific tests pass；
* patch 能被 evaluator 判定为成功。

### 5.2 任务选择原则

任务集需要同时包含 single-agent-readable 和 multi-agent-favorable 场景，避免只构造对 multi-agent 有利的任务。

每个任务需要具备：

1. 明确 issue / bug 描述；
2. 可 checkout 的 repo；
3. 可复现的初始失败；
4. 可运行 evaluator；
5. 可捕获 final patch；
6. 任务难度不能全部过高，避免所有系统 success rate 接近 0。

### 5.3 初始规模与阶段设计

#### Stage 1A: Pilot sanity run

```text
10 tasks × 3 systems × 1 seed = 30 runs
```

目标：

1. 验证完整实验执行流程；
2. 检查日志字段是否完整；
3. 检查 evaluator 是否稳定；
4. 检查 subagent calls 是否能被完整捕获；
5. 发现系统性 instrumentation bug。

Stage 1A 通过后再进入 Stage 1B。

#### Stage 1B: Pilot repeated runs

```text
10 tasks × 3 systems × 3 seeds = 90 runs
```

目标：

1. 初步观察 single vs subagent 是否有方向性差异；
2. 验证 failure attribution 流程；
3. 估计 run-to-run variance；
4. 判断是否需要调整任务难度或预算。

#### Stage 2: Main Pilot

默认规模：

```text
30 tasks × 3 systems × 3 seeds = 270 runs
```

如果资源不足，可降为：

```text
30 tasks × 3 systems × 2 seeds = 180 runs
```

Stage 2 是第一版实验报告主体。

---

## 6. 任务分层与预注册

### 6.1 Difficulty buckets

每个任务标注一个主 difficulty bucket。

```text
local_readable | multi_file | long_context | multi_hypothesis
```

#### local_readable

相关上下文较短，1–2 个文件内即可解决。预期 Build-only 更稳或更便宜。

#### multi_file

需要跨 3–8 个文件定位和修改。subagent 可能通过探索分工获得收益。

#### long_context

候选上下文总量较大，单 agent 很难一次性覆盖全部相关文件或日志。subagent 可能通过上下文分片提升覆盖率。

#### multi_hypothesis

存在多个可能 bug source，需要并行探索不同假设。subagent 可能通过独立探索降低漏查风险。

### 6.2 Pre-run observable features

这些字段可以作为后续 spawn policy 的输入。

```json
{
  "task_id": "...",
  "repo": "...",
  "issue_length_tokens": 0,
  "initial_test_log_length_tokens": 0,
  "repo_file_count": 0,
  "repo_loc_estimate": 0,
  "initial_candidate_files_count": 0,
  "initial_search_results_count": 0,
  "known_failing_tests_count": 0
}
```

### 6.3 Post-hoc oracle features

这些字段只能用于分析，不能作为后续 policy 的在线输入。

```json
{
  "task_id": "...",
  "difficulty_bucket": "local_readable | multi_file | long_context | multi_hypothesis",
  "num_relevant_files": 0,
  "requires_cross_file_reasoning": true,
  "requires_parallel_hypothesis_search": false,
  "actual_patch_files_count": 0,
  "actual_patch_lines_changed": 0
}
```

注意：`num_relevant_files`、`requires_cross_file_reasoning`、`actual_patch_files_count` 等字段通常需要参考最终 patch 或人工事后分析，因此不能暴露给 agent。

### 6.4 预注册标注协议

为防止事后归因偏差，任务标注必须在实验执行前完成并 freeze。

流程：

1. 初筛 candidate tasks，确保各 difficulty bucket 有足够样本。
2. 两名标注者独立审查 issue 描述、repo 结构、test files，标注 difficulty_bucket 和 complexity features。
3. 如果两名标注者不一致，由第三人仲裁或讨论达成共识。
4. 标注完成后写入 `tasks.jsonl`，git commit，并在跑任何实验前 freeze。
5. 保存原始标注文件到 `annotations/`。

建议：任务标注者和失败归因标注者尽量分离，以减少认知偏差传递。

---

## 7. 对比系统

第一版主实验只比较 OpenCode 原生机制下的三种系统。

### 7.1 S1: Build-only Single Agent

```text
system = build_only
```

约束：

* 只使用 OpenCode primary agent `Build`。
* 禁止调用任何 subagent。
* Build 独立完成 issue 理解、代码搜索、修改、测试和 patch 提交。

目标：建立 single-agent baseline。

### 7.2 S2: Build + Explore

```text
system = build_explore
```

约束：

* 允许 Build 调用 OpenCode 内置 `Explore` subagent。
* Explore 只负责代码库探索、文件定位、符号搜索和 bug hypothesis。
* Explore 不允许修改代码。
* Build 负责最终 patch。

Explore 的合法任务包括：

* 查找相关文件；
* 查找相关函数、类、符号；
* 总结代码路径；
* 根据 failing test / issue 描述提出 bug hypothesis；
* 判断哪些文件最值得 Build 进一步阅读。

Explore 的非法任务包括：

* 直接修改代码；
* 生成完整 patch；
* 运行破坏性命令；
* 长篇泛泛分析。

目标：测试只读探索型 subagent 是否能提升代码修复效果。

### 7.3 S3: Build + Explore + General

```text
system = build_explore_general
```

约束：

* 允许 Build 调用 `Explore` 和 `General` 两类 subagent。
* Explore 用于只读代码探索。
* General 只允许处理明确、封闭的子任务。
* Build 仍负责最终 patch 和提交。

General 的推荐任务边界：

* 分析某个具体 failing test；
* 比较两个候选 bug hypothesis；
* review Build 已经形成的 patch plan；
* 检查某个局部实现是否违反已有接口约定；
* 对某段测试日志做原因归纳。

General 的禁止任务：

* “随便看看整个 repo”；
* “帮我完整解决这个 issue”；
* 未限定范围的大规模探索；
* 未经 Build 整合直接提交 patch。

目标：测试 OpenCode 原生多 subagent 可用时，dynamic spawn 是否优于 Build-only 与 Build+Explore。

### 7.4 Stage 0 / Stage 1: General 行为观察

General subagent 的定义较宽泛。如果 General 与 Explore 的实际行为高度重叠，S3 vs S2 将没有清晰信号。

观察内容：

* General 被 Build 调用时，Build 给出的任务是什么；
* General 实际是在搜索、分析、生成 patch，还是 review；
* General 与 Explore 在相同 task 下是否表现出差异；
* Qwen3.5-9B 是否遵守 Explore 只读约束；
* Qwen3.5-9B 是否把 General 当成另一个 Build 来使用。

判定标准：

* 如果 General 和 Explore 在 ≥70% 的 runs 中行为高度重叠，Stage 2 可以考虑弱化 S3 结论，或将其标记为 “additional subagent availability” 而不是 “distinct General role”。
* 如果 General 表现出独特价值，例如 test explanation、hypothesis comparison、patch review，则保留 S3 主比较。
* 如果 Explore 频繁违反只读约束，应报告为模型 instruction-following 限制，并在 system prompt 或 harness 权限层面强制只读。

### 7.5 可选 ablation systems

这些系统不进入 Stage 1 默认主实验。只有 Stage 2 后资源允许时再加入。

#### S2-forced: Build forced Explore once

目的：区分 Explore 本身是否有用，与 OpenCode native policy 是否会正确调用 Explore。

约束：

* 每个任务开始后，Build 必须先调用一次 Explore。
* Explore 返回后，Build 再决定后续是否继续调用。

#### S3-forced-explore: Build forced Explore, General optional

目的：测试 General 的边际价值，而不是把 Explore 是否被调用混入 S3 结果。

---

## 8. 预算控制

### 8.1 核心原则

本实验主预算是 output token，而不是 API cost。

原因：

1. 本地推理没有外部 API 费用；
2. output token 直接决定 GPU 推理时间；
3. output cap 可以防止 subagent workflow 通过无限生成获得优势。

本实验不强制限制所有 agent 可读取的输入上下文总量。因为真实 coding-agent 场景中，multi-agent 的潜在优势之一就是不同 agent 可以阅读不同文件或日志。

但必须记录 total input tokens，并在分析中作为协变量控制，以区分：

```text
spawn 架构带来的收益
vs
multi-agent 多读文件带来的收益
```

### 8.2 Calibration-based output budget

在正式 Stage 1B / Stage 2 前，使用 Stage 1A 或 calibration runs 估计 S1 的自然 output token 消耗。

步骤：

1. 在 calibration runs 中设置宽松 output cap，例如 100k tokens，或不设外部 cap。
2. 记录 S1 在所有 calibration tasks 上的真实 output token 消费。
3. 计算 S1 median output tokens，记为 `M_s1`。
4. Stage 1B / Stage 2 的 per-task budget 设为：

```text
per-task total output cap = M_s1 × 1.5
```

如 `M_s1 × 1.5` 明显过高，可设绝对上限，例如：

```text
absolute output cap = 64000
```

### 8.3 Equal generation budget

主实验使用：

```text
Equal generation budget
```

各系统预算：

```text
S1: Build output ≤ cap
S2: Build + Explore output ≤ cap
S3: Build + Explore + General output ≤ cap
```

说明：这不是 equal compute budget。multi-agent 系统仍可能使用更多 input tokens、更多 tool calls 或更多 wall-clock time。因此必须记录并分析这些成本。

### 8.4 Budget warning 与终止

当累计 output tokens 达到 cap 的 90% 时，harness 应向 Build 注入 warning：

```text
You are close to the experiment output budget. Prioritize final patch, tests, and concise reasoning.
```

当累计 output tokens 达到 cap 时：

1. 终止 run；
2. 保存当前日志和 patch；
3. 标记 `budget_error` 或 `incomplete_due_to_budget`；
4. 仍运行 evaluator，如果存在 patch。

### 8.5 必须记录但不强控的成本

每个 run 必须记录：

* per-agent input tokens；
* total input tokens；
* per-agent output tokens；
* total output tokens；
* total tokens；
* tool calls；
* wall-clock runtime；
* GPU inference time；
* subagent call count；
* files opened；
* files edited；
* test command count；
* final patch size；
* tokens per success。

### 8.6 输入 token 协变量分析

分析模型：

```text
success ~ system + total_input_tokens + difficulty_bucket + (1 | task_id)
```

解释：

* 如果控制 total_input_tokens 后 system 效应仍存在，说明 spawn workflow 的收益不只是“多读文件”。
* 如果控制 total_input_tokens 后 system 效应消失，说明 multi-agent 的优势主要来自上下文覆盖，而不是 subagent 架构本身。

### 8.7 Natural-budget 附加设置

如资源允许，可以追加 Natural-budget setting：

* 各系统按真实 OpenCode workflow 运行；
* 不强行统一 output cap；
* 记录真实成本和 GPU 时间；
* 用于分析真实使用时的 success-cost tradeoff。

Natural-budget 不作为第一版主结论的唯一依据。

---

## 9. 工具与执行环境

### 9.1 统一工具集

所有系统使用统一代码仓库环境与测试接口。

需要支持：

* repo checkout；
* issue/task loading；
* code search；
* file open；
* file edit；
* test execution；
* git diff capture；
* patch submission；
* evaluator execution。

所有工具调用必须记录：

```json
{
  "tool": "search_code | open_file | edit_file | run_tests | git_diff | submit_patch",
  "agent": "build | explore | general",
  "args_summary": {},
  "result_summary": "...",
  "success": true,
  "runtime_sec": 0
}
```

### 9.2 Patch evaluator 规范

使用 SWE-bench 官方 evaluation harness 或等效自动化评估脚本。

成功标准：

```text
Primary success:
  all regression tests pass
  and issue-specific tests pass, if available

Secondary success:
  issue-specific tests pass
  but regression tests unavailable or partially failing

Fail:
  issue-specific tests fail
  or regression tests fail
  or patch cannot apply
```

### 9.3 Evaluator 可靠性验证

Stage 1 前必须验证 evaluator。

验证方式：

1. 对 candidate tasks 的 ground-truth patch 运行 evaluator，确认 expected pass。
2. 对每个 task 注入 knowingly-broken patch，确认 evaluator 能检测 failure。
3. 对同一 patch 重复 evaluator，检测 flaky tests。
4. 标记 flaky task，并在分析时排除或单独报告。

### 9.4 OpenCode + vLLM integration checklist

Stage 1A 前确认：

* [ ] OpenCode 能成功发起 chat completion 到 `http://localhost:8010/v1`。
* [ ] OpenCode 使用 model name `qwen35-9b`。
* [ ] thinking 已关闭。
* [ ] Hermes tool parser 已启用。
* [ ] OpenCode tool call 格式与 vLLM 返回兼容。
* [ ] token usage 可以被解析。
* [ ] subagent spawn 可以走同一 endpoint。
* [ ] GPU OOM 或推理超时时不会丢失已有日志。

---

## 10. 日志格式

### 10.1 Run-level 日志

每个 run 保存为 `runs.jsonl` 的一行。

```json
{
  "run_id": "...",
  "task_id": "...",
  "repo": "...",
  "system": "build_only | build_explore | build_explore_general",
  "budget_setting": "equal_generation | natural | equal_total_tokens | equal_tool_calls",
  "seed": 0,
  "model": "qwen35-9b",
  "vllm_endpoint": "http://localhost:8010/v1",
  "gpu_id": 2,
  "max_model_len": 65536,
  "thinking_enabled": false,
  "tool_parser": "hermes",
  "difficulty_bucket": "local_readable | multi_file | long_context | multi_hypothesis",
  "issue": "...",
  "success": true,
  "tests_pass": true,
  "patch_apply_success": true,
  "eval_result": {
    "regression_tests_total": 0,
    "regression_tests_passed": 0,
    "issue_tests_total": 0,
    "issue_tests_passed": 0,
    "raw_log_path": "..."
  },
  "final_patch_path": "patches/<run_id>.patch",
  "token_usage": {
    "input_tokens": {
      "build": 0,
      "explore": 0,
      "general": 0,
      "total": 0
    },
    "output_tokens": {
      "build": 0,
      "explore": 0,
      "general": 0,
      "total": 0
    },
    "total_tokens": 0
  },
  "runtime_sec": 0,
  "gpu_inference_sec": {
    "build": 0,
    "explore": 0,
    "general": 0,
    "total": 0
  },
  "tool_calls": {
    "build": 0,
    "explore": 0,
    "general": 0,
    "total": 0
  },
  "files_opened": [],
  "files_edited": [],
  "test_commands": [],
  "subagent_calls": [],
  "failure_analysis": {
    "failed": false,
    "primary_failure_type": "none",
    "failure_tags": [],
    "notes": ""
  }
}
```

### 10.2 Step-level trajectory 日志

后续 offline policy learning 需要 step-level trajectory。每个 run 保存一个独立文件：

```text
logs/trajectories/<run_id>.jsonl
```

每一行格式：

```json
{
  "run_id": "...",
  "step": 0,
  "timestamp": "...",
  "actor": "Build | Explore | General | evaluator | system",
  "event_type": "message | tool_call | subagent_call | edit | test | diff | submit | error",
  "action": "...",
  "input_summary": "...",
  "output_summary": "...",
  "tool_name": "search_code | open_file | edit_file | run_tests | git_diff | submit_patch | none",
  "tool_args_summary": {},
  "result_summary": "...",
  "success": true,
  "runtime_sec": 0,
  "cost_delta": {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0
  },
  "budget_remaining": {
    "output_tokens": 0
  }
}
```

### 10.3 Subagent call 日志

每次 subagent call 必须记录为结构化对象。

```json
{
  "call_id": "...",
  "parent_run_id": "...",
  "parent_actor": "Build",
  "subagent": "Explore | General",
  "call_step": 0,
  "task_given": "...",
  "context_pass_summary": "...",
  "input_summary": "...",
  "output_summary": "...",
  "input_tokens": 0,
  "output_tokens": 0,
  "gpu_inference_sec": 0,
  "runtime_sec": 0,
  "files_opened_by_subagent": [],
  "symbols_identified": [],
  "hypotheses_proposed": [],
  "used_by_build": true,
  "introduced_key_file": false,
  "introduced_key_symbol": false,
  "introduced_correct_hypothesis": false,
  "narrowed_search_space": false,
  "explained_failing_test": false,
  "misled_primary": false,
  "net_effect": "helped | harmed | neutral | unknown"
}
```

### 10.4 Subagent 边际信息增益判断

判断 subagent 是否有边际价值时，优先使用客观标准：

* 是否首次指出最终 patch 涉及文件；
* 是否首次指出关键函数、类或符号；
* 是否提出最终正确 bug hypothesis；
* 是否缩小候选文件范围；
* 是否解释 failing test 的真实原因；
* Build 是否显式采纳其输出。

不要仅因为最终 run 成功就标记 subagent 为 `helped`。也不要仅因为最终 run 失败就标记 subagent 为 `harmed`。

---

## 11. 核心指标与统计分析

### 11.1 任务成功指标

* `task_success_rate`
* `tests_pass_rate`
* `patch_apply_success_rate`

### 11.2 成本指标

* `output_tokens_per_task`
* `output_tokens_per_success`
* `total_tokens_per_success`
* `runtime_per_task`
* `gpu_inference_sec_per_task`
* `tool_calls_per_task`
* `subagent_calls_per_task`

### 11.3 架构对比指标

```text
build_explore_delta = success(S2) - success(S1)
build_explore_general_delta = success(S3) - success(S1)
general_marginal_gain = success(S3) - success(S2)
```

### 11.4 Subagent 使用指标

* `subagent_call_rate`
* `explore_call_rate`
* `general_call_rate`
* `explore_help_rate`
* `general_help_rate`
* `subagent_harm_rate`
* `unnecessary_subagent_rate`
* `introduced_key_file_rate`
* `introduced_key_symbol_rate`
* `introduced_correct_hypothesis_rate`
* `misled_primary_rate`

### 11.5 失败归因指标

* `explore_missed_rate`
* `general_noise_rate`
* `integration_error_rate`
* `patch_error_rate`
* `test_error_rate`
* `budget_error_rate`
* `environment_error_rate`

### 11.6 任务复杂度交互

按以下维度分组分析：

* difficulty_bucket；
* issue_length_tokens；
* initial_test_log_length_tokens；
* repo_file_count；
* initial_candidate_files_count；
* num_relevant_files；
* requires_cross_file_reasoning；
* requires_parallel_hypothesis_search。

重点问题：

```text
subagent workflow 的收益是否随任务复杂度上升而增加？
```

### 11.7 统计分析方法预声明

#### 主效应模型

使用 mixed-effects logistic regression：

```r
glmer(success ~ system + difficulty_bucket + log(total_input_tokens + 1) + (1 | task_id),
      family = binomial,
      data = runs)
```

解释：

* `system` 为固定效应；
* `difficulty_bucket` 为固定效应；
* `log(total_input_tokens + 1)` 控制输入 token 差异；
* `task_id` 为 random intercept。

如果样本量太小导致模型不稳定，则退回描述性统计和 clustered bootstrap。

#### 成对比较

比较：

* S1 vs S2；
* S1 vs S3；
* S2 vs S3。

方法：

* clustered bootstrap by task_id；
* 10,000 resamples；
* 报告 percentile 95% CI；
* 报告 paired win/loss/tie counts。

#### 复杂度交互

Stage 2 样本量仍可能不足以做强 interaction test，因此复杂度交互主要做描述性趋势分析：

* 每个 difficulty_bucket 下的 success rate；
* 每个 bucket 下的 cost per success；
* 每个 bucket 下的 subagent help/harm rate。

#### 效应量

报告 Cohen's h 用于 proportion 差异：

```text
h = 0.2 small
h = 0.5 medium
h = 0.8 large
```

#### 报告规范

不以 `p < 0.05` 作为二元判断主线。报告：

```text
point estimate + 95% CI + effect size + representative cases
```

---

## 12. 失败归因标准与可靠性

### 12.1 Failure types

每个 failed run 必须有一个 `primary_failure_type`，并可以有多个 `failure_tags`。

可选 primary failure types：

```text
none
explore_missed
general_noise
unnecessary_subagent
subagent_harm
integration_error
patch_error
test_error
budget_error
environment_error
other
```

### 12.2 标签定义

#### explore_missed

Explore 被调用，但没有找到关键文件、关键函数或关键错误位置。

#### general_noise

General 输出错误方向、错误解释或无关分析，并增加整合负担。

#### unnecessary_subagent

调用 subagent 但没有带来有效信息，Build 未采纳其输出，且输出与最终 patch 无关，同时消耗预算。

#### subagent_harm

Subagent 输出直接导致 Build 选择错误修改方向。要求能在日志中看到 causal link，例如 Build 明确采纳了错误建议。

#### integration_error

Subagent 输出包含有用信息，例如找到了相关文件或正确 hypothesis，但 Build 没有采纳、误读或整合错误。

#### patch_error

定位基本正确，但最终代码修改有语法错误、逻辑错误、不完整 fix 或破坏 regression。

#### test_error

Agent 能运行测试，但测试选择错误、测试日志误读、测试命令不完整、没有运行关键测试，或没有正确利用测试反馈。

#### budget_error

探索或 subagent 调用消耗过多预算，导致没有足够预算完成 patch、test 或 review。

可细分为：

```text
over_exploration
over_delegation
late_testing
late_integration
```

#### environment_error

基础设施错误，包括：

* repo checkout 失败；
* 依赖安装失败；
* evaluator 本身异常；
* vLLM 服务异常；
* OpenCode endpoint 失败；
* GPU OOM；
* 文件权限错误；
* 日志系统故障。

### 12.3 标注流程

1. 两名标注者独立审查每个 failed run 的完整日志，包括 issue、agent 对话历史、tool calls、subagent output、final patch 和 eval result。
2. 每名标注者标记：

   * `primary_failure_type`
   * `secondary_failure_types`
   * `notes`
3. 对比两人标注。
4. 一致的直接采纳。
5. 不一致的讨论达成共识，记录 consensus。
6. 标注者应尽量不知道 run 属于哪个 system，以减少确认偏差。

### 12.4 失败归因决策树

```text
1. 是基础设施错误吗？
   是 → environment_error
   否 → 继续

2. 测试被错误选择、错误运行或错误解释了吗？
   是 → test_error
   否 → 继续

3. 是否预算耗尽，且无法完成 patch / test / review？
   是 → budget_error
   否 → 继续

4. 是否调用了 subagent？
   否 → patch_error 或 other
   是 → 继续

5. subagent 是否提供了正确信息？
   否 → explore_missed 或 general_noise
   是 → 继续

6. Build 是否采纳并正确整合了 subagent 信息？
   否 → integration_error
   是 → 继续

7. 失败是否由 subagent 错误建议直接导致？
   是 → subagent_harm
   否 → patch_error
```

### 12.5 可靠性指标

计算 Cohen's κ。

目标：

```text
κ ≥ 0.6
```

如果：

```text
κ < 0.4
```

则修订标注指南后重新标注，并在 report 中说明。

---

## 13. 分析问题

第一阶段实验回答以下问题：

1. Build-only、Build+Explore、Build+Explore+General 谁的 task_success_rate 更高？
2. 在 equal generation budget 下，subagent workflow 是否仍有优势？
3. 控制 total_input_tokens 后，subagent 系统的优势是否仍存在？
4. Explore 是否主要帮助 multi-file / long-context tasks？
5. General 是否带来额外收益，还是主要引入噪声？
6. Subagent workflow 的失败主要来自 subagent 输出错误，还是 Build 整合错误？
7. Multi-agent 的收益是否随任务复杂度上升而增加？
8. Qwen3.5-9B 在代码修复任务上的 spawn 行为是否不同于强模型预期？
9. 哪些日志字段可以作为后续 RL spawn policy 的 state/action/reward？

---

## 14. 后续 RL / Policy Learning 接口

本阶段不直接训练 RL，但日志必须支持后续 policy learning。

### 14.1 State

可用于后续 policy 的 state 包括：

* issue summary；
* pre-run observable task features；
* files already opened；
* search results；
* current patch status；
* test status；
* subagent reports；
* budget remaining；
* previous tool calls；
* total_input_tokens consumed so far；
* total_output_tokens consumed so far；
* current failure signals，例如 failing test summary。

### 14.2 Action

后续 policy 可学习：

```text
solve_direct / continue_build
call_explore
call_general
ask_followup_to_subagent
run_tests
submit_patch
stop
```

### 14.3 Reward

初始 reward 设计：

```text
+1 task success
-1 task failure
-λ output_tokens
-μ tool_calls
-ν runtime
-α unnecessary_subagent
-β subagent_harm
+γ useful_subagent
```

### 14.4 推荐学习路线

第一阶段之后优先做：

1. spawn-or-not classifier；
2. which-subagent classifier；
3. contextual bandit；
4. offline policy evaluation；
5. 再考虑 offline RL。

不建议一开始做 full joint RL。

---

## 15. 预期结果解释

### 15.1 Build+Explore 在 hard tasks 上提升

说明代码库探索型 subagent 有价值，收益可能来自上下文覆盖和 bug 定位。

### 15.2 Build+Explore+General 低于 Build+Explore

说明 General 可能引入噪声或消耗预算，后续 policy 需要学习何时调用 General。

### 15.3 Build-only 在 local_readable 上最好

说明 subagent 不应默认启用，spawn 应该是条件化决策。

### 15.4 Multi-agent 只在 Natural-budget 胜出

说明 subagent 系统可以用更多计算换成功率，但 equal generation budget 下没有架构效率优势。

### 15.5 Multi-agent 在 Equal generation budget 也胜出

这是较强结果，说明 subagent 架构可能提高了生成预算利用效率。

### 15.6 控制 input_tokens 后 system effect 消失

说明 multi-agent 的优势主要来自能读更多上下文，而不是 spawn 架构本身。后续 policy 应聚焦于如何高效覆盖相关上下文，而不是仅仅学习是否 spawn。

### 15.7 所有系统 success rate 都极低

如果所有系统 success rate < 20%，不要直接视为实验失败。

解释方式：

1. 报告为本地小模型在目标任务分布上的能力边界；
2. 继续分析 subagent 是否在部分成功、定位、测试解释等中间指标上有帮助；
3. 降低任务难度后重新跑 Stage 1；
4. 保留失败轨迹作为 policy learning 和任务难度校准数据。

---

## 16. 输出文件结构

实验输出目录：

```text
outputs/opencode_spawn_pilot/
├── config/
│   ├── stage1_qwen35_9b.yaml
│   └── vllm_launch_command.sh
├── tasks.jsonl
├── annotations/
│   ├── annotator_1/
│   └── annotator_2/
├── runs.jsonl
├── trajectories/
│   └── <run_id>.jsonl
├── summary_by_system.csv
├── summary_by_difficulty.csv
├── summary_by_budget.csv
├── subagent_usage.csv
├── failure_analysis.csv
├── calibration/
│   └── output_token_distribution.csv
├── vllm_metrics/
│   ├── throughput_by_run.csv
│   └── gpu_utilization.csv
├── capability_check/
│   └── prevalidation_report.md
├── patches/
│   └── <run_id>.patch
├── evaluator_logs/
│   └── <run_id>.log
├── raw_agent_logs/
│   └── <run_id>.log
└── report.md
```

---

## 17. Summary 表格式

### 17.1 summary_by_system.csv

字段：

```text
system
num_runs
num_tasks
success_rate
tests_pass_rate
patch_apply_success_rate
mean_input_tokens
mean_output_tokens
mean_total_tokens
mean_runtime_sec
mean_gpu_inference_sec
mean_tool_calls
mean_subagent_calls
output_tokens_per_success
total_tokens_per_success
```

### 17.2 summary_by_difficulty.csv

字段：

```text
difficulty_bucket
system
num_runs
success_rate
mean_output_tokens
mean_total_tokens
mean_tool_calls
mean_subagent_calls
output_tokens_per_success
```

### 17.3 subagent_usage.csv

字段：

```text
system
subagent
call_rate
mean_calls_per_run
mean_input_tokens_per_call
mean_output_tokens_per_call
help_rate
harm_rate
neutral_rate
unknown_rate
introduced_key_file_rate
introduced_key_symbol_rate
introduced_correct_hypothesis_rate
narrowed_search_space_rate
explained_failing_test_rate
misled_primary_rate
```

### 17.4 failure_analysis.csv

字段：

```text
system
primary_failure_type
count
rate
cohens_kappa
```

---

## 18. Report 要求

### 18.1 Stage 1A report

Stage 1A 结束后输出短报告，包含：

1. 实验配置；
2. 任务列表；
3. 三个系统是否都能跑通；
4. 日志完整性检查；
5. evaluator 稳定性；
6. subagent call 捕获是否成功；
7. 是否存在系统性 environment_error；
8. 是否进入 Stage 1B。

### 18.2 Stage 1B report

Stage 1B 结束后输出 pilot report，包含：

1. 成功率和成本初步结果；
2. run-to-run variance；
3. failure attribution 示例；
4. Cohen's κ；
5. subagent help/harm 初步趋势；
6. 是否调整任务难度；
7. 是否调整 output cap；
8. 是否进入 Stage 2。

### 18.3 Stage 2 report

Stage 2 report 至少包含：

1. 研究问题；
2. 实验设置；
3. 任务分布；
4. 主结果表；
5. 按 difficulty 的结果；
6. subagent 使用分析；
7. 失败归因分析；
8. paired task comparison；
9. input token 协变量分析；
10. 代表性 case studies；
11. 对后续 spawn policy 的建议。

---

## 19. 阶段性成功标准

第一阶段不要求证明 multi-agent 一定更好。满足以下条件即可认为成功：

1. 三组系统完整跑通 Stage 1A。
2. Stage 1B 中每个 task-system 组合至少有 3 次重复，除非资源限制被明确记录。
3. 能稳定记录 subagent calls、per-agent token usage、tool calls 和 final patch。
4. 能自动评估 patch success，且 evaluator 通过可靠性验证。
5. 能区分至少 3 类失败来源。
6. 双人失败标注 Cohen's κ ≥ 0.6，或在 κ 较低时明确修订标注协议。
7. 能观察到 subagent 在某些任务类型上有帮助或有害的趋势。
8. 能基于日志定义后续 RL 的 state/action/reward。
9. Calibration 阶段确定了合理的 per-task output budget。
10. 任务标注在实验前 freeze。
11. OpenCode + vLLM 集成验证通过。
12. 即使 Qwen3.5-9B 在 SWE-bench 上能力不足，也能产出可解释的失败轨迹和任务难度调整建议。

---

## 20. Agent 执行纪律

实验执行 agent 必须遵守：

1. 不要在 run 中人工干预 agent 输出。
2. 不要手动修 patch。
3. 不要根据结果选择性丢弃失败 run。
4. environment_error 可以重跑，但必须保留原始失败记录。
5. 每次修改实验脚本或配置，必须记录 config version。
6. 如果某个系统配置导致大量无效 run，先停止并写 issue summary，不要继续消耗任务预算。
7. 所有主结论必须基于完整日志和 evaluator 结果。
8. 不要把 post-hoc oracle features 暴露给 agent。
9. 不要在 Stage 1 默认启用未验证的双实例 endpoint routing。
10. 不要把 General 当作无限制自由分析器。

---

## 21. 当前下一步执行计划

从当前状态开始，执行 agent 应按以下顺序推进。

### Step 1: 固化配置

创建：

```text
outputs/opencode_spawn_pilot/config/stage1_qwen35_9b.yaml
outputs/opencode_spawn_pilot/config/vllm_launch_command.sh
```

配置必须包含：

```yaml
base_url: http://localhost:8010/v1
model: qwen35-9b
gpu: 2
max_model_len: 65536
thinking_enabled: false
tool_call_parser: hermes
```

### Step 2: 完成 Stage 0C 能力预验证

如尚未完成，执行 §4.3 的五项模型能力预验证，并输出：

```text
capability_check/prevalidation_report.md
```

### Step 3: 准备 Stage 1A tasks

准备 10 个任务，写入：

```text
tasks.jsonl
```

确保每个任务有：

* issue 描述；
* repo 信息；
* baseline failing test；
* evaluator；
* pre-run observable features；
* post-hoc oracle features。

### Step 4: Freeze task annotations

保存标注原始数据到：

```text
annotations/
```

并 git commit。

### Step 5: 运行 Stage 1A

执行：

```text
10 tasks × 3 systems × 1 seed = 30 runs
```

输出：

* `runs.jsonl`
* `trajectories/`
* `patches/`
* `evaluator_logs/`
* `raw_agent_logs/`

### Step 6: Stage 1A review

检查：

* 是否有系统性 environment_error；
* token accounting 是否正确；
* subagent call 是否捕获；
* evaluator 是否稳定；
* General 是否有可区分行为；
* output cap 是否需要调整。

### Step 7: 进入 Stage 1B

如果 Stage 1A 通过，执行：

```text
10 tasks × 3 systems × 3 seeds = 90 runs
```

### Step 8: Stage 1B report

输出 pilot report，决定是否进入 Stage 2。

---

## 22. 当前主线表述

本实验不试图证明 subagent 天然更好，也不试图证明 subagent 天然存在目标错位，而是研究：

> spawn subagent 是一个可优化的操作。我们在本地小模型 Qwen3.5-9B + vLLM 上，用 OpenCode 原生 Build / Explore / General 机制评估 single-agent 与 subagent workflow 的差异，识别 spawn 带来的收益、成本和失败来源，并为后续 policy learning 提供轨迹数据。

更稳健的一句话总结：

**用 Qwen3.5-9B + vLLM 本地推理驱动 OpenCode，在代码修复任务上比较 Build-only、Build+Explore、Build+Explore+General 三种 workflow，在 calibration-based equal generation budget 下分析 subagent 是否通过上下文探索和任务分工提升成功率，并通过 input token 协变量、双人失败标注和 step-level trajectory 区分“多读上下文”“任务分工”和“整合失败”等因素，为后续 spawn policy 学习提供数据基础。**

---

## 附录 A：v0.3 → v0.4 关键差异

| 维度                  | v0.3                                | v0.4                                                     |
| ------------------- | ----------------------------------- | -------------------------------------------------------- |
| 默认部署                | 2 GPU / 8000+8001 方案                | 已验证单实例：GPU 2 / port 8010                                 |
| model name          | `Qwen/Qwen3.5-9B` / `qwen3.5-9b` 混用 | 统一为 `qwen35-9b`                                          |
| max_model_len       | 32768                               | 65536                                                    |
| thinking            | 未固化到启动命令                            | 固化 `enable_thinking=false`                               |
| tool call           | 未固化 Hermes parser                   | 固化 `--enable-auto-tool-choice --tool-call-parser hermes` |
| Stage 0             | 写成待完成                               | 拆成 infrastructure completed 与 capability prevalidation   |
| budget              | equal-output-token                  | 明确为 equal generation budget                              |
| subagent help       | `help_label` 为主                     | 增加边际信息增益字段                                               |
| task features       | oracle 与 observable 混合              | 拆成 pre-run observable 与 post-hoc oracle                  |
| failure attribution | test/environment 边界模糊               | 明确拆分 test_error 与 environment_error                      |
| 研究表述                | 使用“第一个”强 claim                      | 改为稳健贡献表述                                                 |
