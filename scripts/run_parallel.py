"""Parallel batch orchestrator: mp4 → FLAC → txt → _verified.html.

Three-stage bounded pipeline overlapping I/O (extract), GPU/CPU (transcribe),
and network (DeepSeek calibrate+polish):

    [videos] → Stage A extract (3 threads) → [flacs]
      → Stage B transcribe (MPS 2 / CPU 3 threads) → [txts]
        → Stage C deepseek (6 threads) → _verified.html

Features:
- Hardware detection: auto-lowers parallelism on weak machines
- MPS auto-detection: torch MPS available → GPU transcription (default)
- Resume: skips files whose _verified.html exists; per-stage artifacts reused
- Single file → falls back to sequential run_pipeline.process_one
- Errors logged to pipeline_errors.log, batch continues

Usage:
    python3 scripts/run_parallel.py --all
    python3 scripts/run_parallel.py --folder "<path/to/课程文件夹>"
    python3 scripts/run_parallel.py --all --extract-j 3 --transcribe-j 2 --deepseek-j 6
    python3 scripts/run_parallel.py --all --transcribe-device cpu   # 强制 CPU 转录
"""
import sys
import os
import queue
import subprocess
import threading
import datetime
import time
from pathlib import Path

from extract_audio import extract_one, VIDEO_EXTS
from run_pipeline import process_one, collect_videos, DEFAULT_ROOT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
AUDIO_DIR = REPO_ROOT / "audio"
TXT_DIR = REPO_ROOT / "txt"
HTML_DIR = REPO_ROOT / "html"
ERROR_LOG = REPO_ROOT / "pipeline_errors.log"

# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def detect_hardware():
    """Return (cpu_count, total_mem_gb)."""
    cpu = os.cpu_count() or 4
    mem_gb = None
    try:
        import psutil
        mem_gb = psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass
    if mem_gb is None:
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
            ).stdout.strip()
            mem_gb = int(out) / (1024 ** 3)
        except (ValueError, subprocess.SubprocessError):
            mem_gb = 8.0
    return cpu, mem_gb


def detect_mps():
    """Check torch MPS availability (GPU transcription)."""
    try:
        import torch
        return torch.backends.mps.is_available()
    except ImportError:
        return False


def adjust_parallelism(extract_j, transcribe_j, deepseek_j, cpu, mem_gb):
    """Auto-lower parallelism for weak hardware. Default 3/3/6 needs >=10 cores + >=16GB."""
    if cpu < 6 or mem_gb < 10:
        print(f"[WARNING] 硬件较弱 ({cpu} 核, {mem_gb:.0f}GB): 并行模式收益有限，建议串行")
    # Scale by cores: 3/3/6 fits 12 cores; scale linearly below that
    if cpu < 10:
        factor = max(cpu - 4, 1) / 8.0
        transcribe_j = max(1, min(transcribe_j, int(round(3 * factor))))
        extract_j = max(1, min(extract_j, int(round(3 * factor))))
        deepseek_j = max(1, min(deepseek_j, int(round(6 * factor))))
    # RAM: each transcribe worker ~2.5GB; keep >=6GB headroom
    max_workers_by_mem = max(1, int((mem_gb - 6) / 2.5))
    transcribe_j = min(transcribe_j, max_workers_by_mem)
    return extract_j, transcribe_j, deepseek_j


# ---------------------------------------------------------------------------
# Stage workers
# ---------------------------------------------------------------------------

class Counter:
    def __init__(self):
        self.n = 0
        self.lock = threading.Lock()

    def inc(self):
        with self.lock:
            self.n += 1
            return self.n


def stage_extract_worker(in_q, out_q, force):
    while True:
        item = in_q.get()
        if item is None:
            in_q.task_done()
            return
        mp4_path, name, course = item
        flac = AUDIO_DIR / course / f"{name}.flac"
        try:
            if not (flac.exists() and flac.stat().st_size > 0):
                extract_one(mp4_path, force=force)
            if flac.exists() and flac.stat().st_size > 0:
                out_q.put((flac, name, course))
            else:
                log_error(mp4_path, "extract 失败")
        except Exception as e:
            log_error(mp4_path, f"extract: {e}")
        finally:
            in_q.task_done()


def stage_transcribe_worker(in_q, out_q, device):
    while True:
        item = in_q.get()
        if item is None:
            in_q.task_done()
            return
        flac, name, course = item
        txt = TXT_DIR / course / f"{name}.txt"
        try:
            if not (txt.exists() and txt.stat().st_size > 0):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "transcribe.py"),
                     str(flac), "--device", device],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    tail = (result.stderr or result.stdout).strip().split("\n")[-2:]
                    raise RuntimeError(f"transcribe: {tail}")
                # transcribe.py writes txt next to flac → move to txt/
                flac_txt = flac.with_suffix(".txt")
                if flac_txt.exists():
                    txt.parent.mkdir(parents=True, exist_ok=True)
                    flac_txt.rename(txt)
            if txt.exists() and txt.stat().st_size > 0:
                out_q.put((txt, name, course))
            else:
                log_error(flac, "transcribe 产物缺失")
        except Exception as e:
            log_error(flac, f"transcribe: {e}")
        finally:
            in_q.task_done()


def stage_deepseek_worker(in_q, done_counter, total):
    while True:
        item = in_q.get()
        if item is None:
            in_q.task_done()
            return
        txt, name, course = item
        html = HTML_DIR / course / f"{name}.html"
        verified = HTML_DIR / course / f"{name}_verified.html"
        try:
            if verified.exists() and verified.stat().st_size > 0:
                print(f"  [SKIP] {verified.name}")
            else:
                html.parent.mkdir(parents=True, exist_ok=True)
                if not (html.exists() and html.stat().st_size > 0):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT_DIR / "calibrate.py"),
                         str(txt), str(html)],
                        capture_output=True, text=True,
                    )
                    if result.returncode != 0:
                        tail = (result.stderr or result.stdout).strip().split("\n")[-2:]
                        raise RuntimeError(f"calibrate: {tail}")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT_DIR / "polish.py"), str(html)],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    tail = (result.stderr or result.stdout).strip().split("\n")[-2:]
                    raise RuntimeError(f"polish: {tail}")
                print(f"  [DONE] {verified.name}")
        except Exception as e:
            log_error(txt, f"deepseek: {e}")
        finally:
            in_q.task_done()
            with done_counter.lock:
                done_counter.n += 1
                print(f"  [{done_counter.n}/{total}] 完成 {name[:40]}")


def log_error(src, msg):
    line = f"{datetime.datetime.now().isoformat()} {src}: {msg}"
    print(f"  [ERROR] {line}", flush=True)
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    def opt_value(flag, default):
        for a in args:
            if a.startswith(flag + "="):
                return a.split("=", 1)[1]
        return default

    extract_j = int(opt_value("--extract-j", 3))
    transcribe_j = int(opt_value("--transcribe-j", 3))
    deepseek_j = int(opt_value("--deepseek-j", 6))
    device = opt_value("--transcribe-device", "auto")
    force = "--force" in args

    # --- Collect videos ---
    if "--all" in args:
        root = DEFAULT_ROOT
        if "--root" in args:
            i = args.index("--root")
            if i + 1 < len(args):
                root = Path(args[i + 1])
        if not root.is_dir():
            print(f"[ERROR] 目录不存在: {root} (可用 --root <路径> 指定)")
            sys.exit(1)
        files = collect_videos(root)
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
    elif "--single" in args:
        i = args.index("--single")
        if i + 1 >= len(args):
            print("用法: --single <file.mp4>")
            sys.exit(1)
        print("[INFO] 单文件走串行管线 (run_pipeline.process_one)")
        try:
            process_one(Path(args[i + 1]))
        except Exception as e:
            log_error(args[i + 1], str(e))
            sys.exit(1)
        sys.exit(0)
    else:
        print(__doc__)
        sys.exit(1)

    if not files:
        print("未找到视频文件")
        sys.exit(1)

    # --- Hardware detection ---
    cpu, mem_gb = detect_hardware()
    mps = detect_mps()
    if device == "auto":
        device = "mps" if mps else "cpu"
        if device == "mps":
            transcribe_j = min(transcribe_j, 2)  # GPU shared, keep low
    print(f"硬件检测: CPU {cpu} 核, 内存 {mem_gb:.0f}GB, "
          f"torch MPS {'可用' if mps else '不可用'}", flush=True)
    print(f"转录设备: {device}", flush=True)
    extract_j, transcribe_j, deepseek_j = adjust_parallelism(
        extract_j, transcribe_j, deepseek_j, cpu, mem_gb)
    print(f"并行度: extract={extract_j}, transcribe={transcribe_j}, "
          f"deepseek={deepseek_j}", flush=True)

    # --- Filter completed ---
    todo = []
    for f in files:
        course = f.parent.name
        name = f.stem
        verified = HTML_DIR / course / f"{name}_verified.html"
        if verified.exists() and verified.stat().st_size > 0:
            continue
        todo.append((f, name, course))
    print(f"待处理: {len(todo)}/{len(files)} 个文件", flush=True)

    if not todo:
        print("全部已完成")
        return

    if len(todo) == 1:
        print("[INFO] 仅 1 个文件，走串行管线")
        try:
            process_one(todo[0][0])
        except Exception as e:
            log_error(todo[0][0], str(e))
        return

    # --- Pipeline setup ---
    flac_q = queue.Queue(maxsize=max(extract_j, 1))
    txt_q = queue.Queue(maxsize=max(transcribe_j, 1))
    deepseek_q = queue.Queue(maxsize=max(deepseek_j, 1))
    done_counter = Counter()

    t0 = time.time()
    threads = []

    for _ in range(extract_j):
        t = threading.Thread(target=stage_extract_worker, args=(flac_q, txt_q, force), daemon=True)
        t.start(); threads.append(t)
    for _ in range(transcribe_j):
        t = threading.Thread(target=stage_transcribe_worker, args=(txt_q, deepseek_q, device), daemon=True)
        t.start(); threads.append(t)
    for _ in range(deepseek_j):
        t = threading.Thread(target=stage_deepseek_worker, args=(deepseek_q, done_counter, len(todo)), daemon=True)
        t.start(); threads.append(t)

    for item in todo:
        flac_q.put(item)

    # Wait for all stages to drain
    flac_q.join()
    for _ in range(extract_j):
        flac_q.put(None)
    txt_q.join()
    for _ in range(transcribe_j):
        txt_q.put(None)
    deepseek_q.join()
    for _ in range(deepseek_j):
        deepseek_q.put(None)

    for t in threads:
        t.join(timeout=5)

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"[Done] 处理 {len(todo)} 个文件, 耗时 {elapsed/60:.1f} 分钟")
    print(f"平均 {elapsed/len(todo):.1f}s/文件")


if __name__ == "__main__":
    main()
