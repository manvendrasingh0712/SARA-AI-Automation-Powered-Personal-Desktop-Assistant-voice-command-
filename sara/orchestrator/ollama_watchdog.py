"""
sara/orchestrator/ollama_watchdog.py
Brings the local Ollama server back when it dies in the middle of a session (part 3, P18).

Until now Ollama was started once at launch; if it crashed afterwards (out of memory, a driver reset, someone ended the task)
every chat turn failed over to the cloud or to an apology until SARA was restarted. This thread checks the server every
`interval_s`; after `fail_threshold` failed checks in a row it calls manager.start() (which re-uses a running server or spawns
a new one), at most `max_restarts` times per run so a machine that cannot run Ollama is not hammered.
`manager` needs is_ready() and start() (ollama_manager.OllamaManager). `on_event(kind, text)` is optional (log / GUI toast).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("sara.ollama_watchdog")


class OllamaWatchdog:
    def __init__(self, manager, interval_s: float = 15.0, fail_threshold: int = 2, max_restarts: int = 3,
                 on_event: Optional[Callable[[str, str], None]] = None):
        self.manager, self.interval_s = manager, float(interval_s)
        self.fail_threshold, self.max_restarts, self.on_event = max(1, int(fail_threshold)), int(max_restarts), on_event
        self.restarts = 0
        self._fails = 0
        self._was_up = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if self.interval_s <= 0 or self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._run, name="ollama-watchdog", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.check_once()

    def check_once(self) -> str:
        """One check. Returns 'up', 'down' (still counting), 'restarted', 'restart_failed' or 'gave_up'."""
        try:
            if self.manager.is_ready():
                self._fails, self._was_up = 0, True
                return "up"
            if not self._was_up:
                return "down"           # never seen up yet: the launch path is still starting it, do not interfere
            self._fails += 1
            if self._fails < self.fail_threshold:
                return "down"
            if self.restarts >= self.max_restarts:
                return "gave_up"
            self.restarts += 1
            self._fails = 0
            logger.warning("Ollama stopped answering; restarting it (%d/%d).", self.restarts, self.max_restarts)
            ok = bool(self.manager.start())
            self._emit("restarted" if ok else "restart_failed",
                       "Local brain was restarted." if ok else "Local brain could not be restarted.")
            return "restarted" if ok else "restart_failed"
        except Exception as exc:  # noqa: BLE001 -- a watchdog must never die
            logger.debug("ollama watchdog check failed: %s", exc)
            return "down"

    def _emit(self, kind: str, text: str) -> None:
        if self.on_event:
            try:
                self.on_event(kind, text)
            except Exception:  # noqa: BLE001
                pass
