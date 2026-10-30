"""Memory 2.0 SQLite schema, predicate registry and database opener."""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

PREDICATES: dict[str, str] = {
    "lives_in": "functional",
    "works_at": "functional",
    "studies_at": "functional",
    "name_is": "functional",
    "birthday": "functional",
    "preferred_language": "functional",
    "wake_time": "functional",
    "relationship_status": "functional",
    "favorite_color": "functional",
    "favorite_food": "functional",
    "age": "functional",
    "likes": "multi",
    "dislikes": "multi",
    "knows": "multi",
    "uses": "multi",
    "interested_in": "multi",
    "has_pet": "multi",
    "speaks": "multi",
    "plays": "multi",
}

# Who a named person is to the user: "my sister is called Riya" ->
# (user, has_sister, Riya). Without these the relation became a bare
# "note" ("user: Riya"), the word "sister" was lost, and "where does my
# sister work?" could never reach "Riya works at Infosys" (memory bench
# multi-hop 45 %, E2E 2026-10-30).
RELATIONS: tuple[str, ...] = (
    "mother", "father", "sister", "brother", "son", "daughter", "wife", "husband",
    "partner", "girlfriend", "boyfriend", "grandmother", "grandfather", "cousin",
    "uncle", "aunt", "friend", "roommate", "boss", "manager", "colleague", "teacher",
)
for _rel in RELATIONS:
    PREDICATES[f"has_{_rel}"] = "multi"

# Hindi / Hinglish relation words the extractor may keep as spoken
# ("has_dost" for "mere dost ka naam Rohan hai").
_RELATION_ALIASES = {
    "dost": "friend", "yaar": "friend", "behen": "sister", "behan": "sister", "bahan": "sister",
    "didi": "sister", "bhai": "brother", "bhaiya": "brother", "maa": "mother", "mummy": "mother",
    "mom": "mother", "mata": "mother", "papa": "father", "dad": "father", "pita": "father",
    "pati": "husband", "patni": "wife", "biwi": "wife", "beta": "son", "beti": "daughter",
    "dadi": "grandmother", "nani": "grandmother", "dada": "grandfather", "nana": "grandfather",
    "flatmate": "roommate", "coworker": "colleague",
}

_FREE_FORM = frozenset({"note", "inferred"})
_COMPACT = {name.replace("_", ""): name for name in PREDICATES}

DDL = """
CREATE TABLE IF NOT EXISTS mem2_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT,
    aliases TEXT DEFAULT '[]',
    first_seen REAL,
    last_seen REAL,
    mention_count INTEGER DEFAULT 1,
    UNIQUE(name, type)
);
CREATE TABLE IF NOT EXISTS mem2_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER,
    predicate TEXT NOT NULL,
    object_text TEXT,
    object_id INTEGER,
    valid_from REAL,
    valid_to REAL,
    status TEXT DEFAULT 'active',
    confidence REAL DEFAULT 0.7,
    importance REAL DEFAULT 0.5,
    pinned INTEGER DEFAULT 0,
    source_turn_id TEXT,
    created_at REAL,
    last_used_at REAL,
    use_count INTEGER DEFAULT 0,
    embedding BLOB NULL
);
CREATE TABLE IF NOT EXISTS mem2_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    summary TEXT NOT NULL,
    entity_ids TEXT DEFAULT '[]',
    importance REAL DEFAULT 0.5,
    pinned INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    source_turn_id TEXT,
    created_at REAL,
    last_used_at REAL,
    use_count INTEGER DEFAULT 0,
    embedding BLOB NULL
);
CREATE TABLE IF NOT EXISTS mem2_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem2_facts_subj ON mem2_facts(subject_id, predicate, status);
CREATE INDEX IF NOT EXISTS idx_mem2_facts_status ON mem2_facts(status);
CREATE INDEX IF NOT EXISTS idx_mem2_events_ts ON mem2_events(ts);
CREATE INDEX IF NOT EXISTS idx_mem2_events_status ON mem2_events(status);
"""


def classify_predicate(predicate: object) -> tuple[str, str]:
    """Return (canonical predicate, "functional" | "multi"); unknown -> ("note", "multi")."""
    key = re.sub(r"[\s\-]+", "_", str(predicate or "").strip().lower())
    if key in PREDICATES:
        return key, PREDICATES[key]
    if key.startswith("has_"):
        alias = _RELATION_ALIASES.get(key[4:])
        if alias:
            return f"has_{alias}", "multi"
    squashed = _COMPACT.get(key.replace("_", ""))
    if squashed:
        return squashed, PREDICATES[squashed]
    if key in _FREE_FORM:
        return key, "multi"
    return "note", "multi"


def default_db_path() -> Path:
    """memory2.sqlite inside the app-data "data" folder."""
    import config as cfg

    resolver = getattr(cfg, "app_data_subdir", None) or getattr(cfg.Config, "app_data_subdir")
    return Path(resolver("data")) / "memory2.sqlite"


def _connect(target: str) -> sqlite3.Connection:
    conn = sqlite3.connect(target, timeout=3.0, check_same_thread=False, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.executescript(DDL)
    except Exception:
        conn.close()
        raise
    return conn


def _quarantine(path: Path) -> None:
    bad = path.with_name(f"{path.name}.bad-{int(time.time())}")
    try:
        path.replace(bad)
    except OSError as exc:
        logger.error("[Memory2] could not move unreadable database aside (%s)", type(exc).__name__)
        path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (and create) the database; a corrupt file is moved aside and replaced."""
    target = str(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    try:
        return _connect(target)
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError as exc:
        if target == ":memory:":
            raise
        logger.error("[Memory2] database unreadable (%s); starting a fresh one", type(exc).__name__)
        _quarantine(Path(target))
        return _connect(target)