"""Learned model router: shadow-first, never worse than the legacy static rule."""
from __future__ import annotations

import atexit
import json
import logging
import random
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

from . import gate as _gate
from .choice import TierChoice, cfg, num, off_choice
from .cooldown import Cooldowns
from .features import extract_features
from .learning import LearnerHost
from .outcomes import Decision, OutcomeTracker
from .policy import adjacent, allowed_tiers, cascade_next, legacy_choice, static_choice
from .reporting import build_stats
from .store import RouterStore
from .tiers import Tier, load_tiers
from .worker import BackgroundWorker

logger = logging.getLogger("sara.router")

_MODES = ("off", "shadow", "on")
_GATE_TTL_S = 30.0
_DEPTH_WINDOW_S = 600.0

__all__ = ["Router", "TierChoice", "get_router"]


class Router:
    """Thread-safe model-tier router with modes off / shadow / on."""

    def __init__(
        self,
        store: Optional[RouterStore] = None,
        tiers: Optional[List[Tier]] = None,
        clock: Any = time.time,
        rng: Any = None,
    ) -> None:
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()
        self._store = store if store is not None else RouterStore()
        self._tiers_arg = list(tiers) if tiers is not None else None
        self._tiers_cache: Optional[List[Tier]] = None
        self._lock = threading.RLock()
        self._cool = Cooldowns(clock)
        self._tracker = OutcomeTracker(clock)
        self._worker = BackgroundWorker(self._handle, self._tick)
        self._learn = LearnerHost(
            self._store,
            lambda: [t.name for t in self.tiers()],
            lambda: self.mode == "on",
            self._to_shadow,
            self._revert,
        )
        self._turns: Deque[float] = deque(maxlen=64)
        self._reverted = False
        self._gate_cache: Tuple[float, Tuple[bool, str]] = (0.0, (False, ""))
        self._warned: set = set()

    # ── state ──

    @property
    def mode(self) -> str:
        """Live Config.ROUTER_MODE ('off' for anything unknown)."""
        value = str(getattr(cfg(), "ROUTER_MODE", "off") or "off").strip().lower()
        return value if value in _MODES else "off"

    def tiers(self) -> List[Tier]:
        """Enabled tiers, cheapest first."""
        with self._lock:
            if self._tiers_cache is None:
                raw = self._tiers_arg if self._tiers_arg is not None else load_tiers()
                self._tiers_cache = sorted((t for t in raw if t.enabled), key=lambda t: t.cost)
            return list(self._tiers_cache)

    def active(self) -> bool:
        """True when the router has anything to do."""
        return self.mode != "off" and len(self.tiers()) >= 2

    def depth(self) -> int:
        """Turns seen in the last 10 minutes (the engine passes it as ctx['depth'])."""
        cutoff = self._clock() - _DEPTH_WINDOW_S
        with self._lock:
            return sum(1 for t in self._turns if t >= cutoff)

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(message)

    def _to_shadow(self) -> None:
        if self.mode == "on":
            setattr(cfg(), "ROUTER_MODE", "shadow")

    def _revert(self) -> None:
        setattr(cfg(), "ROUTER_MODE", "shadow")
        self._reverted = True
        self._warn_once("revert", "[router] learner quality dropped; reverted to shadow mode")

    # ── decisions ──

    def choose(self, text: str, ctx: Optional[Mapping[str, Any]] = None) -> TierChoice:
        """Pick a tier for this prompt; never raises, no network, no disk write."""
        try:
            return self._choose(text, ctx)
        except Exception as exc:
            self._warn_once(
                "choose", f"[router] choose failed ({type(exc).__name__}); keeping legacy routing"
            )
            return off_choice("error")

    def _explore(self, pick: str, cands: List[str], tiers: List[Tier]) -> Tuple[str, float, bool]:
        eps = min(1.0, max(0.0, num("ROUTER_EPSILON", 0.05)))
        adj = [n for n in adjacent(pick, tiers) if n in cands]
        if not adj or eps <= 0.0:
            return pick, 1.0, False
        if self._rng.random() < eps:
            return self._rng.choice(adj), eps / len(adj), True
        return pick, 1.0 - eps, False

    def _choose(self, text: str, ctx: Optional[Mapping[str, Any]]) -> TierChoice:
        mode = self.mode
        if mode == "off":
            return off_choice("off")
        tiers = self.tiers()
        if len(tiers) < 2:
            return off_choice("tiers")
        by_name: Dict[str, Tier] = {t.name: t for t in tiers}
        feat = extract_features(text, ctx)
        static = static_choice(feat, tiers)
        allowed = allowed_tiers(feat, tiers)
        effective, reason = mode, mode
        if mode == "on":
            ok, message = self._gate_ok()
            if not ok:
                effective, reason = "shadow", "gate: " + message
        cands = allowed
        if effective == "on":
            cands = [n for n in allowed if not self._cool.active(n)] or allowed
        learner_pick = self._learn.get().choose(feat.vector, cands)
        applied = effective == "on"
        executed, propensity, explore = legacy_choice(feat, tiers), 1.0, False
        if applied:
            executed, propensity, explore = self._explore(learner_pick, cands, tiers)
        flags = [f"mode:{effective}", f"learner:{learner_pick}"]
        if explore:
            flags.append("explore")
        if applied:
            flags.append("applied")
        decision_id = uuid.uuid4().hex[:12]
        now = self._clock()
        cost = by_name[executed].cost
        self._tracker.add(
            Decision(decision_id, feat.vector.copy(), executed, static, cost, flags), text, now
        )
        self._store.submit_add(
            {
                "ts": now,
                "turn_id": None,
                "tier": executed,
                "static_tier": static,
                "propensity": propensity,
                "x_json": json.dumps([round(float(v), 4) for v in feat.vector]),
                "quality": None,
                "latency_ms": None,
                "cost": cost,
                "reward": None,
                "flags": ",".join(flags),
                "decision_id": decision_id,
                "labelled": 0,
            }
        )
        shown = by_name[executed] if applied else by_name[learner_pick]
        return TierChoice(shown, static, effective, propensity, explore, reason, decision_id, applied)

    def model_for(self, choice: TierChoice, backend: str) -> Optional[str]:
        """Model name to force for `backend`, or None to keep the legacy selection."""
        tier = choice.tier
        if choice.applied and tier is not None and tier.backend == backend:
            return tier.model
        return None

    def cascade(self, choice: TierChoice) -> Optional[Tier]:
        """Next tier up for ONE retry; only in 'on' mode and when it is not cooling down."""
        if not choice.applied or choice.tier is None or self.mode != "on":
            return None
        tiers = self.tiers()
        name = cascade_next(choice.tier.name, tiers)
        if name is None or self._cool.active(name):
            return None
        return next((t for t in tiers if t.name == name), None)

    # ── outcomes ──

    def bind_turn(self, decision_id: str, turn_id: Optional[str]) -> None:
        """Link the decision to a telemetry turn id (needed for thumbs feedback)."""
        if not decision_id or not turn_id:
            return
        try:
            if self._tracker.bind(decision_id, str(turn_id)):
                self._store.submit_update(decision_id, turn_id=str(turn_id))
        except Exception:
            self._warn_once("bind", "[router] bind_turn failed; continuing")

    def report_done(
        self,
        decision_id: str,
        *,
        ok: bool,
        latency_ms: float,
        empty: bool = False,
        escalated: bool = False,
        error_code: Optional[str] = None,
    ) -> None:
        """Store the outcome; the reward is learned later on the background thread."""
        if not decision_id:
            return
        try:
            d = self._tracker.report(
                decision_id,
                ok=bool(ok),
                latency_ms=float(latency_ms),
                empty=bool(empty),
                escalated=bool(escalated),
                error_code=error_code,
            )
            if d is not None:
                if ok and not empty:
                    self._cool.success(d.tier)
                self._worker.start()
        except Exception:
            self._warn_once("report", "[router] report_done failed; continuing")

    def note_user_turn(self, text: str, now: Optional[float] = None) -> None:
        """New user message: detect a rephrase of the previous turn and flush pending rewards."""
        try:
            if self.mode == "off":
                return
            stamp = self._clock() if now is None else float(now)
            with self._lock:
                self._turns.append(stamp)
            for d in self._tracker.take_ready(text or "", stamp):
                self._worker.submit(("finalize", d))
        except Exception:
            self._warn_once("turn", "[router] note_user_turn failed; continuing")

    def note_rate_limited(self, tier_name: str) -> None:
        """A 429 hit this tier: start / extend its exponential cool-down."""
        try:
            if tier_name and self.mode != "off":
                self._cool.note(str(tier_name))
        except Exception:
            self._warn_once("429", "[router] note_rate_limited failed; continuing")

    def feedback(self, turn_id: str, value: int) -> bool:
        """Explicit thumbs (+1 / -1); False when the turn is unknown."""
        try:
            v = 1 if value > 0 else (-1 if value < 0 else 0)
            if not turn_id or v == 0:
                return False
            if self._tracker.set_thumbs(str(turn_id), v):
                return True
            self._store.flush(1.0)
            row = self._store.get_by_turn(str(turn_id))
            if row is None or not row.get("decision_id"):
                return False
            return self._worker.submit(("late", row, v))
        except Exception:
            return False

    def _handle(self, item: Tuple[Any, ...]) -> None:
        if item[0] == "finalize":
            self._learn.apply(item[1])
        elif item[0] == "late":
            self._learn.apply_late(item[1], item[2])

    def _tick(self) -> None:
        for d in self._tracker.take_due(self._clock()):
            self._learn.apply(d)

    # ── gate / modes / reporting ──

    def can_enable_on(self) -> Tuple[bool, str]:
        """(ok, plain-words message): enough labelled samples and a passed offline gate."""
        result = _gate.evaluate(
            self._store.count(labelled_only=True), int(num("ROUTER_MIN_SAMPLES", 200))
        )
        self._gate_cache = (self._clock() + _GATE_TTL_S, result)
        return result

    def _gate_ok(self) -> Tuple[bool, str]:
        expiry, result = self._gate_cache
        return result if self._clock() < expiry else self.can_enable_on()

    def set_mode(self, mode: str) -> Tuple[bool, str]:
        """Switch Config.ROUTER_MODE live; 'on' is refused until the gate allows it."""
        m = str(mode or "").strip().lower()
        if m not in _MODES:
            return False, "unknown mode (use off, shadow or on)"
        if m == "on":
            ok, message = self.can_enable_on()
            if not ok:
                return False, "cannot switch on yet: " + message
        setattr(cfg(), "ROUTER_MODE", m)
        self._reverted = False
        return True, f"router mode is now {m}"

    def stats(self, days: int = 7) -> Dict[str, Any]:
        """Usage, latency, gate and revert status."""
        return build_stats(
            self.mode, self.tiers(), self._store, days, self.can_enable_on(), self._reverted
        )

    def drain(self, timeout: float = 2.0) -> bool:
        """Wait for queued reward and store work (tests, shutdown)."""
        return self._worker.drain(timeout) and self._store.flush(timeout)

    def reset(self) -> None:
        """Forget all samples, learner state, cool-downs and the revert flag."""
        try:
            self._worker.clear()
            self._store.reset()
            self._learn.reset()
            self._tracker.clear()
            self._cool.clear()
            self._reverted = False
            self._gate_cache = (0.0, (False, ""))
        except Exception:
            self._warn_once("reset", "[router] reset failed; continuing")

    def shutdown(self) -> None:
        """Finish pending rewards, save the learner state and close the store."""
        try:
            for d in self._tracker.take_all_done():
                self._worker.submit(("finalize", d))
            self._worker.drain(2.0)
            self._learn.save()
            self._worker.stop()
            self._store.flush(2.0)
            self._store.close()
        except Exception:
            self._warn_once("shutdown", "[router] shutdown failed; continuing")


_ROUTER: Optional[Router] = None
_ROUTER_LOCK = threading.Lock()


def get_router() -> Router:
    """Process-wide lazy Router singleton."""
    global _ROUTER
    if _ROUTER is None:
        with _ROUTER_LOCK:
            if _ROUTER is None:
                _ROUTER = Router()
                atexit.register(_ROUTER.shutdown)
    return _ROUTER