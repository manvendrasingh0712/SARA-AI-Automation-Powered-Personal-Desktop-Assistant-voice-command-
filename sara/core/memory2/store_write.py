"""Write side of Memory2Store: entities, facts (temporal supersede) and events."""
from __future__ import annotations

import json
import sqlite3
from typing import Sequence

import numpy as np

from .entities import resolve_entity
from .schema import classify_predicate
from .types import FactResult
from .util import clamp01, clean_text, decode_vec, encode_vec, fact_text, norm_text, subject_label

_COSINE_DUPLICATE = 0.92
_EVENT_DEDUPE_WINDOW_S = 86400.0
_MAX_EVENT_ENTITIES = 8
_EVENT_SQL = "SELECT id, summary FROM mem2_events WHERE status = 'active' AND ts BETWEEN ? AND ?"


def _blob(vec: np.ndarray | None) -> bytes | None:
    return None if vec is None else encode_vec(vec)


class _WriteMixin:
    """Insert/upsert methods; expects _tx(), _read(), _embed_text(), _now() from Memory2Store."""

    def upsert_entity(self, name: str, type: str | None = None, *, now: float | None = None) -> int:
        """Resolve or create an entity row; raises ValueError for an empty name."""
        clean = clean_text(name, "name")
        etype = None if type is None else (str(type).strip().lower()[:40] or None)
        stamp = self._now(now)
        with self._tx() as conn:
            return resolve_entity(conn, clean, etype, now=stamp)

    def _subject_id(self, conn: sqlite3.Connection, subject: str, stamp: float) -> int:
        label = subject_label(subject)
        return resolve_entity(conn, label, "person" if label == "user" else None, now=stamp)

    def _fact_needs_embedding(self, label: str, predicate: str, text: str) -> bool:
        """Cheap pre-check (no lock held afterwards): is an identical active fact already stored?"""
        rows = self._read(
            "SELECT f.object_text FROM mem2_facts f JOIN mem2_entities e ON e.id = f.subject_id "
            "WHERE f.predicate = ? AND f.status = 'active' AND lower(e.name) = ?",
            (predicate, label.lower()),
        )
        key = norm_text(text)
        return not any(norm_text(r["object_text"]) == key for r in rows)

    def upsert_fact(
        self, subject: str, predicate: str, obj: str, *, valid_from: float | None = None,
        confidence: float = 0.7, importance: float = 0.5, source_turn_id: str | None = None,
        now: float | None = None,
    ) -> FactResult:
        """Insert a fact; functional predicates supersede older values, multi ones dedupe.

        Raises ValueError for an empty or over-long object (before touching the database).
        """
        text = clean_text(obj, "object")
        pred, kind = classify_predicate(predicate)
        stamp = self._now(now)
        start = stamp if valid_from is None else float(valid_from)
        conf, imp = clamp01(confidence, 0.7), clamp01(importance, 0.5)
        turn = None if source_turn_id is None else str(source_turn_id)[:64]
        label = subject_label(subject)
        sentence = fact_text(label, pred, text)
        vec = self._embed_text(sentence) if self._fact_needs_embedding(label, pred, text) else None
        key = norm_text(text)
        with self._tx() as conn:
            sid = self._subject_id(conn, subject, stamp)
            rows = conn.execute(
                "SELECT id, object_text, embedding FROM mem2_facts "
                "WHERE subject_id = ? AND predicate = ? AND status = 'active' ORDER BY id",
                (sid, pred),
            ).fetchall()
            same = next((r for r in rows if norm_text(r["object_text"]) == key), None)
            if kind == "functional":
                old = [int(r["id"]) for r in rows if r is not same]
                for old_id in old:
                    conn.execute(
                        "UPDATE mem2_facts SET status = 'superseded', valid_to = ? WHERE id = ?",
                        (start, old_id),
                    )
                if same is not None:
                    conn.execute(
                        "UPDATE mem2_facts SET use_count = use_count + 1, "
                        "confidence = MAX(confidence, ?), last_used_at = ? WHERE id = ?",
                        (conf, stamp, same["id"]),
                    )
                    return FactResult(int(same["id"]), "reinforced", tuple(old))
                action = "superseded_old" if old else "inserted"
            else:
                if same is not None:
                    return FactResult(int(same["id"]), "duplicate")
                if vec is not None:
                    for row in rows:
                        other = decode_vec(row["embedding"])
                        if other is not None and other.size == vec.size:
                            denom = float(np.linalg.norm(other))
                            if denom and float(np.dot(vec, other)) / denom >= _COSINE_DUPLICATE:
                                return FactResult(int(row["id"]), "duplicate")
                old, action = [], "inserted"
            cur = conn.execute(
                "INSERT INTO mem2_facts(subject_id, predicate, object_text, valid_from, status, "
                "confidence, importance, pinned, source_turn_id, created_at, use_count, embedding) "
                "VALUES (?, ?, ?, ?, 'active', ?, ?, 0, ?, ?, 0, ?)",
                (sid, pred, text, start, conf, imp, turn, stamp, _blob(vec)),
            )
            return FactResult(int(cur.lastrowid), action, tuple(old))

    def add_event(
        self, summary: str, ts: float | None = None, entities: Sequence[str] = (),
        importance: float = 0.5, source_turn_id: str | None = None, now: float | None = None,
    ) -> int:
        """Insert an event (same summary within a day returns the existing id)."""
        text = clean_text(summary, "summary")
        stamp = self._now(now)
        when = stamp if ts is None else float(ts)
        names = [clean_text(e, "entity") for e in entities if str(e or "").strip()][:_MAX_EVENT_ENTITIES]
        turn = None if source_turn_id is None else str(source_turn_id)[:64]
        key = norm_text(text)
        window = (when - _EVENT_DEDUPE_WINDOW_S, when + _EVENT_DEDUPE_WINDOW_S)
        existing = [r for r in self._read(_EVENT_SQL, window) if norm_text(r["summary"]) == key]
        if existing:
            return int(existing[0]["id"])
        vec = self._embed_text(text)
        with self._tx() as conn:
            existing = [r for r in conn.execute(_EVENT_SQL, window).fetchall()
                        if norm_text(r["summary"]) == key]
            if existing:
                return int(existing[0]["id"])
            ids = [resolve_entity(conn, n, None, now=stamp) for n in names]
            cur = conn.execute(
                "INSERT INTO mem2_events(ts, summary, entity_ids, importance, pinned, status, "
                "source_turn_id, created_at, use_count, embedding) "
                "VALUES (?, ?, ?, ?, 0, 'active', ?, ?, 0, ?)",
                (when, text, json.dumps(ids), clamp01(importance, 0.5), turn, stamp, _blob(vec)),
            )
            return int(cur.lastrowid)