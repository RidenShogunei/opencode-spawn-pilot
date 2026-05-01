#!/usr/bin/env python3
"""
机制验证：证明模型读到的确实是我们给的提示词。

方法：用唯一标识字符串[BANANA_MARKER]标记我们的prompt文件，
检查模型输出中是否包含该marker。

实验脚本使用 `script -q -c 'opencode run --message @/path' output` 格式，
我们在这里复现完全相同的调用方式。
"""
import subprocess, json, sys
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'

MARKER = "[BANANA_VERIFY_2026]"

def main():
    work_dir = Path(__file__).parent

    # 创建带marker的prompt文件
    prompt_file = work_dir / 'verify_prompt.txt'
    prompt_file.write_text(
        f"{MARKER} 直接输出方括号内的完整内容，不做任何解释。\n"
        "Question: 1+1等于几？\n",
        encoding='utf-8'
    )

    output_file = work_dir / 'verify_output.jsonl'
    abs_prompt = prompt_file.absolute()
    abs_output = output_file.absolute()

    # 复现实验脚本的调用方式
    cmd = (
        f"script -q -c "
        f"'"
        f"{OPENCODE} run --model {MODEL} --format json "
        f"--title verify_test --message @/{abs_prompt}"
        f"' "
        f"{abs_output}"
    )

    print("=" * 60)
    print("机制验证：模型读到的是我们的提示词吗？")
    print("=" * 60)
    print(f"\nMarker: {MARKER}")
    print(f"Prompt文件: {prompt_file}")
    print(f"\n命令: {cmd[:100]}...")

    proc = subprocess.run(
        cmd, shell=True, cwd=str(work_dir),
        capture_output=True, text=True, timeout=120
    )

    if not output_file.exists():
        print(f"\n✗ 错误：输出文件未创建！")
        print(f"stderr: {proc.stderr[:500]}")
        return 1

    # 解析输出
    marker_found = False
    for line in output_file.read_text().strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get('type') == 'text':
                text = str(obj.get('part', {}).get('text', ''))
                if MARKER in text:
                    marker_found = True
                    print(f"\n✓ 模型输出包含marker:")
                    print(f"  {text[:200]}")
        except:
            pass

    print("\n" + "=" * 60)
    if marker_found:
        print("✓ 结论：模型读到的是我们给的提示词，验证通过！")
        return 0
    else:
        print("✗ 结论：模型输出中未找到marker，可能未读到我们给的提示词")
        return 1

if __name__ == '__main__':
    sys.exit(main())
