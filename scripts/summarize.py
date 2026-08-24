"""Summarize course transcript txt files into one key-points HTML per folder.

For each course folder under txt/, concatenate its .txt files in natural
order (numbering prefix keeps lecture order), chunk at file boundaries,
extract key points via DeepSeek, and write one HTML → html_summary/.

Usage:
    DEEPSEEK_API_KEY=$KEY python3 scripts/summarize.py "01.第一章"
    DEEPSEEK_API_KEY=$KEY python3 scripts/summarize.py --all
"""
import sys
import os
import re
import html as html_mod
import concurrent.futures
from pathlib import Path
from openai import OpenAI

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
TXT_DIR = REPO_ROOT / "txt"
OUT_DIR = REPO_ROOT / "html_summary"

CHUNK_CHARS = 20000
MAX_TOKENS = 8000
WORKERS = 6

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一名课程内容提炼助手。任务：把课程语音转录文本提炼为要点列表。

背景：讲师的讲课中有大量为了活跃气氛而说的话、废话、重复、口头禅和互动。你需要提取每一节课的精髓，即核心知识点。

## 输出格式（严格遵守）

每个要点占一行，编号递增：

N. 要点内容

如果原文中有支撑该要点的具体实例、案例、数据、故事，紧跟一行：

实例：具体内容

要点用一两句话概括核心观点、方法或结论。没有实例的要点不要输出"实例："行。

## 规则

1. 只提取原文明确讲过的内容；严禁添加原文没有的观点、例子、数据、总结
2. 覆盖完整：不得遗漏任何实质性知识点，即使讲得简短也要提取
3. 术语保持原样（如 STAR法则、三支柱、COE）
4. 丢弃口语过渡、提问互动、玩笑、重复强调、寒暄
5. 输入文本按小节拼接，【小节：...】只是小节标题标记，按原文顺序提炼
6. 只输出要点列表本身，不要任何开场白、结尾语或解释"""

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def natural_key(p: Path):
    m = re.match(r'^(\d+)', p.name)
    return (0, int(m.group(1)), p.name) if m else (1, 0, p.name)


def chunk_files(paths):
    """Split files into ~CHUNK_CHARS chunks at file boundaries."""
    chunks, cur, cur_len = [], [], 0
    for p in paths:
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            continue
        block = f"【小节：{p.stem}】\n{text}\n"
        if cur and cur_len + len(block) > CHUNK_CHARS:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(block)
        cur_len += len(block)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


# ---------------------------------------------------------------------------
# DeepSeek extraction + parsing
# ---------------------------------------------------------------------------

def extract_points(chunk, api_key):
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    resp = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=MAX_TOKENS,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"请提炼以下课程文本的要点：\n\n{chunk}"},
        ],
    )
    return resp.choices[0].message.content.strip()


POINT_RE = re.compile(r"^\s*(\d+)[.、．)）:：]\s*(.+)$")
EXAMPLE_RE = re.compile(r"^\s*(?:[-*•]\s*)?实例[：:]\s*(.+)$")


def parse_points(text):
    points = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = POINT_RE.match(line)
        if m:
            points.append({"text": m.group(2).strip(), "example": None})
            continue
        m = EXAMPLE_RE.match(line)
        if m:
            if points:
                points[-1]["example"] = m.group(1).strip()
            continue
        if points:
            points[-1]["text"] += line
    return points


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

def to_html(title, points):
    items = []
    for i, p in enumerate(points, 1):
        t = html_mod.escape(p["text"])
        item = f'<div class="point">{i}. {t}</div>'
        if p["example"]:
            e = html_mod.escape(p["example"])
            item += f'<div class="example">实例：{e}</div>'
        items.append(f"<li>{item}</li>")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html_mod.escape(title)} 要点提炼</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", sans-serif; max-width: 860px; margin: 40px auto; padding: 0 24px; line-height: 1.8; color: #1a1a1a; }}
h1 {{ font-size: 1.6em; border-bottom: 2px solid #333; padding-bottom: 12px; }}
ol {{ padding-left: 1.4em; }}
li {{ margin-bottom: 18px; }}
.point {{ font-weight: 600; }}
.example {{ color: #555; font-size: 0.95em; margin-top: 2px; }}
</style>
</head>
<body>
<h1>{html_mod.escape(title)} 要点提炼</h1>
<ol>
{chr(10).join(items)}
</ol>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def summarize_folder(folder_name, api_key):
    src = TXT_DIR / folder_name
    if not src.is_dir():
        print(f"[ERROR] 目录不存在: {src}")
        return None
    files = sorted(src.glob("*.txt"), key=natural_key)
    if not files:
        print(f"[ERROR] 无 txt 文件: {src}")
        return None
    chunks = chunk_files(files)
    print(f"{folder_name}: {len(files)} 个文件 → {len(chunks)} 个分块", flush=True)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(WORKERS, len(chunks))
    ) as ex:
        results = list(ex.map(lambda c: extract_points(c, api_key), chunks))
    points = []
    for r in results:
        points.extend(parse_points(r))
    out = OUT_DIR / f"{folder_name}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_html(folder_name, points), encoding="utf-8")
    print(f"[OK] {out} ({len(points)} 个要点)", flush=True)
    return points


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("[错误] 请设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--all":
        folders = sorted(d.name for d in TXT_DIR.iterdir() if d.is_dir())
        ok = fail = 0
        for name in folders:
            out = OUT_DIR / f"{name}.html"
            if out.exists() and out.stat().st_size > 0:
                print(f"[SKIP] {out.name}")
                continue
            try:
                summarize_folder(name, api_key)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[ERROR] {name}: {e}", flush=True)
        print(f"[Done] 成功 {ok}, 失败 {fail}")
    else:
        points = summarize_folder(sys.argv[1], api_key)
        if points:
            print("\n--- 预览（前 12 条）---")
            for i, p in enumerate(points[:12], 1):
                print(f"{i}. {p['text']}")
                if p["example"]:
                    e = p["example"]
                    print(f"   实例：{e[:80]}{'...' if len(e) > 80 else ''}")


if __name__ == "__main__":
    main()
