"""Count silently swallowed exceptions: `except ...: pass`, `except ...: continue`, and blind `except Exception`.

    python scripts/count_swallowed.py [path ...]      (default: sara/ plus the top-level .py files)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def scan(paths):
    tot = {"blind": 0, "pass": 0, "continue": 0}
    per_file = {}
    for p in paths:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        c = {"blind": 0, "pass": 0, "continue": 0}
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                t = node.type
                if t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException")):
                    c["blind"] += 1
                body = node.body
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    c["pass"] += 1
                elif len(body) == 1 and isinstance(body[0], ast.Continue):
                    c["continue"] += 1
        per_file[p] = c
        for k in tot:
            tot[k] += c[k]
    return tot, per_file


def main(argv):
    paths = [Path(a) for a in argv] or [*sorted((ROOT / "sara").rglob("*.py")), *sorted(ROOT.glob("*.py"))]
    tot, per_file = scan(paths)
    top = sorted(per_file.items(), key=lambda kv: -(kv[1]["pass"] + kv[1]["continue"]))[:8]
    print(f"blind except: {tot['blind']} | try/except/pass: {tot['pass']} | try/except/continue: {tot['continue']}")
    for p, c in top:
        if c["pass"] or c["continue"]:
            print(f"   {p.relative_to(ROOT)}: pass={c['pass']} continue={c['continue']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
