"""Read-only table of the latest router decisions: python -m sara.core.router.dump [--n 30]"""
from __future__ import annotations

import argparse
import sqlite3
import time
from typing import List, Optional, Sequence

from .store import RouterStore

_HEADER = ("time", "tier", "static", "mode", "propensity", "reward")


def _mode_of(flags: Optional[str]) -> str:
    for token in (flags or "").split(","):
        if token.startswith("mode:"):
            return token[5:]
    return "-"


def _num(value: Optional[float], spec: str) -> str:
    return "-" if value is None else format(float(value), spec)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the newest decisions; only SELECT statements are executed."""
    parser = argparse.ArgumentParser(
        prog="python -m sara.core.router.dump",
        description="Show recent model-router decisions next to the static tier.",
    )
    parser.add_argument("--n", type=int, default=30, help="how many rows (default 30)")
    args = parser.parse_args(argv)
    try:
        path = RouterStore.default_path()
    except Exception:
        print("router database location is not available")
        return 1
    if not path.exists():
        print("no router decisions recorded yet")
        return 0
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT ts, tier, static_tier, flags, propensity, reward "
                "FROM router_samples ORDER BY id DESC LIMIT ?",
                (max(1, min(1000, args.n)),),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        print("could not read the router database")
        return 1
    table: List[Sequence[str]] = [_HEADER]
    for ts, tier, static, flags, propensity, reward in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts or 0)))
        table.append(
            (stamp, str(tier or "-"), str(static or "-"), _mode_of(flags),
             _num(propensity, ".2f"), _num(reward, "+.3f"))
        )
    widths = [max(len(row[i]) for row in table) for i in range(len(_HEADER))]
    for row in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())