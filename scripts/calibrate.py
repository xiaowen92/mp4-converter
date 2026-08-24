"""Calibrate raw ASR transcription using DeepSeek API, output styled HTML.

通用课程/讲座内容：修正 ASR 同音错字、统一领域术语（按 references/terminology.md）、段落化 + 标点。

Usage:
    DEEPSEEK_API_KEY=$KEY python3 scripts/calibrate.py <raw.txt> [output.html]
"""
import sys
import os
import re
from pathlib import Path
from openai import OpenAI

# ---------------------------------------------------------------------------
# System prompt — the key to quality
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一个课程语音转录校准助手。你的任务是将语音识别的原始文本，校准为流畅、准确的 HTML 文章。

## 核心原则：逐句校准，不增不减

原文是你唯一的信息来源。你的输出必须严格对应原文的每一句话：
- 原文有 N 句话，你的输出就有 N 句话
- 只做三件事：修正同音错字、添加标点、统一课程术语
- 严禁添加任何原文没有的句子、背景介绍、行业概述、过渡段落

## 你必须做的事

### 1. 修正 ASR 同音错字

原文来自语音识别，存在大量同音错字。请根据上下文修正。使用下面的术语纠错表（本领域专属的误识 → 正确写法对照）：

{term_table}

### 2. 段落化 + 标点 + 流畅行文
- 将逐行碎片文本合并为连贯段落，每段表达一个完整小主题
- 添加正确的中文标点（，、；。？！）
- 保留讲师口语风格（"大家注意""我多次强调"等），但去冗余语气词
- 段落间用空行分隔

## 严禁做的事

### 反幻觉
1. 严禁添加原文中不存在的任何句子、事实、数据、分析、观点、结论
2. 严禁添加背景介绍、行业概述、补充说明、过渡段落
3. 严禁猜测或编造讲话者没有说过的话
4. 严禁将不认识的词强行解释为热门人物或概念
5. 严禁写任何"总结""展望""学习建议""免责声明"段落
6. 如果某个词无法确定，保持原文，不要强行解释
7. 严禁添加"本文仅校准语音识别文本"之类的 AI 生成声明

### 禁止 Emoji
8. 严禁在 HTML 中使用任何 emoji 字符。包括但不限于：📁📂📊📈📉🔍💡⚠️✅❌🔥
   标题用纯文字，文件名标签用纯文字，不得出现任何 emoji。

## HTML 输出格式

输出完整 HTML 文件，CSS 样式如下：
- 白色容器 + 浅灰背景 #f0f2f5
- PingFang SC 字体, 1.8 行高, 16px 字号, 段落首行缩进 2em
- 文件名标签：浅黄背景 #fffbe6, 橙色文字, 等宽字体, 虚线边框。标签内只显示纯文字文件名，前面不加任何图标
- 最大宽度 900px 居中, padding 40px 60px
- h1 标题居中, 底部 border

只输出 HTML 代码，不要有任何额外解释文字。"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def calibrate(txt_path: str, api_key: str, model: str = "deepseek-chat") -> str:
    """Read raw .txt, call DeepSeek API, return calibrated HTML string."""
    with open(txt_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    basename = os.path.splitext(os.path.basename(txt_path))[0]

    # Load terminology table from references/terminology.md
    term_table = ""
    term_path = Path(__file__).resolve().parent.parent / "references" / "terminology.md"
    if term_path.exists():
        term_table = term_path.read_text(encoding="utf-8")
    system_prompt = SYSTEM_PROMPT.replace("{term_table}", term_table)

    user_message = f"""原始转录文件名: {basename}

请将以下课程的语音识别原始转录校准为 HTML 文章。

注意：以下原文来自语音识别，包含大量同音错字。请利用术语纠错表和相关领域知识修正所有术语。
严格逐句校准，不增加任何原文没有的句子。如有不确定的词，保持原文不要猜测。
文件标签和标题中不要使用任何 emoji 或图标符号。

===== 原始转录开始 =====
{raw_text}
===== 原始转录结束 =====

请直接输出完整 HTML 代码。"""

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")

    response = client.chat.completions.create(
        model=model,
        max_tokens=16000,
        temperature=0.0,  # 确定性输出，杜绝幻觉和 emoji
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    html = response.choices[0].message.content.strip()

    # Strip emoji — safety net, prompt should prevent this but enforce anyway
    html = re.sub(
        r'[\U0001F300-\U0001F9FF☀-➿⭐✂-➰'
        r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF'
        r'\U0001F600-\U0001F64F\U0001F680-\U0001F6FF'
        r'️‍]',
        '', html
    )

    # Strip code fences if wrapped
    if html.startswith("```html"):
        html = html[7:]
    if html.startswith("```"):
        html = html[3:]
    if html.endswith("```"):
        html = html[:-3]
    html = html.strip()

    return html


def main():
    if len(sys.argv) < 2:
        print("用法: python calibrate.py <raw.txt> [output.html]")
        print("环境变量: DEEPSEEK_API_KEY 必须设置")
        sys.exit(1)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 请设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)

    txt_path = sys.argv[1]
    if not os.path.exists(txt_path):
        print(f"[错误] 文件不存在: {txt_path}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        html_path = sys.argv[2]
    else:
        dirname = os.path.dirname(txt_path) or "."
        basename = os.path.splitext(os.path.basename(txt_path))[0]
        html_path = os.path.join(dirname, f"{basename}.html")

    print(f"校准中: {txt_path}")
    html = calibrate(txt_path, api_key)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[完成] {html_path}")


if __name__ == "__main__":
    main()
