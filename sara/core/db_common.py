"""
sara.core.db_common
The SQLite settings every SARA database connection should share (part 3, P6).

    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    apply_pragmas(conn)

WAL + synchronous=NORMAL (safe with WAL, no fsync per commit), a 5 s busy timeout so a second writer waits instead of failing
with "database is locked", in-memory temp tables, a 20 MB page cache. Foreign keys are left to each schema (some tables
rely on SQLite's default). Never raises: a pragma an old SQLite does not know is skipped.
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("sara.db")

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-20000",
)


def apply_pragmas(conn: sqlite3.Connection, extra: tuple = ()) -> None:
    for stmt in PRAGMAS + tuple(extra):
        try:
            conn.execute(stmt)
        except sqlite3.Error as exc:
            logger.debug("%s skipped: %s", stmt, exc)


def optimize_and_checkpoint(conn: sqlite3.Connection, truncate: bool = False) -> None:
    """PRAGMA optimize (refresh planner statistics); optionally shrink the WAL file. Call on shutdown / weekly."""
    try:
        conn.execute("PRAGMA optimize")
        if truncate:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        logger.debug("optimize/checkpoint skipped: %s", exc)
