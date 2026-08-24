"""Batch orchestrator: mp4 → FLAC → txt → html → _verified.html.

Usage:
    python3 scripts/run_pipeline.py --single "<path/to/file.mp4>"
    python3 scripts/run_pipeline.py --folder "<path/to/课程文件夹>"
    python3 scripts/run_pipeline.py --all          # 处理 DEFAULT_ROOT 下全部视频

Per file: extract → transcribe → calibrate → polish. 断点续跑：
  - _verified.html 已存在 → 跳过整个文件
  - 中间 .html 已存在 → 直接 polish
  - txt 已存在 → 直接 calibrate
  - FLAC 已存在 → 直接 transcribe
失败记入 pipeline_errors.log 并继续下一个文件。
"""
import sys
import os
import subprocess
import datetime
from pathlib import Path

from extract_audio import extract_one, VIDEO_EXTS

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
AUDIO_DIR = REPO_ROOT / "audio"
TXT_DIR = REPO_ROOT / "txt"
HTML_DIR = REPO_ROOT / "html"
ERROR_LOG = REPO_ROOT / "pipeline_errors.log"

DEFAULT_ROOT = Path.home() / "Videos"

# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def run_step(cmd, step_name):
    """Run a subprocess step, return True on success."""
    print(f"    [{step_name}] ...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().split("\n")[-3:]
        print(f"    [ERROR] {step_name} 失败: {tail}", flush=True)
        return False
    print(f"    [OK] {step_name}", flush=True)
    return True


def process_one(mp4_path: Path) -> bool:
    """Run full pipeline for one mp4. Return True on success."""
    course = mp4_path.parent.name
    name = mp4_path.stem

    flac = AUDIO_DIR / course / f"{name}.flac"
    txt = TXT_DIR / course / f"{name}.txt"
    html = HTML_DIR / course / f"{name}.html"
    verified = HTML_DIR / course / f"{name}_verified.html"

    # --- Resume checks ---
    if verified.exists() and verified.stat().st_size > 0:
        print(f"  [SKIP] 已完成: {verified.name}", flush=True)
        return True

    if html.exists() and html.stat().st_size > 0:
        print(f"  [RESUME] 已有中间 html，直接校对", flush=True)
    else:
        if not (txt.exists() and txt.stat().st_size > 0):
            if not (flac.exists() and flac.stat().st_size > 0):
                extract_one(mp4_path)
                if not (flac.exists() and flac.stat().st_size > 0):
                    raise RuntimeError(f"extract 失败: {mp4_path.name}")
            # Transcribe → txt written next to flac, then move to txt/
            if not run_step(
                [sys.executable, str(SCRIPT_DIR / "transcribe.py"), str(flac)],
                "transcribe",
            ):
                raise RuntimeError(f"transcribe 失败: {name}")
            flac_txt = flac.with_suffix(".txt")
            if flac_txt.exists():
                txt.parent.mkdir(parents=True, exist_ok=True)
                flac_txt.rename(txt)
        else:
            print(f"  [RESUME] 已有 txt，直接校准", flush=True)

        # Calibrate
        html.parent.mkdir(parents=True, exist_ok=True)
        if not run_step(
            [sys.executable, str(SCRIPT_DIR / "calibrate.py"), str(txt), str(html)],
            "calibrate",
        ):
            raise RuntimeError(f"calibrate 失败: {name}")

    # Polish → _verified.html (deletes intermediate html)
    if not run_step(
        [sys.executable, str(SCRIPT_DIR / "polish.py"), str(html)],
        "polish",
    ):
        raise RuntimeError(f"polish 失败: {name}")

    print(f"  [DONE] {verified.name}", flush=True)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_videos(root: Path) -> list[Path]:
    return sorted(
        f for f in root.rglob("*")
        if f.suffix.lower() in VIDEO_EXTS
    )


def main():
    args = sys.argv[1:]

    if "--single" in args:
        i = args.index("--single")
        if i + 1 >= len(args):
            print("用法: --single <file.mp4>")
            sys.exit(1)
        files = [Path(args[i + 1])]
    elif "--folder" in args:
        i = args.index("--folder")
        if i + 1 >= len(args):
            print("用法: --folder <目录>")
            sys.exit(1)
        folder = Path(args[i + 1])
        if not folder.is_dir():
            print(f"[ERROR] 目录不存在: {folder}")
            sys.exit(1)
        files = collect_videos(folder)
    elif "--all" in args:
        root = DEFAULT_ROOT
        if "--root" in args:
            i = args.index("--root")
            if i + 1 < len(args):
                root = Path(args[i + 1])
        if not root.is_dir():
            print(f"[ERROR] 目录不存在: {root} (可用 --root <路径> 指定)")
            sys.exit(1)
        files = collect_videos(root)
    else:
        print(__doc__)
        sys.exit(1)

    if not files:
        print("未找到视频文件")
        sys.exit(1)

    print(f"共 {len(files)} 个视频文件")
    ok, fail = 0, 0
    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {f.parent.name}/{f.name}", flush=True)
        try:
            if process_one(f):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            msg = f"{datetime.datetime.now().isoformat()} {f}: {e}"
            print(f"  [ERROR] {e}", flush=True)
            with open(ERROR_LOG, "a", encoding="utf-8") as log:
                log.write(msg + "\n")

    print(f"\n{'='*50}")
    print(f"[Done] 成功 {ok}, 失败 {fail}, 共 {len(files)}")
    if fail:
        print(f"失败清单: {ERROR_LOG}")


if __name__ == "__main__":
    main()
