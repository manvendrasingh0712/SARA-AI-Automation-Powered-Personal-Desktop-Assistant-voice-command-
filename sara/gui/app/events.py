"""
sara.gui.app.events
Shared window-lifecycle state (the pywebview window handle, ready/stop
events) and the Python -> JS push-event bridge. Other submodules that need
this state do `from . import events` and use `events._window` etc.
rather than importing the names directly, since `_window` is reassigned
after bootstrap.main() creates the actual webview window.
"""
import os
import json
import time
import queue
import random
import threading
import urllib.request
import urllib.parse
import webview

_window = None
_stop_event = threading.Event()
_manual_wake_event = threading.Event()

# Startup-race fix: buffer pushes until the window has actually loaded.
_window_ready = threading.Event()
_push_buffer: list = []
_push_buffer_lock = threading.Lock()

def _emit(payload: str) -> None:
    window = _window
    if window is None:
        return

    try:
        window.evaluate_js(f"window.saraEvent && window.saraEvent({payload})")
    except Exception as e:
        print(f"[push error] {e}")
def _push(kind: str, *args) -> None:
    payload = json.dumps({"kind": kind, "args": list(args)})

    if _window is None or not _window_ready.is_set():
        # Window doesn't exist yet, or the page/js/app.js hasn't finished
        # loading yet — queue instead of silently dropping the event.
        with _push_buffer_lock:
            _push_buffer.append(payload)
        return

    _emit(payload)
def _flush_push_buffer() -> None:
    with _push_buffer_lock:
        pending = _push_buffer[:]
        _push_buffer.clear()
    for payload in pending:
        _emit(payload)
def _on_window_loaded() -> None:
    _window_ready.set()
    _flush_push_buffer()
