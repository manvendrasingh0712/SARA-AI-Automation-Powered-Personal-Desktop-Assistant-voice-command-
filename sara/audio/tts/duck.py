"""
sara.audio.tts.duck
Ducking controller with a dead-man timer: playback level is always restored
a few seconds after the last duck(True), even if the caller crashes.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from config import Config

logger = logging.getLogger("sara.core_logic")

DEADMAN_S = 3.0
_INIT_LOCK = threading.Lock()
_FALLBACK: dict[int, "DuckController"] = {}


class DuckController:
    """Thread-safe duck/un-duck for one player."""

    def __init__(
        self,
        get_player: Callable[[], Any],
        *,
        deadman_s: float = DEADMAN_S,
        timer_factory: Callable[..., Any] = threading.Timer,
        cfg: Any = Config,
    ) -> None:
        self._get_player = get_player
        self._deadman_s = deadman_s
        self._timer_factory = timer_factory
        self._cfg = cfg
        self._lock = threading.Lock()
        self._timer: Optional[Any] = None

    def _ramp_ms(self) -> int:
        return int(getattr(self._cfg, "VOICE_DUCK_RAMP_MS", 40))

    def duck(self, on: bool) -> None:
        """Duck (on=True) or restore (on=False). No player => no-op."""
        player = self._get_player()
        if player is None or not hasattr(player, "set_duck_gain"):
            return
        with self._lock:
            self._cancel_locked()
            if on:
                db = float(getattr(self._cfg, "VOICE_DUCK_DB", -12.0))
                player.set_duck_gain(db, self._ramp_ms())
                timer = self._timer_factory(self._deadman_s, self._expire)
                timer.daemon = True
                timer.start()
                self._timer = timer
            else:
                player.set_duck_gain(0.0, self._ramp_ms())

    def _cancel_locked(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _expire(self) -> None:
        try:
            with self._lock:
                self._timer = None
                player = self._get_player()
                if player is not None:
                    player.set_duck_gain(0.0, self._ramp_ms())
            logger.info("[Duck] dead-man released duck after %.1fs", self._deadman_s)
        except Exception as exc:
            logger.warning("[Duck] dead-man release failed: %s", type(exc).__name__)


def apply_duck(owner: Any, on: bool) -> None:
    """Duck/un-duck `owner._player` through a lazily created controller."""
    try:
        with _INIT_LOCK:
            ctl = getattr(owner, "_duck_ctl", None) or _FALLBACK.get(id(owner))
            if ctl is None:
                ctl = DuckController(lambda: getattr(owner, "_player", None))
                try:
                    setattr(owner, "_duck_ctl", ctl)
                except AttributeError:
                    _FALLBACK[id(owner)] = ctl
        ctl.duck(bool(on))
    except Exception as exc:
        logger.warning("[Duck] duck(%s) failed: %s", bool(on), type(exc).__name__)