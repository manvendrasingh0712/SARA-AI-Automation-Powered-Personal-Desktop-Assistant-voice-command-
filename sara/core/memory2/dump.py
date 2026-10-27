"""``python -m sara.core.memory2.dump [--db PATH] [--all]``: read-only listing, no network."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .schema import default_db_path
from .store import Memory2Store


def _when(ts: float | None) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M") if ts is not None else "-"
    except (TypeError, ValueError, OverflowError, OSError):
        return "-"


def _fact_line(row: dict) -> str:
    tail = "" if row["status"] == "active" else f" [{row['status']}, until {_when(row['valid_to'])}]"
    return (f"  #{row['id']} {row['subject']} {row['predicate']}: {row['object_text']} "
            f"(since {_when(row['valid_from'])}, conf {row['confidence']:.2f}, src {row['source_turn_id'] or '-'}){tail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Memory 2.0 contents (read-only).")
    parser.add_argument("--db", default=None, help="path to memory2.sqlite")
    parser.add_argument("--all", action="store_true", help="include superseded, retracted and archived rows")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    path = Path(args.db) if args.db else default_db_path()
    if not path.exists():
        print(f"No Memory 2.0 database at {path}")
        return 1
    store = Memory2Store(str(path), embed=lambda _text: None)
    try:
        statuses = ("active", "superseded", "retracted", "archived") if args.all else ("active",)
        entities = store.iter_rows("entities")
        print(f"== Entities ({len(entities)}) ==")
        for row in entities:
            print(f"  #{row['id']} {row['name']} [{row['type'] or '-'}] mentions={row['mention_count']}")
        facts = store.iter_rows("facts", statuses)
        print(f"== Facts ({len(facts)}) ==")
        for row in facts:
            print(_fact_line(row))
        events = store.iter_rows("events", statuses)
        print(f"== Events ({len(events)}) ==")
        for row in events:
            tail = "" if row["status"] == "active" else f" [{row['status']}]"
            print(f"  #{row['id']} {_when(row['ts'])} {row['summary']}{tail}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())