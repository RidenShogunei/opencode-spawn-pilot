# OpenCode-Native Spawn Pilot — Environment State

## vLLM Server
- Status: RUNNING (background)
- PID: 3982396 (proc_39229a2ca05c)
- GPU: 2 (CUDA_VISIBLE_DEVICES=2)
- Port: 8010
- Model: qwen35-9b
- Path: /home/jinxu/.cache/tiny-agents/models/Qwen/Qwen3.5-9B/
- max_model_len: 65536
- gpu_memory_utilization: 0.85
- enforce_eager: true
- enable_prefix_caching: true
- enable_auto_tool_choice: true
- tool_call_parser: hermes
- default_chat_template_kwargs: {"enable_thinking": false}

## OpenCode Config
- Config file: /home/jinxu/.config/opencode/opencode.json
- Provider: local (openai_compatible)
- Endpoint: http://127.0.0.1:8010/v1
- Model: qwen35-9b

## GPU Status (7 total A100 40GB)
- GPU 0: ~37 GB (4 vLLM instances, external)
- GPU 1: ~8 MiB (free)
- GPU 2: ~18 GB (this project's vLLM)
- GPU 3: ~8 MiB (free)
- GPU 4: ~38 GB (vLLM, external)
- GPU 5: ~38 GB (vLLM, external)
- GPU 6: ~38 GB (vLLM, external)

## Key Config Decisions
- max_model_len 65536: needed because OpenCode sends max_tokens=32000 + ~769 system prompt > 32768
- --served-model-name qwen35-9b: OpenCode SDK sends model name as config key, must match vLLM
- Thinking disabled at server level via --default-chat-template-kwargs

## Stage 0 Verification Results
- [x] vLLM loads Qwen3.5-9B successfully (GPU 2, 17.66 GiB)
- [x] Thinking mode disabled (no <think></think> in responses)
- [x] OpenCode connects to vLLM endpoint
- [x] Quick test: "3+2" → "5" (no tool calls needed)
- [ ] Code understanding test
- [ ] Tool use test
- [ ] Instruction following test
- [ ] End-to-end simple task test
