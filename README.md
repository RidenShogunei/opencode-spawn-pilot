# OpenCode Spawn Pilot

研究小模型（Qwen3.5-9B）在 OpenCode 框架下的 subagent spawn 能力是否对多跳问答有帮助。

**核心问题**：Agent 何时该 spawn、怎么 spawn、spawn 后怎么用结果？

---

## 环境要求

### 1. vLLM 服务（模型推理后端）

```bash
# 启动 Qwen3.5-9B（19GB，4 shards）
bash scripts/start_vllm.sh
```

验证：`curl http://localhost:8010/v1/models` 应返回 qwen35-9b。

### 2. OpenCode 配置

OpenCode 使用 `--message @/path/to/prompt.txt` 传入 system prompt。

---

## 快速开始

### 启动 vLLM

```bash
cd /home/jinxu/opencode-spawn-pilot
bash scripts/start_vllm.sh
```

### 运行实验

```bash
# Single 基线（无 spawn）
python3 scripts/run_single_v12.py

# Force-Multi baseline（v14）
python3 scripts/run_fm_v14_baseline.py

# Force-Multi with Deliberation prompt（v13）
python3 scripts/run_fm_v13.py
```

### 查看结果

```bash
# Single 结果
cat outputs/opencode_spawn_pilot/comparison_v12_single/results_single_v12.jsonl

# v14 Force-Multi 结果
cat outputs/opencode_spawn_pilot/comparison_v14/results_fm_v14.jsonl

# v13 Force-Multi 结果（人工核查）
cat outputs/opencode_spawn_pilot/comparison_v13/results_fm_v13.jsonl
```

---

## 目录结构

```
opencode-spawn-pilot/
├── scripts/
│   ├── start_vllm.sh              # 启动 vLLM
│   ├── run_single_v12.py          # Single 基线实验
│   ├── run_fm_v14_baseline.py    # v14 Force-Multi（baseline）
│   └── run_fm_v13.py             # v13 Force-Multi（Deliberation prompt）
├── outputs/opencode_spawn_pilot/
│   ├── task_data_v2/              # 55 个任务 JSON
│   ├── comparison_v12_single/     # Single 基线结果（23/55）
│   ├── comparison_v14/            # v14 Force-Multi 结果（22/55）
│   └── comparison_v13/            # v13 Force-Multi 结果（25/55 人工）
├── docs/
│   └── 组会介绍_v1.md             # 组会汇报文档
├── SPEC.md                        # 详细规范
├── ENV.md                         # 环境状态
└── README.md                      # 项目概览
```

---

## 实验结果

| 模式 | 准确率 | Spawn 率 |
|------|--------|----------|
| **Single** | **23/55 (41.8%)** | 0% |
| **v14 Force-Multi** | 22/55 (40.0%) | 60% |
| **v13 Force-Multi** | 25/55 (45.5%) | 60% |

> v13 准确率为人工逐题核查结果（自动评测有 bug）。

### 核心发现

1. **Multi 与 Single 基本持平**：净效果 -1 题
2. **Agent 不知道如何正确委托**：过早委托、过度委托、工具盲目、整合失败
3. **Deliberation 提示词有效**：让模型先思考再委托，可减少无效 spawn

### Per-Hop 准确率

| Hop | Single | v14 Multi |
|-----|--------|-----------|
| 2-hop | 35% | **50%** |
| 3-hop | 40% | **53%** |
| 4-hop | **36%** | 21% |

---

## 历史版本

| 版本 | 说明 |
|------|------|
| v1 | 当前正式版本：Single vs v14 vs v13（Deliberation）对比，55 任务 |
