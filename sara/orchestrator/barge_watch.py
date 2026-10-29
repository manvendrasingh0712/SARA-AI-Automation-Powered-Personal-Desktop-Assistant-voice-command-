"""
sara.orchestrator.barge_watch
Semantic barge-in: duck on detection, classify what the user said and only
hard-stop Sara for a real interruption. Used by TTSWorker._watch_loop.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from config import Config
from sara.audio.tts.barge_classify import classify_barge, resolve_ambiguous

logger = logging.getLogger("sara.core_logic")

_POLL_S = 0.05
_COOLDOWN_S = 2.0
_DETECT_WINDOW_S = 0.3
_PROBE_WINDOW_S = 0.1
_DECODE_MAX_MS = 700


class SemanticBargeIn:
    """One poll per step(); owns duck state, cool-down and the verdict."""

    def __init__(
        self,
        voice: Any,
        ears: Any,
        hard_stop: Callable[[], None],
        on_interrupt: Optional[Callable[[dict], None]] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        cfg: Any = Config,
        classify: Callable[..., str] = classify_barge,
        resolve: Callable[..., str] = resolve_ambiguous,
    ) -> None:
        self._voice = voice
        self._ears = ears
        self._hard_stop = hard_stop
        self._on_interrupt = on_interrupt
        self._clock = clock
        self._sleeper = sleeper
        self._cfg = cfg
        self._classify = classify
        self._resolve = resolve
        self._cooldown_until = 0.0
        self._speech_since: Optional[float] = None
        self._ducked = False
        self._engaged = False

    # -- helpers ---------------------------------------------------------
    def _num(self, name: str, default: float) -> float:
        try:
            return float(getattr(self._cfg, name, default))
        except (TypeError, ValueError):
            return default

    def _aec_available(self) -> bool:
        """
        Is echo cancellation actually running?

        D62: this tested only that the AEC OBJECT EXISTS -- the same defect
        D42 fixed in core_wiring.run_sara_logic(), in a second place that
        was missed. sara/audio/aec.py catches its own ImportError at module
        level and sets _HAS_AEC_LIB = False; AECProcessor.__init__ then sets
        `self.enabled = Config.AEC_ENABLED and _HAS_AEC_LIB`, and
        CONSTRUCTION STILL SUCCEEDS. So on a machine without the
        `aec-audio-processing` wheel, `_ears._aec` is a live object with
        enabled=False and this read True.
        
        The consequence here is the opposite of D42's and worse. This
        decides whether to apply BARGE_NOAEC_THRESHOLD_BOOST (1.5), which
        exists to stop SARA's own voice tripping barge-in when nothing is
        cancelling the echo. Reading True meant the boost was skipped
        exactly when it was needed, so SARA could interrupt herself
        mid-reply and treat her own speech as the user's next command.

        `hasattr` still returns True when the attribute is missing
        entirely: a stand-in `ears` that never had an `_aec` attribute is a
        test double, not a machine without AEC, and must not be penalised.
        """
        if not hasattr(self._ears, "_aec"):
            return True
        aec = getattr(self._ears, "_aec", None)
        return aec is not None and bool(getattr(aec, "enabled", False))

    def _set_boost(self, value: float) -> None:
        fn = getattr(self._ears, "set_barge_boost", None)
        if fn is not None:
            try:
                fn(value)
            except Exception:
                pass

    def _speaking(self, duration: float) -> bool:
        """is_user_speaking with the no-AEC threshold boost applied only for
        this call (never touches Config)."""
        boost = 1.0 if self._aec_available() else self._num(
            "BARGE_NOAEC_THRESHOLD_BOOST", 1.5
        )
        if boost != 1.0:
            self._set_boost(boost)
        try:
            return bool(self._ears.is_user_speaking(duration=duration))
        finally:
            if boost != 1.0:
                self._set_boost(1.0)

    def _duck(self, on: bool) -> None:
        try:
            if on:
                if not getattr(self._cfg, "VOICE_DUCKING", False):
                    return
                fn = getattr(self._voice, "duck", None)
                if fn is not None:
                    fn(True)
                    self._ducked = True
            elif self._ducked:
                self._ducked = False
                fn = getattr(self._voice, "duck", None)
                if fn is not None:
                    fn(False)
        except Exception as exc:
            logger.warning("[BargeIn] duck(%s) failed: %s", on, type(exc).__name__)

    def release(self) -> None:
        """Restore the playback level. Safe from any thread; never raises."""
        self._duck(False)

    def _emit(self, verdict: str) -> None:
        if self._on_interrupt is None:
            return
        try:
            self._on_interrupt(
                {"at": self._clock(), "verdict": verdict, "chars_spoken": None}
            )
        except Exception:
            pass

    def _decode(self) -> Optional[str]:
        fn = getattr(self._ears, "decode_barge_ring", None)
        if fn is None:
            return None
        try:
            return fn(max_ms=_DECODE_MAX_MS)
        except Exception:
            return None

    # -- detection / verdict ---------------------------------------------
    def _detect(self, now: float) -> Optional[float]:
        """Speech length in ms once an interruption is confirmed, else None."""
        if self._aec_available():
            return _DETECT_WINDOW_S * 1000.0 if self._speaking(_DETECT_WINDOW_S) else None
        if not self._speaking(_PROBE_WINDOW_S):
            self._speech_since = None
            return None
        if self._speech_since is None:
            self._speech_since = now
        elapsed = (now - self._speech_since) * 1000.0
        if elapsed >= self._num("BARGE_NOAEC_MIN_SPEECH_MS", 300.0):
            self._speech_since = None
            return elapsed
        return None

    def _collect(self, base_ms: float) -> float:
        """Wait while the user keeps talking (bounded); return speech ms."""
        start = self._clock()
        deadline = start + self._num("BARGE_COLLECT_MS", 550.0) / 1000.0
        while self._clock() < deadline:
            self._sleeper(_POLL_S)
            if not self._voice.is_speaking():
                break
            if not self._speaking(_PROBE_WINDOW_S):
                break
        return base_ms + (self._clock() - start) * 1000.0

    def _step(self) -> bool:
        now = self._clock()
        if now < self._cooldown_until:
            return True
        if not self._voice.is_speaking():
            self._speech_since = None
            return False
        base_ms = self._detect(now)
        if base_ms is None:
            return True
        self._engaged = True
        self._duck(True)
        speech_ms = self._collect(base_ms)
        if not self._voice.is_speaking():
            self._duck(False)
            self._engaged = False
            return True
        verdict = self._classify(self._decode(), speech_ms, cfg=self._cfg)
        if verdict == "ambiguous":
            verdict = self._resolve(speech_ms, cfg=self._cfg)
        logger.info(
            "[BargeIn] verdict=%s speech_ms=%d ducked=%s",
            verdict, int(speech_ms), self._ducked,
        )
        self._duck(False)
        if verdict == "interrupt":
            self._hard_stop()
            self._emit("interrupt")
        else:
            self._cooldown_until = self._clock() + _COOLDOWN_S
        self._engaged = False
        return True

    def step(self) -> bool:
        """One watcher poll. True when this poll was handled here (caller
        skips the legacy branch). After a failure that happened once an
        interruption was detected, the legacy hard-stop runs immediately;
        before detection the caller gets False and runs its legacy check."""
        self._engaged = False
        try:
            return self._step()
        except Exception as exc:
            logger.warning(
                "[BargeIn] semantic path failed: %s engaged=%s",
                type(exc).__name__, self._engaged,
            )
            self._speech_since = None
            self._duck(False)
            if not self._engaged:
                return False
            self._engaged = False
            try:
                self._hard_stop()
            except Exception as stop_exc:
                logger.warning(
                    "[BargeIn] fallback stop failed: %s", type(stop_exc).__name__
                )
            self._emit("fallback")
            return True