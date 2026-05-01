#!/usr/bin/env python3
"""
机制验证：证明模型读到的是我们给的提示词，而非其他来源。

方法：
1. 在prompt中加入唯一标识字符串 [BANANA_MARKER_XYZ]
2. 同时在一个"干扰文件"中也放入不同的标识字符串
3. 检查模型输出中包含哪个标识 — 如果是我们的marker，说明模型读的是prompt而非干扰文件
"""
import subprocess, json, sys
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'

PROMPT_MARKER = "[BANANA_MARKER_77777]"
DISTRACTOR_MARKER = "[APPLE_MARKER_88888]"

def create_prompt_file(path, content):
    with open(path, 'w') as f:
        f.write(content)
    return path

def run_opencode(prompt_file):
    """Run opencode with the given prompt file, return the raw JSON output."""
    cmd = [
        OPENCODE, 'run',
        '--model', MODEL,
        '--message', f'@{prompt_file}',
        '--no-auto-exit',
        '--max-steps', '5',
        '--format', 'json'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr

def parse_output(raw):
    """Extract the model's answer from JSON output."""
    for line in raw.strip().split('\n'):
        line = line.strip()
        if line.startswith('{') and '"content"' in line:
            try:
                obj = json.loads(line)
                return obj.get('content', '')
            except:
                pass
        # opencode may output JSON array or other formats
        if line.startswith('[') or line.startswith('OK') or line.startswith('DONE'):
            for subline in raw.split('\n'):
                try:
                    obj = json.loads(subline.strip())
                    if isinstance(obj, list) and len(obj) > 0:
                        for item in obj:
                            if isinstance(item, dict) and 'content' in item:
                                return item['content']
                except:
                    pass
    return raw

def main():
    work_dir = Path(__file__).parent

    # 1. 创建主prompt（包含我们的marker）
    our_prompt = f"""{PROMPT_MARKER} 请直接输出方括号中的内容，不需要做任何其他事情。
Question: 谁在1998年演唱了歌曲"Together Again"？
"""
    prompt_file = work_dir / 'test_prompt.txt'
    create_prompt_file(prompt_file, our_prompt)

    # 2. 创建干扰文件（包含不同的marker）
    distractor_content = f"""{DISTRACTOR_MARKER} 这是一个干扰文件，如果模型读到这个文件，它会输出这个标记。
Question: 1932年奥运会的主办城市是哪里？
"""
    distractor_file = work_dir / 'distractor.txt'
    create_prompt_file(distractor_file, distractor_content)

    print("=" * 60)
    print("机制验证：模型读到的是我们的提示词吗？")
    print("=" * 60)
    print(f"\n[测试1] 我们的prompt文件内容:")
    print(our_prompt.strip())
    print(f"\n[测试2] 干扰文件内容（模型不应该读到）:")
    print(distractor_content.strip())

    print("\n" + "-" * 60)
    print("运行 OpenCode...")
    print("-" * 60)

    raw = run_opencode(str(prompt_file))
    answer = parse_output(raw)

    print(f"\n模型输出:\n{answer[:500]}")

    # 检查
    has_our_marker = PROMPT_MARKER in answer
    has_distractor = DISTRACTOR_MARKER in answer

    print("\n" + "=" * 60)
    print("验证结果:")
    print("=" * 60)
    print(f"  包含我们的marker [{PROMPT_MARKER}]: {has_our_marker} {'✓' if has_our_marker else '✗'}")
    print(f"  包含干扰marker [{DISTRACTOR_MARKER}]: {has_distractor} {'✗ (正确)' if not has_distractor else '! 有干扰'}")

    if has_our_marker and not has_distractor:
        print("\n✓ 结论：模型读到的是我们给的提示词，不是其他来源。")
        return 0
    elif has_distractor:
        print("\n✗ 警告：模型可能读到了干扰文件！")
        return 1
    else:
        print("\n? 模型输出中不含任何marker，需要检查输出格式。")
        return 2

if __name__ == '__main__':
    sys.exit(main())
