"""
sara/core/rag.py
Long-term semantic memory (RAG) for Sara AI.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import Future
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


# ══════════════════════════════════════════════════════════════════════
# Fact extraction (Bug 2 fix, item 3) -- pure regex, no model call.
# ══════════════════════════════════════════════════════════════════════
#
_FACT_NAME_EN_RE = re.compile(
    r"\bmy\s+([a-zA-Z][a-zA-Z '\-]{1,40}?)'?s?\s+name\s+is\s+"
    r"([A-Za-z][A-Za-z '\-]{1,60})",
    re.IGNORECASE,
)
_FACT_NAME_HI_RE = re.compile(
    r"\b(?:meri|mera|mere)\s+([a-zA-Z]+)\s+ka\s+naam\s+"
    r"([A-Za-z][A-Za-z '\-]{1,60}?)\s+hai",
    re.IGNORECASE,
)
_FACT_STUDY_WORK_RE = re.compile(
    r"\bi\s+(study|work)\s+at\s+([^.?!]{1,80})", re.IGNORECASE
)
_FACT_LIVE_RE = re.compile(r"\bi\s+live\s+in\s+([^.?!]{1,60})", re.IGNORECASE)
_FACT_GENERIC_RE = re.compile(
    r"\bmy\s+([a-zA-Z][a-zA-Z '\-]{1,40}?)\s+is\s+([^.?!]{1,80})", re.IGNORECASE
)

# Generic "my X is Y" matches on filler/idiom that aren't real facts --
# skip these rather than storing junk memories.
_FACT_GENERIC_STOPWORDS = frozenset({"bad", "pleasure", "fault", "point", "opinion"})


def _extract_fact_sentence(text: str) -> Optional[str]:
   
    text = (text or "").strip()
    if not text:
        return None

    m = _FACT_NAME_EN_RE.search(text)
    if m:
        label, value = m.group(1).strip(), m.group(2).strip().rstrip(".,!?")
        if label and value:
            return f"{value} is the user's {label}."

    m = _FACT_NAME_HI_RE.search(text)
    if m:
        label, value = m.group(1).strip(), m.group(2).strip().rstrip(".,!?")
        if label and value:
            return f"{value} is the user's {label}."

    m = _FACT_STUDY_WORK_RE.search(text)
    if m:
        verb, value = m.group(1).strip().lower(), m.group(2).strip().rstrip(".,!?")
        verb_phrase = "studies at" if verb == "study" else "works at"
        if value:
            return f"The user {verb_phrase} {value}."

    m = _FACT_LIVE_RE.search(text)
    if m:
        value = m.group(1).strip().rstrip(".,!?")
        if value:
            return f"The user lives in {value}."

    m = _FACT_GENERIC_RE.search(text)
    if m:
        label, value = m.group(1).strip(), m.group(2).strip().rstrip(".,!?")
        if label and value and len(value) > 1 and label.lower() not in _FACT_GENERIC_STOPWORDS:
            return f"The user's {label} is {value}."

    return None


class LongTermMemory:
    """Thread-safe long-term semantic memory store. See module docstring
    for the full architecture explanation."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.enabled = bool(getattr(Config, "RAG_ENABLED", True))
        self.db_path = db_path or Config.DB_PATH
        self._embed_model = getattr(Config, "EMBEDDING_MODEL", "nomic-embed-text")
        self._embed_timeout_s = float(getattr(Config, "EMBEDDING_TIMEOUT_S", 4.0))
        self._top_k_default = int(getattr(Config, "RAG_TOP_K", 4))
        self._min_similarity = float(getattr(Config, "RAG_MIN_SIMILARITY", 0.40))
        # NEW: durable facts get their own, more permissive threshold --
        # see search()'s docstring for why.
        self._fact_min_similarity = float(
            getattr(Config, "RAG_FACT_MIN_SIMILARITY", 0.30)
        )
        self._max_in_memory = int(getattr(Config, "RAG_MAX_IN_MEMORY", 5000))
        self._ollama_host = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")
        self._debug = bool(getattr(Config, "DEBUG_MODE", False))

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
            f"model={self._embed_model} | top_k={self._top_k_default} | "
            f"min_sim={self._min_similarity} | fact_min_sim={self._fact_min_similarity}"
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

        # Guard against mixed embedding dimensions -- some rows may have
        # been saved under an older EMBEDDING_MODEL before it was changed.
        # np.vstack() below requires every row to be the same width, so any
        # row that doesn't match the CURRENT model's dimension has to be
        # dropped from the in-memory index before the matrix is built. The
        # DB row itself is left untouched (still there for a future
        # re-embedding pass) -- only the in-RAM index skips it.
        if vecs:
            try:
                # Most recently inserted row (list is chronological, oldest
                # first) reflects whatever EMBEDDING_MODEL is actually
                # configured right now.
                expected_dim = vecs[-1].shape[0]
                filtered_ids, filtered_texts, filtered_sources = [], [], []
                filtered_timestamps, filtered_vecs = [], []
                skipped = 0
                for i, vec in enumerate(vecs):
                    if vec.shape[0] == expected_dim:
                        filtered_ids.append(ids[i])
                        filtered_texts.append(texts[i])
                        filtered_sources.append(sources[i])
                        filtered_timestamps.append(timestamps[i])
                        filtered_vecs.append(vec)
                    else:
                        skipped += 1
                if skipped:
                    msg = (
                        f"[RAG] Skipped {skipped} stored memories with a "
                        f"mismatched embedding dimension while loading "
                        f"(expected {expected_dim}, based on the most "
                        f"recently stored row) -- likely saved under a "
                        f"previous EMBEDDING_MODEL. They remain in the "
                        f"database untouched but won't be searchable until "
                        f"re-embedded."
                    )
                    logger.warning(msg)
                    print(msg)
                ids, texts, sources, timestamps, vecs = (
                    filtered_ids,
                    filtered_texts,
                    filtered_sources,
                    filtered_timestamps,
                    filtered_vecs,
                )
            except Exception as e:
                # Filtering itself must never take down startup -- fall
                # back to the raw (possibly mixed-dimension) lists; the
                # np.vstack() below will surface the same error it always
                # did if that happens, which is no worse than before this
                # fix existed.
                logger.error(f"[RAG] dimension-filter step failed: {e}")
                if self._debug:
                    print(f"[RAG] dimension-filter step failed: {e}")

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
                if self._debug:
                    print(
                        f"[RAG] Embedding call to '{self._embed_model}' returned no "
                        f"vector for text: {text[:80]!r} -- is the model actually pulled?"
                    )
                return None
            return np.asarray(embedding, dtype=np.float32)
        except Exception as e:
            logger.debug(f"[RAG] embedding request failed: {e}")
            if self._debug:
                print(
                    f"[RAG] Embedding request FAILED ({type(e).__name__}: {e}) -- "
                    f"is Ollama running at {self._ollama_host} and is "
                    f"'{self._embed_model}' pulled? (ollama pull {self._embed_model})"
                )
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
            if isinstance(job, tuple) and job and job[0] == "__DELETE__":
                _, memory_id, future = job
                try:
                    self._delete_one(memory_id, future)
                except Exception as e:
                    logger.error(f"[RAG] background delete failed: {e}")
                continue
            if isinstance(job, tuple) and job and job[0] == "__CLEAR__":
                _, future = job
                try:
                    self._clear_all_one(future)
                except Exception as e:
                    logger.error(f"[RAG] background clear failed: {e}")
                continue
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
            if self._debug:
                print(
                    f"[RAG] Skipped storing memory -- embedding unavailable "
                    f"for text: {text[:80]!r} (source={source})"
                )
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
            if self._debug:
                print(f"[RAG] DB insert FAILED for text {text[:80]!r}: {e}")
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

        if self._debug:
            print(f"[RAG] Stored memory id={new_id} source={source} text={text[:80]!r}")

    def _delete_one(self, memory_id: int, future: Optional[Future]) -> None:
        """
        Executes a single delete_memory() job on the writer thread:
        removes the DB row, then (only if a row was actually deleted)
        removes the matching entry from the in-memory index under
        _matrix_lock. Never raises out of this method — any error is
        surfaced via `future.set_exception()` instead, matching
        sara/core/memory.py's PreferencesDB._submit_write() convention.
        """
        try:
            cursor = self._conn.execute(
                "DELETE FROM long_term_memory WHERE id = ?", (memory_id,)
            )
            self._conn.commit()
            deleted = cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"[RAG] delete_memory DB delete failed: {e}")
            if future is not None:
                future.set_exception(e)
            return

        if deleted:
            with self._matrix_lock:
                if memory_id in self._ids:
                    idx = self._ids.index(memory_id)
                    del self._ids[idx]
                    del self._texts[idx]
                    del self._sources[idx]
                    del self._timestamps[idx]
                    if self._matrix.shape[0] > idx:
                        self._matrix = np.delete(self._matrix, idx, axis=0)

        if future is not None:
            future.set_result(deleted)

    def _clear_all_one(self, future: Optional[Future]) -> None:
        """
        Executes a single clear_all() job on the writer thread: wipes
        every row from the long_term_memory table and resets the entire
        in-memory index. Never raises out of this method — see
        _delete_one()'s docstring for the same error-via-future
        convention.
        """
        try:
            self._conn.execute("DELETE FROM long_term_memory")
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error(f"[RAG] clear_all DB delete failed: {e}")
            if future is not None:
                future.set_exception(e)
            return

        with self._matrix_lock:
            self._ids = []
            self._texts = []
            self._sources = []
            self._timestamps = []
            self._matrix = np.zeros((0, 0), dtype=np.float32)

        if future is not None:
            future.set_result(True)

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
            if self._debug:
                print(f"[RAG] add_memory enqueue FAILED: {e}")

    def maybe_extract_fact(self, user_text: str) -> None:
        
        if not self.enabled:
            return
        fact_text = _extract_fact_sentence(user_text)
        if fact_text:
            if self._debug:
                print(f"[RAG] Extracted fact from user message: {fact_text!r}")
            self.add_memory(fact_text, source="fact")

    def list_memories(self) -> List[dict]:
        """
        Returns a snapshot of every currently-loaded long-term memory as
        [{"id", "text", "source", "timestamp"}, ...], oldest first. Used
        by the "forget that I like X" voice intent (see
        sara/orchestrator/intent_handlers.py's
        _h_memory_forget_specific) to fuzzy-match a spoken phrase
        against real stored memories before deleting anything. Returns
        [] if RAG is disabled.
        """
        if not self.enabled:
            return []
        with self._matrix_lock:
            return [
                {
                    "id": self._ids[i],
                    "text": self._texts[i],
                    "source": self._sources[i],
                    "timestamp": self._timestamps[i],
                }
                for i in range(len(self._ids))
            ]

    def delete_memory(
        self, memory_id: int, wait: bool = True, timeout: float = 5.0
    ) -> bool:
        """
        Deletes a single memory by id (both the DB row and its
        in-memory index entry). Runs on the same background writer
        thread as add_memory(), for consistency with this module's
        single-writer discipline. wait=True (default) blocks until the
        delete has actually completed and returns whether a row was
        removed; wait=False is fire-and-forget (returns True as soon as
        the job is queued, matching PreferencesDB._submit_write()'s
        wait=False contract in sara/core/memory.py).
        """
        if not self.enabled or self._closed:
            return False
        future: Optional[Future] = Future() if wait else None
        try:
            self._write_queue.put_nowait(("__DELETE__", memory_id, future))
        except Exception as e:
            logger.debug(f"[RAG] delete_memory enqueue failed: {e}")
            return False
        if wait and future is not None:
            try:
                return bool(future.result(timeout=timeout))
            except Exception as e:
                logger.error(f"[RAG] delete_memory timed out/failed: {e}")
                return False
        return True

    def clear_all(self, wait: bool = True, timeout: float = 5.0) -> bool:
        """
        Deletes EVERY stored long-term memory (DB rows + in-memory
        index). Backs the "forget everything you know about me" voice
        intent — which requires an explicit spoken confirmation BEFORE
        this is ever called (see sara/orchestrator/intent_handlers.py's
        confirm_state flow); this method itself performs no
        confirmation, it just executes the wipe once called. wait=True
        (default) blocks until complete.
        """
        if not self.enabled or self._closed:
            return False
        future: Optional[Future] = Future() if wait else None
        try:
            self._write_queue.put_nowait(("__CLEAR__", future))
        except Exception as e:
            logger.debug(f"[RAG] clear_all enqueue failed: {e}")
            return False
        if wait and future is not None:
            try:
                return bool(future.result(timeout=timeout))
            except Exception as e:
                logger.error(f"[RAG] clear_all timed out/failed: {e}")
                return False
        return True

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> List[MemoryHit]:
    
        if not self.enabled or not query or not query.strip():
            return []

        top_k = top_k if top_k is not None else self._top_k_default
        explicit_min_similarity = min_similarity
        general_min_similarity = (
            explicit_min_similarity
            if explicit_min_similarity is not None
            else self._min_similarity
        )
        fact_min_similarity = (
            explicit_min_similarity
            if explicit_min_similarity is not None
            else self._fact_min_similarity
        )

        query_vec = self._get_embedding(query)
        if query_vec is None:
            if self._debug:
                print(
                    f"[RAG] search() aborted -- embedding unavailable for "
                    f"query: {query[:80]!r}"
                )
            return []

        with self._matrix_lock:
            if self._matrix.size == 0 or len(self._texts) == 0:
                if self._debug:
                    print("[RAG] search() found no stored memories yet.")
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
            msg = (
                f"[RAG] Embedding dimension mismatch (stored={matrix.shape[1]}, "
                f"query={query_vec.shape[0]}) — did Config.EMBEDDING_MODEL change? "
                f"Returning no results for this search."
            )
            logger.warning(msg)
            if self._debug:
                print(msg)
            return []

        scores = _cosine_sim_batch(query_vec, matrix)
        if scores.size == 0:
            return []

        # Buffer widened (was *2) to make room for lower-threshold "fact"
        # rows that might rank below the top general-conversation hits
        # but still need to be considered against their own threshold.
        top_indices = np.argsort(scores)[::-1][: max(1, top_k) * 3]
        hits: List[MemoryHit] = []
        for idx in top_indices:
            score = float(scores[idx])
            row_source = sources[idx]
            threshold = (
                fact_min_similarity if row_source == "fact" else general_min_similarity
            )
            if score < threshold:
                continue
            hits.append(
                MemoryHit(
                    text=texts[idx],
                    score=score,
                    source=row_source,
                    timestamp=timestamps[idx],
                )
            )
            if len(hits) >= top_k:
                break

        if not hits and self._debug:
            top_score = float(scores.max())
            print(
                f"[RAG] search() found {scores.size} candidates but none cleared "
                f"the similarity threshold for query {query[:80]!r} "
                f"(top score={top_score:.3f}, min_sim={general_min_similarity}, "
                f"fact_min_sim={fact_min_similarity})."
            )
        return hits

    def memory_count(self) -> int:
        with self._matrix_lock:
            return len(self._ids)

    def run_diagnostics(self, timeout_s: float = 8.0) -> dict:
        """
        Positively verifies, right now, whether long-term memory is
        actually working end-to-end (Bug 2 fix, item 1):
          1. Embedding model reachable -- a live test call to Ollama's
             /api/embeddings for self._embed_model.
          2. Round-trip write+search -- stores a throwaway probe memory,
             waits (briefly) for the background writer thread to
             actually embed+persist it, searches for it, confirms it
             comes back, then deletes the probe so it never pollutes
             real memory or counts.
        Returns a structured result dict -- {"name", "friendly_name",
        "ok", "detail", ...} -- shaped to match the result format
        health_check.py's checks already use, so this can be merged
        straight into that list (see sara/skills/self_diagnostics.py).
        Never raises.
        """
        result = {
            "name": "rag_memory",
            "friendly_name": "long-term memory",
            "ok": False,
            "detail": "",
            "embedding_model_ok": False,
            "round_trip_ok": False,
            "memory_count": self.memory_count(),
        }

        if not self.enabled:
            result["detail"] = "Long-term memory is disabled (Config.RAG_ENABLED=False)."
            return result

        try:
            probe_vec = self._get_embedding("diagnostic connectivity check")
        except Exception as e:  # noqa: BLE001 -- diagnostics must never raise
            probe_vec = None
            logger.error(f"[RAG] run_diagnostics embedding check crashed: {e}")

        if probe_vec is None:
            result["detail"] = (
                f"Can't reach the '{self._embed_model}' embedding model on Ollama "
                f"({self._ollama_host}). Long-term memory recall is effectively OFF "
                f"right now -- run `ollama pull {self._embed_model}` and make sure "
                f"Ollama is running."
            )
            return result
        result["embedding_model_ok"] = True

        probe_text = f"__sara_rag_diagnostic_probe__ {datetime.now().isoformat()}"
        before_count = self.memory_count()
        self.add_memory(probe_text, source="diagnostic")

        deadline = time.monotonic() + timeout_s
        written = False
        while time.monotonic() < deadline:
            if self.memory_count() > before_count:
                written = True
                break
            time.sleep(0.2)

        if not written:
            result["detail"] = (
                f"The embedding model responds, but a real memory write didn't "
                f"complete within {timeout_s:.0f}s. Something is stuck in the "
                f"background writer -- check the logs for '[RAG] DB insert FAILED' "
                f"or '[RAG] Skipped storing memory'."
            )
            return result

        try:
            hits = self.search(probe_text, top_k=3, min_similarity=0.0)
            found = any(h.text == probe_text for h in hits)
        except Exception as e:  # noqa: BLE001
            found = False
            logger.error(f"[RAG] run_diagnostics search step crashed: {e}")

        # Clean up the probe row either way so it never lingers as a real
        # memory or skews memory_count().
        probe_id: Optional[int] = None
        with self._matrix_lock:
            for i, t in enumerate(self._texts):
                if t == probe_text:
                    probe_id = self._ids[i]
                    break
        if probe_id is not None:
            self.delete_memory(probe_id, wait=False)

        if not found:
            result["detail"] = (
                "Embeddings write successfully, but search() isn't finding them "
                "back. Check RAG_MIN_SIMILARITY / RAG_TOP_K in your .env, or an "
                "embedding-dimension mismatch (look for a '[RAG] Embedding "
                "dimension mismatch' warning in the logs -- it means "
                "EMBEDDING_MODEL changed after existing memories were stored)."
            )
            return result

        result["round_trip_ok"] = True
        result["ok"] = True
        result["memory_count"] = self.memory_count()
        result["detail"] = (
            f"Long-term memory is working -- {result['memory_count']} memories "
            f"stored, embedding model '{self._embed_model}' responding, "
            f"round-trip write+search confirmed."
        )
        return result

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