"""
sara.core.memory_cleanup
Removes noisy long-term memories that Whisper hallucinations and the activity log had put into the store.

    python -m sara.core.memory_cleanup            # dry run: shows what would be removed, changes nothing
    python -m sara.core.memory_cleanup --apply    # backs the database up first, then removes them

What counts as noise (sources "consolidation" and "fact" only; raw "conversation" rows are never touched):
  * not a plain fact about the user (see memory_consolidation._is_durable_fact): guesses, one-off
    remarks, "User toggled play and pause several times", "They likely confused ...";
  * written from a garbled transcript: a "name" that is two letters or a sentence ("User's name is Kya H").
Close SARA before using --apply: the running app keeps its own copy of the store in memory.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from config import Config

_SOURCES = ("consolidation", "fact")
_GARBLED_NAME_RE = re.compile(r"\bname\s+(?:is|:)\s+(?:just\s+)?([^.]{0,40})", re.IGNORECASE)


def _db_path() -> Path:
    return Path(getattr(Config, "DB_PATH", "") or "")


def is_noise(text: str, source: str = "consolidation") -> bool:
    """True when a stored consolidation/fact line should not be kept."""
    from sara.core.memory_consolidation import _is_durable_fact

    t = (text or "").strip()
    if not t:
        return True
    # Model-written summaries must be plain "User ..." sentences. "fact" rows are cut straight from what
    # the user said, so they are only checked for a garbled name below.
    if source == "consolidation" and not _is_durable_fact(t):
        return True
    m = _GARBLED_NAME_RE.search(t)
    if m:
        name = m.group(1).strip(" \"'")
        # "Kya H", "Just More John", "Sarah" from "Sara": a real first name is one capitalised word
        if len(name.split()) > 1 or len(name) < 3:
            return True
    return False


def find_noise(db_file: Path) -> list[tuple[int, str, str]]:
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, source, text FROM long_term_memory WHERE source IN (%s) ORDER BY id"
            % ",".join("?" * len(_SOURCES)),
            _SOURCES,
        ).fetchall()
    finally:
        con.close()
    return [(i, s, t) for i, s, t in rows if is_noise(t, s)]


def apply_cleanup(db_file: Path, ids: list[int]) -> Path:
    """Back the database up (db + wal/shm), then delete the given rows. Returns the backup path."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = db_file.with_name(f"{db_file.name}.bak-memclean-{stamp}")
    shutil.copy2(db_file, backup)
    for ext in ("-wal", "-shm"):
        side = db_file.with_name(db_file.name + ext)
        if side.exists():
            shutil.copy2(side, db_file.with_name(f"{db_file.name}{ext}.bak-memclean-{stamp}"))
    con = sqlite3.connect(str(db_file))
    try:
        con.executemany("DELETE FROM long_term_memory WHERE id = ?", [(i,) for i in ids])
        con.commit()
    finally:
        con.close()
    return backup


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really delete (a backup is made first)")
    ap.add_argument("--db", help="database file (default: the app's database)")
    args = ap.parse_args(argv)

    db_file = Path(args.db) if args.db else _db_path()
    if not db_file.exists():
        print(f"No database at {db_file}")
        return 1
    noise = find_noise(db_file)
    print(f"{len(noise)} noisy memories in {db_file}")
    for i, src, text in noise:
        print(f"  #{i:<4} [{src}] {text[:100]}")
    if not noise:
        return 0
    if not args.apply:
        print("\nDry run only. Close SARA, then run again with --apply to remove them.")
        return 0
    backup = apply_cleanup(db_file, [i for i, _, _ in noise])
    print(f"\nRemoved {len(noise)} memories. Backup: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
