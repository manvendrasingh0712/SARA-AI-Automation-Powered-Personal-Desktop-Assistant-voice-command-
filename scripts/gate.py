"""Single entry point for the project's quality gate, used instead of running each
check by hand (CLAUDE_CODE_PLAN.md §A). Compact output: only prints failures, ends
with one PASS/FAIL summary line. Never prints more than ~15 lines on a pass.

    python scripts/gate.py            (--fast, the default)
    python scripts/gate.py --fast     compileall, pyflakes, GUI build --check,
                                       gen_env_example --check, check_no_secrets, node tests
    python scripts/gate.py --full     --fast, plus: full pytest, the 3 bench gate modes,
                                       and a 60s smoke launch

Exit code 0 = all checks passed, 1 = at least one failed (that check's own
output is shown in full so the failure is actionable).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(label: str, cmd: list[str], cwd: Path = ROOT, timeout: int = 300) -> tuple[bool, str]:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        ok = r.returncode == 0
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        ok, out = False, f"timed out after {timeout}s"
    dt = time.time() - t0
    return ok, f"[{'OK' if ok else 'FAIL'}] {label} ({dt:.1f}s)" + ("" if ok else "\n" + out[-3000:])


def fast_checks() -> list[tuple[bool, str]]:
    results = []
    results.append(run("compileall", [PY, "-m", "compileall", "-q", "sara", "main.py", "config.py"]))
    results.append(run("pyflakes (ignoring unused imports)", [
        PY, "-c",
        "import subprocess,sys;"
        "r=subprocess.run([sys.executable,'-m','pyflakes','sara'],capture_output=True,text=True);"
        "bad=[l for l in (r.stdout+r.stderr).splitlines() if l.strip() and 'imported but unused' not in l];"
        "print('\\n'.join(bad));"
        "sys.exit(1 if bad else 0)",
    ]))
    results.append(run("gui build --check", [PY, "-m", "sara.gui.build", "--check"]))
    results.append(run("gen_env_example --check", [PY, "scripts/gen_env_example.py", "--check"]))
    results.append(run("check_no_secrets", [PY, "scripts/check_no_secrets.py"]))
    for f in sorted((ROOT / "sara/gui/js/tests").glob("*.test.js")):
        results.append(run(f"node {f.name}", ["node", str(f)]))
    return results


def full_checks() -> list[tuple[bool, str]]:
    results = []
    results.append(run("pytest", [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "tests"], timeout=600))
    results.append(run("bench router", [PY, "-m", "bench.run_bench", "--mode", "router", "--compare", "bench/baseline.json"], timeout=300))
    results.append(run("bench redteam", [PY, "-m", "bench.run_bench", "--mode", "redteam", "--gate"], timeout=300))
    results.append(run("bench memory", [PY, "-m", "bench.run_bench", "--mode", "memory", "--embedder", "hash", "--gate"], timeout=300))
    results.append(run("smoke launch (60s)", [PY, "scripts/smoke_launch.py", "--seconds", "60"], timeout=120))
    return results


def main() -> int:
    full = "--full" in sys.argv
    results = fast_checks() + (full_checks() if full else [])
    failed = [r for ok, r in results if not ok]
    for ok, r in results:
        print(r if not ok else r.splitlines()[0])
    print(f"\n{'PASS' if not failed else 'FAIL'}: {len(results) - len(failed)}/{len(results)} checks passed"
          f"{' (' + ('--full' if full else '--fast') + ')'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
