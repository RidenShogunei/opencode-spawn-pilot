# OpenCode Spawn Pilot

研究小模型（Qwen3.5-9B）在 OpenCode 框架下的 subagent spawn 能力是否对多跳问答有帮助。

**核心问题**：当让 Build Agent 可以 spawn subagent 并行搜索文档时，准确率是否比单 agent 更高？spawn 解决了什么问题？什么还是解决不了的？

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

### 运行 Force-Multi 实验（v12）

```bash
python3 scripts/run_fm_v12.py
```

### 运行 Single 基线实验（v12）

```bash
python3 scripts/run_single_v12.py
```

### 运行 Agent-Decides 实验（v13，新增）

```bash
python3 scripts/run_agent_decides_v13.py
```

### 查看结果

```bash
# FM v12 结果
cat outputs/opencode_spawn_pilot/comparison_v12/results_fm_v12.jsonl

# Single v12 结果
cat outputs/opencode_spawn_pilot/comparison_v12_single/results_single_v12.jsonl

# Agent-Decides v13 结果（新增）
cat outputs/opencode_spawn_pilot/comparison_v13_agent_decides/results_agent_decides_v13.jsonl
```

---

## 目录结构

```
opencode-spawn-pilot/
├── scripts/
│   ├── start_vllm.sh          # 启动 vLLM
│   ├── run_fm_v12.py          # Force-Multi 实验
│   ├── run_single_v12.py      # Single 基线实验
│   ├── run_agent_decides_v13.py # Agent-Decides 实验（新增）
│   └── expand_tasks_60.py     # 数据集扩展
├── outputs/opencode_spawn_pilot/
│   ├── task_data_v2/           # 55 个任务 JSON
│   ├── comparison_v12/         # FM 完整结果
│   ├── comparison_v12_single/  # Single 部分结果
│   └── comparison_v13_agent_decides/ # Agent-Decides 结果（新增）
├── SPEC.md                     # 详细规范 + 版本历史
├── ENV.md                      # 环境状态
└── README.md                   # 项目概览
```

---

## 实验设计

### 三个模式对比

| 模式 | 说明 | Spawn 行为 |
|------|------|------------|
| **Single** | 单 agent，直接搜索文档 | **不提及** `task()` 工具 |
| **Agent-Decides（AD）** | 单 agent，**告知**可用 `task()` 工具，让模型自主决定 | **可选** spawn |
| **Force-Multi（FM）** | 单 agent，但**强制**通过 `task()` spawn 子代理 | **必须** spawn |

**核心假设**：之前"模型从不主动 spawn"的结论可能是 prompt 传递机制的问题（`--message` 参数 vs 配置文件），而非模型本身的能力限制。Agent-Decides 模式用于验证这一假设。

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

## 核心发现

### 发现 1：模型从不自愿 spawn（v5.0：22 任务 0 spawn）

即使文档有 100 个段落，任务对并行搜索有明显帮助，模型也从不主动使用 `task()` 工具。

### 发现 2：强制 spawn 后 spawn 率达到 92-100%

用强制提示词后模型能服从，但 subagent **返回结果**的比例只有 35%。

### 发现 3：Spawn 解决搜索，不解决推理

| 任务类型 | Spawn 帮助程度 |
|----------|---------------|
| 直接提取（数字、人名、事实） | ✅ 有效，subagent 找到就对了 |
| 链式推理（空间关系、多跳逻辑） | ❌ 无效，subagent 找到信息，Build Agent 还是推理错 |

**典型失败**："A 在 B 以南 25 英里" → subagent 找到了 A 和 B 的位置，但 Build Agent 不会逆推出 "B 在 A 以北 25 英里"。

### 发现 4：9B 模型的推理是瓶颈，不是搜索

核心结论：**subagent 的搜索能力不是问题，Build Agent 的链式推理能力才是瓶颈。**

---

## 当前实验状态

### v12（当前版本）

| 模式 | 任务数 | 准确率 | Spawn 率 | Subagent 返回率 |
|------|--------|--------|----------|----------------|
| Force-Multi | 55 | 7/55 (13%) strict, **20/55 (36%) fuzzy** | 100% | 35% |
| Single | 18/55（跑中） | 12/18 (67%) fuzzy | 0% | — |

> 注：FM fuzzy 20/55 和 Single 12/18 均为 fuzzy is_correct，不完全可比。

### v13（新增 — Agent-Decides）

| 模式 | 任务数 | 准确率 | Spawn 率 |
|------|--------|--------|----------|
| Agent-Decides | 待运行 | — | — |

**预期对比**：
- 如果 AD spawn 率 > 0%：说明之前"模型从不主动 spawn"是 prompt 传递问题，不是模型能力问题
- 如果 AD spawn 率 ≈ 0%：说明即使正确告知工具，9B 模型仍不会自主 spawn
- 如果 AD 准确率 ≈ Single：说明 spawn 决策不影响最终表现
- 如果 AD 准确率 ≈ FM：说明模型能正确判断何时需要 spawn

### v11（有完整配对）

| 模式 | 任务数 | 准确率 |
|------|--------|--------|
| Force-Multi | 30 | 16/30 (53%) |
| Single | 30 | 15/30 (50%) |

Spawn 带来了 **+3%** 的提升（53% vs 50%）。

---

## 已验证结论

1. **模型不主动 spawn**：在可选 spawn 模式下，Qwen3.5-9B 从不主动 spawn
2. **强制 spawn 有效**：用 prompt 强制后，92-100% 任务确实 spawn 了
3. **Spawn 有上限**：即使强制 spawn，3/4 跳复杂推理任务仍然失败，根因是 Build Agent 推理链断裂
4. **推理是瓶颈**：9B 模型不是搜索能力不够，是链式推理能力不够
