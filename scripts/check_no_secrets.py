"""Fail (exit 1) when a tracked file looks like it contains a credential. Never prints the matched value.

Usage: python scripts/check_no_secrets.py [path ...]     (no paths = every file `git ls-files` lists)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "Google OAuth client secret": re.compile(r"GOCSPX-[0-9A-Za-z_\-]{10,}"),
    "refresh_token value": re.compile(r"""["']?refresh_token["']?\s*[:=]\s*["'][A-Za-z0-9_\-./]{20,}["']"""),
    "client_secret value": re.compile(r"""["']?client_secret["']?\s*[:=]\s*["'][A-Za-z0-9_\-]{16,}["']"""),
    "key/token/secret assigned a 32+ hex string": re.compile(
        r"""(?i)(?:api[_-]?key|secret|token|password)\w*["']?\s*[:=]\s*["']?[0-9a-f]{32,}\b"""
    ),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".wav", ".mp3", ".onnx", ".bin", ".pyc", ".zip", ".woff", ".woff2", ".sqlite", ".db"}
SELF = Path(__file__).resolve()


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]


def scan_text(text: str):
    for lineno, line in enumerate(text.splitlines(), 1):
        if "secret-scan: allow" in line:   # a deliberately fake value (the scanner's own test)
            continue
        for name, rx in PATTERNS.items():
            if rx.search(line):
                yield lineno, name


def scan_paths(paths) -> list[tuple[str, int, str]]:
    hits = []
    for p in paths:
        p = Path(p)
        if p.resolve() == SELF or p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, name in scan_text(text):
            try:
                shown = p.resolve().relative_to(ROOT)
            except ValueError:
                shown = p
            hits.append((str(shown), lineno, name))
    return hits


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or tracked_files()
    hits = scan_paths(paths)
    for f, n, what in hits:
        print(f"{f}:{n}: possible {what} (value not shown)")
    print(f"checked {len(paths)} files, {len(hits)} finding(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
