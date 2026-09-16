"""
sara.gui.app.events
Shared window-lifecycle state (the pywebview window handle, ready/stop
events) and the Python -> JS push-event bridge. Other submodules that need
this state do `from . import events` and use `events._window` etc.
rather than importing the names directly, since `_window` is reassigned
after bootstrap.main() creates the actual webview window.

THREAD-SAFETY (v14):
`_push()` is called from many threads at once -- the typed-command worker,
the voice loop (run_sara_logic), the weather-refresh thread, the reminder
scheduler and the proactive-nudge loop. `window.evaluate_js()` is NOT
thread-safe on the WinForms/Chromium renderer this app uses: two concurrent
calls can interleave on the bridge and produce a garbled/lost event, and
the old code also had a read-check-append race between `_push()` and
`_flush_push_buffer()` that could emit buffered events out of order (or
drop one entirely, if the window became ready in between the `_window_ready`
check and the buffer append).

Both problems are fixed by ONE reentrant lock (`_push_lock`) that guards the
buffer AND serializes the evaluate_js() call. Using a single lock for both
-- rather than a separate buffer lock and emit lock -- is deliberate: it is
what guarantees events reach the frontend in the order they were pushed,
because the ready-check, the append and the emit are all one atomic step.
It is an RLock so `_flush_push_buffer()` can call `_emit()` while already
holding it.
"""
import json
import threading

_window = None
_stop_event = threading.Event()
_manual_wake_event = threading.Event()

# Startup-race fix: buffer pushes until the window has actually loaded.
_window_ready = threading.Event()
_push_buffer: list = []

# Guards `_push_buffer` AND serializes every window.evaluate_js() call.
_push_lock = threading.RLock()

# Back-compat alias: older code (and any out-of-tree patch) that still does
# `with events._push_buffer_lock:` keeps working and now correctly takes the
# same lock the emitter uses.
_push_buffer_lock = _push_lock

# Safety valve: if the window never becomes ready (renderer failed to load),
# don't let the buffer grow without bound for the life of the process.
# Oldest events are dropped first.
_MAX_BUFFERED_PUSHES = 500


def _emit(payload: str) -> None:
    """
    Low-level send. Takes `_push_lock` so only one thread is inside
    evaluate_js() at a time. Callers that already hold the lock (i.e.
    `_push` / `_flush_push_buffer`) re-enter it harmlessly.
    """
    with _push_lock:
        window = _window
        if window is None:
            return
        try:
            window.evaluate_js(f"window.saraEvent && window.saraEvent({payload})")
        except Exception as e:
            print(f"[push error] {e}")


def _push(kind: str, *args) -> None:
    # json.dumps() is done OUTSIDE the lock on purpose -- it's pure CPU work
    # on local data, so there's no reason to serialize it behind the bridge.
    payload = json.dumps({"kind": kind, "args": list(args)})

    with _push_lock:
        if _window is None or not _window_ready.is_set():
            # Window doesn't exist yet, or the page/js/app.js hasn't finished
            # loading yet -- queue instead of silently dropping the event.
            if len(_push_buffer) >= _MAX_BUFFERED_PUSHES:
                dropped = _push_buffer.pop(0)
                print(f"[push buffer full] dropped oldest event: {dropped[:80]}")
            _push_buffer.append(payload)
            return

        _emit(payload)


def _flush_push_buffer() -> None:
    # The drain and the replay both happen under the one lock, so a
    # concurrent _push() either lands in the buffer before the drain (and
    # gets replayed here, in order) or blocks and emits directly after --
    # never both, and never out of order.
    with _push_lock:
        pending = _push_buffer[:]
        _push_buffer.clear()
        for payload in pending:
            _emit(payload)


def _on_window_loaded() -> None:
    with _push_lock:
        _window_ready.set()
        _flush_push_buffer()