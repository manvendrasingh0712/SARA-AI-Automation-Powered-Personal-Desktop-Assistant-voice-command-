"""Shared fakes for the TTS prewarm tests (no hardware, no model files)."""
import threading
from types import SimpleNamespace

import numpy as np

from sara.audio.tts import engine, prewarm


class RaisingLock:
    """Stands in for the speak lock: any use is a test failure."""

    def __enter__(self):
        raise AssertionError("prewarm must never take the speak lock")

    def __exit__(self, *exc):
        return False

    def acquire(self, *a, **k):
        raise AssertionError("prewarm must never take the speak lock")


class FakePlayer:
    def __init__(self):
        self.played = []

    def play_and_wait(self, pcm, stop, volume):
        self.played.append(np.array(pcm))

    def clear(self):
        self.cleared = getattr(self, "cleared", 0) + 1


class Synth:
    """Deterministic fake for _synth_kokoro; records (text, voice, speed)."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)
        self.on_call = None

    def __call__(self, text, kokoro, params, lock):
        self.calls.append((text, params.voice, params.speed))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        if len(self.calls) in self.fail_on:
            raise RuntimeError("synth exploded")
        return np.full(240, len(self.calls) % 100 + 1, dtype=np.float32)


def make_tts():
    tts = engine.TextToSpeech.__new__(engine.TextToSpeech)
    tts._shutdown_lock = threading.Lock()
    tts._shutdown_done = False
    tts._stop = threading.Event()
    tts._speaking = threading.Event()
    tts._interrupted = threading.Event()
    tts._lock = RaisingLock()
    tts._synth_lock = threading.Lock()
    tts._speed_lock = threading.Lock()
    tts._speed_override = None
    tts._forced_lang = None
    tts._disabled = False
    tts._kokoro = object()
    tts._volume = 1.0
    tts._warmup_done = threading.Event()
    tts._warmup_done.set()
    tts._player = FakePlayer()
    return tts


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.05
        return self.now


def cfg(**over):
    base = dict(
        TTS_PHRASE_CACHE_MAXLEN=40,
        VOICE_PREWARM_MAX_PHRASES=64,
        WAKE_ACK_PHRASE="Yes?",
    )
    base.update(over)
    return SimpleNamespace(**base)


def run(tts, sleeper=lambda s: None):
    prewarm._run(tts, sleeper, Clock())