"""Adaptive end-of-speech endpointing: pure decision rules (no I/O, audio or threads)."""
from __future__ import annotations

import collections
import math
import threading
import time
from typing import Callable, Deque, Optional

from config import Config
from sara.audio.stt.endpoint_cues import (
    ends_with_any_continuation,
    ends_with_continuation,
    is_lone_filler,
    starts_with_continuation,
    tokens,
)

__all__ = [
    "EarlyCutGuard",
    "decide_endpoint",
    "ends_with_continuation",
    "is_complete_command",
    "is_lone_filler",
    "noise_level_from",
    "required_silence_ms",
]

_SHORT_UTTERANCE_MS = 500.0
_NOISE_GATE = 0.5
_BACKOFF_CAP_MS = 300.0
_MAX_COMMAND_CHARS = 400


def _f(value: object, default: float) -> float:
    """Finite float or the default."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _limits(cfg: object) -> tuple[float, float, float, float]:
    """(min_ms, max_ms, continuation_extra_ms, noise_extra_ms), clamped; max >= min + 100."""
    src = Config if cfg is None else cfg
    lo = _clamp(_f(getattr(src, "ENDPOINT_MIN_MS", 350), 350), 200.0, 800.0)
    hi = _clamp(_f(getattr(src, "ENDPOINT_MAX_MS", 900), 900), 400.0, 2500.0)
    cont = _clamp(_f(getattr(src, "ENDPOINT_CONTINUATION_EXTRA_MS", 400), 400), 0.0, 1000.0)
    noise = _clamp(_f(getattr(src, "ENDPOINT_NOISE_EXTRA_MS", 150), 150), 0.0, 500.0)
    return lo, max(hi, lo + 100.0), cont, noise


def noise_level_from(floor: Optional[float], threshold: float) -> float:
    """Noise level 0..1 as floor / threshold (None or invalid -> 0.0)."""
    if floor is None:
        return 0.0
    f, t = _f(floor, 0.0), _f(threshold, 0.0)
    return _clamp(f / t, 0.0, 1.0) if t > 0 else 0.0


def is_complete_command(text: str) -> bool:
    """True for a finished T0/T1 command: >= 2 words, no trailing cue, slot filled."""
    try:
        if not isinstance(text, str) or len(text) > _MAX_COMMAND_CHARS:
            return False
        if len(tokens(text)) < 2 or ends_with_any_continuation(text) or is_lone_filler(text):
            return False
        from sara.core.intent import engine as intent_engine
        from sara.core.security import tiers

        intent, match = intent_engine.detect_intent(text)
        if not intent or intent == "chat" or tiers.tier_of(intent) > 1:
            return False
        if match is not None and match.re.groups:
            return any(g and g.strip() for g in match.groups())
        return True
    except Exception:
        return False


def _classify(text: object, lang: str) -> str:
    """Rule family for a partial: filler | continuation | complete | base."""
    if not isinstance(text, str) or not text.strip():
        return "base"
    if is_lone_filler(text):
        return "filler"
    if ends_with_continuation(text, lang):
        return "continuation"
    return "complete" if is_complete_command(text) else "base"


def required_silence_ms(
    partial_text: Optional[str],
    noise_level: float,
    lang: str,
    *,
    utterance_ms: float = 0.0,
    base_ms: Optional[float] = None,
    backoff_ms: float = 0.0,
    cfg: object = None,
) -> tuple[float, str]:
    """Silence (ms) needed to end the turn, and the rule that decided it.

    Rules: base = clamp(base_ms or MIN + (MAX - MIN) / 2); backoff raises MIN and base.
    "complete" -> MIN; "continuation" / "filler" -> base + extra (<= MAX); noise level
    above 0.5 adds NOISE_EXTRA * level and never goes below base; utterances under
    500 ms (default 0.0 = unknown) never go below base; no partial -> base.
    """
    lo, hi, cont, noise_x = _limits(cfg)
    back = max(0.0, _f(backoff_ms, 0.0))
    base = min(hi, _clamp(_f(base_ms, lo + 0.5 * (hi - lo)), lo, hi) + back)
    rule = _classify(partial_text, lang)
    need = base
    if rule == "complete":
        need = min(hi, lo + back)
    elif rule in ("continuation", "filler"):
        need = min(hi, base + cont)
    if need < base and _f(utterance_ms, 0.0) < _SHORT_UTTERANCE_MS:
        need, rule = base, "base"
    level = _clamp(_f(noise_level, 0.0), 0.0, 1.0)
    if level > _NOISE_GATE and noise_x > 0:
        need, rule = min(hi, max(need, base) + noise_x * level), "noise"
    return float(min(need, hi)), rule


def decide_endpoint(
    partial_text: Optional[str],
    silence_ms: float,
    noise_level: float,
    lang: str,
    *,
    utterance_ms: float = 0.0,
    base_ms: Optional[float] = None,
    backoff_ms: float = 0.0,
    cfg: object = None,
) -> bool:
    """True when the trailing silence is long enough; always at MAX, never before MIN."""
    lo, hi, _, _ = _limits(cfg)
    silence = _f(silence_ms, 0.0)
    if silence >= hi:
        return True
    if silence < lo:
        return False
    need, _rule = required_silence_ms(
        partial_text, noise_level, lang,
        utterance_ms=utterance_ms, base_ms=base_ms, backoff_ms=backoff_ms, cfg=cfg,
    )
    return silence >= need


class EarlyCutGuard:
    """Spots endpoints that cut a sentence short and temporarily widens the wait."""

    _KEEP = 20
    _CUT_LIMIT = 3

    def __init__(
        self, window_ms: float, backoff_ms: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.window_ms = float(window_ms)
        self.backoff_step_ms = float(backoff_ms)
        self._clock = clock
        self._lock = threading.Lock()
        self._outcomes: Deque[bool] = collections.deque(maxlen=self._KEEP)
        self._pending: Optional[tuple[float, Optional[str]]] = None
        self._extra = 0.0
        self._clean = 0
        self._endpoints = 0
        self._early_cuts = 0
        self._rules: dict[str, int] = {}

    def note_endpoint(self, text: Optional[str], rule: str) -> None:
        """Record an endpoint decision (text is never logged)."""
        with self._lock:
            self._outcomes.append(False)
            self._pending = (self._clock(), text if isinstance(text, str) else None)
            self._endpoints += 1
            self._clean += 1
            if self._clean >= self._KEEP:
                self._extra = 0.0
            self._rules[str(rule)] = self._rules.get(str(rule), 0) + 1

    def note_next_speech_start(self, text_hint: Optional[str] = None) -> bool:
        """True when speech resumed inside the window after a cue-ended endpoint."""
        try:
            with self._lock:
                pending, self._pending = self._pending, None
                if pending is None:
                    return False
                at, prev = pending
                if (self._clock() - at) * 1000.0 > self.window_ms:
                    return False
                cue = (prev is not None and ends_with_any_continuation(prev)) or (
                    isinstance(text_hint, str) and starts_with_continuation(text_hint)
                )
                if not cue:
                    return False
                self._outcomes[-1] = True
                self._early_cuts += 1
                self._clean = 0
                if sum(self._outcomes) >= self._CUT_LIMIT:
                    self._extra = min(_BACKOFF_CAP_MS, self._extra + self.backoff_step_ms)
                return True
        except Exception:
            return False

    def backoff_ms(self) -> float:
        """Extra wait in ms: 0 normally, grows per early cut (cap 300), decays after 20 clean."""
        with self._lock:
            return self._extra

    def stats(self) -> dict:
        """Counters: endpoints, early_cuts and a per-rule histogram."""
        with self._lock:
            return {
                "endpoints": self._endpoints,
                "early_cuts": self._early_cuts,
                "rules": dict(self._rules),
            }