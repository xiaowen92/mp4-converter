"""Extract audio from mp4 video files to high-quality FLAC via ffmpeg.

Usage:
    python3 scripts/extract_audio.py <file.mp4>          # Single file
    python3 scripts/extract_audio.py --folder <dir>      # Batch (recursive)
    python3 scripts/extract_audio.py --folder <dir> --force   # Re-extract all

Output: audio/<parent-folder>/<original-name>.flac
  - Keeps the original filename (including "N、" numbering prefix)
  - Keeps native 44.1kHz stereo, s24 lossless
"""

import sys
import os
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".m4v"}

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def extract_one(mp4_path: Path, force: bool = False) -> Path | None:
    """Extract audio from one mp4 → audio/<parent-folder>/<name>.flac.

    Returns output FLAC path on success, None on failure.
    """
    parent = mp4_path.parent.name
    base = mp4_path.stem
    out_dir = AUDIO_DIR / parent
    out_path = out_dir / f"{base}.flac"

    if out_path.exists() and out_path.stat().st_size > 0 and not force:
        print(f"  [SKIP] 已存在: {out_path.name}")
        return out_path

    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(mp4_path),
        "-vn", "-acodec", "flac", "-compression_level", "12",
        "-sample_fmt", "s32",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        err = result.stderr.strip().split("\n")[-1] if result.stderr.strip() else "unknown"
        print(f"  [ERROR] {mp4_path.name}: {err}")
        if out_path.exists():
            out_path.unlink()
        return None

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"  [OK] {out_path.name} ({size_mb:.1f}MB)")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    if not args:
        print(__doc__)
        sys.exit(1)

    target = args[0]

    if target == "--folder":
        if len(args) < 2:
            print("用法: --folder <目录>")
            sys.exit(1)
        folder = Path(args[1])
        if not folder.is_dir():
            print(f"[ERROR] 目录不存在: {folder}")
            sys.exit(1)
        files = sorted(
            f for f in folder.rglob("*")
            if f.suffix.lower() in VIDEO_EXTS
        )
        print(f"找到 {len(files)} 个视频文件")
        ok = 0
        for i, f in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {f.parent.name}/{f.name}")
            if extract_one(f, force=force):
                ok += 1
        print(f"\n[Done] 成功 {ok}/{len(files)}")
    else:
        mp4 = Path(target)
        if not mp4.exists():
            print(f"[ERROR] 文件不存在: {mp4}")
            sys.exit(1)
        extract_one(mp4, force=force)


if __name__ == "__main__":
    main()
