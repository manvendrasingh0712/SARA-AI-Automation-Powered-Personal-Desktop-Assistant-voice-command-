"""
sara.gui.app.router_api
ApiRouterMixin -- Settings > Smart routing card: stats, mode, reset and thumbs feedback.

The router package is imported lazily, so a missing package only disables this card.
Every method returns a dict and never raises; raw exception text never leaves this module.
The accepted mode is stored as preference "ui:router_mode" through the shared pref writer and
applied lazily on the first get_router_stats call. Prompt and reply texts are never logged.
No relative imports on purpose: this file can be imported and tested on its own.
"""

import importlib
import logging
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_PREF_KEY = "ui:router_mode"
_MODES = ("off", "shadow", "on")
_MAX_TURN_ID = 40
_MAX_MESSAGE = 200


def _normalize_mode(value):
    """Return the lower-cased mode when it is one of off / shadow / on, else None."""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    return mode if mode in _MODES else None


def _load_router():
    """Return the process-wide router, or None when the router package is unavailable."""
    try:
        return importlib.import_module("sara.core.router").get_router()
    except Exception as exc:
        logger.warning("[router_api] router unavailable (%s)", type(exc).__name__)
        return None


def _read_mode(router):
    """Live mode of the router (property or method); 'off' on any failure."""
    try:
        value = getattr(router, "mode", "off")
        if callable(value):
            value = value()
        return _normalize_mode(value) or "off"
    except Exception:
        return "off"


def _clean_turn_id(value):
    """Stripped turn id of 1 to 40 characters, or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if 0 < len(text) <= _MAX_TURN_ID else None


def _clean_vote(value):
    """+1 or -1 from an int or an integral float (JavaScript numbers), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and value in (1, -1):
        return value
    return None


def _text(value):
    """Short plain-text message for the GUI."""
    return str(value or "")[:_MAX_MESSAGE]


def _apply_saved(host, router):
    """Apply the saved mode once per Api instance (idempotent); failures only log."""
    with _LOCK:
        if getattr(host, "_router_pref_applied", False):
            return
        host._router_pref_applied = True
    try:
        mode = _normalize_mode(host.db.get_preference(_PREF_KEY))
        if mode is not None:
            router.set_mode(mode)
    except Exception as exc:
        logger.warning("[router_api] saved mode not applied (%s)", type(exc).__name__)


class ApiRouterMixin:

    def get_router_stats(self):
        router = _load_router()
        if router is None:
            return {"ok": False, "data": None}
        try:
            _apply_saved(self, router)
            return {"ok": True, "data": router.stats()}
        except Exception as exc:
            logger.warning("[get_router_stats] failed (%s)", type(exc).__name__)
            return {"ok": False, "data": None}

    def set_router_mode(self, mode):
        router = _load_router()
        if router is None:
            return {"ok": False, "mode": "off", "message": "Smart routing is not available."}
        chosen = _normalize_mode(mode)
        if chosen is None:
            return {"ok": False, "mode": _read_mode(router), "message": "Unknown mode."}
        try:
            with _LOCK:
                self._router_pref_applied = True
            accepted, message = router.set_mode(chosen)
            if not accepted:
                return {"ok": False, "mode": _read_mode(router), "message": _text(message)}
            writer = getattr(self, "_pref_writer", None)
            if writer is not None:
                try:
                    writer.enqueue(_PREF_KEY, chosen)
                except Exception as exc:
                    logger.warning("[set_router_mode] persist failed (%s)", type(exc).__name__)
            return {"ok": True, "mode": chosen, "message": _text(message)}
        except Exception as exc:
            logger.warning("[set_router_mode] failed (%s)", type(exc).__name__)
            return {"ok": False, "mode": _read_mode(router), "message": "Could not change the mode."}

    def reset_router(self):
        router = _load_router()
        if router is None:
            return {"ok": False}
        try:
            router.reset()
            return {"ok": True}
        except Exception as exc:
            logger.warning("[reset_router] failed (%s)", type(exc).__name__)
            return {"ok": False}

    def submit_feedback(self, turn_id, value):
        clean_id = _clean_turn_id(turn_id)
        vote = _clean_vote(value)
        if clean_id is None or vote is None:
            return {"ok": False}
        router = _load_router()
        if router is None:
            return {"ok": False}
        try:
            return {"ok": bool(router.feedback(clean_id, vote))}
        except Exception as exc:
            logger.warning("[submit_feedback] failed (%s)", type(exc).__name__)
            return {"ok": False}