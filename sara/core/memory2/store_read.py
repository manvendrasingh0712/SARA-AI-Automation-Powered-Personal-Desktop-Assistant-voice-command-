"""Read side of Memory2Store: row views, listings, stats and vector candidates."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Sequence

import numpy as np

from .entities import parse_aliases
from .schema import SCHEMA_VERSION
from .util import decode_vec, fact_text, norm_text, valid_id

logger = logging.getLogger(__name__)

STATUSES = frozenset({"active", "superseded", "retracted", "archived"})
TABLES = {"facts": "mem2_facts", "events": "mem2_events"}
_FACT_SQL = (
    "SELECT f.id, f.predicate, f.object_text, f.valid_from, f.valid_to, f.status, f.confidence, "
    "f.importance, f.pinned, f.source_turn_id, f.created_at, f.last_used_at, f.use_count, "
    "e.name AS subject FROM mem2_facts f LEFT JOIN mem2_entities e ON e.id = f.subject_id"
)
_EVENT_SQL = (
    "SELECT id, ts, summary, entity_ids, importance, pinned, status, source_turn_id, created_at, "
    "last_used_at, use_count FROM mem2_events"
)
_ENTITY_SQL = "SELECT id, name, type, aliases, first_seen, last_seen, mention_count FROM mem2_entities"
_SCAN_CAP = 5000


def _fact_dict(row: sqlite3.Row) -> dict:
    subject = row["subject"] or "?"
    return {
        "kind": "facts", "id": int(row["id"]), "subject": subject, "predicate": row["predicate"],
        "object_text": row["object_text"], "valid_from": row["valid_from"], "valid_to": row["valid_to"],
        "status": row["status"], "confidence": row["confidence"], "importance": row["importance"],
        "pinned": bool(row["pinned"]), "source_turn_id": row["source_turn_id"],
        "created_at": row["created_at"], "last_used_at": row["last_used_at"],
        "use_count": row["use_count"],
        "text": fact_text(subject, row["predicate"], row["object_text"] or ""),
        "inferred": row["predicate"] == "inferred",
    }


def _event_dict(row: sqlite3.Row, names: dict[int, str]) -> dict:
    try:
        ids = [int(i) for i in json.loads(row["entity_ids"] or "[]")]
    except (TypeError, ValueError):
        ids = []
    return {
        "kind": "events", "id": int(row["id"]), "ts": row["ts"], "summary": row["summary"],
        "entity_ids": ids, "entities": [names[i] for i in ids if i in names],
        "importance": row["importance"], "pinned": bool(row["pinned"]), "status": row["status"],
        "source_turn_id": row["source_turn_id"], "created_at": row["created_at"],
        "last_used_at": row["last_used_at"], "use_count": row["use_count"],
        "text": row["summary"], "inferred": False,
    }


def _entity_dict(row: sqlite3.Row) -> dict:
    label = f"{row['name']} ({row['type']})" if row["type"] else row["name"]
    return {
        "kind": "entities", "id": int(row["id"]), "name": row["name"], "type": row["type"],
        "aliases": parse_aliases(row["aliases"]), "first_seen": row["first_seen"],
        "last_seen": row["last_seen"], "mention_count": row["mention_count"], "status": "active",
        "text": label, "inferred": False,
    }


class _ReadMixin:
    """Query methods; expects _read(), _lock, _conn, _gen from Memory2Store."""

    def _entity_names(self) -> dict[int, str]:
        return {int(r["id"]): r["name"] for r in self._read("SELECT id, name FROM mem2_entities")}

    def _rows(self, kind: str, statuses: Sequence[str], cap: int = _SCAN_CAP) -> list[dict]:
        if kind == "entities":
            return [_entity_dict(r) for r in self._read(_ENTITY_SQL + " ORDER BY id DESC LIMIT ?", (cap,))]
        marks = ",".join("?" * len(statuses))
        if kind == "facts":
            sql = f"{_FACT_SQL} WHERE f.status IN ({marks}) ORDER BY f.id DESC LIMIT ?"
            return [_fact_dict(r) for r in self._read(sql, (*statuses, cap))]
        sql = f"{_EVENT_SQL} WHERE status IN ({marks}) ORDER BY id DESC LIMIT ?"
        names = self._entity_names()
        return [_event_dict(r, names) for r in self._read(sql, (*statuses, cap))]

    def iter_rows(self, kind: str, statuses: Sequence[str] = ("active",)) -> list[dict]:
        """All rows of ``kind`` with one of ``statuses`` (entities ignore status), oldest first."""
        if kind not in ("facts", "events", "entities"):
            return []
        wanted = [s for s in statuses if s in STATUSES]
        if kind != "entities" and not wanted:
            return []
        return list(reversed(self._rows(kind, wanted, cap=10**9)))

    def list_items(
        self, kind: str, *, status: str | None = "active", query: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """Newest-first page of items with human "text" and "inferred" flag."""
        try:
            limit = max(1, min(int(limit), 200))
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            return []
        if kind == "archived":
            items = self._rows("facts", ("archived",)) + self._rows("events", ("archived",))
            items.sort(key=lambda d: d.get("created_at") or 0.0, reverse=True)
        elif kind in ("facts", "events", "entities"):
            if status is not None and status not in STATUSES:
                return []
            items = self._rows(kind, tuple(STATUSES) if status is None else (status,))
        else:
            return []
        needle = norm_text(query) if query else ""
        if needle:
            items = [d for d in items if needle in norm_text(d["text"])]
        return items[offset : offset + limit]

    def get_item(self, item_id: int, kind: str) -> dict | None:
        """One item by id, or None."""
        if not valid_id(item_id):
            return None
        if kind == "facts":
            rows = self._read(_FACT_SQL + " WHERE f.id = ?", (item_id,))
            return _fact_dict(rows[0]) if rows else None
        if kind == "events":
            rows = self._read(_EVENT_SQL + " WHERE id = ?", (item_id,))
            return _event_dict(rows[0], self._entity_names()) if rows else None
        if kind == "entities":
            rows = self._read(_ENTITY_SQL + " WHERE id = ?", (item_id,))
            return _entity_dict(rows[0]) if rows else None
        return None

    def stats(self) -> dict:
        """Counts per kind/status plus last_extract_ts, db_bytes and schema_version."""
        out: dict = {"schema_version": SCHEMA_VERSION}
        for kind, table in TABLES.items():
            counts = {s: 0 for s in sorted(STATUSES)}
            for row in self._read(f"SELECT status, COUNT(*) AS c FROM {table} GROUP BY status"):
                counts[row["status"] or "active"] = int(row["c"])
            counts["total"] = sum(counts.values())
            out[kind] = counts
        rows = self._read("SELECT COUNT(*) AS c FROM mem2_entities")
        out["entities"] = int(rows[0]["c"]) if rows else 0
        raw = self.get_meta("last_extract_ts")
        try:
            out["last_extract_ts"] = float(raw) if raw is not None else None
        except ValueError:
            out["last_extract_ts"] = None
        try:
            out["db_bytes"] = os.path.getsize(self._path)
        except OSError:
            out["db_bytes"] = 0
        return out

    def _matrix(self, kind: str, status: str) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        """Per-dimension (ids, unit-norm matrix) cache; caller holds the lock."""
        cached = self._mat_cache.get((kind, status))
        if cached is not None and cached[0] == self._gen:
            return cached[1]
        rows = self._conn.execute(
            f"SELECT id, embedding FROM {TABLES[kind]} WHERE status = ? AND embedding IS NOT NULL",
            (status,),
        ).fetchall()
        groups: dict[int, tuple[list[int], list[np.ndarray]]] = {}
        for row in rows:
            vec = decode_vec(row["embedding"])
            norm = float(np.linalg.norm(vec)) if vec is not None else 0.0
            if vec is None or norm == 0.0:
                continue
            ids, vecs = groups.setdefault(vec.size, ([], []))
            ids.append(int(row["id"]))
            vecs.append(vec / norm)
        built = {
            dim: (np.asarray(ids, dtype=np.int64), np.vstack(vecs).astype(np.float32))
            for dim, (ids, vecs) in groups.items()
        }
        self._mat_cache[(kind, status)] = (self._gen, built)
        return built

    def vector_candidates(
        self, query_vec: object, kinds: Sequence[str] = ("facts", "events"), top_k: int = 20,
        statuses: Sequence[str] = ("active",),
    ) -> list[tuple[str, int, float]]:
        """Top (kind, id, cosine) by similarity; rows of another dimension are skipped."""
        try:
            query = np.asarray(query_vec, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(query))
            if query.size == 0 or not np.isfinite(norm) or norm == 0.0:
                return []
            query = query / norm
            top_k = max(1, min(int(top_k), 200))
            parts, skipped = [], 0
            for kind in kinds:
                for status in statuses:
                    if kind not in TABLES or status not in STATUSES:
                        continue
                    with self._lock:
                        if self._closed:
                            return []
                        built = self._matrix(kind, status)
                    for dim, (ids, mat) in built.items():
                        if dim == query.size:
                            parts.append((kind, ids, mat @ query))
                        else:
                            skipped += int(ids.size)
            if skipped and not self._dim_warned:
                self._dim_warned = True
                logger.warning("[Memory2] %d stored embeddings skipped: dimension mismatch", skipped)
            hits: list[tuple[str, int, float]] = []
            for kind, ids, scores in parts:
                k = min(top_k, int(scores.size))
                order = np.argpartition(-scores, k - 1)[:k] if k < scores.size else range(scores.size)
                hits.extend((kind, int(ids[i]), float(scores[i])) for i in order)
            hits.sort(key=lambda h: h[2], reverse=True)
            return hits[:top_k]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Memory2] vector search failed (%s)", type(exc).__name__)
            return []