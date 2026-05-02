#!/usr/bin/env python3
"""
机制验证 v2：使用真实任务数据，验证prompt传递与实验完全一致。

方法：
1. 读取一个真实任务（question + paragraphs）
2. 用和实验脚本完全相同的prompt模板构建prompt
3. 用和实验脚本完全相同的命令格式运行opencode
4. 检查模型答案是否引用了文档内容

这样验证的是"prompt传递 + 模型遵循指令"的完整链路。
"""
import subprocess, json
from pathlib import Path

OPENCODE = '/home/jinxu/.opencode/bin/opencode'
MODEL = 'local/qwen35-9b'

def build_prompt(question, paragraphs):
    """和run_single_v12.py完全相同的prompt构建逻辑"""
    docs_text = "\n\n".join([f"[Paragraph {i}] {p}" for i, p in enumerate(paragraphs)])
    return f"""You are a research agent answering multi-hop questions by searching through documents.

Your task: Read the provided documents, find the information needed to answer the question, and output your answer.

RULES:
- Use the read and grep tools to search through documents
- Base your answer ONLY on information found in the documents
- Do not guess or use your own knowledge

Output your final answer on its own line:
ANSWER: <your answer>

---

Answer this multi-hop question using ONLY the provided documents.

Question: {question}

Documents:
{docs_text}

Find the answer using the read and grep tools.

ANSWER:
"""

def run_opencode(prompt_content, task_id, output_file):
    """和实验脚本完全相同的调用方式"""
    prompt_file = output_file.parent / f".prompt_{task_id}.txt"
    prompt_file.write_text(prompt_content, encoding='utf-8')
    
    abs_prompt = prompt_file.absolute()
    abs_output = output_file.absolute()
    
    cmd = (
        f"script -q -c "
        f"'{OPENCODE} run --model {MODEL} --format json --title {task_id} --message @/{abs_prompt}' "
        f"{abs_output}"
    )
    
    proc = subprocess.run(cmd, shell=True, cwd=str(output_file.parent),
                          capture_output=True, text=True, timeout=120)
    return proc.returncode, prompt_file

def parse_output(output_file):
    """解析JSONL输出，返回所有text事件"""
    if not output_file.exists():
        return []
    content = output_file.read_text()
    texts = []
    for line in content.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get('type') == 'text':
                texts.append(obj['part']['text'])
        except:
            pass
    return texts

def main():
    work_dir = Path(__file__).parent
    task_dir = Path('../../opencode-spawn-pilot/outputs/opencode_spawn_pilot/task_data_v2')
    
    # 用第一个hotpot任务
    task_id = "hotpot_5a722a6855429971e9dc9320"
    task_file = task_dir / f"task_{task_id}.json"
    
    if not task_file.exists():
        print(f"Task file not found: {task_file}")
        return 1
    
    task = json.loads(task_file.read_text())
    question = task['question']
    paragraphs = task['paragraphs']
    answer = task['answer']
    
    print("=" * 60)
    print("机制验证 v2：真实任务 + 完整prompt传递链路")
    print("=" * 60)
    print(f"\nTask: {task_id}")
    print(f"Question: {question[:80]}...")
    print(f"Correct answer: {answer}")
    print(f"Num paragraphs: {len(paragraphs)}")
    
    # 构建prompt
    prompt = build_prompt(question, paragraphs)
    
    # 运行
    output_file = work_dir / f"real_test_output_{task_id}.jsonl"
    ret, prompt_file = run_opencode(prompt, task_id, output_file)
    
    print(f"\nReturn code: {ret}")
    print(f"Output file: {output_file.exists()}")
    
    # 解析
    texts = parse_output(output_file)
    if texts:
        final = texts[-1]
        print(f"\n最终答案:\n{final[:400]}")
        
        # 验证
        uses_doc = '[Paragraph' in final
        print(f"\n验证结果:")
        print(f"  答案引用了文档(Paragraph): {uses_doc} {'✓' if uses_doc else '✗'}")
        print(f"  答案来自模型自身知识: {'✗ (正确)' if not uses_doc else '! 可能幻觉'}")
        
        if uses_doc:
            print(f"\n✓ 结论：模型基于我们提供的文档回答问题，")
            print(f"  prompt传递链路验证通过。")
            return 0
    
    print("\n✗ 无法解析输出")
    return 1

if __name__ == '__main__':
    import sys
    sys.exit(main())
