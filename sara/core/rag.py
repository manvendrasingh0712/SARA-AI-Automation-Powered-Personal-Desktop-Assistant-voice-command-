"""
sara/core/rag.py
Long-term semantic memory (RAG) for Sara AI.

WHY THIS FILE EXISTS
---------------------
Before this module, Sara's ONLY memory was `SaraLLM._history` — a fixed
deque of the last `Config.MAX_MEMORY_EXCHANGES` turns, wiped as soon as
that window slides past. That means anything the user said more than a
handful of turns ago (a preference mentioned once, a fact about
themselves, something they asked to be remembered) was completely gone
from the LLM's context — even though it was still sitting, unused, in
`conversation_log` the whole time.

This module gives Sara real long-term recall: every exchange is stored
here AND semantically indexed, so a relevant memory from days ago can
be pulled back into context on demand — "what's my dog's name again?"
works even if that was mentioned three sessions ago.

ARCHITECTURE
------------
  - One SQLite table `long_term_memory` (id, text, embedding BLOB,
    source, timestamp), living in the SAME canonical DB file as
    preferences/conversation_log/reminders (Config.DB_PATH) — this does
    NOT create a new split-brain DB file (see database.py/reminders.py's
    own DB_PATH fix for why that matters).
  - Embeddings come from Ollama's own `/api/embeddings` endpoint (a
    plain HTTP POST via urllib — no dependency on any particular
    version of the `ollama` pip package, and no extra heavy ML library
    like sentence-transformers). This reuses the Ollama server Sara
    already depends on for chat; pull the embedding model once with:
        ollama pull nomic-embed-text
  - Embeddings are cached in RAM as one (N, D) numpy matrix, loaded once
    at construction and appended to incrementally on writes. Cosine
    similarity over an in-memory matrix is more than fast enough for a
    single-user desktop assistant's memory size (thousands of rows) —
    a real vector database would be overkill here.
  - WRITES (add_memory) go through a background thread + queue, mirroring
    gui_main.py's AsyncDBWriter pattern — a slow/unavailable embedding
    call must never block the conversation loop. add_memory() enqueues
    and returns immediately (fire-and-forget, matching log_message()'s
    default elsewhere in this codebase).
  - READS (search) run on the CALLING thread, since retrieval happens on
    the hot path right before an LLM response — but the embedding call
    itself is given a hard timeout (Config.EMBEDDING_TIMEOUT_S), and any
    failure (Ollama down, model not pulled, timeout) makes search()
    return an empty list rather than raising — a broken embedding
    backend degrades Sara back to exactly her pre-RAG behavior, never a
    crash or a hang.

WHAT THIS IS NOT
-----------------
This is not a general-purpose document RAG system (no chunking
strategy, no file ingestion pipeline) — it is specifically long-term
CONVERSATIONAL memory. Feeding it documents/files is a reasonable
future extension (the storage/retrieval core here would work
unchanged) but is out of scope for this revision.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import numpy as np

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class MemoryHit:
    text: str
    score: float
    source: str
    timestamp: str


def _cosine_sim_batch(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of one query vector against every row of
    `matrix` at once (vectorized — this is the only per-search hot loop
    and it's a simple numpy matmul, fast even at a few thousand rows)."""
    if matrix.size == 0:
        return np.array([])
    matrix_norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query_vec)
    denom = matrix_norms * query_norm
    # Avoid division by zero for any degenerate zero-vector rows.
    denom = np.where(denom == 0, 1e-9, denom)
    return (matrix @ query_vec) / denom


class LongTermMemory:
    """Thread-safe long-term semantic memory store. See module docstring
    for the full architecture explanation."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.enabled = bool(getattr(Config, "RAG_ENABLED", True))
        self.db_path = db_path or Config.DB_PATH
        self._embed_model = getattr(Config, "EMBEDDING_MODEL", "nomic-embed-text")
        self._embed_timeout_s = float(getattr(Config, "EMBEDDING_TIMEOUT_S", 4.0))
        self._top_k_default = int(getattr(Config, "RAG_TOP_K", 4))
        self._min_similarity = float(getattr(Config, "RAG_MIN_SIMILARITY", 0.55))
        self._max_in_memory = int(getattr(Config, "RAG_MAX_IN_MEMORY", 5000))
        self._ollama_host = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")

        self._closed = False
        self._matrix_lock = threading.Lock()
        self._ids: List[int] = []
        self._texts: List[str] = []
        self._sources: List[str] = []
        self._timestamps: List[str] = []
        self._matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)

        self._conn: Optional[sqlite3.Connection] = None
        self._write_queue: "queue.Queue" = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None

        if not self.enabled:
            print("[RAG] Disabled via Config.RAG_ENABLED — long-term memory inactive.")
            return

        try:
            self._conn = self._open_connection()
            self._ensure_table()
            self._load_into_memory()
        except Exception as e:
            logger.error(f"[RAG] Failed to initialize: {e}")
            print(f"[RAG] Failed to initialize — long-term memory disabled: {e}")
            self.enabled = False
            self._conn = None
            return

        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="RAG-Writer", daemon=True
        )
        self._writer_thread.start()
        print(
            f"[RAG] Ready — {len(self._ids)} memories loaded | "
            f"model={self._embed_model} | top_k={self._top_k_default}"
        )

    # ── Setup ────────────────────────────────────────────────────────────

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _ensure_table(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                text      TEXT    NOT NULL,
                embedding BLOB    NOT NULL,
                source    TEXT    NOT NULL DEFAULT 'conversation',
                timestamp TEXT    NOT NULL
            )
            """)
        self._conn.commit()

    def _load_into_memory(self) -> None:
        cursor = self._conn.execute(
            """
            SELECT id, text, embedding, source, timestamp
            FROM long_term_memory
            ORDER BY id DESC
            LIMIT ?
            """,
            (self._max_in_memory,),
        )
        rows = cursor.fetchall()
        rows.reverse()  # chronological order, oldest first

        ids, texts, sources, timestamps, vecs = [], [], [], [], []
        for row_id, text, embedding_blob, source, timestamp in rows:
            try:
                vec = np.frombuffer(embedding_blob, dtype=np.float32)
            except Exception:
                continue  # skip a corrupted row rather than failing the whole load
            ids.append(row_id)
            texts.append(text)
            sources.append(source)
            timestamps.append(timestamp)
            vecs.append(vec)

        with self._matrix_lock:
            self._ids = ids
            self._texts = texts
            self._sources = sources
            self._timestamps = timestamps
            self._matrix = (
                np.vstack(vecs) if vecs else np.zeros((0, 0), dtype=np.float32)
            )

    # ── Embeddings (Ollama HTTP, no client-version coupling) ──────────────

    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        if not text or not text.strip():
            return None
        try:
            payload = json.dumps({"model": self._embed_model, "prompt": text}).encode(
                "utf-8"
            )
            req = urllib.request.Request(
                f"{self._ollama_host}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._embed_timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            embedding = body.get("embedding")
            if not embedding:
                return None
            return np.asarray(embedding, dtype=np.float32)
        except Exception as e:
            logger.debug(f"[RAG] embedding request failed: {e}")
            return None

    # ── Writer thread ────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        while not self._closed:
            try:
                job = self._write_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if job is None:
                break
            text, source, timestamp = job
            try:
                self._write_one(text, source, timestamp)
            except Exception as e:
                logger.error(f"[RAG] background write failed: {e}")

    def _write_one(self, text: str, source: str, timestamp: str) -> None:
        vec = self._get_embedding(text)
        if vec is None:
            # Embedding backend unavailable for this item — skip it rather
            # than storing a memory with no vector (would be unsearchable
            # and would corrupt the in-memory matrix's row width anyway).
            return

        try:
            cursor = self._conn.execute(
                "INSERT INTO long_term_memory (text, embedding, source, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (text, vec.tobytes(), source, timestamp),
            )
            self._conn.commit()
            new_id = cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"[RAG] DB insert failed: {e}")
            return

        with self._matrix_lock:
            self._ids.append(new_id)
            self._texts.append(text)
            self._sources.append(source)
            self._timestamps.append(timestamp)
            if self._matrix.size == 0:
                self._matrix = vec.reshape(1, -1)
            else:
                self._matrix = np.vstack([self._matrix, vec])
            # Trim oldest rows if the in-memory index has grown past the
            # configured cap — bounds RAM on a long-running install.
            # (DB rows themselves are left untouched; only the in-RAM
            # index is trimmed, so nothing is ever permanently lost.)
            overflow = len(self._ids) - self._max_in_memory
            if overflow > 0:
                self._ids = self._ids[overflow:]
                self._texts = self._texts[overflow:]
                self._sources = self._sources[overflow:]
                self._timestamps = self._timestamps[overflow:]
                self._matrix = self._matrix[overflow:]

    # ── Public API ───────────────────────────────────────────────────────

    def add_memory(self, text: str, source: str = "conversation") -> None:
        """Fire-and-forget: enqueues `text` for background embedding +
        storage. Safe to call from the hot conversation-loop path — never
        blocks, never raises."""
        if not self.enabled or not text or not text.strip():
            return
        timestamp = datetime.now().isoformat()
        try:
            self._write_queue.put_nowait((text.strip(), source, timestamp))
        except Exception as e:
            logger.debug(f"[RAG] add_memory enqueue failed: {e}")

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> List[MemoryHit]:
        """
        Returns up to `top_k` memories most semantically similar to
        `query`, above `min_similarity` (cosine, 0-1). Returns an empty
        list (never raises) if RAG is disabled, the query is empty, the
        embedding backend is unavailable/times out, or nothing clears
        the similarity threshold.
        """
        if not self.enabled or not query or not query.strip():
            return []

        top_k = top_k if top_k is not None else self._top_k_default
        min_similarity = (
            min_similarity if min_similarity is not None else self._min_similarity
        )

        query_vec = self._get_embedding(query)
        if query_vec is None:
            return []

        with self._matrix_lock:
            if self._matrix.size == 0 or len(self._texts) == 0:
                return []
            # Snapshot references under the lock; numpy arrays/lists are
            # not mutated in place elsewhere (only reassigned), so reading
            # them just after releasing the lock is safe.
            matrix = self._matrix
            texts = self._texts
            sources = self._sources
            timestamps = self._timestamps

        if matrix.shape[1] != query_vec.shape[0]:
            # Embedding model changed since these memories were stored
            # (different dimensionality) — can't compare them meaningfully.
            logger.warning(
                f"[RAG] Embedding dimension mismatch (stored={matrix.shape[1]}, "
                f"query={query_vec.shape[0]}) — did Config.EMBEDDING_MODEL change? "
                f"Returning no results for this search."
            )
            return []

        scores = _cosine_sim_batch(query_vec, matrix)
        if scores.size == 0:
            return []

        top_indices = np.argsort(scores)[::-1][
            : max(1, top_k) * 2
        ]  # small buffer before filtering
        hits: List[MemoryHit] = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_similarity:
                continue
            hits.append(
                MemoryHit(
                    text=texts[idx],
                    score=score,
                    source=sources[idx],
                    timestamp=timestamps[idx],
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def memory_count(self) -> int:
        with self._matrix_lock:
            return len(self._ids)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._writer_thread is not None:
            try:
                self._write_queue.put_nowait(None)
            except Exception:
                pass
            self._writer_thread.join(timeout=3.0)
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
