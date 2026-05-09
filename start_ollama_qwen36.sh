#!/bin/bash
# start_ollama_qwen36.sh
# 启动 Ollama + Qwen3.6-27B-Q4_K_M
# 使用前需确保 GPU 空闲（kill 自己的 VLLM）

set -e

export OLLAMA_LIB=/home/jinxu/ollama_lib
export OLLAMA_HOST=0.0.0.0
export OLLAMA_MODELS=/home/jinxu/.ollama/models

OLLAMA_BIN=~/ollama-new

if ! pgrep -f "ollama-new serve" > /dev/null; then
    echo "[Ollama] 启动服务..."
    nohup $OLLAMA_BIN serve > ~/.ollama/ollama.log 2>&1 &
    sleep 3
else
    echo "[Ollama] 服务已在运行"
fi

# 检查服务是否响应
if curl -s --max-time 5 http://127.0.0.1:11434/api/tags > /dev/null; then
    echo "[Ollama] 服务正常 (http://127.0.0.1:11434)"
    echo "[Ollama] 模型列表:"
    $OLLAMA_BIN list | grep qwen
else
    echo "[Ollama] 警告: 服务未响应"
fi

echo ""
echo "用法示例（禁用思考模式）:"
echo 'curl http://localhost:11434/api/generate -d \'{"model": "qwen3.6:27b-q4_K_M", "prompt": "1+1等于几", "think": false}\' --max-time 60'
