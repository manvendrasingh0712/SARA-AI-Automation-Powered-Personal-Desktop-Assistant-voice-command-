"""Smoke-launch main.py and confirm it survives N seconds without crashing.

Starts `python main.py` as a subprocess (repo root as CWD), waits N seconds
(default 60), asserts the process is still alive, then checks the log file
written DURING this run for "Traceback" or "CRITICAL". Terminates cleanly
(graceful terminate, then a bounded kill if it doesn't exit) either way, so
this never leaves a stray SARA process running or holding the single-instance
mutex.

Log path: same resolution as logging_config.py (P2.1) -- app_data_subdir("logs")
when importable, else <repo root>/logs as a last-resort fallback.

Refuses to run if another SARA instance is already up (tasklist check) --
the single-instance mutex would just make this process exit immediately,
which would look like "crashed in < 1s" and is misleading, and this must
never compete with (or kill) the user's own running SARA.

    python scripts/smoke_launch.py [--seconds 60] [--python path\to\python.exe]

Exit code 0 = alive and clean; 1 = already running (refused); 2 = died
early; 3 = Traceback/CRITICAL in its own fresh log output.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from config import app_data_subdir

    LOG_FILE = app_data_subdir("logs") / "sara.log"
except Exception:
    LOG_FILE = ROOT / "logs" / "sara.log"


def _sara_already_running() -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
             "| Where-Object { $_.CommandLine -match 'main\\.py' }).Count"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() not in ("", "0")
    except Exception:
        return False  # can't tell -> don't block the smoke test over a detection failure


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if _sara_already_running():
        print("[smoke_launch] Another main.py is already running on this PC -- refusing to start a second one.")
        return 1

    before_size = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

    proc = subprocess.Popen(
        [args.python, "main.py"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    print(f"[smoke_launch] started pid={proc.pid}, waiting {args.seconds}s ...")
    try:
        time.sleep(args.seconds)
        alive = proc.poll() is None
        bad_lines = []
        if LOG_FILE.exists():
            with LOG_FILE.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(before_size)
                for line in f:
                    if "Traceback" in line or "CRITICAL" in line:
                        bad_lines.append(line.rstrip("\n"))

        if not alive:
            out = proc.stdout.read() if proc.stdout else ""
            print(f"[smoke_launch] FAILED: process exited within {args.seconds}s (code={proc.returncode})")
            if out:
                print(out[-2000:])
            return 2
        if bad_lines:
            print(f"[smoke_launch] FAILED: {len(bad_lines)} Traceback/CRITICAL line(s) in {LOG_FILE}:")
            for line in bad_lines[:10]:
                print("   " + line)
            return 3

        print(f"[smoke_launch] OK: alive after {args.seconds}s, log clean.")
        return 0
    finally:
        # taskkill /T: main.py's WebView2 window spawns its own child process
        # tree (renderer/gpu/network/crashpad helpers) that proc.terminate()
        # alone does NOT reach -- without /T those survive as orphans after
        # main.py itself exits (confirmed while building this script).
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        print("[smoke_launch] terminated.")


if __name__ == "__main__":
    sys.exit(main())
