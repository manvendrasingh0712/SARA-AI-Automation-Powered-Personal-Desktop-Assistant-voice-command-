"""
sara.core.response_cache
Cache for final answers to stable, non-personal knowledge questions (part 3, P7).

A hit is answered in a few milliseconds and still spoken through the TTS. The rules are deliberately conservative: a wrong
cache hit is worse than a slow answer, so anything that could depend on time, the user, memory, a tool or the web is NEVER
cached (`is_cacheable_question`), and a reply is only stored when the whole turn was a plain model answer (`put` callers
check `clean_turn`).

Key = normalized question + language + persona version + model id (so a model / persona change starts a new cache).
Optional near-duplicate match: pass `embed` (text -> list[float]) and a hit needs cosine >= `min_similarity`.
TTL and LRU size are configurable; SQLite file next to the main database.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger("sara.cache")

# Words that make a question depend on "now", on the user, on memory, or on what SARA just did.
_NEVER = re.compile(
    r"\b(?:time|date|day|today|tonight|tomorrow|yesterday|now|abhi|aaj|kal|parso|latest|current(?:ly)?|news|weather|mausam|"
    r"price|rate|score|stock|temperature|my|mine|mera|meri|mere|mujhe|mujhko|main|meko|hamara|hum|i|i'm|im|"
    r"you|your|tum|tumhara|aap|aapka|sara|remember|yaad|reminder|remind|note|alarm|timer|"
    r"it|this|that|these|those|uska|uski|uske|isko|usko|yeh|ye|woh|wo|"
    r"again|also|previous|last|pehle|phir|dobara|continue|more)\b",
    re.IGNORECASE,
)
_KNOWLEDGE = re.compile(
    r"^\W*(?:what\s+(?:is|are|does)|what's|whats|who\s+(?:is|was|invented|discovered)|why\s+(?:is|are|do|does)|how\s+(?:does|do)|"
    r"explain|define|difference\s+between|meaning\s+of|full\s+form\s+of|kya\s+hota\s+hai|kya\s+hai|kaise\s+kaam|kyun|kyon|"
    r".+\s+(?:kya\s+hota\s+hai|kya\s+hai|kya\s+hote\s+hain|me\s+kya\s+fark\s+hai|mein\s+kya\s+fark\s+hai|ka\s+matlab))",
    re.IGNORECASE,
)
_BAD_REPLY = ("sorry", "try that again", "couldn't", "could not", "i don't know", "i do not know", "interrupted", "error")


def normalize(text: str) -> str:
    t = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def is_cacheable_question(text: str) -> bool:
    t = normalize(text)
    if not (8 <= len(t) <= 160) or re.search(r"\d", t):
        return False
    if _NEVER.search(t):
        return False
    return bool(_KNOWLEDGE.search(text or ""))


def is_cacheable_reply(reply: str) -> bool:
    r = (reply or "").strip()
    return 20 <= len(r) <= 900 and not any(m in r.lower() for m in _BAD_REPLY)


def _cos(a: List[float], b: List[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


_shared: Optional["ResponseCache"] = None
_shared_lock = threading.Lock()


def shared_cache(db_path: str, ttl_days: float = 7.0, max_entries: int = 500) -> "ResponseCache":
    """The process-wide cache, kept in `response_cache.db` next to the main database."""
    global _shared
    with _shared_lock:
        if _shared is None:
            import os

            _shared = ResponseCache(os.path.join(os.path.dirname(db_path) or ".", "response_cache.db"), ttl_days, max_entries)
        return _shared


def scope_of(brain) -> tuple:
    """(language, persona version, model id) of the brain: a persona / model / language change starts a fresh cache."""
    persona = hashlib.sha1((getattr(brain, "system_instruction", "") or "").encode("utf-8")).hexdigest()[:10]
    return str(getattr(brain, "_lang", "")), persona, str(getattr(brain, "model_name", ""))


class ResponseCache:
    def __init__(self, path: str = ":memory:", ttl_days: float = 7.0, max_entries: int = 500,
                 embed: Optional[Callable[[str], List[float]]] = None, min_similarity: float = 0.92,
                 clock: Callable[[], float] = time.time):
        self.ttl_s, self.max_entries, self.embed, self.min_similarity = ttl_days * 86400.0, max_entries, embed, min_similarity
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        try:
            from sara.core.db_common import apply_pragmas

            apply_pragmas(self._conn)
        except Exception:  # noqa: BLE001
            pass
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS response_cache (k TEXT PRIMARY KEY, scope TEXT NOT NULL, q TEXT NOT NULL, "
            "reply TEXT NOT NULL, vec TEXT, created REAL NOT NULL, last_used REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_rc_scope ON response_cache(scope)")
        self._conn.commit()
        self.lookups = self.hit_count = 0

    @staticmethod
    def _scope(lang: str, persona: str, model: str) -> str:
        return f"{lang}|{persona}|{model}"

    def get(self, question: str, lang: str = "", persona: str = "", model: str = "") -> Optional[str]:
        if not is_cacheable_question(question):
            return None
        scope, q, now = self._scope(lang, persona, model), normalize(question), self._clock()
        key = hashlib.sha256(f"{scope}\n{q}".encode("utf-8")).hexdigest()
        with self._lock:
            self.lookups += 1
            row = self._conn.execute("SELECT reply, created FROM response_cache WHERE k=?", (key,)).fetchone()
            if row is None and self.embed is not None:
                row = self._near(scope, question, now)
                key = row[2] if row else key
            if row is None or now - row[1] > self.ttl_s:
                return None
            self._conn.execute("UPDATE response_cache SET hits=hits+1, last_used=? WHERE k=?", (now, key))
            self._conn.commit()
            self.hit_count += 1
            return row[0]

    def _near(self, scope: str, question: str, now: float):
        try:
            qv = self.embed(question)
            best = None
            for k, reply, created, vec in self._conn.execute(
                "SELECT k, reply, created, vec FROM response_cache WHERE scope=? AND vec IS NOT NULL", (scope,)
            ):
                if now - created > self.ttl_s:
                    continue
                s = _cos(qv, json.loads(vec))
                if s >= self.min_similarity and (best is None or s > best[0]):
                    best = (s, reply, created, k)
            return (best[1], best[2], best[3]) if best else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("near-duplicate lookup skipped: %s", exc)
            return None

    def put(self, question: str, reply: str, lang: str = "", persona: str = "", model: str = "") -> bool:
        if not (is_cacheable_question(question) and is_cacheable_reply(reply)):
            return False
        scope, q, now = self._scope(lang, persona, model), normalize(question), self._clock()
        key = hashlib.sha256(f"{scope}\n{q}".encode("utf-8")).hexdigest()
        vec = None
        if self.embed is not None:
            try:
                vec = json.dumps(self.embed(question))
            except Exception:  # noqa: BLE001
                vec = None
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO response_cache (k, scope, q, reply, vec, created, last_used, hits) VALUES (?,?,?,?,?,?,?,0)",
                (key, scope, q, reply.strip(), vec, now, now),
            )
            over = self._conn.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0] - self.max_entries
            if over > 0:   # LRU: drop the least recently used
                self._conn.execute(
                    "DELETE FROM response_cache WHERE k IN (SELECT k FROM response_cache ORDER BY last_used ASC LIMIT ?)", (over,)
                )
            self._conn.commit()
        return True

    def clear(self) -> int:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0]
            self._conn.execute("DELETE FROM response_cache")
            self._conn.commit()
            return n

    def size(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0]

    def hit_rate(self) -> float:
        return self.hit_count / self.lookups if self.lookups else 0.0
