# OpenCode Spawn Pilot

研究小模型（Qwen3.5-9B）在 OpenCode 框架下的 subagent spawn 能力是否对多跳问答有帮助。

**核心问题**：当让 Build Agent 可以 spawn subagent 并行搜索文档时，准确率是否比单 agent 更高？Spawn 解决了什么问题？什么还是解决不了的？

---

## 环境要求

### 1. vLLM 服务（模型推理后端）

```bash
# 启动 Qwen3.5-9B（19GB，4 shards）
bash scripts/start_vllm.sh
```

验证：`curl http://localhost:8010/v1/models` 应返回 qwen35-9b。

### 2. OpenCode 配置

OpenCode 使用 `--message @/path/to/prompt.txt` 传入 system prompt。配置在 `~/.config/opencode/opencode.json` 的设置被**忽略**，只有 `--message` 参数有效。

---

## 快速开始

### 启动 vLLM

```bash
cd /home/jinxu/opencode-spawn-pilot
bash scripts/start_vllm.sh
```

### 运行实验

```bash
# Force-Multi 实验（强制 spawn）
python3 scripts/run_fm_v12.py

# Single 基线实验（无 spawn）
python3 scripts/run_single_v12.py

# Agent-Decides 实验（模型自主决定是否 spawn）
python3 scripts/run_agent_decides_v13.py
```

### 查看结果

```bash
# FM v12 结果
cat outputs/opencode_spawn_pilot/comparison_v12/results_fm_v12.jsonl

# Single v12 结果
cat outputs/opencode_spawn-pilot/comparison_v12_single/results_single_v12.jsonl

# Agent-Decides v13 结果
cat outputs/opencode_spawn_pilot/comparison_v13_agent_decides/results_agent_decides_v13.jsonl
```

---

## 目录结构

```
opencode-spawn-pilot/
├── scripts/
│   ├── start_vllm.sh              # 启动 vLLM
│   ├── run_fm_v12.py              # Force-Multi 实验
│   ├── run_single_v12.py           # Single 基线实验
│   ├── run_agent_decides_v13.py    # Agent-Decides 实验
│   └── expand_tasks_60.py         # 数据集扩展
├── outputs/opencode_spawn_pilot/
│   ├── task_data_v2/              # 55 个任务 JSON
│   ├── comparison_v12/            # FM v12 完整结果（55 任务）
│   ├── comparison_v12_single/     # Single v12 完整结果（55 任务）
│   └── comparison_v13_agent_decides/ # Agent-Decides v13 完整结果（55 任务）
├── SPEC.md                        # 详细规范 + 版本历史
├── ENV.md                         # 环境状态
└── README.md                      # 项目概览
```

---

## 实验结果（v12/v13 三类对比）

**55 任务完整配对对比（2025-05-02）**

| 模式 | 准确率 (fuzzy) | Spawn率 | Subagent返回率 |
|------|---------------|---------|---------------|
| **Single** | **23/55 (42%)** | 0% | — |
| Agent-Decides | 17/55 (31%) | 6/55 (11%) | 2/55 (4%) |
| Force-Multi | 15/55 (27%) | 42/55 (76%) | 31/55 (56%) |

> ⚠️ 旧数据（FM subagent返回率 0%）是 OpenCode binary v1.3.6 JSONL 输出 bug 导致的。修复后为 56%。

### 关键发现

1. **Single 基线表现最好**（42%）—— 强制 spawn 反而降低了准确率
2. **OpenCode binary bug**：v1.3.6 JSONL 不输出 `tool_result` 事件，旧检测逻辑 100% 失效
3. **Subagent 实际返回了**（56%）：通过 token delta + 文本引用确认 subagent 结果被内部注入
4. **Spawn 帮助有限**：即使 subagent 返回，spawn 准确率也只有 32.3%（vs Single 42%）
5. **核心瓶颈是推理能力，不是搜索能力**

### 结论

- **强制 spawn 有害无益**：FM 准确率比 Single 低 15 个百分点
- **模型不愿主动 spawn**：AD 模式只有 11% spawn 率
- **推理是瓶颈**：9B 模型不是搜索能力不够，是链式推理能力不够

---

## 实验设计

### 三个模式对比

| 模式 | 说明 | Spawn 行为 |
|------|------|-----------|
| **Single** | 单 agent，直接搜索文档 | **不提及** `task()` 工具 |
| **Agent-Decides（AD）** | 单 agent，**告知**可用 `task()` 工具，让模型自主决定 | **可选** spawn |
| **Force-Multi（FM）** | 单 agent，但**强制**通过 `task()` spawn 子代理 | **必须** spawn |

### 任务数据集

- **来源**：MuSiQue（多跳问答标准数据集）+ 少量 HotpotQA 原始任务
- **难度分层**：2-hop、3-hop、4-hop
- **任务数**：55 个任务

### 评估标准

**Fuzzy is_correct**（v12 采用）：
1. 标准化（去标点小写）后严格相等
2. 答案核心词（跳过句首 stopwords）是预测的连续子串
3. 答案的所有内容词均作为完整词出现在预测中

---

## OpenCode System Prompt 源码

OpenCode 的 system prompt 源码公开在 [GitHub](https://github.com/opencode-ai/opencode)：

- `internal/llm/prompt/coder.go` — AgentCoder prompt（详细 system 指令）
- `internal/llm/agent/agent-tool.go` — Agent 工具定义（只能访问 GlobTool, GrepTool, LS, View）

> 注：`opencode run` 命令的部分实现在预编译二进制中，未完全开源。

---

## 历史版本

| 版本 | 说明 |
|------|------|
| v13 | 新增 Agent-Decides 模式（模型自主决定是否 spawn） |
| v12 | 三类实验完整配对（55 任务），Force-Multi / Single / Agent-Decides |
| v11 | 30 任务配对实验，FM 53% vs Single 50%（+3%） |
