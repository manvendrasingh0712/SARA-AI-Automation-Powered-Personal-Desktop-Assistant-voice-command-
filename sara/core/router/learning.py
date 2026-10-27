"""Learner host: lazy state load, reward application, atomic state saves, auto-revert."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional

from . import gate
from .choice import num
from .features import FEATURE_DIM, FEATURE_SCHEMA_VERSION
from .linucb import LinUCB
from .outcomes import Decision, finalize_signals, late_signals
from .store import RouterStore

logger = logging.getLogger("sara.router.learning")

STATE_FILENAME = "router_state.json"
SAVE_EVERY = 20
REVERT_WINDOW = 50
REVERT_BASELINE_MIN = 10
REVERT_DROP = 0.15


class LearnerHost:
    """Owns the LinUCB model; all reward work runs on the router's background thread."""

    def __init__(
        self,
        store: RouterStore,
        names_fn: Callable[[], List[str]],
        is_on_fn: Callable[[], bool],
        on_mismatch: Callable[[], None],
        on_revert: Callable[[], None],
    ) -> None:
        self._store = store
        self._names = names_fn
        self._is_on = is_on_fn
        self._on_mismatch = on_mismatch
        self._on_revert = on_revert
        self._lock = threading.RLock()
        self._learner: Optional[LinUCB] = None
        self._updates = 0

    def _path(self) -> Optional[Path]:
        try:
            return Path(self._store.path).parent / STATE_FILENAME
        except Exception:
            return None

    def get(self) -> LinUCB:
        """The learner, loaded from the state file on first use."""
        with self._lock:
            if self._learner is None:
                self._learner = self._load()
            return self._learner

    def _load(self) -> LinUCB:
        names = self._names()
        alpha = num("ROUTER_ALPHA", 0.8)
        learner: Optional[LinUCB] = None
        try:
            path = self._path()
            if path is not None:
                learner = LinUCB.load(
                    path,
                    arms=names,
                    dim=FEATURE_DIM,
                    schema=FEATURE_SCHEMA_VERSION,
                    alpha=alpha,
                )
                if learner is None and path.exists():
                    logger.warning(
                        "[router] learner state does not match this setup; starting fresh in shadow"
                    )
                    self._on_mismatch()
        except Exception as exc:
            logger.warning("[router] could not load learner state (%s)", type(exc).__name__)
            learner = None
        if learner is None:
            learner = LinUCB(names, FEATURE_DIM, alpha, 1.0, FEATURE_SCHEMA_VERSION)
        return learner

    def save(self) -> None:
        """Atomic state write; failures are logged, never raised."""
        path = self._path()
        learner = self._learner
        if path is None or learner is None:
            return
        try:
            learner.save(path)
        except Exception as exc:
            logger.warning("[router] could not save learner state (%s)", type(exc).__name__)

    def reset(self) -> None:
        """Drop the model and delete its state file."""
        with self._lock:
            self._learner = None
            self._updates = 0
        path = self._path()
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def apply(self, d: Decision) -> None:
        """Learn from one finished decision and label its stored row."""
        quality, reward, flags = finalize_signals(
            d, num("ROUTER_LAMBDA_LATENCY", 0.15), num("ROUTER_LAMBDA_COST", 0.05)
        )
        self.get().update(d.tier, d.x, reward)
        self._store.submit_update(
            d.decision_id,
            turn_id=d.turn_id,
            quality=quality,
            latency_ms=d.latency_ms,
            reward=reward,
            flags=flags,
            labelled=1,
        )
        self._after_update()

    def apply_late(self, row: Mapping[str, Any], value: int) -> None:
        """Late explicit thumbs for an already-labelled row."""
        if not row.get("labelled"):
            return
        result = late_signals(
            row, value, num("ROUTER_LAMBDA_LATENCY", 0.15), num("ROUTER_LAMBDA_COST", 0.05)
        )
        if result is None:
            return
        quality, reward, flags, x = result
        if len(x) == FEATURE_DIM:
            self.get().update(str(row.get("tier")), x, reward)
        self._store.submit_update(
            str(row["decision_id"]), quality=quality, reward=reward, flags=flags
        )

    def _after_update(self) -> None:
        with self._lock:
            self._updates += 1
            due = self._updates % SAVE_EVERY == 0
        if due:
            self.save()
        if self._is_on() and gate.quality_dropped(
            self._store, REVERT_WINDOW, REVERT_BASELINE_MIN, REVERT_DROP
        ):
            self._on_revert()