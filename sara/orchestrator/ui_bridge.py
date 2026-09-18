"""
sara.orchestrator.ui_bridge
Wraps the raw _push callback into a ui_update(kind, *args) signature and
coalesces rapid-fire duplicate UI events.
"""

import queue
import threading


# ----------------------------------------------------------------------------
# UI update helpers
# ----------------------------------------------------------------------------


def _make_ui_update(ui_queue: queue.Queue, stop_event: threading.Event):
    def ui_update(kind: str, *args) -> None:
        if not stop_event.is_set():
            try:
                ui_queue.put_nowait((kind, *args))
            except queue.Full:
                pass

    return ui_update


class _UICoalescer:
    __slots__ = ("_inner", "_last")

    def __init__(self, inner):
        self._inner = inner
        self._last = {}

    def __call__(self, kind, *args):
        if kind == "status" or kind == "footer":
            if self._last.get(kind) == args:
                return
            self._last[kind] = args
        self._inner(kind, *args)
