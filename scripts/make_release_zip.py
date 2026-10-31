"""Build a shareable zip from `git ls-files` only, so ignored/secret files (.env, token.json, *.db, backups) can
never be included. Usage: python scripts/make_release_zip.py [output.zip]"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEVER = (".env", "token.json", "credentials.json")


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else ROOT.parent / f"{ROOT.name}-release.zip"
    raw = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    files = [p for p in raw.decode("utf-8", "replace").split("\0") if p]
    bad = [p for p in files if Path(p).name in NEVER or Path(p).name.startswith(".env.") and Path(p).name != ".env.example"
           or p.lower().endswith((".db", ".sqlite"))]
    if bad:
        print("refusing: these tracked files look private:", ", ".join(bad))
        return 1
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            fp = ROOT / p
            if fp.is_file():
                z.write(fp, p)
    print(f"{len(files)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
