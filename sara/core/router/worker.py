"""Bounded single-thread background worker shared by the router's store and learner."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("sara.router.worker")

_STOP = object()


class BackgroundWorker:
    """Daemon thread draining a bounded queue; a full queue drops the item and counts it."""

    def __init__(
        self,
        handle: Callable[[Any], None],
        tick: Optional[Callable[[], None]] = None,
        queue_max: int = 256,
        name: str = "SaraRouterWorker",
        idle_s: float = 1.0,
    ) -> None:
        self._handle = handle
        self._tick = tick
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=queue_max)
        self._name = name
        self._idle_s = idle_s
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._warned: set = set()
        self.dropped = 0

    def start(self) -> None:
        """Start the thread if it is not running (idempotent)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()

    def submit(self, item: Any) -> bool:
        """Queue an item without blocking; False (and a drop count) when full."""
        self.start()
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False

    def drain(self, timeout: float = 2.0) -> bool:
        """Wait until everything queued before this call has been handled."""
        self.start()
        event = threading.Event()
        try:
            self._queue.put(event, timeout=timeout)
        except queue.Full:
            return False
        return event.wait(timeout)

    def clear(self) -> None:
        """Discard everything still waiting in the queue."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the thread to finish; it restarts lazily on the next submit."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._thread = None
            return
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            return
        if thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            if not thread.is_alive():
                self._thread = None

    def _guard(self, fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:
            kind = type(exc).__name__
            if kind not in self._warned:
                self._warned.add(kind)
                logger.warning("[router] background task failed (%s)", kind)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=self._idle_s)
            except queue.Empty:
                if self._tick is not None:
                    self._guard(self._tick)
                continue
            if item is _STOP:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            self._guard(self._handle, item)