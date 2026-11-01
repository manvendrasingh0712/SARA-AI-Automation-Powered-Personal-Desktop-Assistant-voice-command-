"""
sara.core.llm.gemini_gate
A quota-aware gate in front of the Gemini client (part 3, P15).

  * token buckets per model: requests per minute and per day (free-tier limits are configurable);
  * a per-model circuit breaker: after `fail_threshold` consecutive 429/5xx the model is skipped for `cooldown_s`
    (or for the Retry-After the server asked for);
  * a failover chain: choose(model, chain) returns the first model that is allowed (pro -> fast -> ... ), or None when every
    cloud model is blocked, and the caller falls back to the local model at once (no 8 s of retries first);
  * backoff with full jitter that honours Retry-After, and a sleep that a cancel / barge-in event interrupts;
  * request coalescing: identical in-flight prompts share one call;
  * a usage meter ("312/500 today") for the GUI / logs.
Pure Python, clock injected for tests. The engine owns one instance (`GATE`).
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple


class GeminiGate:
    def __init__(self, rpm: int = 10, rpd: int = 250, fail_threshold: int = 3, cooldown_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self.rpm, self.rpd, self.fail_threshold, self.cooldown_s = rpm, rpd, fail_threshold, cooldown_s
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._minute: Dict[str, List[float]] = {}
        self._day: Dict[str, Tuple[str, int]] = {}
        self._fails: Dict[str, int] = {}
        self._blocked_until: Dict[str, float] = {}
        self._inflight: Dict[str, "_Flight"] = {}

    # ── admission ────────────────────────────────────────────────────────────
    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._wall()))

    def allow(self, model: str) -> bool:
        now = self._clock()
        with self._lock:
            if now < self._blocked_until.get(model, 0.0):
                return False
            window = [t for t in self._minute.get(model, []) if now - t < 60.0]
            self._minute[model] = window
            if self.rpm and len(window) >= self.rpm:
                return False
            day, n = self._day.get(model, (self._today(), 0))
            if day != self._today():
                day, n = self._today(), 0
            return not (self.rpd and n >= self.rpd)

    def note_request(self, model: str) -> None:
        now = self._clock()
        with self._lock:
            self._minute.setdefault(model, []).append(now)
            day, n = self._day.get(model, (self._today(), 0))
            if day != self._today():
                day, n = self._today(), 0
            self._day[model] = (day, n + 1)

    def note_result(self, model: str, ok: bool, retry_after_s: Optional[float] = None, hard: bool = False) -> None:
        """ok=False for a 429 / 5xx / timeout. `hard` (a daily-quota 429) blocks at once."""
        with self._lock:
            if ok:
                self._fails[model] = 0
                return
            self._fails[model] = self._fails.get(model, 0) + 1
            if hard or self._fails[model] >= self.fail_threshold or (retry_after_s or 0) > 0:
                wait = float(retry_after_s) if retry_after_s else self.cooldown_s
                self._blocked_until[model] = max(self._blocked_until.get(model, 0.0), self._clock() + wait)

    def choose(self, model: str, chain: Sequence[str] = ()) -> Optional[str]:
        """The first allowed model starting with `model` then `chain`; None = every cloud model is blocked."""
        seen = []
        for m in [model, *chain]:
            if m and m not in seen:
                seen.append(m)
                if self.allow(m):
                    return m
        return None

    # ── retry timing ─────────────────────────────────────────────────────────
    @staticmethod
    def backoff(attempt: int, base: float = 1.0, cap: float = 8.0, retry_after_s: Optional[float] = None,
                rng: Callable[[], float] = random.random) -> float:
        if retry_after_s is not None and retry_after_s > 0:
            return min(float(retry_after_s), cap)
        return rng() * min(cap, base * (2 ** attempt))      # "full jitter"

    @staticmethod
    def interruptible_sleep(seconds: float, cancel_event, slice_s: float = 0.1) -> bool:
        """Sleep in short slices so a cancel / barge-in is noticed within `slice_s`.
        True = slept the whole time, False = cancelled."""
        remaining = float(seconds)
        while remaining > 0:
            if cancel_event is not None and cancel_event.is_set():
                return False
            step = min(slice_s, remaining)
            time.sleep(step)
            remaining -= step
        return not (cancel_event is not None and cancel_event.is_set())

    # ── coalescing ───────────────────────────────────────────────────────────
    def coalesce(self, key: str, fn: Callable[[], object]):
        """Identical in-flight requests share one call (the late callers wait for, and get, the first one's result)."""
        with self._lock:
            flight = self._inflight.get(key)
            leader = flight is None
            if leader:
                flight = self._inflight[key] = _Flight()
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value
        try:
            flight.value = fn()
            return flight.value
        except BaseException as exc:  # noqa: BLE001
            flight.error = exc
            raise
        finally:
            flight.done.set()
            with self._lock:
                self._inflight.pop(key, None)

    # ── meter ────────────────────────────────────────────────────────────────
    def usage(self) -> Dict[str, Tuple[int, int]]:
        with self._lock:
            return {m: (n if d == self._today() else 0, self.rpd) for m, (d, n) in self._day.items()}


_latest: "GeminiGate | None" = None


def register(gate: "GeminiGate") -> "GeminiGate":
    """Remember the engine's gate so the GUI can show its usage meter (no import of the engine needed)."""
    global _latest
    _latest = gate
    return gate


def latest_usage() -> Dict[str, Tuple[int, int]]:
    """{model: (requests today, daily cap or 0)} of the registered gate; {} when there is none or nothing was sent."""
    try:
        return {m: v for m, v in (_latest.usage() if _latest else {}).items() if v[0] > 0}
    except Exception:  # noqa: BLE001
        return {}


class _Flight:
    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error: Optional[BaseException] = None
