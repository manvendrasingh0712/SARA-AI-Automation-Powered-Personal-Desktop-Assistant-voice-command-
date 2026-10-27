"""
sara.gui.app.perf_api
ApiPerfMixin -- read-only view of the response-speed telemetry for the Settings > System card.

Telemetry lives in sara.core.telemetry (imported lazily, so a missing package only disables this card).
The on/off choice is stored as preference "ui:telemetry_enabled" ("1"/"0") through the shared pref writer
and applied lazily on the first get_perf_summary call. Methods never raise and never return raw
exception text.
No relative imports on purpose: this file can be imported and tested on its own.
"""

import importlib
import threading

_LOCK = threading.Lock()
_PREF_KEY = "ui:telemetry_enabled"


def _clamp(value, lo, hi, default):
    """Return int(value) limited to [lo, hi]; `default` when it is not a number."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, number))


def _to_bool(value):
    """Interpret JS booleans and common string forms."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _load_telemetry():
    """Return the telemetry package, or None when it is not available."""
    try:
        return importlib.import_module("sara.core.telemetry")
    except Exception:
        return None


def _apply_saved(host, telemetry):
    """Apply the saved on/off preference once per Api instance (idempotent)."""
    with _LOCK:
        if getattr(host, "_perf_pref_applied", False):
            return
        host._perf_pref_applied = True
    try:
        saved = host.db.get_preference(_PREF_KEY)
        if saved is not None and str(saved).strip() != "":
            telemetry.set_enabled(str(saved).strip() == "1")
    except Exception as e:
        print(f"[perf pref apply error] {e}")


class ApiPerfMixin:

    def get_perf_summary(self, window_turns=50):
        telemetry = _load_telemetry()
        if telemetry is None:
            return {"ok": False, "data": None}
        try:
            _apply_saved(self, telemetry)
            data = dict(telemetry.get_summary(_clamp(window_turns, 5, 500, 50)))
            data["enabled"] = bool(telemetry.is_enabled())
            return {"ok": True, "data": data}
        except Exception as e:
            print(f"[get_perf_summary error] {e}")
            return {"ok": False, "data": None}

    def get_recent_turns(self, n=10):
        telemetry = _load_telemetry()
        if telemetry is None:
            return {"ok": False, "data": None}
        try:
            return {"ok": True, "data": list(telemetry.get_recent(_clamp(n, 1, 50, 10)))}
        except Exception as e:
            print(f"[get_recent_turns error] {e}")
            return {"ok": False, "data": None}

    def set_telemetry_enabled(self, enabled):
        telemetry = _load_telemetry()
        if telemetry is None:
            return {"ok": False, "data": None, "enabled": False}
        try:
            on = _to_bool(enabled)
            with _LOCK:
                self._perf_pref_applied = True
            telemetry.set_enabled(on)
            try:
                self._pref_writer.enqueue(_PREF_KEY, "1" if on else "0")
            except Exception as e:
                print(f"[set_telemetry_enabled persist error] {e}")
            return {"ok": True, "enabled": bool(telemetry.is_enabled())}
        except Exception as e:
            print(f"[set_telemetry_enabled error] {e}")
            return {"ok": False, "data": None, "enabled": False}