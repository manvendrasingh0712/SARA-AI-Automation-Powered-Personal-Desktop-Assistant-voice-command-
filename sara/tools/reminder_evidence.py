"""
sara/tools/reminder_evidence.py
Finds the few facts that make a reminder message smarter: a related note,
or something the user recently said or did about the same subject.

    builder = ReminderContextBuilder(notes_search=..., recent_messages=..., ...)
    ctx = builder.build(intent)     # ReminderContext; never raises, never blocks long

Rules:
  - Nothing is stated as fact unless it was actually retrieved.
  - Note matches are tiered by similarity: high -> may be stated directly,
    medium -> may only be hinted at softly, below the minimum -> dropped.
  - Every retrieved text is checked by the prompt-injection guard; flagged
    text is dropped, never rewritten.
  - Results are cached per reminder subject for a short time, and gathering
    has its own time limit, so a slow search can never delay a reminder.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_STOPWORDS = frozenset(
    "the a an to for of and or in on at my me i is it with this that you your "
    "do did does get got going go remind reminder please about".split()
)
_MAX_NOTE_CHARS = 200
_MAX_MESSAGE_CHARS = 120
_RECENT_WINDOW = timedelta(hours=12)


@dataclass(frozen=True)
class ReminderContext:
    notes: Tuple[str, ...] = ()        # high confidence: may be stated directly
    soft_notes: Tuple[str, ...] = ()   # medium confidence: mention only softly
    activity: Tuple[str, ...] = ()     # recent related conversation / actions

    @property
    def is_empty(self) -> bool:
        return not (self.notes or self.soft_notes or self.activity)


EMPTY_CONTEXT = ReminderContext()


def keywords(text: Optional[str]) -> frozenset:
    """Lowercase words of 3+ letters, minus filler words."""
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return frozenset(w for w in words if w not in _STOPWORDS)


def _excerpt(text: Optional[str], limit: int) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return (cut or flat[:limit]).rstrip(" ,;:-") + "..."


def _default_is_safe(text: str) -> bool:
    """True if the text does not look like a prompt injection (fail closed)."""
    try:
        from config import Config
        from sara.core.security.detector import scan, security_mode

        if security_mode(Config) == "off":
            return True
        return not scan(text, cfg=Config).flagged
    except Exception:  # noqa: BLE001 - if the guard cannot run, use nothing
        return False


class ReminderContextBuilder:
    def __init__(
        self,
        *,
        notes_search: Optional[Callable[[str, int, float], Sequence[Any]]] = None,
        recent_messages: Optional[Callable[[int], Sequence[Dict[str, str]]]] = None,
        recent_actions: Optional[Callable[[int], Sequence[Dict[str, str]]]] = None,
        is_safe: Optional[Callable[[str], bool]] = None,
        enabled: Optional[Callable[[], bool]] = None,
        min_score: float = 0.45,
        high_score: float = 0.65,
        max_notes: int = 3,
        timeout_s: float = 1.5,
        cache_ttl_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._notes_search = notes_search
        self._recent_messages = recent_messages
        self._recent_actions = recent_actions
        self._is_safe = is_safe or _default_is_safe
        self._enabled = enabled
        self._min_score = min_score
        self._high_score = max(high_score, min_score)
        self._max_notes = max(1, max_notes)
        self.timeout_s = max(0.2, timeout_s)
        self._ttl = cache_ttl_s
        self._clock = clock
        self._now = now
        self._cache: Dict[str, Tuple[float, ReminderContext]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def build(self, intent: Any) -> ReminderContext:
        subject = (getattr(intent, "subject", "") or "").strip()
        if not subject:
            return EMPTY_CONTEXT
        try:
            if self._enabled is not None and not self._enabled():
                return EMPTY_CONTEXT
        except Exception:  # noqa: BLE001
            return EMPTY_CONTEXT

        key = subject.lower()
        with self._lock:
            cached = self._cache.get(key)
            if cached and (self._clock() - cached[0]) < self._ttl:
                return cached[1]

        box: Dict[str, ReminderContext] = {}

        def _work() -> None:
            try:
                box["ctx"] = self._gather(subject)
            except Exception:  # noqa: BLE001
                box["ctx"] = EMPTY_CONTEXT

        worker = threading.Thread(target=_work, daemon=True, name="sara-reminder-context")
        worker.start()
        worker.join(self.timeout_s)
        ctx = box.get("ctx")
        if ctx is None:
            return EMPTY_CONTEXT  # too slow: not cached, next call may succeed
        with self._lock:
            self._cache[key] = (self._clock(), ctx)
            if len(self._cache) > 64:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
        return ctx

    # ------------------------------------------------------------------

    def _gather(self, subject: str) -> ReminderContext:
        words = keywords(subject)
        notes, soft = self._notes(subject, words)
        activity = self._conversation(words) + self._actions(words)
        return ReminderContext(tuple(notes), tuple(soft), tuple(activity))

    def _notes(self, subject: str, words: frozenset) -> Tuple[List[str], List[str]]:
        if self._notes_search is None:
            return [], []
        try:
            hits = self._notes_search(subject, self._max_notes * 2, self._min_score)
        except Exception:  # noqa: BLE001
            return [], []
        scored: List[Tuple[float, str]] = []
        for hit in hits or []:
            if not str(getattr(hit, "source", "")).startswith("notes:"):
                continue
            score = float(getattr(hit, "score", 0.0) or 0.0)
            text = _excerpt(getattr(hit, "text", ""), _MAX_NOTE_CHARS)
            if score < self._min_score or not text:
                continue
            high = score >= self._high_score
            if not high and not (words & keywords(text)):
                continue  # a medium match must share a word with the reminder
            if not self._is_safe(text):
                continue
            scored.append((score, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        high_notes: List[str] = []
        soft_notes: List[str] = []
        seen = set()
        for score, text in scored:
            if text in seen or len(high_notes) + len(soft_notes) >= self._max_notes:
                continue
            seen.add(text)
            (high_notes if score >= self._high_score else soft_notes).append(text)
        return high_notes, soft_notes

    def _conversation(self, words: frozenset) -> List[str]:
        if self._recent_messages is None or not words:
            return []
        try:
            rows = list(self._recent_messages(6) or [])
        except Exception:  # noqa: BLE001
            return []
        cutoff = self._now() - _RECENT_WINDOW
        found: List[str] = []
        for row in reversed(rows):  # newest first
            if row.get("role") != "user":
                continue
            message = row.get("message", "")
            if not (words & keywords(message)):
                continue
            try:
                if datetime.fromisoformat(row.get("timestamp", "")) < cutoff:
                    continue
            except (TypeError, ValueError):
                pass
            text = _excerpt(message, _MAX_MESSAGE_CHARS)
            if text and self._is_safe(text):
                found.append(f'The user recently said: "{text}"')
            if len(found) >= 2:
                break
        return found

    def _actions(self, words: frozenset) -> List[str]:
        if self._recent_actions is None or not words:
            return []
        try:
            rows = list(self._recent_actions(3) or [])
        except Exception:  # noqa: BLE001
            return []
        for row in reversed(rows):
            blob = f"{row.get('action_name', '')} {row.get('reason', '') or ''}"
            if not (words & keywords(blob)):
                continue
            name = _excerpt(str(row.get("action_name", "")), 60)
            if name and self._is_safe(name):
                return [f"Recent action: {name} ({row.get('outcome', 'done')})"]
        return []