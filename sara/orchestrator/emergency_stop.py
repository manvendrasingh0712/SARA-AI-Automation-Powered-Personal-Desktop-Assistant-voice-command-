"""
sara.orchestrator.emergency_stop
Global "panic button" hotkey -- immediately stops TTS playback and
cancels the notification watcher's pending watch, WITHOUT closing the
app. Registered once at startup (sara/gui/app/bootstrap.py, right after
the Api object is built) and unregistered cleanly during teardown so a
future internal restart within the same process can never end up with
two handlers firing for one keypress.

WHY api.stop_sara() IS SAFE TO CALL DIRECTLY HERE
----------------------------------------------------
sara/gui/app/core.py's Api.stop_sara() already does exactly the "stop
talking right now" half of this job: it forwards to
TTSWorker.stop() -> TextToSpeech.stop() (the SAME interrupt call used
for mid-speech barge-in, sara/orchestrator/tts_worker.py's
_watch_loop()) and pushes a "sleeping" status update. It does NOT close
the app -- confirmed, it only stops TTS and updates status -- so it is
safe to call directly from a hotkey callback. This module deliberately
reuses it as-is rather than reimplementing a second TTS-stop path.

The "cancel pending notifications" half of the panic button is NEW --
stop_sara() has no notion of it -- and is handled separately here,
alongside the stop_sara() call, not inside it (stop_sara() itself is
left completely unmodified).
"""

import logging

import keyboard

from config import Config
from . import notifications

logger = logging.getLogger("sara.core_logic")

_DEBUG = getattr(Config, "DEBUG_MODE", False)

# Module-level handle for the registered hotkey (as returned by
# keyboard.add_hotkey()) -- None means "not currently registered".
_hotkey_handle = None


def _on_emergency_stop(api) -> None:
    """
    The actual panic-button action, run on keyboard's own hotkey-listener
    thread. Must never raise -- an exception escaping a `keyboard`
    hotkey callback can silently break that library's internal listener
    depending on platform/version, which would leave the user with a
    dead hotkey and no visible error anywhere.
    """
    try:
        api.stop_sara()
    except Exception as e:  # noqa: BLE001 -- must never propagate
        print(f"[EmergencyStop] stop_sara() failed: {e}")

    try:
        watcher = notifications.get_watcher()
        if watcher is not None:
            watcher.cancel()
    except Exception as e:  # noqa: BLE001 -- must never propagate
        print(f"[EmergencyStop] notification watcher cancel failed: {e}")

    if _DEBUG:
        print("[EmergencyStop] Panic button triggered -- TTS stopped, watch cancelled.")


def register_emergency_stop(api) -> None:
    """
    Registers the global emergency-stop hotkey
    (Config.EMERGENCY_STOP_HOTKEY, default 'ctrl+alt+s'). Config-gated
    by EMERGENCY_STOP_ENABLED -- a no-op if disabled. Idempotent:
    calling this again without an intervening
    unregister_emergency_stop() call is safely ignored instead of
    double-registering the same hotkey (this is the "doesn't leak or
    double-register if the app restarts internally" requirement).

    Uses the `keyboard` library already in requirements.txt (no new
    dependency) -- keyboard.add_hotkey() installs a low-level Windows
    keyboard hook, which works without admin elevation for a standard
    modifier+letter combo like ctrl+alt+s.

    EDGE CASE: if Config.EMERGENCY_STOP_HOTKEY is changed to a combo
    another running application has already claimed system-wide,
    `keyboard` has no way to detect or report that conflict from here --
    whichever app's hook happens to run first silently "wins" for that
    keypress.
    """
    global _hotkey_handle

    if not getattr(Config, "EMERGENCY_STOP_ENABLED", True):
        if _DEBUG:
            print("[EmergencyStop] EMERGENCY_STOP_ENABLED=False, not registering.")
        return

    if _hotkey_handle is not None:
        if _DEBUG:
            print("[EmergencyStop] Hotkey already registered, skipping.")
        return

    combo = getattr(Config, "EMERGENCY_STOP_HOTKEY", "ctrl+alt+s")
    try:
        _hotkey_handle = keyboard.add_hotkey(combo, lambda: _on_emergency_stop(api))
        if _DEBUG:
            print(f"[EmergencyStop] Registered hotkey '{combo}'.")
    except Exception as e:  # noqa: BLE001 -- must never crash startup
        print(f"[EmergencyStop] Failed to register hotkey '{combo}': {e}")
        _hotkey_handle = None


def unregister_emergency_stop() -> None:
    """
    Cleanly unregisters the hotkey, if one is registered -- a no-op
    otherwise. Called during app teardown (sara/gui/app/bootstrap.py,
    after webview.start() returns) so a future internal restart within
    the same process never leaks a stale hook or ends up
    double-registered.
    """
    global _hotkey_handle
    if _hotkey_handle is None:
        return
    try:
        keyboard.remove_hotkey(_hotkey_handle)
        if _DEBUG:
            print("[EmergencyStop] Hotkey unregistered.")
    except Exception as e:  # noqa: BLE001
        print(f"[EmergencyStop] Failed to unregister hotkey: {e}")
    finally:
        _hotkey_handle = None