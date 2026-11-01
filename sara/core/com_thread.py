"""
sara.core.com_thread
One persistent worker thread for every COM / WMI call made outside the GUI media code (volume, brightness).

Why: comtypes / pycaw interface pointers are apartment bound. When a voice command created them on whatever dispatcher thread
happened to run it and Python dropped them later (reference count or garbage collector, on another thread), `Release()` ran in the
wrong apartment and the whole process died with "access violation" inside `_ctypes.pyd` (seen in the live test: a native crash
about every 80 commands after volume / brightness commands). sara/gui/app/media.py already routes its pycaw calls through such a
thread (same root cause, same fix); this is the shared copy for the chat commands.

    from sara.core.com_thread import run_on_com_thread
    result = run_on_com_thread(fn)       # fn creates, uses AND deletes every COM object itself; returns plain Python values

The thread calls CoInitialize once, never exits, and runs a garbage collection after every job so a COM pointer that ended up in a
reference cycle is also released here, on its own thread.
"""
from __future__ import annotations

import gc
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")
_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def _init_thread() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001 -- not Windows / comtypes missing: callers fail on their own import
        pass


def _get() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sara-com")
            ex.submit(_init_thread).result()
            _executor = ex
        return _executor


def run_on_com_thread(fn: Callable[[], T], timeout: float = 30.0) -> T:
    """Run fn() on the single COM thread and return its result (or re-raise its exception here)."""

    def job() -> T:
        try:
            return fn()
        finally:
            gc.collect()          # release any COM pointer held in a cycle on THIS thread

    return _get().submit(job).result(timeout=timeout)
