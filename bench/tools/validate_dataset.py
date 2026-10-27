"""Validate golden files: schema, duplicates, intents, coverage; print a review sample."""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.dataset import CATEGORY_BY_INTENT, DEFAULT_FILES, Row, load_rows  # noqa: E402

SAMPLE_SEED = 1337
SAMPLE_SIZE = 30
REQUIRED_FILES = ("intents_en.jsonl", "intents_en_noisy.jsonl")


def _name_of(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return item
    if isinstance(item, (tuple, list)) and item and isinstance(item[0], str):
        return item[0]
    if isinstance(item, Mapping):
        value = item.get("name")
    else:
        value = getattr(item, "name", None)
    return value if isinstance(value, str) else None


def registered_intents() -> List[str]:
    """Registered intent names read from sara.core.intent.patterns._INTENT_PATTERNS."""
    from sara.core.intent import patterns

    raw = patterns._INTENT_PATTERNS
    candidates = list(raw.keys()) if isinstance(raw, Mapping) else [_name_of(i) for i in raw]
    names = [n for n in dict.fromkeys(candidates) if isinstance(n, str)]
    if not names:
        raise RuntimeError("cannot read intent names from _INTENT_PATTERNS")
    return names


def _load_all(errors: List[str]) -> Tuple[List[Row], Dict[str, int]]:
    rows: List[Row] = []
    per_file: Dict[str, int] = {}
    for name in DEFAULT_FILES:
        missing: List[str] = []
        try:
            loaded = load_rows([name], missing=missing)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if missing:
            if name in REQUIRED_FILES:
                errors.append(f"{name}: required file is missing")
            else:
                print(f"SKIP {name} (missing)")
            continue
        per_file[name] = len(loaded)
        rows.extend(loaded)
    return rows, per_file


def _check_rows(rows: Sequence[Row], registered: Set[str], errors: List[str]) -> None:
    allowed = registered | {"chat"}
    seen: Dict[Tuple[str, str], str] = {}
    for row in rows:
        if row.expected_intent not in allowed:
            errors.append(f"{row.id}: unknown intent {row.expected_intent!r}")
        if row.kind == "negative" and row.expected_intent != "chat":
            errors.append(f"{row.id}: negative row must expect 'chat'")
        key = (row.lang, row.text)
        if key in seen:
            errors.append(f"{row.id}: duplicate of {seen[key]} ({row.lang}, {row.text!r})")
        else:
            seen[key] = row.id
    covered = {
        r.expected_intent for r in rows if r.lang == "en" and r.kind == "positive"
    }
    for name in sorted(registered - covered):
        errors.append(f"intent {name!r} has no English positive row")
    mapped = set(CATEGORY_BY_INTENT) - {"chat"}
    for name in sorted(registered - mapped):
        errors.append(f"intent {name!r} missing from CATEGORY_BY_INTENT")
    for name in sorted(mapped - registered):
        errors.append(f"CATEGORY_BY_INTENT has unregistered intent {name!r}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_dataset")
    parser.add_argument("--sample", type=int, default=SAMPLE_SIZE)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    errors: List[str] = []
    rows, per_file = _load_all(errors)
    try:
        registered = set(registered_intents())
    except Exception as exc:
        errors.append(f"registered intents unreadable ({type(exc).__name__}: {exc})")
        registered = set()
    if registered:
        _check_rows(rows, registered, errors)

    for name, count in per_file.items():
        print(f"{name}: {count} rows")
    print("per lang: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r.lang for r in rows).items())))
    print("per kind: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r.kind for r in rows).items())))

    sample = random.Random(SAMPLE_SEED).sample(rows, min(max(args.sample, 0), len(rows)))
    print(f"--- sample of {len(sample)} rows ---")
    for row in sample:
        print(f"{row.id} [{row.lang}/{row.style}] {row.expected_intent}: {row.text}")

    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)
    if errors:
        print(f"FAIL: {len(errors)} error(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())