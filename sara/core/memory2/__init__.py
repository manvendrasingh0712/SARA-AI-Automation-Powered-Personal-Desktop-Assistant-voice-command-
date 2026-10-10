"""Memory 2.0: structured, temporal, correctable memory (lazy accessors only)."""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Memory2Store

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STORE: "Memory2Store | None" = None
_FAILED = False


def is_enabled() -> bool:
    """True when Config.MEMORY2_ENABLED is set."""
    try:
        from config import Config

        return bool(getattr(Config, "MEMORY2_ENABLED", False))
    except Exception:  # noqa: BLE001
        return False


def get_store() -> "Memory2Store | None":
    """Process-wide store, or None when disabled or initialisation failed."""
    global _STORE, _FAILED
    if not is_enabled():
        return None
    with _LOCK:
        if _STORE is not None:
            return _STORE
        if _FAILED:
            return None
        try:
            from .store import Memory2Store

            _STORE = Memory2Store()
        except Exception as exc:  # noqa: BLE001
            _FAILED = True
            logger.error("[Memory2] store init failed (%s); feature off", type(exc).__name__)
            return None
        return _STORE


def reset_for_tests() -> None:
    """Close and forget the singleton (tests only)."""
    global _STORE, _FAILED
    with _LOCK:
        store, _STORE, _FAILED = _STORE, None, False
    if store is not None:
        store.close()