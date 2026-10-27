"""Outcome signals -> quality -> reward for the learned router."""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

_NEGATIONS = frozenset({"no", "nahi", "nahin", "galat", "wrong", "nope", "नहीं", "गलत"})
_PUNCT = re.compile(r"[!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~\u0964]")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def quality_from_signals(
    *,
    thumbs: int = 0,
    rephrased: bool = False,
    tool_failure: bool = False,
    empty: bool = False,
    escalated: bool = False,
) -> float:
    """Quality in [0, 1]: start 0.7, adjusted by implicit and explicit signals."""
    q = 0.7
    if thumbs > 0:
        q += 0.3
    elif thumbs < 0:
        q -= 0.7
    if rephrased:
        q -= 0.4
    if tool_failure or empty:
        q -= 0.6
    if escalated:
        q -= 0.3
    return _clamp(q, 0.0, 1.0)


def compute_reward(
    quality: float, latency_ms: float, cost: float, lam_lat: float, lam_cost: float
) -> float:
    """quality - latency penalty (1.5 s free, saturating at 7.5 s) - cost penalty."""
    lat = float(latency_ms)
    if not math.isfinite(lat):
        lat = 0.0
    penalty = _clamp((lat - 1500.0) / 6000.0, 0.0, 1.0)
    return float(quality) - lam_lat * penalty - lam_cost * float(cost)


def _normalise(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", (text or "").lower()).split())


def is_rephrase(prev_text: str, text: str, dt_s: float) -> bool:
    """True when the user repeats or negates the previous request within 20 s."""
    if dt_s > 20.0:
        return False
    new = _normalise(text)
    old = _normalise(prev_text)
    if not new:
        return False
    tokens = new.split()
    if all(t in _NEGATIONS for t in tokens):
        return True
    if not old:
        return False
    return SequenceMatcher(None, old, new).ratio() >= 0.8