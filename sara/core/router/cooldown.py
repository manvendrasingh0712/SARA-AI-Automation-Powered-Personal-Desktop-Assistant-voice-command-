"""Per-tier exponential cool-down after repeated rate-limit (429) errors."""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict

_MAX_COOLDOWN_S = 3600.0


def _base() -> float:
    try:
        from config import Config

        return max(0.1, float(getattr(Config, "ROUTER_COOLDOWN_BASE_S", 30.0)))
    except Exception:
        return 30.0


class Cooldowns:
    """base * 2^(n-1) seconds for the n-th consecutive 429, capped at one hour."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        base_fn: Callable[[], float] = _base,
    ) -> None:
        self._clock = clock
        self._base_fn = base_fn
        self._lock = threading.Lock()
        self._until: Dict[str, float] = {}
        self._streak: Dict[str, int] = {}

    def note(self, tier_name: str) -> float:
        """Record one more 429 for the tier; returns the new cool-down length."""
        with self._lock:
            streak = self._streak.get(tier_name, 0) + 1
            self._streak[tier_name] = streak
            delay = min(_MAX_COOLDOWN_S, self._base_fn() * (2 ** min(streak - 1, 16)))
            self._until[tier_name] = self._clock() + delay
            return delay

    def active(self, tier_name: str) -> bool:
        """True while the tier is cooling down."""
        with self._lock:
            return self._until.get(tier_name, 0.0) > self._clock()

    def remaining(self, tier_name: str) -> float:
        """Seconds of cool-down left (0 when none)."""
        with self._lock:
            return max(0.0, self._until.get(tier_name, 0.0) - self._clock())

    def success(self, tier_name: str) -> None:
        """A healthy reply ends the consecutive-429 streak."""
        with self._lock:
            self._streak.pop(tier_name, None)

    def clear(self) -> None:
        """Forget every cool-down."""
        with self._lock:
            self._until.clear()
            self._streak.clear()