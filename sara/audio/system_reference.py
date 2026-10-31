"""
sara.audio.system_reference
Feeds EVERYTHING the PC is playing (YouTube, Spotify, a video call ...) to the echo canceller as its reference
signal, so that audio is removed from the microphone instead of being transcribed as if the user had said it.

Background: until now the canceller only knew SARA's own voice (the text-to-speech output). With a video or music
playing, the microphone heard it together with the user and the recogniser wrote it down ("kahaan kahaan ...",
song lyrics) while the user's real words were lost. Windows can hand us a copy of the final mix going to the
speakers (WASAPI "loopback"); that copy is exactly what the room echo is made from.

Measured here (YouTube-like speech through the laptop speakers, 41 s): the echo canceller removed 4 dB without the
reference problem solved and 26 dB with it; see AECProcessor for the 80 ms microphone delay that makes it work
(the loopback copy reaches us ~25 ms AFTER the echo reaches the microphone, and the canceller needs it first).

The device used is the physical output when the default one is a virtual "enhancer" (FxSound, Voicemeeter ...):
the loopback of the physical device carries the signal after the enhancer's gain, which is what the speakers emit.
Everything here is best-effort: if the `soundcard` library or the device is missing the assistant works as before.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_VIRTUAL_HINTS = ("fxsound", "voicemeeter", "vb-audio", "cable", "virtual", "equalizer apo", "enhancer")
_RATE = 16000
_BLOCK = 480          # 30 ms per read: fewer wake-ups (10 ms reads cost 7 % of a core while idle); the canceller re-frames to 10 ms


def pick_loopback(hint: str = ""):
    """The loopback microphone to read, or None. `hint` = part of a device name (AEC_LOOPBACK_DEVICE)."""
    import soundcard as sc

    loops = [m for m in sc.all_microphones(include_loopback=True) if getattr(m, "isloopback", False)]
    if not loops:
        return None
    hint = (hint or "").strip().lower()
    if hint:
        for m in loops:
            if hint in m.name.lower():
                return m
    try:
        default_name = sc.default_speaker().name
    except Exception:
        default_name = ""
    if default_name and any(h in default_name.lower() for h in _VIRTUAL_HINTS):
        for m in loops:
            if not any(h in m.name.lower() for h in _VIRTUAL_HINTS):
                return m
    for m in loops:
        if m.name == default_name:
            return m
    return loops[0]


class SystemAudioReference:
    """Background thread: loopback frames -> aec.feed_external_reference()."""

    def __init__(self, aec, device_hint: str = "") -> None:
        self._aec = aec
        self._hint = device_hint
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.device_name = ""
        self.frames = 0
        self.last_error = ""

    def start(self) -> bool:
        try:
            import soundcard  # noqa: F401
        except Exception as e:
            logger.info("system audio reference unavailable (soundcard missing: %s)", e)
            return False
        self._thread = threading.Thread(target=self._run, name="aec-system-reference", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                mic = pick_loopback(self._hint)
                if mic is None:
                    raise RuntimeError("no loopback device")
                self.device_name = mic.name
                print(f"[AEC] system-audio reference on '{mic.name}'")
                with mic.recorder(samplerate=_RATE, channels=1, blocksize=_BLOCK) as rec:
                    backoff = 1.0
                    while not self._stop.is_set():
                        # Blocks while nothing is playing: Windows delivers loopback data only while a stream renders.
                        data = rec.record(numframes=_BLOCK)
                        pcm = np.clip(data[:, 0] * 32767.0, -32768, 32767).astype(np.int16)
                        self.frames += 1
                        self._aec.feed_external_reference(pcm)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.debug("system audio reference stopped: %s", self.last_error)
                if self._stop.wait(backoff):
                    return
                backoff = min(30.0, backoff * 2)
