---
name: mp4-converter
description: Use this skill to convert mp4 video files into high-quality FLAC audio and polished, hallucination-free HTML articles. General-purpose for any course/lecture content — customize references/terminology.md for your domain. Triggers when the user asks to 转写视频/课程, convert mp4 to audio/text, 生成HTML, or batch-process video folders.
---

# MP4 Converter

将 mp4 视频转换为高质量 FLAC 音频，并转录、校准为流畅的 HTML 文章。适用于任意课程/讲座/会议内容。

## Directory Structure

```
mp4-converter/
├── SKILL.md                        # This file
├── scripts/                        # Pipeline scripts
│   ├── extract_audio.py            # ffmpeg: mp4 → FLAC (lossless, 44.1kHz stereo, s32)
│   ├── transcribe.py               # FunASR Paraformer STT (支持 --device mps|cpu)
│   ├── calibrate.py                # DeepSeek 校准 → HTML (术语纠错 + 段落化)
│   ├── polish.py                   # DeepSeek 二次校对 → _verified.html
│   ├── summarize.py                # 章节要点提炼 → html_summary/ (可选)
│   ├── run_pipeline.py             # 串行批量编排 + 断点续跑
│   └── run_parallel.py             # 并行批量编排 (三阶段流水线)
├── references/
│   └── terminology.md              # 领域 ASR 纠错表 (prompt 注入, 用户自定义)
├── audio/                          # FLAC 输出 (按源文件夹分子目录)
├── txt/                            # 原始转录输出 (.txt)
├── html/                           # 最终交付 (.html 中间版 + _verified.html)
└── html_summary/                   # 要点提炼输出 (每章节一个 HTML, 可选)
```

## 定制你的领域（重要）

管线本身领域无关。唯一需要定制的文件是 `references/terminology.md`：

1. **编辑纠错表**：按「常见误识 → 正确写法」的表格格式，列出你所在领域的常见 ASR 同音错字、术语、人名、课程名
2. **保留反幻觉规则**：文件末尾的反幻觉注意事项不要删
3. calibrate.py 和 polish.py 会在运行时自动读取并注入 prompt

示例（本仓库当前配置为职场管理课程）：术语表涵盖「三支柱」「STAR法则」「胜任力」等通用 HR 术语，替换为你的领域（医学/法律/编程/历史...）即可。

## Pipeline

```
mp4 → ffmpeg extract_audio.py → audio/<源文件夹>/xxx.flac (lossless)
  → FunASR Paraformer-large → txt/<源文件夹>/xxx.txt
  → DeepSeek 校准 (terminology.md 注入) → html/<源文件夹>/xxx.html
  → DeepSeek 二次校对 → html/<源文件夹>/xxx_verified.html (最终)
  → 自动删除中间 .html
```

## 文件清理规则

**仅保留最终版本 `_verified.html`，中间 `.html` 在 polish 完成后自动删除。**

- `audio/` FLAC → 保留（源音频，供 debug 和重新转录）
- `txt/` 原始转录 → 保留（供归档和 debug）
- `html/xxx_verified.html` → 最终交付物，保留
- `html/xxx.html`（calibrate 输出）→ 仅作为 polish.py 的输入，运行后自动删除

## Performance

| Metric | Value |
|--------|-------|
| Model | FunASR Paraformer-large (Chinese-optimized) |
| VAD | fsmn_vad_zh-cn |
| Punctuation | punc_ct-transformer_zh-cn |
| Audio | FLAC lossless, 原生采样率不重采样 (s32) |
| RTF (CPU) | ~0.11 (10 min 音频 → ~1 min 转录) |
| RTF (MPS GPU) | ~0.016 (10 min 音频 → ~10s, Apple Silicon 实测) |
| Calibration | ~1 min/文件 via DeepSeek API |
| Polish | ~1 min/文件 via DeepSeek API |
| Cost | Transcription: free (local); Calibration+Polish: ~¥0.12-0.15/文件 |
| Hallucination | Zero, with strict anti-hallucination constraints |
| 串行速度 | ~150-180s/文件 |
| 并行速度 | ~31s/文件 (5-6 倍提速) |

## Usage

### 单文件

```bash
# mp4 → FLAC
python3 scripts/extract_audio.py "<path/to/video.mp4>"

# 全管线单文件: mp4 → _verified.html
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_pipeline.py --single "<path/to/video.mp4>"
```

### 批量（串行）

```bash
# 一个文件夹
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_pipeline.py --folder "<path/to/视频文件夹>"

# 全部（默认根目录见下方「源目录配置」）
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_pipeline.py --all
```

断点续跑内置：`_verified.html` 已存在则跳过；中间产物（FLAC/txt/html）存在则从对应步骤继续。失败记入 `pipeline_errors.log` 并继续。

### 批量（并行，推荐）

```bash
# 全量并行（默认并行度 3/3/6，MPS 自动检测）
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_parallel.py --all

# 指定并行度 / 强制 CPU 转录
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_parallel.py --all --extract-j 3 --transcribe-j 2 --deepseek-j 6
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_parallel.py --all --transcribe-device cpu
```

三阶段流水线：extract（ffmpeg, I/O）→ transcribe（FunASR, GPU/CPU）→ deepseek（网络等待），跨文件重叠执行。

| 特性 | 说明 |
|------|------|
| 默认并行度 | extract 3 + transcribe 2 (MPS) / 3 (CPU) + deepseek 6 |
| 硬件检测 | 启动时读 CPU 核数 + 内存；<10 核或 <16GB 自动下调；4 核/8GB 以下警告建议串行 |
| MPS 默认 | torch MPS 可用则默认 GPU 转录（实测 RTF 0.016 vs CPU 0.11，6.5x）；失败自动回退 CPU |
| 单文件 | 仅 1 个文件时自动走串行管线（run_pipeline.process_one） |
| 资源占用 | 3/2/6 并行度下 ~7/12 核 + ~8GB 内存，GPU 仅转录时短暂占用，日常使用不受影响 |

### 分步执行

```bash
# Step 1: mp4 → FLAC
python3 scripts/extract_audio.py "<path/to/file.mp4>"

# Step 2: FLAC → txt (输出在音频同目录)
python3 scripts/transcribe.py audio/<文件夹>/xxx.flac

# Step 3: txt → HTML (DeepSeek 校准)
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/calibrate.py txt/<文件夹>/xxx.txt html/<文件夹>/xxx.html

# Step 4: HTML → _verified.html (DeepSeek 二次校对, 自动删除中间 html)
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/polish.py html/<文件夹>/xxx.html
```

### 要点提炼（可选）

将每章节的所有小节合并提炼为一份要点列表（去除讲师暖场/废话/重复），输出 `html_summary/<章节>.html`：

```bash
# 单章节
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/summarize.py "01.第一章"

# 全部章节（断点续跑：html_summary/<章节>.html 已存在则跳过）
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/summarize.py --all
```

- 输入：`txt/<章节>/` 下全部 .txt，按编号前缀自然排序拼接（连续小节合并为一个章节）
- 分块：~20K chars/块（DeepSeek 上下文限制），块内 6 线程并行，块间按顺序拼接
- 格式：编号要点 + 可选「实例：」行；反幻觉规则与 calibrate.py 一致（只提取不添加）
- 成本：约 ¥0.02-0.05/章节

### 源目录配置

```bash
# --all 默认扫描 DEFAULT_ROOT（run_pipeline.py 顶部常量，可改为你自己的视频库路径）
# 或用 --root 临时指定任意目录
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python3 scripts/run_parallel.py --all --root "<path/to/你的视频库>"
```

## Requirements

| Tool | Install | Purpose |
|------|---------|---------|
| ffmpeg | `brew install ffmpeg` | mp4 → FLAC 音频提取 |
| FunASR | `pip3 install funasr torch torchaudio` | Transcription |
| OpenAI SDK | `pip3 install openai` | DeepSeek API 校准 + 校对 |
| DeepSeek API key | → `DEEPSEEK_API_KEY` env var | Calibration + Polish |

- FunASR Paraformer-large model (~1.3GB, auto-downloaded on first run)
- Apple Silicon (M 系列) 自动启用 MPS GPU 转录；Intel Mac / 无 GPU 自动回退 CPU
- 并行模式会按硬件自动调整；串行模式任意机器可跑

## Files

| File | Purpose |
|------|---------|
| `scripts/extract_audio.py` | ffmpeg 提取 mp4 音频 → FLAC (lossless, 保留原文件名, 按源文件夹分子目录) |
| `scripts/transcribe.py` | FunASR Paraformer STT with VAD + punctuation (支持 --device mps/cpu) |
| `scripts/calibrate.py` | DeepSeek 校准 → styled HTML (术语纠错, 段落化, anti-hallucination) |
| `scripts/polish.py` | DeepSeek 二次校对 → _verified.html (缩写格式化, 残留错字, 格式规范) |
| `scripts/summarize.py` | 章节要点提炼 → html_summary/<章节>.html (分块并行, 去废话) |
| `scripts/run_pipeline.py` | 串行批量编排 + 断点续跑 + 错误日志 |
| `scripts/run_parallel.py` | 并行批量编排 + 硬件检测 + MPS 自动探测 |
| `references/terminology.md` | **领域定制点**：ASR 纠错表 (prompt 注入素材) |
| `audio/` | FLAC 音频输出 (.flac, 按源文件夹) |
| `txt/` | 原始转录 (.txt) |
| `html/` | 最终 HTML (.html 中间版自动删除, _verified.html 保留) |
| `html_summary/` | 章节要点提炼 (每章节一个 HTML) |
