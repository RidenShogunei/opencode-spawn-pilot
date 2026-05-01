# OpenCode Spawn Pilot

研究小模型（Qwen3.5-9B）在 OpenCode 框架下的 subagent spawn 能力是否对多跳问答有帮助。

**核心问题**：当让 Build Agent 可以 spawn subagent 来并 行搜索文档时，准确率是否比单 agent 更高？spawn 解决了什么问题？什么还是解决不了的？

---

## 环境要求

### 1. vLLM 服务（模型推理后端）

```bash
# 启动 Qwen3.5-9B（19GB，4 shards）
bash scripts/start_vllm.sh
# 或手动：
CUDA_VISIBLE_DEVICES=4,5,6,7 vllm serve ~/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/ \
  --served-model-name qwen35-9b \
  --port 8010 \
  --gpu-memory-utilization 0.85 \
  --enforce-eager
```

验证：`curl http://localhost:8010/v1/models` 应返回 qwen35-9b。

### 2. OpenCode 配置

OpenCode 使用自定义 system prompt 覆盖默认行为，配置在：

```
~/.config/opencode/opencode.json
```

运行脚本会自动写入这个文件，所以确保 OpenCode 对该文件有写权限。

### 3. 目录结构

```
opencode-spawn-pilot/
├── scripts/
│   ├── start_vllm.sh              # 启动 vLLM
│   ├── run_comparison_v4_forced.py  # 主要实验脚本（single vs force-multi）
│   ├── run_opencode.py            # 单次 OpenCode 运行
│   └── prompt_variants.py         # 多种 prompt 变体测试
├── configs/                       # OpenCode 配置模板（已过时）
├── system_prompts/                # system prompt 模板（已过时）
├── outputs/opencode_spawn_pilot/
│   ├── task_data/                 # MuSiQue 原始任务
│   ├── task_data_v2/              # HotpotQA + MuSiQue 任务
│   ├── comparison_v4_forced/      # v4 force 实验结果
│   └── comparison_v4_test/       # 最新配对实验结果（single + force-multi 同任务对比）
├── SPEC.md                        # 详细实验规范和历史
└── README.md                      # 本文档
```

---

## 快速开始

### 启动 vLLM

```bash
cd /home/jinxu/opencode-spawn-pilot
bash scripts/start_vllm.sh
```

### 运行配对实验（single vs force-multi）

```bash
python3 scripts/run_comparison_v4_forced.py --limit 10
```

这会：
1. 读取 `task_data_v2/` 中的任务
2. 对每个任务分别以 **single** 和 **force-multi** 模式运行
3. 输出配对结果到 `outputs/opencode_spawn_pilot/comparison_v4_test/`

### 查看结果

结果在 `outputs/opencode_spawn_pilot/comparison_v4_test/` 下按任务名组织，每个任务有：

```
<task_id>__single-test/opencode_raw_output.jsonl   # 单 agent 原始输出
<task_id>__force-multi-test/opencode_raw_output.jsonl  # 强制 spawn 原始输出
```

---

## 实验模式说明

### Single 模式（单 agent 基线）

```python
SYSTEM_SINGLE = '''You are a research agent answering multi-hop questions...

RULES:
- Use the read, grep, and bash tools to search through documents
- Base your answer ONLY on information found in the documents
- Do not guess or use your own knowledge

Output your final answer on its own line:
ANSWER: <your answer>'''
```

Build Agent 只能用自己的 read/grep/bash 工具搜索文档，**完全不知道 subagent 的存在**。

### Force-Multi 模式（强制 spawn）

```python
SYSTEM_FORCE_MULTI = '''You are a research agent solving multi-hop questions...

CRITICAL:
1. You MUST use task(description="<search>", prompt="Read the file <FILEPATH> and find <info>", subagent_type="explore") to search documents
2. Do NOT use read or grep to search documents — only use task tool
3. Wait for subagent results before answering
4. You must spawn at least one subagent before giving your final answer

Output your final answer on its own line:
ANSWER: <your answer>'''
```

Build Agent **必须**用 `task` 工具 spawn subagent 来搜索文档，不能直接 read/grep。

### 可选 Multi 模式（历史版本，已弃用）

可选 spawn，不强制。在 v5.0 实验中模型 **从不主动 spawn**（22 任务 0 spawn），因此后续改用 force 模式。

---

## 任务数据格式

任务 JSON 文件结构：

```json
{
  "id": "hotpot_5adfa22655429942ec259ac4",
  "question": "The broadcaster that released \"HyperNormalisation\" has how many employees in total?",
  "answer": "35,402",
  "paragraphs": [
    {
      "idx": 0,
      "title": "HyperNormalisation",
      "text": "HyperNormalisation is a 2016 BBC documentary..."
    },
    {
      "idx": 1,
      "title": "British Broadcasting Corporation",
      "text": "The British Broadcasting Corporation (BBC) is a British public service broadcaster... It employs over 20,950 staff in total, 16,672 of whom are in public sector broadcasting. The total number of staff is 35,402..."
    }
  ]
}
```

---

## 核心发现（v6.0）

### 实验结果摘要

**配对实验（10 个任务，single 和 force-multi 同任务对比）：**

| 指标 | Force-Multi | Single |
|------|------------|--------|
| 准确率 | **7/10 (70%)** | 4/10 (40%) |
| Spawn 率 | 6/10 (60%) | 0（不适用） |
| Subagent 返回率 | 6/10 (60%) | 0 |

**Spawn 帮助了 3 个任务（全部是搜索-答案类）：**
- `hotpot_5adfa22` — BBC Staff 数量：FM 找到 35,402，SG 格式错误 35402
- `hotpot_5adfff075` — Rachel, Nevada：FM spawn 2 次找到答案，SG 放弃
- `hotpot_5a722a68` — Chief Detective Maria Shvetsova：FM 正确，SG 错

**Spawn 没有伤害任何任务（FM✗ SG✓ = 0）**

### 关键洞察

**Spawn 只解决"搜索"问题，不解决"推理"问题。**

成功案例（搜到=答对）：
- Subagent 返回 "BBC total staff is 35,402" → Build Agent 直接输出 35,402 ✓

失败案例（搜到≠答对）：
- Subagent 返回 "Rachel, Nevada is 25 miles north of Groom Lake" → Build Agent 推理链断裂，不知道 Groom Lake 以南 25 英里就是 Rachel ✗

**当 subagent 给出可以直接使用的答案时，spawn 有用；当需要链式推理时，spawn 仍然不够。**

### Spawn 行为分析

| Spawn 情况 | 任务数 | 正确数 | 准确率 |
|-----------|--------|--------|--------|
| Spawn 了 | 6 | 4 | 67% |
| 没 Spawn（prompt 强制但模型拒绝） | 4 | 3 | 75% |

模型倾向于在更困难的任务上 spawn，但 spawn 后的准确率反而更低，说明 spawn 主要帮助的是并 行搜索，而不是推理能力。

---

## 已验证结论

1. **模型不主动 spawn**：在可选 spawn 模式下，Qwen3.5-9B 从不主动 spawn（v5.0 实验：22 任务 0 spawn）
2. **强制 spawn 有效**：用 prompt 强制后，60% 任务确实 spawn 了
3. **Spawn 有上限**：即使强制 spawn，3/4 跳复杂推理任务仍然失败，根因是 Build Agent 推理链断裂，不是 subagent 搜索失败
4. **配对优势明显**：Force-Multi (70%) vs Single (40%)，但这是因为任务偏向搜索类而非推理类

---

## 待解决问题

1. **4 跳任务没有 force-multi 数据**：large_4hop1 的两个任务只在 single 模式下跑了，需要补全
2. **3 跳失败根因**：subagent 找到了 "1853" 和 "Casa Loma" 相关段落，但 Build Agent 还是答错，需要区分是 subagent 输出格式问题还是 Build Agent 推理问题
3. **不同模型对比**：目前只测了 Qwen3.5-9B，没有对比更大或更小的模型

---

## 相关文件索引

| 文件 | 说明 |
|------|------|
| `SPEC.md` | 完整实验规范、版本历史、所有数据表格 |
| `scripts/run_comparison_v4_forced.py` | 主要实验脚本 |
| `scripts/start_vllm.sh` | vLLM 启动脚本 |
| `outputs/opencode_spawn_pilot/comparison_v4_test/` | 最新配对实验结果 |
| `outputs/opencode_spawn_pilot/task_data_v2/` | HotpotQA 任务数据 |
| `outputs/opencode_spawn_pilot/task_data/` | MuSiQue 任务数据 |

---

## 版本历史

- **v6.0** (2026-05-01)：Force-spawn 配对实验，FM 70% vs SG 40%，spawn 帮助搜索但不帮助推理
- **v5.0** (2026-05-01)：可选 spawn 全面失败，22 任务 0 spawn，单 agent 反而更好
- **v4.0** (2026-04-30)：发现 config 文件覆盖方案，实验框架成熟
- **v3.0** (2026-04-29)：Proof of concept，2 任务验证可行
