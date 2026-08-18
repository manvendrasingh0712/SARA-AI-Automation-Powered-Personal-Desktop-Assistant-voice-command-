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
  - DELETES (delete_memory / clear_all) also go through the SAME
    background writer thread/queue as add_memory(), tagged with a
    leading string sentinel ("__DELETE__" / "__CLEAR__") so the writer
    loop can tell an add-job from a delete-job apart without changing
    the original (text, source, timestamp) job shape at all. Unlike
    add_memory(), these support wait=True (default) via a
    concurrent.futures.Future so a caller (the "forget that I like X"
    / "forget everything" voice intents) can know for certain the
    delete actually happened before telling the user it did.

WHAT'S NEW IN THIS REVISION (Bug 2 fix -- "Sara doesn't remember facts")
-------------------------------------------------------------------------
1. VISIBLE DIAGNOSTICS: every point where search()/add_memory() used to
   fail silently (embedding call down, dimension mismatch, no rows
   cleared the similarity threshold) now also prints a `[RAG] ...` line
   whenever Config.DEBUG_MODE is True, in addition to the existing
   logger calls -- so "why isn't Sara remembering this" is diagnosable
   from the console instead of requiring log-level surgery.
2. run_diagnostics(): a real, on-demand round-trip test -- confirms the
   embedding model is actually reachable, then writes a throwaway probe
   memory and confirms search() can find it back, cleaning up after
   itself. Returns a structured result consumable by both console
   output and the self_diagnostics voice skill.
3. FACT EXTRACTION LAYER: maybe_extract_fact() -- a lightweight,
   regex-based (NOT LLM-based, so it's instant and has zero extra
   Ollama round-trips) detector for durable personal-fact statements
   ("my girlfriend's name is Parul", "meri girlfriend ka naam Parul
   hai", "I study at DPS Ajmer"). Matches are stored as their own
   memory entry tagged source="fact", SEPARATE from the raw
   "User said/Sara replied" exchange text that's already being
   embedded -- so recall of a stated fact doesn't depend on lucky
   semantic overlap with an entire Q&A pair. Fact-tagged rows use their
   own, more permissive Config.RAG_FACT_MIN_SIMILARITY threshold in
   search() (see the docstring on search() below for why).

WHAT THIS IS NOT
-----------------
This is not a general-purpose document RAG system (no chunking
strategy, no file ingestion pipeline) — it is specifically long-term
CONVERSATIONAL memory. Feeding it documents/files is a reasonable
future extension (the storage/retrieval core here would work
unchanged) but is out of scope for this revision. The fact extractor
above is also intentionally simple pattern-matching, not an LLM-based
extractor -- it will miss facts phrased in ways the patterns don't
cover. It's a floor, not a replacement for whatever your existing
memory-consolidation pass (Config.MEMORY_CONSOLIDATION_*) does.
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
# Each pattern maps to a normalized output sentence. Name-style facts
# ("my girlfriend's name is Parul") are normalized as
# "<Value> is the user's <label>." specifically because that phrasing
# has strong lexical/semantic overlap with how the RECALL question will
# actually be asked later ("who is Parul") -- maximizing the odds it
# clears even the (already lowered) fact similarity threshold.

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
    """
    Returns a normalized fact sentence if `text` looks like the user
    stating a durable personal fact, else None. Deliberately simple and
    pattern-based (not LLM-based) -- see module docstring. Checked in
    order from most to least specific so "my girlfriend's name is
    Parul" hits the name pattern before the generic "my X is Y" one.
    """
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
        """
        Fire-and-forget: if `user_text` looks like the user stating a
        durable personal fact ("my girlfriend's name is Parul", "meri
        girlfriend ka naam Parul hai", "I study at DPS Ajmer"), stores a
        distinct, normalized memory entry tagged source="fact" --
        separate from the regular full-exchange embedding -- so recall
        doesn't depend on lucky semantic overlap with an entire Q&A
        exchange. Purely regex-based (see module docstring for why), so
        this is effectively free to call on every user turn. Safe no-op
        if nothing matches or RAG is disabled.
        """
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
        """
        Returns up to `top_k` memories most semantically similar to
        `query`, above a similarity threshold (cosine, 0-1). Returns an
        empty list (never raises) if RAG is disabled, the query is
        empty, the embedding backend is unavailable/times out, or
        nothing clears the threshold.

        PER-SOURCE THRESHOLD (Bug 2 fix, item 3): rows with
        source="fact" (see maybe_extract_fact() above) are compared
        against Config.RAG_FACT_MIN_SIMILARITY instead of the general
        `min_similarity` -- a short, explicitly-stated fact like "Parul
        is the user's girlfriend" against a query like "who is Parul"
        genuinely tends to score lower on cosine similarity than a
        topically-similar full conversational exchange would, simply
        because there's so little text on either side to overlap. A
        single lower threshold for ALL memories would either miss these
        facts (threshold too high) or flood every search with loosely-
        related conversational noise (threshold too low) -- splitting
        it by source avoids that trade-off. `min_similarity` passed in
        explicitly by a caller still overrides both defaults uniformly.
        """
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