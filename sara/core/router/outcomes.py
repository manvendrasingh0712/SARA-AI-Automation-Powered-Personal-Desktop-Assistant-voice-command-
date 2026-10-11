"""Pending router decisions: outcome capture, rephrase detection and reward signals."""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .rewards import compute_reward, is_rephrase, quality_from_signals

_TEXT_CAP = 400


@dataclass
class Decision:
    """In-memory record of one routed turn (feature vector only, no reply text)."""

    decision_id: str
    x: Any
    tier: str
    static_tier: str
    cost: float
    flags: List[str] = field(default_factory=list)
    turn_id: Optional[str] = None
    done_at: Optional[float] = None
    ok: bool = True
    latency_ms: float = 0.0
    empty: bool = False
    escalated: bool = False
    error_code: Optional[str] = None
    rephrased: bool = False
    thumbs: int = 0
    finalized: bool = False


class OutcomeTracker:
    """Bounded, thread-safe table of decisions waiting for their reward."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        max_items: int = 64,
        timeout_s: float = 20.0,
    ) -> None:
        self._clock = clock
        self._max = max_items
        self._timeout = timeout_s
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, Decision]" = OrderedDict()
        self._turns: Dict[str, str] = {}
        self._last_id = ""
        self._last_text = ""
        self._last_ts = 0.0

    def _forget(self, d: Decision) -> None:
        if d.turn_id:
            self._turns.pop(d.turn_id, None)

    def add(self, d: Decision, text: str, now: float) -> None:
        """Register a decision and remember its text (memory only) for rephrase checks."""
        with self._lock:
            self._items[d.decision_id] = d
            while len(self._items) > self._max:
                _, old = self._items.popitem(last=False)
                self._forget(old)
            self._last_id = d.decision_id
            self._last_text = (text or "")[:_TEXT_CAP]
            self._last_ts = now

    def bind(self, decision_id: str, turn_id: str) -> bool:
        """Attach the telemetry turn id; False when the decision is unknown."""
        with self._lock:
            d = self._items.get(decision_id)
            if d is None:
                return False
            d.turn_id = turn_id
            self._turns[turn_id] = decision_id
            return True

    def report(
        self,
        decision_id: str,
        *,
        ok: bool,
        latency_ms: float,
        empty: bool,
        escalated: bool,
        error_code: Optional[str],
    ) -> Optional[Decision]:
        """Store the outcome; the reward itself is applied later."""
        with self._lock:
            d = self._items.get(decision_id)
            if d is None or d.finalized:
                return None
            d.ok, d.latency_ms, d.empty = ok, latency_ms, empty
            d.escalated, d.error_code = escalated, error_code
            d.done_at = self._clock()
            return d

    def set_thumbs(self, turn_id: str, value: int) -> bool:
        """Explicit thumbs for a decision that has not been finalised yet."""
        with self._lock:
            did = self._turns.get(turn_id)
            d = self._items.get(did) if did else None
            if d is None or d.finalized:
                return False
            d.thumbs = value
            return True

    def _take(self, pred: Callable[[Decision], bool]) -> List[Decision]:
        out = [d for d in self._items.values() if d.done_at is not None and pred(d)]
        for d in out:
            d.finalized = True
            self._items.pop(d.decision_id, None)
            self._forget(d)
        return out

    def take_ready(self, text: str, now: float) -> List[Decision]:
        """New user turn: flag a rephrase of the previous decision, release finished ones."""
        with self._lock:
            prev = self._items.get(self._last_id)
            if prev is not None and prev.done_at is not None and not prev.finalized:
                prev.rephrased = is_rephrase(self._last_text, text, now - self._last_ts)
            return self._take(lambda d: True)

    def take_due(self, now: float) -> List[Decision]:
        """Finished decisions whose wait for the next user turn timed out."""
        with self._lock:
            return self._take(lambda d: now - float(d.done_at or 0.0) >= self._timeout)

    def take_all_done(self) -> List[Decision]:
        """Every finished decision (shutdown)."""
        with self._lock:
            return self._take(lambda d: True)

    def clear(self) -> None:
        """Forget everything."""
        with self._lock:
            self._items.clear()
            self._turns.clear()
            self._last_id = self._last_text = ""
            self._last_ts = 0.0


def finalize_signals(d: Decision, lam_lat: float, lam_cost: float) -> Tuple[float, float, str]:
    """(quality, reward, flags text) for a finished decision."""
    empty = d.empty or not d.ok
    quality = quality_from_signals(
        thumbs=d.thumbs, rephrased=d.rephrased, empty=empty, escalated=d.escalated
    )
    reward = compute_reward(quality, d.latency_ms, d.cost, lam_lat, lam_cost)
    flags = list(d.flags)
    for active, tag in ((d.rephrased, "rephrased"), (empty, "empty"), (d.escalated, "escalated")):
        if active:
            flags.append(tag)
    if d.thumbs:
        flags.append(f"thumbs:{d.thumbs:+d}")
    if d.error_code:
        flags.append("err:" + str(d.error_code).replace(",", "_")[:24])
    return quality, reward, ",".join(flags)


def late_signals(
    row: Mapping[str, Any], value: int, lam_lat: float, lam_cost: float
) -> Optional[Tuple[float, float, str, List[float]]]:
    """Recompute quality/reward for a stored row after a late thumbs click."""
    try:
        flags = [
            f
            for f in str(row.get("flags") or "").split(",")
            if f and not f.startswith("thumbs:")
        ]
        quality = quality_from_signals(
            thumbs=value,
            rephrased="rephrased" in flags,
            empty="empty" in flags,
            escalated="escalated" in flags,
        )
        reward = compute_reward(
            quality,
            float(row.get("latency_ms") or 0.0),
            float(row.get("cost") or 0.0),
            lam_lat,
            lam_cost,
        )
        flags.append(f"thumbs:{value:+d}")
        x = [float(v) for v in json.loads(row.get("x_json") or "[]")]
        return quality, reward, ",".join(flags), x
    except Exception:
        return None