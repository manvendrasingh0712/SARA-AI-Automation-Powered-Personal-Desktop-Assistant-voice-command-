import sys
import time
import types

import numpy as np
import pytest

from config import Config
from sara.audio import aec as aec_mod
from sara.audio import system_reference as sysref


class _FakeAP:
    def __init__(self):
        self.reverse = []
        self.stream = []

    def process_reverse_stream(self, frame):
        self.reverse.append(frame)

    def process_stream(self, frame):
        self.stream.append(frame)
        return frame


@pytest.fixture
def proc(monkeypatch):
    monkeypatch.setattr(Config, "AEC_SYSTEM_REFERENCE", False, raising=False)
    monkeypatch.setattr(Config, "AEC_EXT_MIC_DELAY_MS", 80, raising=False)
    monkeypatch.setattr(Config, "AEC_EXT_MIN_RMS", 25, raising=False)
    p = aec_mod.AECProcessor()
    if not p.enabled:
        pytest.skip("aec-audio-processing is not available")
    p._ap = _FakeAP()
    p._reverse_method_name = "process_reverse_stream"
    return p


def _tone(n=160, amp=3000):
    return (np.sin(np.arange(n) / 5.0) * amp).astype(np.int16)


def test_loud_system_audio_becomes_the_reference(proc):
    assert not proc.external_active()
    proc.feed_external_reference(_tone(480))
    assert proc.external_active()
    assert len(proc._ap.reverse) == 3          # 480 samples re-framed to three 10 ms frames


def test_silence_alone_does_not_switch_it_on(proc):
    proc.feed_external_reference(np.zeros(480, np.int16))
    assert not proc.external_active() and proc._ap.reverse == []


def test_saras_own_speech_keeps_priority_over_the_loopback(proc):
    proc.feed_far_end(_tone(160), 16000)               # TTS path, as before
    n_tts = len(proc._ap.reverse)
    proc.feed_external_reference(_tone(480))           # the mix contains the same speech: must be ignored
    assert len(proc._ap.reverse) == n_tts
    assert not proc.external_active()


def test_reference_stays_on_through_short_pauses(proc):
    proc.feed_external_reference(_tone(160))
    proc._ext_last_energy = time.monotonic() - 1.5      # a 1.5 s hole in the loopback (Windows resumes late)
    assert proc.external_active()
    proc._ext_last_energy = time.monotonic() - 2.5
    assert not proc.external_active()


def test_microphone_is_delayed_only_while_other_audio_plays(proc):
    frame = lambda k: bytes([k]) * 320
    # no external audio: pass-through, no delay
    out = proc.process_near_end(frame(1))
    assert out == frame(1)
    # external audio on: eight frames of silence come first, then the real audio
    proc.feed_external_reference(_tone(160))
    out = proc.process_near_end(frame(2) * 3)
    assert out[:320] == bytes(320) and frame(2) not in [out[i:i + 320] for i in range(0, 960, 320)][:3]
    # other audio stops: the delay drains (two frames out per frame in) and then disappears
    proc._ext_last_energy = time.monotonic() - 5
    total_out = 0
    for _ in range(12):
        total_out += len(proc.process_near_end(frame(3)))
    assert not proc._dline
    assert total_out >= 12 * 320                                   # nothing was dropped
    assert proc.process_near_end(frame(4)) == frame(4)


# ---------------------------------------------------------------- device choice
class _Dev:
    def __init__(self, name, loop=True):
        self.name, self.isloopback = name, loop


def _fake_soundcard(monkeypatch, default, names):
    mod = types.ModuleType("soundcard")
    mod.all_microphones = lambda include_loopback=False: [_Dev(n) for n in names]
    mod.default_speaker = lambda: types.SimpleNamespace(name=default)
    monkeypatch.setitem(sys.modules, "soundcard", mod)


def test_physical_device_is_preferred_over_a_virtual_enhancer(monkeypatch):
    names = ["Speakers (FxSound Audio Enhancer)", "Speakers (Realtek(R) Audio)"]
    _fake_soundcard(monkeypatch, names[0], names)
    assert sysref.pick_loopback().name == "Speakers (Realtek(R) Audio)"


def test_default_device_is_used_when_it_is_physical(monkeypatch):
    names = ["Headphones (USB)", "Speakers (Realtek(R) Audio)"]
    _fake_soundcard(monkeypatch, names[0], names)
    assert sysref.pick_loopback().name == "Headphones (USB)"


def test_explicit_device_name_wins(monkeypatch):
    names = ["Speakers (FxSound Audio Enhancer)", "Speakers (Realtek(R) Audio)"]
    _fake_soundcard(monkeypatch, names[0], names)
    assert sysref.pick_loopback("fxsound").name == names[0]
