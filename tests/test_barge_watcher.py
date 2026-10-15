import os
import sys
import time
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import Config
from sara.audio.tts.duck import DuckController
from sara.orchestrator.barge_watch import SemanticBargeIn
from sara.orchestrator.tts_worker import TTSWorker


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class FakeVoice:
    def __init__(self, events):
        self.events = events
        self.speaking = True

    def is_speaking(self):
        return self.speaking

    def stop(self):
        self.events.append("stop")
        self.speaking = False

    def duck(self, on):
        self.events.append(("duck", on))

    def shutdown(self):
        pass


class FakeEars:
    def __init__(self, events, clock=None, speech_until=None, text="hmm", aec=True):
        self.events = events
        self.clock = clock
        self.speech_until = speech_until
        self.text = text
        self.calls = 0
        self.boosts = []
        self.raise_speaking = False
        if not aec:
            self._aec = None

    def is_user_speaking(self, duration=0.3):
        self.calls += 1
        if self.raise_speaking:
            raise RuntimeError("boom")
        if self.clock is None:
            return True
        return self.clock.t < self.speech_until

    def decode_barge_ring(self, max_ms=700):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text

    def capture_barge_in_audio(self):
        self.events.append("capture")

    def set_barge_boost(self, v):
        self.boosts.append(v)


CFG = dict(
    VOICE_DUCKING=True, BARGE_COLLECT_MS=550, BARGE_AMBIGUOUS_MS=800,
    BARGE_NOAEC_MIN_SPEECH_MS=300, BARGE_NOAEC_THRESHOLD_BOOST=1.5,
)


def make(text="hmm", until=0.5, aec=True, ducking=True, classify=None):
    events, clock, infos = [], Clock(), []
    voice = FakeVoice(events)
    ears = FakeEars(events, clock, until, text, aec)

    def hard_stop():
        voice.stop()
        ears.capture_barge_in_audio()

    kw = {"classify": classify} if classify else {}
    cfg = SimpleNamespace(**{**CFG, "VOICE_DUCKING": ducking})
    sem = SemanticBargeIn(
        voice, ears, hard_stop, infos.append,
        clock=clock.now, sleeper=clock.sleep, cfg=cfg, **kw,
    )
    return sem, events, clock, infos, ears, voice


def test_backchannel_ducks_restores_no_stop_and_cooldown():
    sem, events, clock, _infos, ears, _v = make(text="hmm")
    assert sem.step() is True
    assert events == [("duck", True), ("duck", False)]
    calls = ears.calls
    clock.t += 1.0
    assert sem.step() is True
    assert ears.calls == calls
    clock.t += 1.5
    ears.speech_until = clock.t + 0.2
    sem.step()
    assert ears.calls > calls


def test_interrupt_unducks_then_legacy_stop_sequence():
    sem, events, _c, infos, _e, _v = make(text="wait what time is it", until=5)
    sem.step()
    assert events == [("duck", True), ("duck", False), "stop", "capture"]
    assert set(infos[0]) == {"at", "verdict", "chars_spoken"}
    assert infos[0]["verdict"] == "interrupt" and infos[0]["chars_spoken"] is None


def test_ducking_off_has_no_duck_calls():
    sem, events, *_ = make(text="stop", ducking=False)
    sem.step()
    assert events == ["stop", "capture"]


def test_ambiguous_long_is_interrupt():
    sem, events, *_ = make(text="banana", until=5)
    sem.step()
    assert "stop" in events


def test_decode_none_short_is_backchannel_long_is_interrupt():
    sem, events, *_ = make(text=None, until=0.1)
    sem.step()
    assert "stop" not in events
    sem, events, *_ = make(text=None, until=5)
    sem.step()
    assert "stop" in events


def test_decode_exception_treated_as_none():
    sem, events, *_ = make(text=RuntimeError("x"), until=0.1)
    sem.step()
    assert "stop" not in events


def test_classifier_exception_falls_back_to_legacy_stop():
    def bad(*a, **k):
        raise RuntimeError("x")

    sem, events, _c, infos, *_ = make(classify=bad, until=5)
    assert sem.step() is True
    assert events == [("duck", True), ("duck", False), "stop", "capture"]
    assert infos[0]["verdict"] == "fallback"


def test_exception_before_detection_defers_to_legacy_check():
    sem, events, _c, _i, ears, _v = make()
    ears.raise_speaking = True
    assert sem.step() is False
    assert events == []


def test_no_aec_requires_min_speech_and_boost_not_persistent():
    before = Config.BARGE_NOAEC_THRESHOLD_BOOST if hasattr(Config, "BARGE_NOAEC_THRESHOLD_BOOST") else None
    sem, events, clock, _i, ears, _v = make(text="stop", until=5, aec=False)
    for t in (0.0, 0.15):
        clock.t = t
        assert sem.step() is True
        assert "stop" not in events
    clock.t = 0.35
    sem.step()
    assert "stop" in events
    assert set(ears.boosts) == {1.5, 1.0} and ears.boosts[-1] == 1.0
    assert (Config.BARGE_NOAEC_THRESHOLD_BOOST if hasattr(Config, "BARGE_NOAEC_THRESHOLD_BOOST") else None) == before


def test_no_aec_short_blip_never_triggers():
    sem, events, clock, *_ = make(text="stop", until=0.1, aec=False)
    for t in (0.0, 0.15, 0.4):
        clock.t = t
        sem.step()
    assert events == []


class FakeTimer:
    instances = []

    def __init__(self, interval, fn):
        self.fn, self.started, self.cancelled, self.daemon = fn, False, False, False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def test_deadman_timer_releases_stuck_duck():
    FakeTimer.instances.clear()
    calls = []
    player = SimpleNamespace(set_duck_gain=lambda db, ms: calls.append((db, ms)))
    cfg = SimpleNamespace(VOICE_DUCK_DB=-12.0, VOICE_DUCK_RAMP_MS=40)
    ctl = DuckController(lambda: player, timer_factory=FakeTimer, cfg=cfg)
    ctl.duck(True)
    assert calls == [(-12.0, 40)] and FakeTimer.instances[0].started
    FakeTimer.instances[0].fn()
    assert calls[-1] == (0.0, 40)
    ctl.duck(True)
    ctl.duck(False)
    assert FakeTimer.instances[-1].cancelled and calls[-1] == (0.0, 40)


def test_duck_without_player_is_noop():
    DuckController(lambda: None).duck(True)


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _worker(monkeypatch, semantic, text="stop"):
    monkeypatch.setattr(Config, "BARGE_IN_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "VOICE_SEMANTIC_BARGE_IN", semantic, raising=False)
    monkeypatch.setattr(Config, "VOICE_DUCKING", True, raising=False)
    events = []
    voice, ears = FakeVoice(events), FakeEars(events, text=text)
    w = TTSWorker(voice, ears)
    w._speech_started_at = 0.0
    w._barge_stop.clear()
    w._speaking.set()
    return w, events, ears


def test_worker_flag_off_is_legacy_hard_stop(monkeypatch):
    w, events, ears = _worker(monkeypatch, False)
    assert _wait(w._barge_stop.is_set)
    w.shutdown()
    assert events == ["stop", "capture"]
    assert ears.boosts == [] and w.last_interrupt_info is None


def test_worker_flag_on_interrupt_records_info(monkeypatch):
    w, events, _ears = _worker(monkeypatch, True, text="stop")
    assert _wait(w._barge_stop.is_set)
    w.shutdown()
    assert events[:4] == [("duck", True), ("duck", False), "stop", "capture"]
    assert w.last_interrupt_info["verdict"] == "interrupt"


def test_worker_releases_duck_on_shutdown(monkeypatch):
    w, events, _ears = _worker(monkeypatch, True)
    w._speaking.clear()
    sem = SemanticBargeIn(w._voice, w._ears, w._hard_stop, cfg=Config)
    sem._duck(True)
    w._semantic = sem
    events.clear()
    w.shutdown()
    assert ("duck", False) in events