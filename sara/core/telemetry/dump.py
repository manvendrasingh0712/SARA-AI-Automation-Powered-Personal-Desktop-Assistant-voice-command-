"""
Dev CLI: python -m sara.core.telemetry.dump --last 5
Prints recent turn_trace rows from telemetry.sqlite (read-only connection).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from .store import db_path

_COLUMNS = (
    "ts", "source", "route", "intent", "outcome", "stt_ms", "route_ms",
    "llm_ttft_ms", "tool_ms", "tts_start_ms", "ttfa_ms", "total_ms",
)
_HEADERS = (
    "time", "src", "route", "intent", "out", "stt", "rte", "ttft", "tool",
    "tts", "ttfa", "total",
)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0f}"
    return str(value)


def fetch_rows(path: Path, limit: int) -> list[tuple]:
    """Newest `limit` rows via a read-only connection."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        return conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM turn_trace ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def render(rows: list[tuple]) -> str:
    """Format rows as an aligned plain-text table (milliseconds)."""
    table: list[tuple] = [_HEADERS]
    for ts, *rest in rows:
        clock = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "-"
        table.append((clock, *[_cell(v) for v in rest]))
    widths = [max(len(str(row[i])) for row in table) for i in range(len(_HEADERS))]
    return "\n".join(
        "  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)).rstrip()
        for row in table
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Show recent SARA turn traces.")
    parser.add_argument("--last", type=int, default=10, help="number of rows")
    parser.add_argument("--db", default=None, help="path to telemetry.sqlite")
    args = parser.parse_args(argv)
    path = Path(args.db) if args.db else db_path()
    if not path.exists():
        print(f"No telemetry database found at {path}")
        return 1
    try:
        rows = fetch_rows(path, max(1, args.last))
    except sqlite3.Error as exc:
        print(f"Could not read telemetry database: {exc}")
        return 1
    print(render(rows) if rows else "No traces recorded yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())