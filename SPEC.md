# OpenCode Spawn Pilot — Research Specification

**Version: v13 — Complete three-way comparison finished**
**Status**: All 55 tasks complete for FM v12, Single v12, and AD v13
**Summary**: Single baseline (42%) outperforms both FM (27%) and AD (31%). Force-spawning subagents actually hurts accuracy. Subagent return rate is near zero, indicating the spawn mechanism itself is broken.

---

## 0. 实验结果（55任务完整配对）

**完成时间**：2025-05-02

| 模式 | 准确率 (fuzzy) | Spawn率 | Subagent返回率 |
|------|---------------|---------|---------------|
| **Single** | **23/55 (42%)** | 0% | — |
| Agent-Decides | 17/55 (31%) | 6/55 (11%) | 2/55 (4%) |
| Force-Multi | 15/55 (27%) | 25/55 (45%) | 0/55 (0%) |

### 核心发现

1. **Single 基线最强**：强制 spawn 反而降低准确率（27% vs 42%）
2. **Subagent 返回率为 0%**（FM）和 4%（AD）—— spawn 机制根本不起作用
3. **模型不愿主动 spawn**：AD 模式只有 11% spawn 率
4. **推理是瓶颈**：9B 模型的链式推理能力不够

---

## 1. 关键发现

### Finding 1：`opencode run` 不读取配置文件

**Critical.** `opencode run --format json` 完全忽略 `~/.config/opencode/opencode.json` 中的 system prompt。只有 `--message @/path/to/file.txt` 参数能传递 prompt。

**Proof**: 将 `BANANA_TEST` 标记写入 config 文件 → 模型输出无标记。通过 `--message` 传入 → 标记出现在输出中。

**修复**：将 system prompt + user prompt 合并为字符串，用 `json.dumps()` 通过 `--message @/tmp/prompt_<id>.txt` 传入。

### Finding 2：Prompt 文件位置必须在 run_dir 之外

如果 prompt 文件放在 `run_dir/` 内部，模型会把 prompt 本身当作文档读取（因为 OpenCode 的 read 工具可以访问 cwd 下的文件），导致 "BANANA_TEST" 污染输出。

**修复**：prompt 文件放在 `OUTPUT_DIR/.prompt_<task_id>_<run_id>.txt`，执行后立即删除。

### Finding 3：Single 模式之前永远返回 None

`run_single_task()` 函数内部逻辑完整，但**缺少 return 语句**，导致函数隐式返回 None。这导致 v11 Single 准确率显示为 0%（实际脚本有 bug）。

**修复**：添加 `return {...}` 语句。

### Finding 4：Strict is_correct 严重低估准确率

Strict 模式（标准化后完全相等）要求预测和答案完全一致，但自然语言答案差异很大：

- 预测 `35,402` vs 答案 `35,402 total staff` → strict 失败，fuzzy 通过
- 预测 `The African Queen` vs 答案 `African Queen` → strict 失败，fuzzy 通过

**实际影响**：FM v12 strict 13% → fuzzy 27%（提升 2 倍）

### Finding 5：Spawn 解决搜索，不解决推理

| 任务类型 | Spawn 帮助程度 |
|----------|---------------|
| 直接提取（数字、人名、事实） | ✅ 有效，subagent 找到就对了 |
| 链式推理（空间关系、多跳逻辑） | ❌ 无效，subagent 找到信息，Build Agent 推理链断裂 |

### Finding 6：9B 模型推理是瓶颈，不是搜索

即使 subagent 100% 正确返回了信息，Build Agent 也经常在最后一步推理错误。这是模型能力的问题，不是 spawn 机制的问题。

### Finding 7：Spawn 机制本身是坏的

Force-Multi 模式下，模型确实调用了 task 工具（25/55 = 45% spawn 率），但 **subagent 返回率为 0%**。这说明：

- OpenCode 的 subagent spawn 机制本身有问题
- 模型调用了 task 工具，但 subagent 从未成功执行并返回结果
- 强制 spawn 不但没帮助，反而因为破坏了 Build Agent 的直接搜索流程而降低了准确率

---

## 2. 历史实验结果

### v13 Agent-Decides（55 任务）

```
AD v13: 17/55 (31%) fuzzy
Spawn 率: 6/55 (11%)
Subagent 返回率: 2/55 (4%)
```

### v12 Force-Multi（55 任务，fuzzy is_correct）

```
FM v12: 15/55 (27%) fuzzy, 7/55 (13%) strict
Spawn 率: 25/55 (45%)
Subagent 返回率: 0/55 (0%)
```

### v12 Single（55 任务，fuzzy is_correct）

```
Single v12: 23/55 (42%) fuzzy
Spawn 率: 0%
```

### v11 配对对比（30 任务）

| 模式 | 准确率 | Spawn 率 | Subagent 返回率 |
|------|--------|----------|----------------|
| Force-Multi | 16/30 (53%) | 29/30 (97%) | 26/30 (87%) |
| Single | 15/30 (50%) | 0% | — |

**注意**：v11 的 subagent 返回率 87% 与 v12 的 0% 差异巨大，原因可能是 v11 和 v12 的 OpenCode 版本或 prompt 不同。

---

## 3. 环境

```
vLLM:      0.19.1, Qwen3.5-9B, GPU 1, port 8010
OpenCode:  1.3.6, local openai-compatible
Base URL:  http://localhost:8010/v1
Model:     local/qwen35-9b
```

启动：`bash scripts/start_vllm.sh`

---

## 4. 任务数据集（task_data_v2/）

55 个任务，来自 MuSiQue dev + 少量 HotpotQA：

| 难度 | 数量 | 示例 |
|------|------|------|
| hotpot（原始） | 6 | BBC Staff, Groom Lake 等 |
| 2-hop | 20 | 电影演员、歌曲、城堡等 |
| 3-hop | 10 | 君主所在地、词语含义等 |
| 4-hop | 19 | 县府所在地、历史首都等 |

---

## 5. 三个模式的提示词

### Single（不提及 spawn）

```
You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

RULES:
- Use the read and grep tools to search documents
- Base your answer ONLY on information found in the documents
- Do not guess or use your own knowledge

Output your final answer on its own line:
ANSWER: <your answer>
```

### Agent-Decides（告知工具，自主决定）

```
You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

You have access to two approaches for searching documents:
1. Direct search: Use read and grep tools to search documents yourself
2. Delegation: Use the 'task' tool to spawn a research subagent that searches on your behalf

WHEN TO DELEGATE (use task tool):
- When the question requires finding MULTIPLE pieces of information from different parts of the documents
- When you would need to run several separate searches and cross-reference results
- When the documents are large and parallel search would be more efficient

WHEN TO SEARCH DIRECTLY (use read/grep):
- When the question can be answered with a single search
- When you can quickly locate the answer yourself

DELEGATION FORMAT:
  task(description="<short topic>", prompt="Read <FILEPATH> and find <INFO>", subagent_type="general")

After any subagent completes, review its findings and give your final answer.

Output your final answer on its own line:
ANSWER: <your answer>
```

**设计原则**：
1. 不禁止 read/grep（模型需要这些工具）
2. 解释 WHEN，不只是 HOW（模型用成本收益分析）
3. 承认直接搜索是有效的（减少认知失调）
4. 让模型自己决定（模型的内部工具选择启发式往往是正确的）
5. 框定为战略选择（"有助手的主管" > "必须使用子代理"）

### Force-Multi（必须 spawn）

```
You are a research agent. You MUST use the 'task' tool to spawn subagents for ALL document searches.

CRITICAL RULE:
- You MUST spawn at least one subagent using task(...) to search documents before answering
- task(description="<topic>", prompt="Read <FILEPATH> and find <info>", subagent_type="general")

After the subagent completes, synthesize the findings and give your answer.

ANSWER: <your answer>
```

---

## 6. OpenCode System Prompt 源码

OpenCode 的 system prompt 源码公开在 GitHub：

- `internal/llm/prompt/coder.go` — AgentCoder prompt（完整 system 指令）
- `internal/llm/agent/agent-tool.go` — Agent 工具定义

> 注：`opencode run` 命令的部分实现在预编译二进制中，未完全开源。

---

## 7. 运行命令

```bash
# 启动 vLLM
bash scripts/start_vllm.sh

# 运行 Force-Multi（v12，55 任务）
python3 scripts/run_fm_v12.py

# 运行 Single 基线（v12，55 任务）
python3 scripts/run_single_v12.py

# 运行 Agent-Decides（v13，55 任务）
python3 scripts/run_agent_decides_v13.py

# 扩展任务数据集
python3 scripts/expand_tasks_60.py
```

---

## 8. 指标定义

| 指标 | 定义 |
|------|------|
| `spawned` | 模型是否调用了 task() 工具 |
| `subagent_returned` | subagent 是否返回了结果 |
| `correct` | fuzzy is_correct 判断答案正确 |
| `accuracy` | correct / total |

---

## 9. 文件索引

| 文件/目录 | 说明 |
|----------|------|
| `scripts/run_fm_v12.py` | FM 实验脚本 |
| `scripts/run_single_v12.py` | Single 实验脚本 |
| `scripts/run_agent_decides_v13.py` | Agent-Decides 实验脚本 |
| `scripts/expand_tasks_60.py` | 任务数据集扩展脚本 |
| `scripts/start_vllm.sh` | vLLM 启动脚本 |
| `outputs/.../task_data_v2/` | 55 个任务 JSON |
| `outputs/.../comparison_v12/` | FM v12 结果（55 任务） |
| `outputs/.../comparison_v12_single/` | Single v12 结果（55 任务） |
| `outputs/.../comparison_v13_agent_decides/` | AD v13 结果（55 任务） |
| `README.md` | 项目概览 |
| `SPEC.md` | 本文档 |

---

## 10. 待解决 / 未来方向

1. **修复 spawn 机制**：subagent 返回率为 0% 是最关键的问题，需要了解为什么 task 工具调用后 subagent 不返回
2. **更大模型对比**：9B 推理瓶颈明显，14B/32B 是否能解决？
3. **扩大样本量**：55 任务仍不足以做统计显著性检验，建议 100-200 任务
4. **Error analysis**：深入分析 fuzzy 判断中哪些是真错误、哪些是表述差异
5. **v11 vs v12 subagent 返回率差异**：v11 87% vs v12 0%，需调查原因
