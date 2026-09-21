"""
sara/orchestrator/supervisor.py
Lightweight watchdog for Sara's long-lived background daemon threads.

WHY THIS EXISTS
---------------
The reminders / notifications / proactive poll threads are all daemon
threads. If one ever dies (an exception that escapes its loop), nothing
in the app notices -- reminders just silently stop firing. The poll loops
now catch-and-continue, but this supervisor is the second line of
defence: every `interval_s` seconds it confirms each registered thread is
still running, and if one is gone it logs a WARNING (and, only where a
restart is known to be safe, restarts it).

HOW A THREAD IS DETECTED
------------------------
Liveness is checked BY THREAD NAME via threading.enumerate() (names:
"sara-reminders", "sara-notifications", "sara-proactive"). This avoids
depending on how the owner object is wrapped (reminders lives behind a
_Lazy proxy). The owner object is only used to (a) read its `_stop_event`
so an intentional shutdown is never reported as a death, and (b) call its
`start()` to restart -- and only for targets registered restartable=True.

A target that has never been seen running is NOT reported as dead (it may
be lazily built, disabled in Config, or not initialised yet). The one
exception: if the owner exposes a correctly-named thread object that is
not running, that is a thread which died before the first check.

RESTART POLICY
--------------
restartable=True  -> owner.start() is called (at most _MAX_RESTARTS times
                     per target per run, so a crash-loop can't spam).
restartable=False -> log only:
                     "UNCERTAIN -- restart not attempted, needs manual review"

The supervisor thread itself never dies: every check is wrapped in
try/except Exception, same "catch everything, log, continue" pattern as
the other poll loops.
"""

import logging
import threading
from typing import Any, Callable, List, Optional

logger = logging.getLogger("sara.supervisor")

_DEFAULT_INTERVAL_S = 30.0
_FIRST_CHECK_DELAY_S = 10.0   # first look happens early, then every interval
_MAX_RESTARTS = 3


def _emit(level: int, msg: str) -> None:
    """Log via `logging` AND print (the sibling poll threads use print)."""
    try:
        logger.log(level, msg)
    except Exception:
        pass
    try:
        print(f"[Supervisor] {msg}", flush=True)
    except Exception:
        pass


def _peek(obj: Any, attr: str) -> Any:
    """getattr that never raises (owner may be a lazy proxy)."""
    try:
        return getattr(obj, attr, None)
    except Exception:
        return None


def get_notification_watcher_if_any() -> Optional[Any]:
    """
    Returns the existing NotificationWatcher singleton, or None.
    Calling get_watcher() with NO arguments never creates one.
    """
    from .notifications import get_watcher

    return get_watcher()


class _Target:
    def __init__(
        self,
        name: str,
        thread_name: str,
        owner_getter: Callable[[], Any],
        restartable: bool,
    ) -> None:
        self.name = name
        self.thread_name = thread_name
        self.owner_getter = owner_getter
        self.restartable = restartable
        self.seen_alive = False
        self.dead_reported = False
        self.restarts = 0


class ThreadSupervisor:
    def __init__(self, interval_s: float = _DEFAULT_INTERVAL_S) -> None:
        self._interval = max(5.0, float(interval_s))
        self._targets: List[_Target] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------
    # Setup / lifecycle
    # ------------------------------------------------------------

    def register(
        self,
        name: str,
        thread_name: str,
        owner_getter: Callable[[], Any],
        restartable: bool = False,
    ) -> None:
        """
        name         -- label used in log lines ("reminders").
        thread_name  -- the exact threading.Thread name to look for.
        owner_getter -- callable returning the object that owns the thread
                        (has `_stop_event` and `start()`), or None if it
                        doesn't exist (yet). Called on every check.
        restartable  -- True only if owner.start() is known to be a safe,
                        idempotent restart. False = log only.
        """
        self._targets.append(_Target(name, thread_name, owner_getter, restartable))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="sara-supervisor"
        )
        self._thread.start()
        names = ", ".join(t.name for t in self._targets)
        _emit(
            logging.INFO,
            f"watching {len(self._targets)} threads ({names}), "
            f"checking every {self._interval:g}s",
        )

    def stop(self) -> None:
        self._stop_event.set()
        th = self._thread
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2.0)

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------

    def _run(self) -> None:
        delay = min(_FIRST_CHECK_DELAY_S, self._interval)
        while not self._stop_event.wait(timeout=delay):
            delay = self._interval
            try:
                self.check_once()
            except Exception as e:  # noqa: BLE001 -- a bad check must never kill the supervisor
                print(f"[Supervisor] check failed (continuing): {e}")

    def check_once(self) -> None:
        for t in list(self._targets):
            if self._stop_event.is_set():
                return
            try:
                self._check_target(t)
            except Exception as e:  # noqa: BLE001
                print(f"[Supervisor] check of '{t.name}' failed (continuing): {e}")

    # ------------------------------------------------------------
    # Per-target logic
    # ------------------------------------------------------------

    @staticmethod
    def _is_running(t: _Target) -> bool:
        return any(
            th.name == t.thread_name and th.is_alive() for th in threading.enumerate()
        )

    def _check_target(self, t: _Target) -> None:
        if self._is_running(t):
            if not t.seen_alive:
                t.seen_alive = True
                _emit(logging.INFO, f"'{t.name}' thread detected alive")
            elif t.dead_reported:
                _emit(logging.INFO, f"'{t.name}' thread is alive again")
            t.dead_reported = False
            return

        # Not running right now. Is that a death, or just "not started"?
        owner = None
        try:
            owner = t.owner_getter()
        except Exception as e:  # noqa: BLE001
            print(f"[Supervisor] could not reach '{t.name}' owner (continuing): {e}")

        # Stopped on purpose (shutdown/close) -> never a death.
        stop_ev = _peek(owner, "_stop_event") if owner is not None else None
        is_set = _peek(stop_ev, "is_set") if stop_ev is not None else None
        try:
            if callable(is_set) and is_set():
                t.seen_alive = False
                t.dead_reported = False
                return
        except Exception:
            pass

        if not t.seen_alive:
            th = _peek(owner, "_thread") if owner is not None else None
            if not (isinstance(th, threading.Thread) and th.name == t.thread_name):
                return  # never started / not built yet / disabled: not a death

        # ---- the thread is DEAD ----
        if not t.restartable:
            if not t.dead_reported:
                t.dead_reported = True
                _emit(
                    logging.WARNING,
                    f"'{t.name}' thread is DEAD (not stopped on purpose). "
                    f"UNCERTAIN -- restart not attempted, needs manual review",
                )
            return

        start_fn = _peek(owner, "start") if owner is not None else None
        if not callable(start_fn):
            if not t.dead_reported:
                t.dead_reported = True
                _emit(
                    logging.WARNING,
                    f"'{t.name}' thread is DEAD and its owner object is not "
                    f"reachable. UNCERTAIN -- restart not attempted, needs manual review",
                )
            return

        if t.restarts >= _MAX_RESTARTS:
            if not t.dead_reported:
                t.dead_reported = True
                _emit(
                    logging.WARNING,
                    f"'{t.name}' thread is DEAD again; restart limit "
                    f"({_MAX_RESTARTS}) reached. UNCERTAIN -- restart not "
                    f"attempted, needs manual review",
                )
            return

        t.restarts += 1
        _emit(
            logging.WARNING,
            f"'{t.name}' thread is DEAD -- restart attempt "
            f"{t.restarts}/{_MAX_RESTARTS}",
        )
        try:
            start_fn()
        except Exception as e:  # noqa: BLE001
            _emit(logging.WARNING, f"'{t.name}' restart attempt raised: {e}")

        if self._is_running(t):
            t.seen_alive = True
            t.dead_reported = False
            _emit(logging.WARNING, f"'{t.name}' thread restarted OK")
        else:
            _emit(
                logging.WARNING,
                f"'{t.name}' restart attempted but thread is still not "
                f"running (disabled in Config?) -- needs manual review",
            )
