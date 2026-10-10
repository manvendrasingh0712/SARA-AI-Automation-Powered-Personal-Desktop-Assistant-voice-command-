"""Background tick for Memory 2.0: extraction with rate-limit backoff, then optional decay."""
from __future__ import annotations

import importlib
import logging
import threading
import time
from typing import Any

from . import extract, get_store, is_enabled

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 300.0
_BACKOFF_MAX_S = 3600.0
_LOCK = threading.Lock()
_state = {"until": 0.0, "delay": 0.0}


def reset_backoff() -> None:
    """Clear the backoff state (tests and success path)."""
    with _LOCK:
        _state["until"] = 0.0
        _state["delay"] = 0.0


def _penalize(now: float) -> None:
    with _LOCK:
        delay = _state["delay"]
        _state["delay"] = _BACKOFF_BASE_S if delay <= 0 else min(delay * 2, _BACKOFF_MAX_S)
        _state["until"] = now + _state["delay"]
        wait = _state["delay"]
    logger.warning("[Memory2] rate limited; next extraction attempt in %ds", int(wait))


def _daily_pass(store: Any) -> None:
    try:
        module = importlib.import_module("sara.core.memory2.decay")
    except ImportError:
        return
    fn = getattr(module, "daily_pass", None)
    if callable(fn):
        fn(store)


def tick(db: Any, brain: Any, *, now: float | None = None) -> None:
    """One Memory 2.0 maintenance step; no-op when disabled; never raises."""
    try:
        if not is_enabled():
            return
        stamp = time.time() if now is None else float(now)
        with _LOCK:
            if stamp < _state["until"]:
                return
        if not brain.is_usable():
            return
        store = get_store()
        if store is None:
            return
        try:
            result = extract.run_extraction_pass(store, db, brain, now=stamp)
        except Exception as exc:  # noqa: BLE001
            if not extract.is_rate_limited(str(exc)):
                raise
            _penalize(stamp)
            return
        if result.reason == "rate_limited":
            _penalize(stamp)
            return
        if result.ok:
            reset_backoff()
        _daily_pass(store)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] tick failed (%s)", type(exc).__name__)