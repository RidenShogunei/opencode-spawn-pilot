# OpenCode Spawn Pilot — Research Specification

**Version: v13 — Agent-Decides mode added**
**Status**: v12 FM complete (55 tasks), v12 Single partial (18/55 tasks), v13 AD pending
**Summary**: v13 adds a third experimental condition: Agent-Decides (AD), where the model is informed about the task tool but chooses whether to spawn. This tests the hypothesis that previous "0% voluntary spawn" findings were due to prompt delivery issues, not model capability.

---

## 0. 实验设计（三类对比）

| 模式 | Prompt 策略 | Spawn 行为 | 研究目的 |
|------|------------|-----------|---------|
| **Single** | 不提及 task 工具 | 模型不知道可以 spawn | 纯单 agent 基线 |
| **Agent-Decides (AD)** | 告知 task 工具可用，提供使用场景指导 | 模型自主决定 | 测试模型是否有 spawn 意愿 |
| **Force-Multi (FM)** | 强制必须使用 task 工具 | 必须 spawn | 测试 spawn 的上限效果 |

**核心假设**：之前"模型从不主动 spawn"（v5.0: 22 任务 0 spawn）的结论，可能是因为 prompt 没有正确传递给模型（`opencode run --format json` 忽略配置文件），而非模型本身不会判断。

**验证逻辑**：
- 如果 AD spawn 率 > 0% → 之前的"0%"是 prompt 传递问题 ✓
- 如果 AD spawn 率 ≈ 0% → 9B 模型确实不会自主 spawn，需要强制
- 如果 AD 准确率 ≈ Single → spawn 决策不影响结果
- 如果 AD 准确率 ≈ FM → 模型能正确判断何时 spawn 有帮助

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

**实际影响**：FM v12 strict 13% → fuzzy 36%（提升 2.8 倍）

### Finding 5：Spawn 解决搜索，不解决推理

| 任务类型 | Spawn 帮助程度 |
|----------|---------------|
| 直接提取（数字、人名、事实） | ✅ 有效，subagent 找到就对了 |
| 链式推理（空间关系、多跳逻辑） | ❌ 无效，subagent 找到信息，Build Agent 推理链断裂 |

### Finding 6：9B 模型推理是瓶颈，不是搜索

即使 subagent 100% 正确返回了信息，Build Agent 也经常在最后一步推理错误。这是模型能力的问题，不是 spawn 机制的问题。

---

## 2. 实验结果

### v12 Force-Multi（55 任务，fuzzy is_correct）

```
FM v12: 20/55 (36%) fuzzy, 7/55 (13%) strict
Spawn 率: 55/55 (100%)
Subagent 返回率: 19/55 (35%)
```

### v12 Single（18/55 任务，fuzzy is_correct）

```
Single v12 partial: 12/18 (67%) fuzzy
注：18 任务样本量小，且 fuzzy 逻辑对简单任务有偏向，无法与 FM 直接比较
```

### v11 配对对比（30 任务，有完整两边数据）

| 模式 | 准确率 | Spawn 率 | Subagent 返回率 |
|------|--------|----------|----------------|
| Force-Multi | 16/30 (53%) | 29/30 (97%) | 26/30 (87%) |
| Single | 15/30 (50%) | 0% | — |

**结论**：Spawn 带来 +3% 提升（53% vs 50%），但样本量小，统计意义有限。

### v10 Force-Multi（12 任务，最终版 prompt）

```
FM v10: 7/12 (58%)
Spawn 率: 11/12 (92%)
```

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
- Use the read and grep tools to search through documents
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

## 6. Fuzzy is_correct 实现

```python
def is_correct(pred, answer, aliases=None):
    p = normalize(pred)   # 去标点、小写
    a = normalize(answer)

    # 第一层：严格相等
    if p == a: return True
    # 别名
    for alias in aliases:
        if p == normalize(alias): return True

    # 第二层：答案核心词（跳过句首 stopwords）是预测的子串
    a_words = a.split()
    for i in range(len(a_words)):
        if a_words[i].lower() not in STOPWORDS:
            suffix = ' '.join(a_words[i:])
            if len(suffix) >= 4 and suffix in p:
                return True
            break

    # 第三层：所有内容词均出现在预测中（词边界）
    words_a = [w for w in a.split() if len(w) >= 2 and w.lower() not in STOPWORDS]
    if words_a:
        matched = sum(1 for w in words_a if word_in_text(w, p))
        if matched == len(words_a):
            return True

    return False
```

---

## 7. 运行命令

```bash
# 启动 vLLM
bash scripts/start_vllm.sh

# 运行 Force-Multi（v12，55 任务）
python3 scripts/run_fm_v12.py

# 运行 Single 基线（v12，55 任务）
python3 scripts/run_single_v12.py

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
| `scripts/run_agent_decides_v13.py` | Agent-Decides 实验脚本（新增） |
| `scripts/expand_tasks_60.py` | 任务数据集扩展脚本 |
| `scripts/start_vllm.sh` | vLLM 启动脚本 |
| `outputs/.../task_data_v2/` | 55 个任务 JSON |
| `outputs/.../comparison_v12/` | FM v12 结果（55 任务） |
| `outputs/.../comparison_v12_single/` | Single v12 部分结果（18/55） |
| `outputs/.../comparison_v13_agent_decides/` | Agent-Decides v13 结果（待运行） |
| `README.md` | 项目概览 |
| `SPEC.md` | 本文档 |

---

## 10. 待解决问题

1. **补全 Single v12**：还需要 37 个任务跑完才能做完整配对对比
2. **运行 Agent-Decides v13**：验证"模型从不主动 spawn"是否为伪命题
3. **三类对比分析**：Single vs Agent-Decides vs Force-Multi 的完整配对对比
4. **扩大样本量**：55 任务仍不足以做统计显著性检验，建议 100-200 任务
5. **更大模型对比**：9B 推理瓶颈明显，14B/32B 是否能解决？
6. **Error analysis**：深入分析 fuzzy 判断中哪些是真错误、哪些是表述差异
