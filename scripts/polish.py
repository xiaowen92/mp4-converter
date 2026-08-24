"""DeepSeek second-pass proofread of calibrated HTML → _verified.html.

通用课程/讲座内容：修正残留 ASR 错误、统一术语（按 references/terminology.md）、规范格式。
不做任何实体验证，只做文本校对。

Usage:
    DEEPSEEK_API_KEY=$KEY python3 scripts/polish.py html/xxx.html
    # → html/xxx_verified.html，随后自动删除中间 html/xxx.html
"""
import sys
import os
import re
from pathlib import Path
from openai import OpenAI

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
POLISH_PROMPT = """你是一个中文课程文本校对助手。你的任务是修正校准后 HTML 中的残留格式问题。

## 你必须做的事

1. **缩写格式化**
   - 将错误大写的缩写修正为正确格式：Hr → HR，Kpi → KPI，Ai → AI，5g → 5G
   - 对关键行业缩写，在首次出现时补充全称解释（根据上下文判断哪些需要解释），例如：
     HR → HR（Human Resources，人力资源）
     KPI → KPI（Key Performance Indicator，关键绩效指标）
     OKR → OKR（Objectives and Key Results，目标与关键结果）
     AI → AI（Artificial Intelligence，人工智能）
     等

2. **缺少分隔符**
   - 多个英文缩写连写时添加顿号：KPIOKR → KPI、OKR，AIHPC → AI、HPC

3. **数字/日期规范**
   - 保留原文风格，只修正明显的格式错误
   - 中文数字和阿拉伯数字混用的情况统一为阿拉伯数字

4. **残留同音错字**
   - 对照下面的术语表，修正校准后仍残留的同音错字：

{term_table}

5. **去冗余但保留风格**
   - 删除无意义重复词（"这个这个""就是说就是说"）
   - 保留讲师口语风格和语气

## 严禁做的事

- 严禁添加原文不存在的事实、数据、分析、观点
- 严禁编造任何信息或补充原文没有的内容
- 严禁修改 HTML 结构（标签、CSS、class 名）
- 严禁添加"总结""展望""学习建议"段落或免责声明
- 严禁使用任何 emoji
- 无法确定的内容保持原文
- 只输出校对后的完整 HTML，不要任何额外解释"""


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def polish(html, api_key, model="deepseek-chat"):
    """Use DeepSeek to fix residual text issues in HTML."""
    term_table = ""
    term_path = Path(__file__).resolve().parent.parent / "references" / "terminology.md"
    if term_path.exists():
        term_table = term_path.read_text(encoding="utf-8")

    system_prompt = POLISH_PROMPT.replace("{term_table}", term_table)

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")

    response = client.chat.completions.create(
        model=model,
        max_tokens=16000,
        temperature=0.0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请校对以下 HTML 文档，只输出校对后的完整 HTML：\n\n{html}"},
        ],
    )

    result = response.choices[0].message.content.strip()
    if result.startswith("```html"):
        result = result[7:]
    if result.startswith("```"):
        result = result[3:]
    if result.endswith("```"):
        result = result[:-3]
    result = result.strip()

    # Strip emoji — safety net
    result = re.sub(
        r'[\U0001F300-\U0001F9FF☀-➿⭐✂-➰'
        r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF'
        r'\U0001F600-\U0001F64F\U0001F680-\U0001F6FF'
        r'️‍]',
        '', result
    )
    return result


def main():
    if len(sys.argv) != 2:
        print("用法: python polish.py <article.html>")
        print("环境变量: DEEPSEEK_API_KEY 必须设置")
        sys.exit(1)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 请设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)

    html_path = Path(sys.argv[1])
    if not html_path.exists():
        print(f"[错误] 文件不存在: {html_path}")
        sys.exit(1)

    html = html_path.read_text(encoding="utf-8")
    print(f"校对中: {html_path.name}")

    polished = polish(html, api_key)

    out_path = html_path.with_name(f"{html_path.stem}_verified.html")
    out_path.write_text(polished, encoding="utf-8")
    print(f"[完成] {out_path}")

    # 清理中间 .html（沿用管线清理规则）
    html_path.unlink()
    print(f"[清理] 已删除中间文件: {html_path.name}")


if __name__ == "__main__":
    main()
