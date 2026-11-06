"""P2.3: sara/ uses the logging pipeline (redacted, rotated, non-blocking --
see sara/core/log.py), not bare print(). This fails on any NEW print() call
added to sara/, so the 558 converted in this phase stay converted.

Allowlist: standalone CLI/report tools that happen to live under sara/ but
are run directly for their printed output (same exception as scripts/ and
bench/, which this test does not scan at all) -- argparse + a __main__
guard, verified individually when this allowlist was created.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = ROOT / "sara"

ALLOWLIST = {
    "sara/core/memory2/dump.py",
    "sara/core/memory2/run_once.py",
    "sara/core/router/dump.py",
    "sara/core/telemetry/dump.py",
    "sara/gui/build.py",
}


def _print_calls(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except SyntaxError:
        return []
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]


def test_no_print_outside_the_cli_tool_allowlist():
    offenders = []
    for f in sorted(TARGET_DIR.rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        rel = f.relative_to(ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        for lineno in _print_calls(f):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "print() found in sara/ outside the CLI-tool allowlist -- use logger.<level>(...) instead "
        "(see sara/core/log.py's redacted/rotated pipeline), or add the file to ALLOWLIST above if it "
        "is genuinely a standalone CLI/report tool (argparse + __main__) whose printed output is the "
        f"actual product:\n  " + "\n  ".join(offenders)
    )


def test_allowlisted_files_still_exist_and_are_cli_tools():
    """Keeps the allowlist honest -- a file removed/renamed, or one that stops being a
    standalone CLI tool, should fail loudly instead of silently staying exempt forever."""
    for rel in ALLOWLIST:
        path = ROOT / rel
        assert path.is_file(), f"{rel} is in ALLOWLIST but no longer exists"
        src = path.read_text(encoding="utf-8")
        assert "__main__" in src, f"{rel} is allowlisted as a CLI tool but has no __main__ guard anymore"
