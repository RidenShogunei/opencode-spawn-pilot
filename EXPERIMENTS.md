# OpenCode Spawn Pilot — 实验流程文档

## 快速开始（做一次就够了）

### 1. 启动 vLLM（GPU 2, 端口 8010）

```bash
cd /home/jinxu && source hermes-agent/venv/bin/activate

CUDA_VISIBLE_DEVICES=2 python3 -m vllm.entrypoints.openai.api_server \
    --model /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/ \
    --served-model-name qwen35-9b \
    --port 8010 \
    --host 0.0.0.0 \
    --max-model-len 65536 \
    --gpu-memory-utilization 0.85 \
    --enforce-eager \
    --tensor-parallel-size 1 \
    --enable-prefix-caching \
    --default-chat-template-kwargs '{"enable_thinking": false}' \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder
```

> **脚本位置**: `scripts/start_vllm.sh`  
> **关键参数**: `--tool-call-parser qwen3_coder` + `--enforce-eager` + `--default-chat-template-kwargs '{"enable_thinking": false}'`  
> **缺少任何一个都会导致模型输出错乱或 0% spawn**

验证 vLLM 就绪：
```bash
curl http://127.0.0.1:8010/health   # 返回 OK
```

### 2. 确认环境

```bash
opencode --version        # 1.3.6+
python3 --version         # 3.10+
nvidia-smi | grep "2,"    # GPU 2 空闲
```

---

## 实验流程（每次改 prompt 只需做这一步）

### 1. 基于 v15 模板创建新版本

复制 `scripts/run_fm_v15_verification.py` → `scripts/run_fm_vXX.py`，修改三处：

| 位置 | 改什么 |
|------|--------|
| 文件头注释 | 版本号 + 改动说明 |
| `OUTPUT_DIR` | `comparison_v15` → `comparison_vXX` |
| `SYSTEM_FORCE_MULTI` | **只改这个 prompt**，其他代码不动 |

```python
# 第1行: 改注释
"""
OpenCode Spawn Pilot vXX — <你的改动描述>
"""

# 第18行: 改输出目录
OUTPUT_DIR = Path('.../comparison_vXX')

# 第28行: 改 prompt（这是唯一影响模型行为的地方）
SYSTEM_FORCE_MULTI = '''你的新 prompt'''
```

### 2. 运行

```bash
cd /home/jinxu/opencode-spawn-pilot
python3 scripts/run_fm_vXX.py 2>&1 | tee outputs/opencode_spawn_pilot/comparison_vXX/vXX_run.log
```

55 个任务约需 30-50 分钟。

### 3. 查看结果

```bash
# 实时进度
cat outputs/opencode_spawn_pilot/comparison_vXX/progress_vXX.txt

# 最终汇总
python3 -c "
import json
results = [json.loads(l) for l in open('outputs/opencode_spawn_pilot/comparison_vXX/results_fm_vXX.jsonl')]
correct = sum(1 for r in results if r['correct'])
spawned = sum(1 for r in results if r['spawned'])
total = len(results)
print(f'Accuracy: {correct}/{total} ({100*correct/total:.1f}%)')
print(f'Spawn: {spawned}/{total} ({100*spawned/total:.0f}%)')
"
```

### 4. 分析（可选）

```bash
# 按 hop 数分组统计
python3 -c "
import json
results = [json.loads(l) for l in open('outputs/opencode_spawn_pilot/comparison_vXX/results_fm_vXX.jsonl')]
for hop in ['2hop', '3hop', '4hop']:
    subset = [r for r in results if hop in r['task_id']]
    if subset:
        c = sum(1 for r in subset if r['correct'])
        s = sum(1 for r in subset if r['spawned'])
        print(f'{hop}: acc={c}/{len(subset)} ({100*c/len(subset):.0f}%), spawn={s}/{len(subset)} ({100*s/len(subset):.0f}%)')
"
```

---

## 基线数据（v15）

| 指标 | 值 |
|------|-----|
| 模型 | Qwen3.5-9B (vLLM) |
| 任务数 | 55 |
| 准确率 | 40.0% (22/55) |
| Spawn 率 | ~58% |
| Prompt 结构 | 3 步验证 (a/b/c) |

---

## 已知坑（必读）

### ❌ vLLM 参数缺失
**症状**: 0% spawn，模型输出乱码或"conversation compaction"  
**原因**: 缺 `--enforce-eager`、`--default-chat-template-kwargs '{"enable_thinking": false}'`、或用了 `--tool-call-parser hermes`  
**解决**: 用 `scripts/start_vllm.sh` 的完整命令

### ❌ 改了子进程/解析代码
**症状**: 与基线不可比  
**原则**: **只改 SYSTEM_FORCE_MULTI**，其他代码（子进程、答案提取、比较逻辑）与 v15 完全一致

### ❌ Prompt 过度结构化
**症状**: spawn 率下降，模型"分析瘫痪"  
**教训**: v17 (FOUND/SOURCE/CONFIDENCE 模板) → 18% 准确率；v18 (paste paragraphs 占位符) → 0% spawn  
**原则**: 自然语言 > 模板化指令

### ❌ 太长/太短的 prompt
**症状**: 跳过 spawn 直接回答（太短）；spawn 但不利用结果（太长）  
**原则**: 保持与 v15 相近的长度和结构

---

## 版本记录

| 版本 | 改动 | 准确率 | 备注 |
|------|------|--------|------|
| v15 | 基线：3 步验证 (a/b/c) | 40.0% (55任务) | ✅ 旧基线 |
| v17 | 结构化子 agent 格式 | 18% (55任务) | ❌ 过度结构化 |
| v18 | 回退 v15 + paste paragraphs | 0% | ❌ 占位符导致困惑 |
| v19 | 单句 Finding + 强制 ANSWER | 38.2% (55任务) | 无显著效果 |
| v20 | FM 新基线 (3步验证) | 52.6% (97任务) | ✅ 新基线 |
| v20-single | Single Agent (无spawn) | 49.5% (97任务) | FM优于Single (+3.1pp) |

---

## 文件结构

```
opencode-spawn-pilot/
├── scripts/
│   ├── start_vllm.sh              # vLLM 启动命令（标准版）
│   ├── run_fm_v15_verification.py # 基线脚本（以此为模板）
│   ├── run_fm_vXX.py              # 各版本实验脚本
│   └── analyze_results.py         # 结果分析
├── outputs/opencode_spawn_pilot/
│   ├── task_data_v2/              # 55 个 MuSiQue 任务 (.json)
│   └── comparison_vXX/            # 各版本输出
│       ├── results_fm_vXX.jsonl   # 结果 (每行一个 JSON)
│       └── <task_id>__fm-vXX-*/   # 每个任务的原始输出
└── EXPERIMENTS.md                 # 本文档
```
