# OpenCode-Native Spawn Pilot Spec

版本：v0.3  
上一版本：v0.2  
变更类型：模型框架从 API 切换到本地推理  
主题：用本地 Qwen3.5-9B + vLLM 驱动 OpenCode，评测 single-agent vs subagent workflow。

---

## 变更记录

| 版本 | 变更项 | 说明 |
|------|--------|------|
| v0.2→v0.3 | §3 模型 | Claude Sonnet 4 API → Qwen3.5-9B + 本地 vLLM |
| v0.2→v0.3 | 新增 §3.4 | vLLM 部署方案（2×A100 40GB 可用） |
| v0.2→v0.3 | 新增 §4.3 | 模型能力预验证（Stage 0 必须项） |
| v0.2→v0.3 | §7 预算 | API cost 视角 → GPU throughput 视角，逻辑不变 |
| v0.2→v0.3 | §8 执行环境 | 增加 vLLM server 配置和 OpenCode endpoint 配置 |
| v0.2→v0.3 | §9 日志 | 增加 vLLM 指标字段 |
| v0.2→v0.3 | §16 成功标准 | 增加模型能力预验证项 |

---

## 1. 研究目标

本实验研究真实 coding-agent 框架中的 `spawn subagent` 操作是否能提升代码修复任务的成功率与成本效率。

核心问题不是证明 subagent 一定优于 single agent，也不是证明 subagent 一定存在目标错位，而是回答：

> 在代码任务中，什么时候应该 spawn subagent，spawn 哪类 subagent，subagent 的输出是否真的帮助 primary agent，失败主要发生在探索、执行、整合还是预算控制环节？

实验聚焦 OpenCode 原生 agent 机制，优先使用 OpenCode 内置的 primary agents 与 subagents，而不是一开始手搓复杂多角色系统。

**本实验全部使用本地推理（Qwen3.5-9B + vLLM）**，不依赖外部 API。这既排除了 API cost 变量，也测试小模型在 spawn workflow 下的行为模式——这是一个重要但被现有文献忽视的设置。

---

## 2. 背景与动机

此前探索性实验显示，spawned-agent delegation 并不是稳定有害，也不是稳定有益。subagent 有时能通过额外上下文和分工提升任务成功率，但也可能引入 recommendation drift、过度保守、动作语义混淆或 integration error。

现存 spawn / multi-agent 研究几乎全部基于 GPT-4 / Claude 级别的强模型。一个重要空缺是：

> 在本地可部署的小模型（~9B）上，spawn subagent 的效果如何？小模型是否也能从任务分工中受益？还是 spawn overhead 压倒了收益？

因此，新的研究主线调整为：

> 将 spawn agent 视为一个需要优化的操作，研究如何在真实任务中学习何时 spawn、spawn 给谁、传递什么上下文、如何整合 subagent 输出。

代码任务是第一阶段的合适场景，因为 coding-agent 工作流天然包含代码库探索、bug 定位、patch 生成、测试解释和 review 等子任务。

---

## 3. 实验框架与模型配置 🔴 重大修订

### 3.1 主框架

第一阶段使用 OpenCode 原生 agent/subagent 机制。

OpenCode 中的基本角色包括：

- Primary agent：用户直接交互的主 agent，例如 Build / Plan。
- Subagent：由 primary agent 调用的专门 agent，例如 Explore / General。

第一版不手搓 Explorer / Implementer / Reviewer / Tester 四类角色，而是优先使用 OpenCode 原生结构。

### 3.2 原因

使用 OpenCode 原生机制的原因：

1. 更贴近真实 coding-agent 使用方式。
2. 避免实验被质疑为"自定义多 agent workflow"，而不是框架内真实 spawn 行为。
3. 先观察默认 subagent 机制在小模型上是否有价值，再决定是否设计 custom subagents 或 RL policy。
4. OpenCode 相对开源和可控，方便插入日志、预算、subagent call 记录和失败归因。

### 3.3 Model Specification

#### 推理引擎与硬件

```yaml
engine: vLLM 0.19.1
gpu: 2 × NVIDIA A100 40GB available (7 total, 5 occupied)
parallel_instances: 最多 2 个并发 vLLM 实例（每 GPU 一个）
```

#### 模型

```yaml
model: Qwen/Qwen3.5-9B
local_path: /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/
size: ~19 GB (4 shards: model.safetensors-00001~4-of-00004.safetensors)
architecture: Qwen3_5ForConditionalGeneration
max_model_len: 32768
```

#### Primary Agent (Build) 配置

```yaml
model: Qwen3.5-9B
temperature: 0.0
max_output_tokens: 8192               # per-turn generation cap
gpu_memory_utilization: 0.85
enforce_eager: true                    # 9B 模型必须，避免 CUDA graph 内存溢出
tensor_parallel_size: 1                # 单 GPU 推理
enable_prefix_caching: true
api_endpoint: http://localhost:{port}/v1  # OpenAI-compatible
system_prompt: opencode 内置 Build agent 默认 prompt
```

#### Subagent (Explore / General) 配置

```yaml
model: Qwen3.5-9B                      # 与 primary 相同模型
temperature: 0.0
max_output_tokens: 4096                # subagent per-turn 限制更严
gpu_memory_utilization: 0.85
enforce_eager: true
tensor_parallel_size: 1
enable_prefix_caching: true
api_endpoint: 与 primary 共用或独立实例（见 §3.4）
system_prompt: opencode 内置对应 subagent 默认 prompt
```

#### 设计理由

- **Primary 和 subagent 使用同一模型**：排除 "模型能力差异导致提升" 的混淆。
- **temperature = 0.0**：最大化可复现性。如果 Stage 0 发现温度过低导致死循环，可放宽到 0.1–0.3 并报告。
- **Qwen3.5-9B 的选择**：本地最大可用模型（19GB），单 A100 40GB 可运行，推理吞吐预期合理。
- **9B vs 强模型的差距**：这本身是研究贡献——揭示 spawn workflow 在能力有限模型上的行为规律。

### 3.4 vLLM 部署方案（2×A100 40GB 可用）🟡 新增

仅 2 张 A100 40GB 可用。每 GPU 运行一个 Qwen3.5-9B 实例（19GB 模型 + KV cache ≈ 25GB，gpu_memory_utilization=0.85 可行）。

#### 实例分配

```
GPU 0: vLLM instance → primary endpoint (port 8000)
GPU 1: vLLM instance → dedicated subagent endpoint (port 8001)
```

两实例加载**同一模型**。分工：
- GPU 0 承担所有 primary agent（Build）推理 + Explore subagent 推理
- GPU 1 承担所有 General subagent 推理（如果 S3 使用）

**关键设计决策：同一 run 内的 subagent 调用是串行的**（Build → wait Explore → Build → wait General），因此不需要 per-run 独占 GPU。两个 vLLM 实例可以通过请求队列服务多个 run：

```
Time →
GPU 0: [Run 1: Build] [Run 2: Build] [Run 1: Explore] [Run 3: Build] ...
GPU 1: [idle       ] [Run 1: General] [idle         ] [Run 2: General] ...
```

#### 并发 run 调度

Stage 1 需跑 90 runs。2 GPU 下全部串行不可接受。策略：

- **流水线化**：同时 submit 多个 run 到 GPU 0 的请求队列。vLLM 支持 continuous batching，可同时处理多个请求（只要显存容纳 KV cache）。
- **预估吞吐**：Qwen3.5-9B on A100 40GB，continuous batching 下预估 30–50 tok/s per request，2–4 concurrent requests。90 runs × avg 40k output / 40 tok/s / 3 concurrent ≈ 30,000s ≈ 8.3 小时。
- 如果 subagent 调用很少（大部分 run Build 不 spawn），GPU 1 长期空闲 → 可以考虑两 GPU 都跑 primary，subagent 请求轮询分发。

#### vLLM 实例启动命令

```bash
# GPU 0 — Primary + Explore
CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/ \
    --port 8000 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.85 \
    --enforce-eager \
    --tensor-parallel-size 1 \
    --enable-prefix-caching \
    --max-num-seqs 4

# GPU 1 — General subagent (if needed)
CUDA_VISIBLE_DEVICES=1 python -m vllm.entrypoints.openai.api_server \
    --model /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/ \
    --port 8001 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.85 \
    --enforce-eager \
    --tensor-parallel-size 1 \
    --enable-prefix-caching \
    --max-num-seqs 2
```

#### 成本估算

- 无 API 费用。
- GPU 计算时间：Stage 1 (90 runs) ≈ 8–10 小时在两 GPU 上。
- Stage 2 (270 runs) ≈ 24–30 小时。

#### 备用方案

如果 Qwen3.5-9B 吞吐不足或因 continuous batching 导致 OOM：
- 降级到串行模式（一次只发一个请求到 vLLM），牺牲速度换取稳定性。
- 或者每个 run 独占一个 GPU 实例（极端保守，但 2 GPU 只能同时跑 2 runs）。

---

## 4. 实验对象

### 4.1 任务类型

第一阶段任务为代码修复任务，优先使用：

- SWE-bench Lite / Verified 小子集；或
- curated Python bug-fix tasks；或
- 小型真实 repo 的 failing-test 修复任务。

任务需要具备可自动评估的 oracle，例如：

- regression tests pass；
- issue-specific tests pass；
- patch 能被 evaluator 判定为成功。

### 4.2 初始规模与重复设计

#### Stage 0: Pipeline Debug ＆ Calibration ＆ 能力预验证

- 5 tasks
- 3 systems
- 每个组合 **1 run**（先用单次调试 pipeline）
- 共 15 runs

目标：
1. 确认 repo checkout、测试运行、patch apply、OpenCode 调用、日志记录和 token budget 能跑通。
2. **Calibration**：记录各系统在无预算约束下的真实 output token 分布，为 Stage 1/2 确定 budget 提供数据基础（详见 §7.2）。
3. 验证 evaluator 在 candidate tasks 上的可靠性。
4. 观察 General subagent 实际行为模式（详见 §6.4）。
5. **🔴 新增 — 模型能力预验证**（详见 §4.3）。

#### Stage 1: Pilot

- 10 tasks
- 3 systems
- 每个组合 **3 runs**
- 共 90 runs

目标：初步观察 single vs subagent 是否有差异，并验证失败归因流程与标注可靠性。

#### Stage 2: Main Pilot

- 30 tasks
- 3 systems
- 每个组合 **3 runs**
- 共 270 runs

作为第一版实验报告主体。

后续如信号清楚，再扩展到 100 tasks。

#### 重复运行说明

- 即使 temperature=0，vLLM 推理的浮点计算仍可能在不同 GPU 或不同 run 产生微小差异。
- 3 次重复允许使用 mixed-effects model（task 作为 random effect）而非朴素均值比较。

### 4.3 模型能力预验证 🔴 新增

Qwen3.5-9B 不是 Claude Sonnet 4。在投入 270 runs 之前，Stage 0 必须确认模型有能力完成 SWE-bench 级别的任务。

#### 验证项目

| 项目 | 方法 | 通过标准 |
|------|------|----------|
| 代码理解 | 给 issue + repo，要求 model 定位 bug 文件（不给 patch 任务） | 在 top-3 candidates 中命中 ≥ 60% tasks |
| 代码生成 | 给定 bug 位置和修复方案，要求生成 patch | 语法正确，能 apply |
| Tool use | 验证 model 能否正确使用 OpenCode tools（search、edit、test） | 工具调用语法正确 ≥ 80% |
| Instruction following | 验证 Build 在你禁止 subagent 时是否确实不调用 | 零 subagent 调用 |
| 端到端 | 在 2 个已知 simple task 上跑完整 S1 run | 至少 1/2 能生成可 apply 的 patch |

#### 如果预验证失败

- 如果 Qwen3.5-9B 在 SWE-bench 级别任务上接近 0% success → 降级任务难度：
  - 使用 curated Python bug-fix（更短上下文、更明确 bug 描述）
  - 或自己构造小型 failing-test 修复任务
- 如果 Qwen3.5-9B 无法可靠使用 OpenCode tools → 考虑切换到 Qwen3-Coder 系列（如果可用）或降低工具复杂度
- **无论如何不切换到 API 模型**——本地小模型是实验的核心自变量之一

---

## 5. 任务分层与预注册

为了避免只构造有利于 multi-agent 的任务，任务集需要包含 single-agent-readable 和 multi-agent-favorable 两类场景。

### 5.1 复杂度特征

每个任务标注以下复杂度特征：

```json
{
  "task_id": "...",
  "repo": "...",
  "difficulty_bucket": "local_readable | multi_file | long_context | multi_hypothesis",
  "estimated_repo_context_tokens": 0,
  "num_candidate_files": 0,
  "num_relevant_files": 0,
  "num_failing_tests": 0,
  "requires_cross_file_reasoning": true,
  "requires_parallel_hypothesis_search": false
}
```

### 5.2 分层定义

#### local_readable

相关上下文较短，1–2 个文件内即可解决。预期 single agent 更稳或更便宜。

#### multi_file

需要跨 3–8 个文件定位和修改。subagent 可能通过探索分工获得收益。

#### long_context

候选上下文总量较大，单 agent 很难一次性覆盖全部相关文件或日志。subagent 可能通过上下文分片提升覆盖率。

#### multi_hypothesis

存在多个可能 bug source，需要并行探索不同假设。subagent 可能通过独立探索降低漏查风险。

### 5.3 预注册标注协议

为防止事后归因偏差，任务标注必须在实验执行前完成并 freeze。

**步骤：**

1. **初筛**：从任务来源筛选 candidate tasks，确保各 difficulty_bucket 有足够样本（建议每个 bucket ≥ 5 tasks for Stage 1，≥ 8 for Stage 2）。
2. **独立标注**：两名标注者独立审查每个 task 的 issue 描述、repo 结构、test files，标注 difficulty_bucket 和所有 complexity features。
3. **不一致仲裁**：如两名标注者的 difficulty_bucket 不一致，由第三人仲裁或讨论达成共识。
4. **Freeze**：标注完成后将所有标注存入 `tasks.jsonl`，git commit，且**在跑任何实验之前** freeze。标注者不能是后续分析 failure attribution 的人（避免认知偏差传递）。
5. **记录**：标注协议和每名标注者的标注原始数据保存为 `annotations/` 目录下的文件。

---

## 6. 对比系统

第一版只比较 OpenCode 原生机制下的三种系统。

### S1: Build-only Single Agent

只使用 OpenCode primary agent `Build`。

约束：

- 禁止调用 subagent。
- Build 直接完成 issue 理解、代码搜索、修改、测试和 patch 提交。

目标：建立 single-agent baseline。

### S2: Build + Explore

允许 Build 调用 OpenCode 内置 `Explore` subagent。

约束：

- Explore 只负责代码库探索、文件定位、符号搜索和 bug hypothesis。
- Explore 不允许修改代码。
- Build 负责最终 patch。

目标：测试只读探索型 subagent 是否能提升代码修复效果。

### S3: Build + Explore + General

允许 Build 调用 `Explore` 和 `General` 两类 subagent。

约束：

- Explore 用于快速只读代码探索。
- General 用于更复杂的分析或子任务处理。
- Build 仍负责最终 patch 和提交。

目标：测试 OpenCode 原生多 subagent 可用时，dynamic spawn 是否优于 Build-only 与 Build+Explore。

### 6.4 Stage 0: General Subagent 行为观察

`General` subagent 的定义在 OpenCode 中较为宽泛（"更复杂的分析或子任务处理"）。如果 General 与 Explore 的实际行为高度重叠，S3 vs S2 将没有信号。此外，Qwen3.5-9B 对 OpenCode subagent 指令的遵循程度未知。

因此在 Stage 0 必须完成以下观察：

**观察内容：**

- General 被 Build 调用时的 prompt 内容（Build 要求 General 做什么？）
- General 的实际输出模式（代码探索？分析推理？代码生成？）
- General 与 Explore 在相同 task 下的行为差异
- 是否有 task 类型天然适合 General 而非 Explore
- Qwen3.5-9B 是否遵守 "Explore 只读" 约束

**判定标准：**

- 如果 General 和 Explore 在 ≥70% 的 runs 中行为高度重叠 → 考虑 S3 仅用 Build+Explore，或替换为更差异化的 subagent
- 如果 General 表现出独特行为模式（如：分析已有文件内容得出结构性结论，而非搜索新文件）→ 保留但记录行为差异
- 如果 Qwen3.5-9B 频繁违反 subagent 约束（如 Explore 修改代码）→ 报告为模型限制，考虑在 system prompt 中强化约束

---

## 7. 预算控制

### 7.1 核心原则

本实验不强制限制所有 agent 可读取的输入上下文总量。因为真实 coding-agent 场景中，multi-agent 的优势之一就是不同 agent 可以阅读不同文件或日志。

但必须记录 total input tokens（跨所有 agent 求和），并在分析中作为协变量控制，以区分 "spawn 操作本身的价值" 与 "多读了文件导致成功率提升"。

对于 output tokens，使用 calibration-based budget。在本地推理场景中，output cap 直接决定了 per-run 推理时间，是比 API cost 更实际的约束。

### 7.2 Calibration-Based Budget 确定

**步骤：**

1. Stage 0 运行时**不设 output cap**（或设一个远高于预期的宽松上限，如 100k tokens）。
2. 记录每个系统在每个 task 上的真实 output token 消费，得到分布。
3. 取 S1（Build-only）在全部 5 个 task 上的 **median output tokens**，记为 `M_s1`。
4. Stage 1/2 的 per-task budget 设为：

```text
per-task total output cap = M_s1 × 1.5
```

**理由：**

- 基于 S1 的实际消费设定 baseline，而非人工猜测。
- ×1.5 系数给 S2/S3 留出 orchestration 空间，避免 budget 系统性地对 multi-agent 不利（S2/S3 有 coordination overhead，S1 没有）。
- 如 Stage 0 数据显示 S1 的 output 已经很高（如 80k+），可考虑用 75th percentile 替代 median，或设绝对 cap（如 64k）。

### 7.3 系统预算分配（Equal-output-token budget）

```text
S1 Build-only:              Build output ≤ cap
S2 Build + Explore:         Build + Explore output ≤ cap
S3 Build + Explore + General: Build + Explore + General output ≤ cap
```

Dynamic 系统（S2、S3）可自由分配预算，但总输出不得超过 cap。

**实施方式：** OpenCode 提供 token tracking 机制。当累计 output tokens 接近 cap 时（如达到 90%），向 primary agent 注入 warning message 提示 budget 即将耗尽。

### 7.4 记录但不强控的成本

所有 runs 必须记录：

- **Per-agent input tokens**（每个 agent 的独立输入量）
- **Total input tokens**（跨所有 agent 求和）
- **Per-agent output tokens**
- **Total output tokens**
- total tokens
- tool calls
- wall-clock runtime
- **GPU inference time**（各 agent 占用 GPU 的实际推理时长）
- subagent call count
- tokens per success

### 7.5 输入 Token 的协变量分析

由于不控制输入 token，multi-agent 系统可能天然阅读更多文件。为分离此效应：

1. **记录** 每 run 的 total_input_tokens（跨所有 agent）。
2. **分析时** 将 total_input_tokens 作为协变量纳入统计模型：

```text
success ~ system + total_input_tokens + difficulty_bucket + (1|task_id)
```

3. **解释**：如果加入 input_tokens 协变量后 system 效应仍显著，说明 spawn 操作的价值独立于多读文件；如果不再显著，说明 multi-agent 的优势主要来自上下文覆盖而非 spawn 架构。

### 7.6 Natural-budget 附加设置

如资源允许，可以追加 Natural-budget setting：

- 各系统按真实 OpenCode workflow 运行；
- 不强行统一 output cap；
- 记录真实成本和 GPU 时间；
- 用于分析真实使用时的 success-cost tradeoff。

第一版主表以 Equal-output-budget 为主，Natural-budget 作为附表或后续实验。

---

## 8. 工具与执行环境

所有系统使用统一代码仓库环境与测试接口。

### 8.1 工具集

需要支持：

- repo checkout
- issue/task loading
- code search
- file open
- file edit
- test execution
- git diff capture
- patch submission
- evaluator execution

所有工具调用必须记录：

```json
{
  "tool": "search_code | open_file | edit_file | run_tests | git_diff | submit_patch",
  "agent": "build | explore | general",
  "args": {},
  "result_summary": "...",
  "success": true,
  "runtime_sec": 0
}
```

### 8.2 Patch Evaluator 规范

使用 **SWE-bench 官方 evaluation harness**（或等效的自动化评估脚本）。

**成功标准：**

- **Primary**：所有 regression tests 通过，且 issue-specific tests（如有标注）通过。
- **Secondary**：仅 issue-specific tests 通过（regression 允许部分失败，但需记录）。
- **Fail**：regression tests 或 issue-specific tests 仍失败。

**Evaluator 可靠性验证（Stage 0）：**

1. 在 5 个 candidate task 上，对 ground-truth patch 运行 evaluator，确认得到 expected pass。
2. 对每个 task 注入一个 knowingly-broken patch，确认 evaluator 能检测到 failure。
3. 记录 flaky test 情况：如某 task 的同一 patch 在多次 evaluator 运行中结果不一致（flaky），标记该 task 并在分析时排除或单独处理。

### 8.3 OpenCode + vLLM 集成 🟡 新增

OpenCode 通过 OpenAI-compatible API 连接本地 vLLM 实例。

#### 配置要点

```yaml
# OpenCode config (示例)
provider: openai_compatible
base_url: http://localhost:8000/v1
api_key: not-needed                    # vLLM 本地推理无需认证
model: qwen3.5-9b                      # 模型标识符
```

#### 验证清单（Stage 0）

- [ ] OpenCode 能成功发起 chat completion 请求到 vLLM
- [ ] OpenCode 的 tool call 格式与 Qwen3.5-9B 的 function calling 兼容
- [ ] vLLM 返回的 token usage 可以被 OpenCode 正确解析
- [ ] OpenCode 的 subagent spawn 能正确配置为使用同一 vLLM endpoint
- [ ] GPU OOM 或推理超时时有 graceful failure（不丢失已记录日志）

---

## 9. 日志格式

每个 run 保存为一条 JSONL。

```json
{
  "run_id": "...",
  "task_id": "...",
  "repo": "...",
  "system": "build_only | build_explore | build_explore_general",
  "budget_setting": "equal_output | natural",
  "difficulty_bucket": "local_readable | multi_file | long_context | multi_hypothesis",
  "issue": "...",
  "final_patch": "...",
  "success": true,
  "tests_pass": true,
  "eval_result": {
    "regression_tests_total": 0,
    "regression_tests_passed": 0,
    "issue_tests_total": 0,
    "issue_tests_passed": 0
  },
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
  "gpu_id": 0,
  "tool_calls": {
    "build": 0,
    "explore": 0,
    "general": 0,
    "total": 0
  },
  "subagent_calls": [
    {
      "call_id": "...",
      "subagent": "explore | general",
      "context_pass_summary": "...",
      "input_summary": "...",
      "output_summary": "...",
      "output_tokens": 0,
      "input_tokens": 0,
      "gpu_inference_sec": 0,
      "used_by_build": true,
      "help_label": "helped | harmed | neutral | unknown"
    }
  ],
  "failure_analysis": {
    "failed": true,
    "annotator_1": {
      "primary_failure_type": "...",
      "secondary_failure_types": [],
      "notes": "..."
    },
    "annotator_2": {
      "primary_failure_type": "...",
      "secondary_failure_types": [],
      "notes": "..."
    },
    "consensus_failure_type": "...",
    "agreement": true
  }
}
```

---

## 10. 核心指标与统计分析

### 10.1 任务成功指标

- `task_success_rate`
- `tests_pass_rate`
- `patch_apply_success_rate`

### 10.2 成本指标

- `output_tokens_per_task`
- `output_tokens_per_success`
- `runtime_per_task`
- `gpu_inference_sec_per_task`
- `tool_calls_per_task`
- `subagent_calls_per_task`

### 10.3 架构对比指标

- `build_explore_delta = success(Build+Explore) - success(Build-only)`
- `build_explore_general_delta = success(Build+Explore+General) - success(Build-only)`
- `general_marginal_gain = success(Build+Explore+General) - success(Build+Explore)`

### 10.4 subagent 使用指标

- `subagent_call_rate`
- `explore_call_rate`
- `general_call_rate`
- `explore_help_rate`
- `general_help_rate`
- `subagent_harm_rate`
- `unnecessary_subagent_rate`

### 10.5 失败归因指标

- `explore_missed_rate`
- `general_noise_rate`
- `integration_error_rate`
- `patch_error_rate`
- `test_error_rate`
- `budget_error_rate`
- `environment_error_rate`

### 10.6 任务复杂度交互

按以下维度分组分析：

- difficulty_bucket
- estimated_repo_context_tokens
- num_candidate_files
- num_relevant_files
- requires_cross_file_reasoning
- requires_parallel_hypothesis_search

重点看：

> subagent workflow 的收益是否随任务复杂度上升而增加。

### 10.7 统计分析方法预声明

以下统计方法在实验执行前声明，防止 p-hacking。

#### 主效应检验

**Model：** Mixed-effects logistic regression

```r
glmer(success ~ system + difficulty_bucket + log(total_input_tokens) + (1|task_id),
      family = binomial, data = runs)
```

- `system` 为固定效应（S1/S2/S3）。
- `task_id` 为 random intercept，控制 task 间方差。
- `log(total_input_tokens)` 为协变量（§7.5）。
- 从模型中提取 system 的 marginal effect 和 95% CI。

#### 成对比较

- S1 vs S2, S1 vs S3, S2 vs S3。
- 使用 **bootstrap percentile CI**（10,000 resamples, clustered by task_id），而非 naive t-test。
- 报告 bootstrap p-value。

#### 复杂度交互

- 对每个 difficulty_bucket 分层计算各系统的 success rate 和 bootstrap CI。
- 不做 formal interaction test（样本量不足），只做描述性趋势分析。

#### 效应量

- 报告 **Cohen's h**（两个 proportion 之间的效应量）用于 success rate 比较。
- 参考：h = 0.2 小，0.5 中，0.8 大。

#### 报告规范

- 不做 null-hypothesis significance testing 的二元 "显著 / 不显著" 判定。
- 报告 point estimate + 95% bootstrap CI + effect size。
- 强调 "信号方向与幅度"，而非 "p < 0.05"。

---

## 11. 失败归因标准与可靠性

### 11.1 explore_missed

Explore 被调用，但没有找到关键文件、关键函数或关键错误位置。

### 11.2 general_noise

General 输出错误方向、错误解释或无关分析，误导 Build。

### 11.3 unnecessary_subagent

调用 subagent 但没有带来有效信息（Build 未采纳 subagent 输出，且 subagent 输出与最终 patch 无关），且消耗预算。

### 11.4 subagent_harm

subagent 输出直接导致 Build 选择错误修改方向。区别于 general_noise：harm 要求 causal link（Build 的行为确实被 subagent 改变了）。

### 11.5 integration_error

subagent 输出包含有用信息（找到了相关文件或正确 hypothesis），但 Build 没有采纳或整合错误。区别于 explore_missed：前者是 subagent 没找到好信息，后者是找到了但 Build 没用。

### 11.6 patch_error

定位基本正确，但最终代码修改有语法错误、逻辑错误或不完整的 fix。

### 11.7 test_error

测试运行失败、环境异常、依赖错误或测试日志解释错误。

### 11.8 budget_error

探索或 subagent 调用消耗过多预算，导致没有足够预算完成 patch 或 review。

### 11.9 归因标注协议与可靠性检验

失败归因依赖人工判断。为保证可靠性：

#### 标注流程

1. **双人独立标注**：两名标注者独立审查每个 failed run 的完整日志（issue、agent 对话历史、tool calls、subagent output、final patch、eval result）。
2. **标注内容**：每名标注者标记：
   - `primary_failure_type`（从 11.1–11.8 中选择，允许 `other`）
   - `secondary_failure_types`（可选，允许多个）
   - 自由文本 `notes`
3. **共识**：对比两人标注。一致的直接采纳。不一致的讨论达成共识，记录讨论结果。
4. **分层抽样**：标注者不应知道 run 属于哪个 system，以减少确认偏差。

#### 标注指南（决策树）

标注时按以下优先级判定：

```
1. 环境或测试基础设施故障？ → test_error 或 environment_error
2. 预算耗尽且无法判断后续是否会成功？ → budget_error
3. Subagent 被调用吗？
   a. 否 → 直接判定 patch_error（Build 自己没改对）
   b. 是 → 继续
4. Subagent 找到了正确信息吗？
   a. 否 → explore_missed 或 general_noise
   b. 是 → 继续
5. Build 采纳了 subagent 的信息吗？
   a. 否 → integration_error
   b. 是 → 继续
6. Build 的 patch 错在哪里？
   a. 被 subagent 误导 → subagent_harm
   b. Build 自己改错 → patch_error
```

#### 可靠性指标

- 计算 **Cohen's κ**（两名标注者间一致性）。
- 目标：κ ≥ 0.6（substantial agreement）。
- 如 κ < 0.4，修订标注指南后重新标注。
- 在 Stage 1 报告中明确呈现 κ 值。

---

## 12. 分析问题

第一阶段实验要回答以下问题：

1. Build-only、Build+Explore、Build+Explore+General 谁的 task_success_rate 更高？
2. 在 equal-output-token budget 下，subagent workflow 是否仍有优势？
3. 控制 total_input_tokens 后，subagent 系统的优势是否仍然存在？
4. Explore 是否主要帮助 multi-file / long-context tasks？
5. General 是否带来额外收益，还是主要引入噪声？
6. subagent workflow 的失败主要来自 subagent 输出错误，还是 Build 整合错误？
7. multi-agent 的收益是否随任务复杂度上升而增加？
8. **🟡 新增**：Qwen3.5-9B 在 SWE-bench 级别任务上的 spawn 行为与已知的强模型行为有何差异？
9. 哪些日志字段可以作为后续 RL spawn policy 的 state/action/reward？

---

## 13. 后续 RL 接口

本阶段不直接训练 RL，但日志必须支持后续 policy learning。

### 13.1 State

可用于 RL 的 state 包括：

- issue summary
- repo/task complexity features
- files already opened
- search results
- current patch status
- test status
- subagent reports
- budget remaining
- previous tool calls
- total_input_tokens consumed so far
- total_output_tokens consumed so far

### 13.2 Action

后续 RL policy 可学习：

- solve_direct / continue_build
- call_explore
- call_general
- ask_followup_to_subagent
- run_tests
- submit_patch
- stop

### 13.3 Reward

初始 reward 设计：

```text
+1 task success
-1 task failure
-λ output_tokens
-μ tool_calls
-α unnecessary_subagent
-β subagent_harm
+γ useful_subagent
```

第一阶段可先训练：

- spawn-or-not classifier
- which-subagent classifier
- contextual bandit
- offline policy learning

不建议一开始做 full joint RL。

---

## 14. 预期结果解释

### 14.1 Build+Explore 在 hard tasks 上提升

说明代码库探索型 subagent 有价值，收益来自上下文覆盖和 bug 定位。

### 14.2 Build+Explore+General 低于 Build+Explore

说明 General 可能引入噪声或消耗预算，后续 RL 需要学习何时调用 General。

### 14.3 Build-only 在 local_readable 上最好

说明 subagent 不应默认启用，spawn 应该是条件化决策。

### 14.4 multi-agent 只在 Natural-budget 胜出

说明 subagent 系统可以用更多计算换成功率，但 equal-budget 下没有架构效率优势。

### 14.5 multi-agent 在 Equal-budget 也胜出

这是最强结果，说明 subagent 架构提高了计算利用效率。

### 14.6 控制 input_tokens 后系统效应消失

说明 multi-agent 的优势主要来自能读更多上下文，而非 spawn 架构本身。这对后续设计有重要启示：如果结论如此，spawn policy 应聚焦于 "如何帮 agent 更高效地覆盖相关上下文"，而非 "何时 spawn subagent"。

### 14.7 所有系统 success rate 都极低（< 20%）

**这是本地小模型的现实可能性。** 如果发生：
- 报告为 key finding：小模型在 SWE-bench 上的 spawn 行为与强模型有本质差异。
- 仍分析 subagent 使用模式（在 task 成功或部分完成时，subagent 是否起到了 positive role）。
- 降级任务难度后重新跑。
- 成功标准 §16 仍成立，因为核心目标是 "有据可查的观察" 而非 "高成功率的系统"。

---

## 15. 第一阶段输出文件

建议输出：

```text
outputs/opencode_spawn_pilot/
├── tasks.jsonl              # 预注册的任务标注（含 freeze 时间戳）
├── annotations/             # 原始标注数据
│   ├── annotator_1/
│   └── annotator_2/
├── runs.jsonl               # 完整运行日志
├── summary_by_system.csv
├── summary_by_difficulty.csv
├── summary_by_budget.csv
├── subagent_usage.csv
├── failure_analysis.csv     # 含双人标注一致性报告
├── calibration/             # Stage 0 calibration 数据
│   └── output_token_distribution.csv
├── vllm_metrics/            # vLLM 性能数据
│   ├── throughput_by_run.csv
│   └── gpu_utilization.csv
├── capability_check/        # 模型能力预验证结果 (§4.3)
│   └── prevalidation_report.md
├── patches/
├── logs/
└── report.md
```

---

## 16. 阶段性成功标准

第一阶段不要求证明 multi-agent 一定更好。只要满足以下条件，就算成功：

1. 三组系统完整跑通 10–30 个代码任务（每个组合 ≥3 次）。
2. 能稳定记录 subagent calls、per-agent token usage、tool calls 和 final patch。
3. 能自动评估 patch success，且 evaluator 通过 Stage 0 可靠性验证。
4. 能区分至少 3 类失败来源，且双人标注 Cohen's κ ≥ 0.6。
5. 能观察到 subagent 在某些任务类型上有帮助或有害的趋势。
6. 能基于日志定义后续 RL 的 state/action/reward。
7. Calibration 阶段确定了合理的 per-task output budget（基于 S1 实际数据，非猜测值）。
8. 任务标注在实验前 freeze，标注者和归因分析者分离。
9. **🔴 新增：** 模型能力预验证（§4.3）的五项验证全部完成并记录结论——即使结论是 "Qwen3.5-9B 在 SWE-bench 上能力不足，需调整任务难度"。
10. **🟡 新增：** OpenCode + vLLM 集成验证通过（§8.3 五项 checklist）。

---

## 17. 当前主线表述

本实验不再试图证明 subagent 天然存在目标错位，而是研究：

> spawn subagent 是一个可优化的操作。我们在本地小模型（Qwen3.5-9B + vLLM）上，用 OpenCode 原生 Build / Explore / General 机制评估 single-agent 与 subagent workflow 的差异——这是第一个系统性地在 9B 级别模型上研究 spawn subagent 行为的实验。

一句话总结：

**用 Qwen3.5-9B + vLLM 本地推理驱动 OpenCode，在 SWE-bench 代码修复任务上比较 single-agent 与 subagent workflow，在 calibration-based 相同输出预算下分析 subagent 是否在小模型上也能通过上下文探索和任务分工提升成功率，控制输入 token 协变量分离 "多读" 与 "分工" 效应，通过双人标注和预注册保证归因可靠性，为后续 RL spawn policy 提供小模型特有的轨迹数据。**

---

## 附录 A：v0.2 → v0.3 关键差异

| 维度 | v0.2 | v0.3 |
|------|------|------|
| 推理引擎 | Anthropic API | vLLM 0.19.1 本地推理 |
| 模型 | Claude Sonnet 4 | Qwen3.5-9B (19GB) |
| 硬件 | 不涉及 | 2× A100 40GB（7 总，5 被占） |
| API cost | $400–800 | $0（电力除外） |
| 预算约束 | API cost | GPU 推理时间 + output cap |
| 模型能力前提 | 默认足够 | 必须 Stage 0 预验证 |
| 研究增量 | spawn 策略对比 | spawn 策略 + 小模型行为 |
| 最重要风险 | API 可达性 | 模型能力不足导致 floor effect |
| vLLM 集成 | 不需要 | 新增 §3.4 §8.3 |
| 模型能力预验证 | 不需要 | 新增 §4.3（关键必选项） |
