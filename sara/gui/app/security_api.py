"""
sara.gui.app.security_api
ApiSecurityMixin -- Settings > Security card: summary counters, recent events and the protection mode.

The security package is imported lazily, so a missing package only disables this card.
The mode (standard | strict | off) is applied to Config.SECURITY_MODE for the running app, which
sara.core.security.detector and sara.core.security.policy re-read on every call. It is stored as
preference "ui:security_mode" through the shared pref writer and applied lazily on the first
get_security_summary call. Methods never raise and never return raw exception text.
No relative imports on purpose: this file can be imported and tested on its own.
"""

import importlib
import threading

_LOCK = threading.Lock()
_PREF_KEY = "ui:security_mode"
_MODES = ("standard", "strict", "off")
_EVENT_KEYS = ("ts", "kind", "tool", "source", "tier")
_SUMMARY_EVENTS = 10
_SUMMARY_DAYS = 7


def _clamp(value, lo, hi, default):
    """Return int(value) limited to [lo, hi]; `default` when it is not a number."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, number))


def _load(name):
    """Return the named module, or None when it cannot be imported."""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _slim(rows):
    """Keep only the GUI-safe keys of each event dict."""
    out = []
    for row in rows or ():
        if isinstance(row, dict):
            out.append({key: row.get(key) for key in _EVENT_KEYS})
    return out


def _normalize_mode(value):
    """Return a valid mode string, or None."""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    return mode if mode in _MODES else None


def _current_mode():
    detector = _load("sara.core.security.detector")
    try:
        return str(detector.security_mode()) if detector is not None else "standard"
    except Exception:
        return "standard"


def _set_config_mode(mode):
    """Set Config.SECURITY_MODE for the running app; True on success."""
    cfg = _load("config")
    try:
        cfg.Config.SECURITY_MODE = mode
        return True
    except Exception:
        return False


def _tainted_now():
    taint = _load("sara.core.security.taint")
    try:
        return bool(taint.turn_tainted()) if taint is not None else False
    except Exception:
        return False


def _apply_saved(host):
    """Apply the saved mode once per Api instance (idempotent)."""
    with _LOCK:
        if getattr(host, "_security_pref_applied", False):
            return
        host._security_pref_applied = True
    try:
        mode = _normalize_mode(host.db.get_preference(_PREF_KEY))
        if mode is not None:
            _set_config_mode(mode)
    except Exception as e:
        print(f"[security pref apply error] {type(e).__name__}")


class ApiSecurityMixin:

    def get_security_summary(self):
        events = _load("sara.core.security.events")
        if events is None:
            return {"ok": False, "data": None}
        try:
            _apply_saved(self)
            counts = dict(events.get_counts(_SUMMARY_DAYS))
            data = {
                "mode": _current_mode(),
                "counts": {
                    "total": int(counts.get("total", 0)),
                    "blocked": int(counts.get("blocked", 0)),
                    "by_kind": dict(counts.get("by_kind") or {}),
                },
                "tainted_now": _tainted_now(),
                "last_events": _slim(events.get_events(_SUMMARY_EVENTS)),
            }
            return {"ok": True, "data": data}
        except Exception as e:
            print(f"[get_security_summary error] {type(e).__name__}")
            return {"ok": False, "data": None}

    def get_security_events(self, limit=50, kind=None):
        events = _load("sara.core.security.events")
        if events is None:
            return {"ok": False, "data": None}
        try:
            if kind in (None, ""):
                kind = None
            elif not isinstance(kind, str) or kind not in events.KINDS:
                return {"ok": True, "data": []}
            rows = events.get_events(_clamp(limit, 1, 200, 50), kind=kind)
            return {"ok": True, "data": _slim(rows)}
        except Exception as e:
            print(f"[get_security_events error] {type(e).__name__}")
            return {"ok": False, "data": None}

    def set_security_mode(self, mode):
        if _load("sara.core.security.events") is None:
            return {"ok": False, "data": None}
        try:
            chosen = _normalize_mode(mode)
            if chosen is None:
                return {"ok": False, "error": "invalid_mode"}
            with _LOCK:
                self._security_pref_applied = True
            if not _set_config_mode(chosen):
                return {"ok": False, "data": None}
            try:
                self._pref_writer.enqueue(_PREF_KEY, chosen)
            except Exception as e:
                print(f"[set_security_mode persist error] {type(e).__name__}")
            return {"ok": True, "mode": chosen}
        except Exception as e:
            print(f"[set_security_mode error] {type(e).__name__}")
            return {"ok": False, "data": None}