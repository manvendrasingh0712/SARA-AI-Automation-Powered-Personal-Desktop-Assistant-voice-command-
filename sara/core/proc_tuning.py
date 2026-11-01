"""
sara.core.proc_tuning
Windows process tuning (part 3, P16). Every function is safe to call anywhere: on a non-Windows host, or when an API is
missing, it returns False / None and changes nothing.

  * disable_power_throttling(): opt this process out of EcoQoS ("efficiency mode"), so a minimised / background SARA is not
    slowed down by Windows 11 (SetProcessInformation(ProcessPowerThrottling)); optionally ABOVE_NORMAL priority;
  * on_battery(): True on battery or Battery Saver (psutil), so callers can pick the lighter settings;
  * KeepWarm: calls a tiny inference function every `interval_s` while the assistant is idle, so the first utterance after a
    long idle does not pay a model reload / page-in. interval_s <= 0 = off.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger("sara.proc")

_ProcessPowerThrottling = 4          # PROCESS_INFORMATION_CLASS
_POWER_THROTTLING_CURRENT_VERSION = 1
_POWER_THROTTLING_EXECUTION_SPEED = 0x1
_ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000


def disable_power_throttling(above_normal: bool = False) -> bool:
    """True when Windows accepted the opt-out."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation.restype = wintypes.BOOL
        proc = k32.GetCurrentProcess()
        # ControlMask set + StateMask clear = "do not throttle execution speed" (explicit opt-out, not 'let the system decide')
        state = _State(_POWER_THROTTLING_CURRENT_VERSION, _POWER_THROTTLING_EXECUTION_SPEED, 0)
        ok = bool(k32.SetProcessInformation(proc, _ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)))
        if above_normal:
            k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.SetPriorityClass.restype = wintypes.BOOL
            k32.SetPriorityClass(proc, _ABOVE_NORMAL_PRIORITY_CLASS)
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.debug("power-throttling opt-out unavailable: %s", exc)
        return False


def on_battery() -> Optional[bool]:
    """True = running on battery, False = plugged in, None = no battery / unknown."""
    try:
        import psutil

        b = psutil.sensors_battery()
        if b is None or b.power_plugged is None:
            return None
        return not b.power_plugged
    except Exception:  # noqa: BLE001
        return None


class KeepWarm:
    """Calls `ping()` every `interval_s` seconds while `idle()` says SARA is idle. Stops with stop()."""

    def __init__(self, ping: Callable[[], None], interval_s: float, idle: Callable[[], bool] = lambda: True,
                 skip_on_battery: bool = True, battery: Callable[[], Optional[bool]] = on_battery):
        self.ping, self.interval_s, self.idle = ping, float(interval_s), idle
        self.skip_on_battery, self._battery = skip_on_battery, battery
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.pings = 0

    def start(self) -> bool:
        if self.interval_s <= 0 or self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._run, name="keep-warm", daemon=True)
        self._thread.start()
        return True

    def tick(self) -> bool:
        """One decision: ping if idle (and not on battery). Separated from the loop so it can be tested."""
        try:
            if not self.idle():
                return False
            if self.skip_on_battery and self._battery():
                return False
            self.ping()
            self.pings += 1
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("keep-warm ping failed: %s", exc)
            return False

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
