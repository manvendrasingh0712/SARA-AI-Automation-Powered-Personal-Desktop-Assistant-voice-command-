"""
sara/audio/echo_guard.py
Self-Echo Guard — transcript-level fallback for TTS self-hearing.

sara/audio/aec.py already does real-time acoustic echo cancellation using the
ACTUAL TTS playback samples as the reference signal (see player.py's
feed_far_end()). This module is a second, much cheaper line of defense for
what AEC can still miss:
  - the tail of a TTS phrase that keeps reaching the mic (room reverb /
    speaker bleed) for a moment after playback has already stopped and the
    far-end reference has stopped feeding, and
  - the STT engine finishing a transcript slightly after TTS output has
    actually ended ("delayed final words").

It keeps a short, bounded (time-based) history of what SARA actually just
said (real text, real wall-clock start/end), and scores a candidate user
transcript against that history on THREE independent, stdlib-only text
signals (never exact-text-only):
  - difflib.SequenceMatcher character-level ratio (same convention already
    used in sara/core/memory.py),
  - word-set (Jaccard) overlap -- robust to STT word-order jitter,
  - trailing-word suffix match -- the "delayed final words" shape, where only
    the tail of what SARA said got transcribed.
The best of the three is compared against a similarity floor that is the
ONLY place "acoustic evidence" (AEC active/inactive -> tail window, and the
STT engine's own per-transcript confidence -> threshold) feeds in: low STT
confidence on a transcript that's ALREADY a strong text match lowers the bar
a little (an unsure transcript that also looks like an echo is more likely
to actually be one); a confident transcript is always held to the normal,
stricter floor, so genuine speech is never penalized by this signal.

Deliberately NOT done here (see the task's forbidden list):
  - no microphone muting/disabling,
  - no blanket "ignore all speech while TTS plays",
  - no arbitrary listening cooldown unrelated to actual TTS content,
  - no exact-text-only suppression,
  - no dependency beyond the stdlib (difflib).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Deque, List, Optional

from config import Config

# Phrases older than this are dropped outright regardless of the tail
# window below — bounds memory/compute no matter how long a session runs.
_HISTORY_MAX_AGE_S = 12.0
_HISTORY_MAX_ENTRIES = 12

# Below this normalized length, fuzzy scoring is too easy to hit by
# coincidence (two different short replies like "okay" / "okay thanks" can
# score high). Short candidates are only ever flagged on a near-exact match.
_MIN_CHARS_FOR_FUZZY = 12


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", str(text or "").lower()).strip()


def _jaccard(a_words: List[str], b_words: List[str]) -> float:
    a, b = set(a_words), set(b_words)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _suffix_ratio(cand_words: List[str], ref_words: List[str]) -> float:
    """Fraction of `cand_words` that match, consecutively from the end, the
    tail of `ref_words` -- "delayed final words": STT only caught the end of
    what SARA said, possibly with a little garbled lead-in before it."""
    if not cand_words or not ref_words:
        return 0.0
    n = min(len(cand_words), len(ref_words))
    matched = 0
    for i in range(1, n + 1):
        if cand_words[-i] == ref_words[-i]:
            matched = i
        else:
            break
    return matched / len(cand_words)


def _text_score(cand_norm: str, cand_words: List[str], ref_norm: str, ref_words: List[str]) -> float:
    """Best of three cheap, independent stdlib-only similarity signals."""
    char_ratio = SequenceMatcher(None, cand_norm, ref_norm).ratio()
    return max(char_ratio, _jaccard(cand_words, ref_words), _suffix_ratio(cand_words, ref_words))


@dataclass
class _Spoken:
    norm: str
    words: List[str]
    started_at: float
    ended_at: Optional[float]  # None = still playing / queued ("open" -- see record_start())


class SelfEchoGuard:
    """Thread-safe; one instance is shared process-wide (see
    get_default_guard() below, sara/audio/tts/engine.py,
    sara/orchestrator/tts_worker.py, and the command loop in
    sara/orchestrator/core_wiring.py for the call sites)."""

    def __init__(
        self,
        sim_threshold: Optional[float] = None,
        conf_adjust_max: Optional[float] = None,
        tail_with_aec_s: Optional[float] = None,
        tail_without_aec_s: Optional[float] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._history: Deque[_Spoken] = deque()
        self._sim_threshold = float(sim_threshold if sim_threshold is not None else getattr(Config, "ECHO_GUARD_SIM_THRESHOLD", 0.82))
        self._conf_adjust_max = float(conf_adjust_max if conf_adjust_max is not None else getattr(Config, "ECHO_GUARD_CONF_ADJUST_MAX", 0.10))
        self._tail_with_aec = float(tail_with_aec_s if tail_with_aec_s is not None else getattr(Config, "ECHO_GUARD_TAIL_WITH_AEC_S", 0.6))
        self._tail_without_aec = float(tail_without_aec_s if tail_without_aec_s is not None else getattr(Config, "ECHO_GUARD_TAIL_WITHOUT_AEC_S", 1.8))

    def record(self, text: str, started_at: float, ended_at: float) -> None:
        """Called once a phrase has actually finished playing (or been cut
        short by Stop/cancellation) — `text` is what was really spoken."""
        norm = _normalize(text)
        if not norm:
            return
        now = time.monotonic()
        with self._lock:
            self._history.append(_Spoken(norm=norm, words=norm.split(), started_at=started_at, ended_at=min(ended_at, now)))
            self._prune(now)

    def record_start(self, text: str, started_at: Optional[float] = None) -> None:
        """Opens an entry the MOMENT SARA commits to speaking `text` --
        before playback (or even synthesis) has actually begun. Needed
        because sara/orchestrator/tts_worker.py's speak(..., block=False)
        (wake-ack / filler / progress phrases) returns to its caller
        immediately, so the orchestrator can call ears.listen() again
        before record()/record_end() for that phrase has run; without an
        open entry already in history, the real echo would be invisible to
        is_echo() for that race window. An open entry (ended_at=None) is
        always treated as currently active by is_echo()."""
        norm = _normalize(text)
        if not norm:
            return
        now = time.monotonic()
        with self._lock:
            self._history.append(_Spoken(norm=norm, words=norm.split(), started_at=started_at if started_at is not None else now, ended_at=None))
            self._prune(now)

    def record_end(self, text: str, ended_at: Optional[float] = None) -> bool:
        """Closes the oldest still-open entry matching `text` (see
        record_start()). Returns True if it found and closed one, False if
        there wasn't one (e.g. this text never went through record_start() —
        the caller should fall back to record() so the phrase is still
        captured)."""
        norm = _normalize(text)
        if not norm:
            return False
        now = time.monotonic()
        end = min(ended_at, now) if ended_at is not None else now
        closed = False
        with self._lock:
            for entry in self._history:
                if entry.norm == norm and entry.ended_at is None:
                    entry.ended_at = end
                    closed = True
                    break
            self._prune(now)
        return closed

    def _prune(self, now: float) -> None:
        while self._history and (now - (self._history[0].ended_at if self._history[0].ended_at is not None else now)) > _HISTORY_MAX_AGE_S:
            self._history.popleft()
        while len(self._history) > _HISTORY_MAX_ENTRIES:
            self._history.popleft()

    def _threshold_for(self, confidence: Optional[float]) -> float:
        """The normal floor, unless `confidence` (the STT engine's own
        confidence in THIS candidate transcript, 0..1) is low -- then the
        floor is lowered by up to `conf_adjust_max`, never raised. A
        confident transcript is always judged at the normal, stricter floor."""
        if confidence is None:
            return self._sim_threshold
        c = max(0.0, min(1.0, float(confidence)))
        return self._sim_threshold - self._conf_adjust_max * (1.0 - c)

    def best_match(self, candidate_text: str, aec_enabled: bool = True) -> Optional[float]:
        """The highest text_score against any entry still inside its tail
        window, or None if history is empty/candidate is empty. Exposed for
        diagnostics/logging; is_echo() is the actual decision."""
        cand_norm = _normalize(candidate_text)
        if not cand_norm:
            return None
        cand_words = cand_norm.split()
        tail = self._tail_with_aec if aec_enabled else self._tail_without_aec
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entries = list(self._history)
        best: Optional[float] = None
        for entry in entries:
            if entry.ended_at is not None and (now - entry.ended_at) > tail:
                continue
            score = 1.0 if cand_norm == entry.norm else _text_score(cand_norm, cand_words, entry.norm, entry.words)
            if best is None or score > best:
                best = score
        return best

    def is_echo(self, candidate_text: str, aec_enabled: bool = True, confidence: Optional[float] = None) -> bool:
        """True if `candidate_text` looks like SARA hearing her own recent
        TTS rather than genuine user speech. Never raises."""
        cand_norm = _normalize(candidate_text)
        if not cand_norm:
            return False
        cand_words = cand_norm.split()
        tail = self._tail_with_aec if aec_enabled else self._tail_without_aec
        threshold = self._threshold_for(confidence)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entries = list(self._history)
        for entry in entries:
            if entry.ended_at is not None and (now - entry.ended_at) > tail:
                continue  # closed and past the tail window
            if cand_norm == entry.norm:
                return True
            if len(cand_norm) < _MIN_CHARS_FOR_FUZZY or len(entry.norm) < _MIN_CHARS_FOR_FUZZY:
                continue  # too short for fuzzy scoring to be trustworthy; only exact (above) counts
            if _text_score(cand_norm, cand_words, entry.norm, entry.words) >= threshold:
                return True
        return False

    def reset(self) -> None:
        with self._lock:
            self._history.clear()


_default_guard: Optional[SelfEchoGuard] = None
_default_guard_lock = threading.Lock()


def get_default_guard() -> SelfEchoGuard:
    global _default_guard
    if _default_guard is None:
        with _default_guard_lock:
            if _default_guard is None:
                _default_guard = SelfEchoGuard()
    return _default_guard
