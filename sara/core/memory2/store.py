"""Memory2Store: temporal, correctable structured memory on SQLite."""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import numpy as np

from .schema import SCHEMA_VERSION, default_db_path, open_db
from .store_read import STATUSES, TABLES, _ReadMixin
from .store_write import _WriteMixin
from .util import clean_text, encode_vec, subject_label, fact_text, valid_id

logger = logging.getLogger(__name__)

_EMBED_CACHE_MAX = 256
_TOUCH_MAX = 200
_KEEP_META = ("extract_watermark", "schema_version")


def _default_embed(text: str) -> np.ndarray | None:
    """sara.core.rag.embed_text, or None when unavailable."""
    from sara.core.rag import embed_text

    vec = embed_text(text)
    return None if vec is None else np.asarray(vec, dtype=np.float32)


class Memory2Store(_ReadMixin, _WriteMixin):
    """Thread-safe store; one RLock serialises all access to the shared connection."""

    def __init__(
        self,
        db_path: str | None = None,
        embed: Callable[[str], np.ndarray | None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = str(db_path) if db_path else str(default_db_path())
        self._conn = open_db(self._path)
        self._lock = threading.RLock()
        self._embed_fn = embed or _default_embed
        self._clock = clock
        self._depth = 0
        self._closed = False
        self._gen = 0
        self._mat_cache: dict[tuple[str, str], tuple[int, dict]] = {}
        self._dim_warned = False
        self._embed_warned = False
        self._emb_cache: dict[str, np.ndarray | None] = {}
        self._emb_lock = threading.Lock()
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    # ---- infrastructure -------------------------------------------------
    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    @contextmanager
    def _tx(self, invalidate: bool = True) -> Iterator[sqlite3.Connection]:
        """Re-entrant write transaction (BEGIN IMMEDIATE ... COMMIT); rolls back on error."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Memory2Store is closed")
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                self._gen += 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if invalidate:
                self._gen += 1
            if outer:
                try:
                    self._conn.execute("COMMIT")
                except sqlite3.Error:
                    self._gen += 1
                    self._conn.execute("ROLLBACK")
                    raise

    def transaction(self):
        """Public re-entrant transaction: everything inside commits or rolls back together."""
        return self._tx()

    def _read(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        try:
            with self._lock:
                if self._closed:
                    return []
                return self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            logger.warning("[Memory2] read failed (%s)", type(exc).__name__)
            return []

    def _call_embed(self, text: str) -> np.ndarray | None:
        try:
            raw = self._embed_fn(text)
            if raw is None:
                return None
            vec = np.asarray(raw, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vec))
            if vec.size == 0 or not math.isfinite(norm) or norm == 0.0:
                return None
            return vec / norm
        except Exception as exc:  # noqa: BLE001
            if not self._embed_warned:
                self._embed_warned = True
                logger.warning("[Memory2] embedding unavailable (%s); storing without vector", type(exc).__name__)
            return None

    def _embed_text(self, text: str) -> np.ndarray | None:
        with self._emb_lock:
            if text in self._emb_cache:
                return self._emb_cache.pop(text)
        return self._call_embed(text)

    def warm_embeddings(self, texts: Iterable[str]) -> None:
        """Pre-compute embeddings outside any lock so a later transaction never waits on the model."""
        for text in list(dict.fromkeys(texts))[:_EMBED_CACHE_MAX]:
            vec = self._call_embed(text)
            with self._emb_lock:
                if len(self._emb_cache) >= _EMBED_CACHE_MAX:
                    self._emb_cache.pop(next(iter(self._emb_cache)))
                self._emb_cache[text] = vec

    def fact_embedding_text(self, subject: str, predicate: str, obj: str) -> str:
        """The exact sentence upsert_fact embeds (used to pre-warm the cache)."""
        from .schema import classify_predicate

        return fact_text(subject_label(subject), classify_predicate(predicate)[0], obj)

    # ---- meta -------------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        rows = self._read("SELECT value FROM mem2_meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_meta(self, key: str, value: str) -> None:
        with self._tx(invalidate=False) as conn:
            conn.execute("INSERT OR REPLACE INTO mem2_meta(key, value) VALUES (?, ?)", (str(key), str(value)))

    # ---- item management --------------------------------------------------
    def update_item(self, item_id: int, kind: str, patch: Mapping[str, Any]) -> bool:
        """Edit object_text/summary, importance or pinned; invalid input returns False."""
        try:
            if kind not in TABLES or not valid_id(item_id) or not isinstance(patch, Mapping) or not patch:
                return False
            text_key = "object_text" if kind == "facts" else "summary"
            if any(k not in ("importance", "pinned", text_key) for k in patch):
                return False
            sets: list[str] = []
            params: list[Any] = []
            if "importance" in patch:
                value = patch["importance"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                    return False
                sets.append("importance = ?")
                params.append(float(value))
            if "pinned" in patch:
                if patch["pinned"] not in (True, False, 0, 1):
                    return False
                sets.append("pinned = ?")
                params.append(int(bool(patch["pinned"])))
            if text_key in patch:
                if not isinstance(patch[text_key], str):
                    return False
                text = clean_text(patch[text_key], text_key)
                current = self.get_item(item_id, kind)
                if current is None:
                    return False
                sets.append(f"{text_key} = ?")
                params.append(text)
                if current["status"] != "retracted":
                    sentence = text if kind == "events" else fact_text(current["subject"], current["predicate"], text)
                    vec = self._call_embed(sentence)
                    sets.append("embedding = ?")
                    params.append(None if vec is None else encode_vec(vec))
            with self._tx() as conn:
                cur = conn.execute(f"UPDATE {TABLES[kind]} SET {', '.join(sets)} WHERE id = ?", (*params, item_id))
                return cur.rowcount > 0
        except (ValueError, sqlite3.Error, RuntimeError) as exc:
            logger.warning("[Memory2] update_item failed (%s)", type(exc).__name__)
            return False

    def _simple_update(self, sql_set: str, params: Sequence[Any], item_id: int, kind: str) -> bool:
        if kind not in TABLES or not valid_id(item_id):
            return False
        try:
            with self._tx() as conn:
                cur = conn.execute(f"UPDATE {TABLES[kind]} SET {sql_set} WHERE id = ?", (*params, item_id))
                return cur.rowcount > 0
        except (sqlite3.Error, RuntimeError) as exc:
            logger.warning("[Memory2] update failed (%s)", type(exc).__name__)
            return False

    def set_pinned(self, item_id: int, kind: str, pinned: bool) -> bool:
        return self._simple_update("pinned = ?", (int(bool(pinned)),), item_id, kind)

    def set_status(self, item_id: int, kind: str, status: str) -> bool:
        if status not in STATUSES:
            return False
        return self._simple_update("status = ?", (status,), item_id, kind)

    def retract(self, item_id: int, kind: str) -> bool:
        """Mark retracted and drop the embedding so it can never be retrieved."""
        return self._simple_update("status = 'retracted', embedding = NULL", (), item_id, kind)

    def delete_row(self, item_id: int, kind: str) -> bool:
        """Hard delete (row and embedding)."""
        if kind not in TABLES or not valid_id(item_id):
            return False
        try:
            with self._tx() as conn:
                return conn.execute(f"DELETE FROM {TABLES[kind]} WHERE id = ?", (item_id,)).rowcount > 0
        except (sqlite3.Error, RuntimeError) as exc:
            logger.warning("[Memory2] delete failed (%s)", type(exc).__name__)
            return False

    def touch(self, ids: Sequence[tuple[str, int]], now: float | None = None) -> None:
        """Record usage (last_used_at, use_count); never raises."""
        try:
            stamp = self._now(now)
            groups: dict[str, list[int]] = {"facts": [], "events": []}
            for kind, row_id in list(ids)[:_TOUCH_MAX]:
                if kind in groups and valid_id(row_id):
                    groups[kind].append(row_id)
            with self._tx(invalidate=False) as conn:
                for kind, row_ids in groups.items():
                    if row_ids:
                        marks = ",".join("?" * len(row_ids))
                        conn.execute(
                            f"UPDATE {TABLES[kind]} SET last_used_at = ?, use_count = use_count + 1 "
                            f"WHERE id IN ({marks})",
                            (stamp, *row_ids),
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Memory2] touch failed (%s)", type(exc).__name__)

    def wipe_all(self) -> None:
        """Delete every row of all four tables ("forget everything").

        The extraction watermark and schema version are written back so old
        conversation rows are never re-extracted after a wipe.
        """
        with self._tx() as conn:
            marks = ",".join("?" * len(_KEEP_META))
            keep = conn.execute(f"SELECT key, value FROM mem2_meta WHERE key IN ({marks})", _KEEP_META).fetchall()
            for table in ("mem2_facts", "mem2_events", "mem2_entities", "mem2_meta"):
                conn.execute(f"DELETE FROM {table}")
            for row in keep:
                conn.execute("INSERT INTO mem2_meta(key, value) VALUES (?, ?)", (row["key"], row["value"]))

    def close(self) -> None:
        """Close the connection; safe to call repeatedly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._mat_cache.clear()
            try:
                self._conn.close()
            except sqlite3.Error:
                pass