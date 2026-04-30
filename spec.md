# OpenCode Spawn Pilot — Research Specification

版本：v0.6
上一版本：v0.5
变更类型：研究目标与架构完全重构
主题：用 Qwen3.5-9B + vLLM + OpenCode 在 MuSiQue multi-hop QA 上，对比 agent 在 spawn 关闭 vs 打开时的行为差异。

---

## 0. 核心研究问题

**在成熟 agent 架构下，subagent spawn 能力在不同长度任务上是否以及如何影响 agent 表现？**

具体而言：

- 2-hop（简单）：agent 自己能覆盖，spawn 是否多余？
- 3-hop（中等）：spawn 能否帮助覆盖额外 hop？
- 4-hop（复杂）：spawn 是否成为必要能力？

这是一个探索性研究，不是对照假设验证。没有"成功"或"失败"——只有观察到的行为模式。

---

## 1. 环境配置

### 1.1 vLLM / 模型配置

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

### 1.2 OpenCode 配置

```yaml
framework: OpenCode 1.3.6
provider: openai_compatible
base_url: http://localhost:8010/v1
model: qwen35-9b
mode: foreground only
```

OpenCode 已配置 agents：build (primary), explore (subagent), general (subagent), plan, summary, title, compaction。

### 1.3 Token 限制

```yaml
max_tokens: 8192  # per request cap
```

---

## 2. 任务域

### 2.1 Benchmark

```yaml
benchmark: MuSiQue
format: multi-hop QA
context: 20-paragraph document pool per question
supporting_evidence: 2–4 support paragraphs per question
answer_type: short answer
```

### 2.2 任务分层

| Bucket           | MuSiQue hop | 任务数 | 含义                       |
| ---------------- | ----------: | --: | ------------------------ |
| `local_readable` |       2-hop |   3 | 2 段支持，Build-only 有机会直接覆盖 |
| `multi_file`     |       3-hop |   3 | 3 段跨文档追踪                 |
| `long_context`   |       4-hop |   4 | 4 段支持藏在 20 段文档中          |

总计：10 任务。

---

## 3. 两种模式设计

### 3.1 Mode A: spawn_closed

```text
Build agent 单独运行
subagent 系统：关闭（配置层面）
Build 对 subagent 的存在：完全不知道
```

实现方式：

- OpenCode 运行 `opencode run --agent build`
- Build 的 prompt 不包含任何关于 subagent 的提示
- 即使 Build 尝试调用 explore 命令，subagent 配置也是禁用的（Mode B 中启用）

### 3.2 Mode B: spawn_open

```text
Build agent 单独运行（但可以自行决定 spawn）
subagent 系统：开启
Build 对 subagent 的存在：知道如何使用
```

实现方式：

- OpenCode 运行 `opencode run --agent build`
- Build 的 prompt 包含一个如何调用 explore subagent 的示例
- Build 自己决定何时使用 spawn

Spawn 提示（Mode B Build prompt 中）：

```
If you want to delegate document exploration to a subagent, you can call:
   opencode run --agent explore --dir <workdir> -- <your exploration task>
   The explore subagent will search documents and return findings.
```

Build 会在处理任务时自己判断：

- 2-hop → 大概率不 spawn，直接答
- 3-hop → 有时 spawn，有时不
- 4-hop → 很可能 spawn

---

## 4. 关键观察点

### 4.1 每次 Run 记录

每个 run 必须记录：

- `spawn_attempted`: boolean — Build 是否尝试了 spawn
- `spawn_method`: "cli" | "direct" | None — 如何检测到 spawn 尝试
- `success`: boolean — 最终答案是否正确
- `token_usage`: input / output / total tokens
- `runtime_sec`: wall-clock time
- `steps`: agent 步数

### 4.2 日志调试

日志输出到 `runs/{run_id}/stdout_build.txt`。

检测 spawn 尝试的方式：

- **CLI 模式**：在 stdout 中搜索 `opencode run --agent explore` 字符串
- **直接提及**：搜索 "spawn explore" / "call explore" 等模式

### 4.3 事后全盘阅读

所有过程结果（stdout 文件）都会被保存。研究者（你）会在实验后进行全量阅读，理解 agent 在两种模式下的行为差异。

---

## 5. 实验规模

```
10 tasks × 2 modes = 20 runs
```

每题都在两种模式下跑。没有 seed 扩展（当前阶段不需要统计显著性）。

---

## 6. 不做的事

- 不在 harness 层强制调用 Explore subagent
- 不做 4 系统（S1/S2/S3/S4）对照
- 不计算 M1-M5 机制指标（这些是 Stage 1 的遗留指标）
- 不定义"成功"或"失败"——只有观察结果

---

## 7. 期望的观察结果类型

### 7.1 行为层面

- Mode B 中，agent spawn 的频率和时机是什么？
- 在 2/3/4-hop 上，spawn 决策有何不同？
- spawn 后，Build 是否真的整合了 subagent 的输出？

### 7.2 性能层面

- Mode B 的准确率 vs Mode A 在各 hop 层级
- spawn 是否带来 token 成本上升？
- spawn 带来的收益是否值得其成本？

### 7.3 失败模式

- Build spawn 了但答案仍错 → 失败发生在哪个环节？
- Build 没 spawn 但答对了 → 是判断正确还是蒙对？
- Build spawn 了但没用结果 → integration 问题？

---

## 8. OpenCode-native Spawn 架构说明

当前实现使用 `opencode run --agent build`，Build agent 内部自己决定是否调用 `opencode run --agent explore`。这比 harness 强制调度更接近 OpenCode-native 的 spawn 理念。

两种实现路径：

| 路径 | 说明 |
| ---- | ---- |
| Harness 强制调度 | harness.py 控制调用顺序，完全可控但非 native |
| OpenCode-native spawn | Build 自己决定何时 spawn，实验更真实但不可控 |

当前选择后者，因为研究目标是 agent 自己决定 spawn 的行为，不是强制 spawn 的效果。

---

## 9. 运行环境

```bash
# 启动 vLLM
bash /home/jinxu/opencode-spawn-pilot/scripts/start_vllm.sh

# 运行实验（全部）
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py

# 运行实验（仅测试）
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py test

# 仅 spawn_closed
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py closed

# 仅 spawn_open
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py open

# 仅 2-hop
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py 2hop

# 断点续跑
python3 /home/jinxu/opencode-spawn-pilot/scripts/harness.py --resume
```
