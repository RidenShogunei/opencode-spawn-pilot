#!/bin/bash
# vLLM launch for Qwen3.5-9B on GPU 1+2
# TP=1, port 8010

CUDA_VISIBLE_DEVICES=1,2 python3 -m vllm.entrypoints.openai.api_server \
    --model /home/jinxu/.cache/huggingface/hub/models--Qwen--Qwen3.5-9B \
    --served-model-name qwen35-9b \
    --port 8010 \
    --host 0.0.0.0 \
    --max-model-len 65536 \
    --gpu-memory-utilization 0.85 \
    --enforce-eager \
    --enable-prefix-caching \
    --default-chat-template-kwargs '{"enable_thinking": false}' \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder
