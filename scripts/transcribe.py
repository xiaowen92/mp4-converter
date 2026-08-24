"""Transcribe audio to text using FunASR Paraformer (Chinese-optimized).
Supports: FLAC, WAV, M4A, MP3, OGG, and any format soundfile can read.

Usage:
    python3 scripts/transcribe.py audio/课程/xxx.flac
    python3 scripts/transcribe.py audio/课程/xxx.flac --device mps
    # → txt written next to the audio file (audio/课程/xxx.txt)
"""
import sys
import os
from funasr import AutoModel

# Paraformer-large: best Chinese ASR + VAD + punctuation
MODEL_ID = "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch"

def main():
    args = sys.argv[1:]
    device = "cpu"
    if "--device" in args:
        i = args.index("--device")
        if i + 1 < len(args):
            device = args[i + 1]
            del args[i:i + 2]
    elif any(a.startswith("--device=") for a in args):
        for a in list(args):
            if a.startswith("--device="):
                device = a.split("=", 1)[1]
                args.remove(a)

    if len(args) != 1:
        print("用法: python transcribe.py <audio_file> [--device mps|cpu]")
        print("支持: FLAC, WAV, M4A, MP3 等常见格式")
        sys.exit(1)

    audio_path = args[0]
    if not os.path.exists(audio_path):
        print(f"[错误] 文件不存在: {audio_path}")
        sys.exit(1)

    output_dir = os.path.dirname(audio_path) or "."
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    output_path = os.path.join(output_dir, f"{basename}.txt")

    print(f"加载 FunASR Paraformer-large (中文优化)... device={device}")
    model = AutoModel(
        model=MODEL_ID,
        # Use VAD to split long audio, skip silences
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        # Punctuation recovery
        punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        device=device,
    )

    print(f"转录中: {audio_path}")
    result = model.generate(
        input=audio_path,
        batch_size_s=300,  # process in 300s chunks for long audio
    )

    # FunASR returns list of dicts with 'text' key
    # With VAD+punc, result is one merged string with punctuation
    # Split on Chinese punctuation for line-by-line output
    with open(output_path, "w", encoding="utf-8") as f:
        for item in result:
            text = item.get("text", "")
            # Split on sentence endings, keep the delimiter
            for delimiter in ["。", "？", "！", "；"]:
                text = text.replace(delimiter, delimiter + "\n")
            f.write(text + "\n")

    print(f"[完成] {output_path}")

if __name__ == "__main__":
    main()
