"""Entity name normalisation and resolution (exact, alias, fuzzy merge)."""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import time
import unicodedata

_HONORIFICS = frozenset({"mr", "mrs", "ms", "dr", "shri", "smt", "ji"})
_WS = re.compile(r"\s+")
_FUZZY_MIN = 0.9
_MAX_ALIASES = 20


def normalize_name(name: object) -> str:
    """Lowercase, NFKC, drop honorifics, collapse spaces."""
    text = unicodedata.normalize("NFKC", str(name or "")).lower()
    kept = [t for t in (tok.strip(".,") for tok in _WS.split(text)) if t and t not in _HONORIFICS]
    return " ".join(kept) if kept else _WS.sub(" ", text).strip()


def parse_aliases(raw: object) -> list[str]:
    """Decode the aliases JSON column into a list of strings."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [a for a in value if isinstance(a, str)] if isinstance(value, list) else []


def resolve_entity(
    conn: sqlite3.Connection, name: str, type: str | None = None, *, now: float | None = None
) -> int:
    """Return the id of the entity ``name``: exact/alias match, else fuzzy merge, else new row.

    Does not commit; the caller owns the transaction. Raises ValueError for an empty name.
    """
    display = _WS.sub(" ", str(name or "")).strip()
    key = normalize_name(display)
    if not key:
        raise ValueError("entity name must not be empty")
    ts = time.time() if now is None else float(now)
    best_id, best_ratio, best_aliases = None, 0.0, []
    for row in conn.execute("SELECT id, name, aliases FROM mem2_entities").fetchall():
        aliases = parse_aliases(row["aliases"])
        names = [n for n in {normalize_name(row["name"]), *(normalize_name(a) for a in aliases)} if n]
        if key in names:
            conn.execute(
                "UPDATE mem2_entities SET last_seen=?, mention_count=mention_count+1, "
                "type=COALESCE(type, ?) WHERE id=?",
                (ts, type, row["id"]),
            )
            return int(row["id"])
        ratio = max((difflib.SequenceMatcher(None, key, n).ratio() for n in names), default=0.0)
        if ratio > best_ratio:
            best_id, best_ratio, best_aliases = int(row["id"]), ratio, aliases
    if best_id is not None and best_ratio >= _FUZZY_MIN:
        if display not in best_aliases and len(best_aliases) < _MAX_ALIASES:
            best_aliases.append(display)
        conn.execute(
            "UPDATE mem2_entities SET aliases=?, last_seen=?, mention_count=mention_count+1 WHERE id=?",
            (json.dumps(best_aliases, ensure_ascii=False), ts, best_id),
        )
        return best_id
    cur = conn.execute(
        "INSERT INTO mem2_entities(name, type, aliases, first_seen, last_seen, mention_count) "
        "VALUES (?, ?, '[]', ?, ?, 1)",
        (display, type, ts, ts),
    )
    return int(cur.lastrowid)