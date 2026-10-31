import sys
import types

import pytest

from sara.audio import ducking
from sara.audio.stt import engine as stt_engine


# ------------------------------------------------------------------ ducking (fake Core Audio sessions)
class _Vol:
    def __init__(self, v):
        self.v = v

    def GetMasterVolume(self):
        return self.v

    def SetMasterVolume(self, v, _ctx):
        self.v = v


class _Sess:
    def __init__(self, pid, name, state, vol, ident="x"):
        self.ProcessId = pid
        self.State = state
        self.SimpleAudioVolume = _Vol(vol)
        self.Identifier = f"{ident}{pid}"
        self.Process = types.SimpleNamespace(name=lambda: name)


@pytest.fixture
def fake_audio(monkeypatch, tmp_path):
    sessions = [_Sess(111, "spotify.exe", 1, 0.8), _Sess(222, "msedge.exe", 1, 1.0),
                _Sess(333, "idle.exe", 0, 0.5), _Sess(0, "system", 1, 1.0), _Sess(999, "sara.exe", 1, 1.0)]
    mod = types.ModuleType("pycaw.pycaw")
    mod.AudioUtilities = types.SimpleNamespace(GetAllSessions=lambda: sessions)
    monkeypatch.setitem(sys.modules, "pycaw", types.ModuleType("pycaw"))
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", mod)
    monkeypatch.setattr(ducking, "_own_pids", lambda: {999})
    monkeypatch.setattr(ducking, "_STATE_FILE", tmp_path / "duck_state.json")
    return sessions


def test_duck_lowers_only_playing_foreign_sessions_and_restore_puts_them_back(fake_audio):
    d = ducking.AudioDucker(level=0.2)
    assert d.duck() == 2
    vols = {s.ProcessId: s.SimpleAudioVolume.v for s in fake_audio}
    assert vols[111] == pytest.approx(0.16) and vols[222] == pytest.approx(0.2)
    assert vols[333] == 0.5 and vols[0] == 1.0 and vols[999] == 1.0     # idle, system, SARA itself untouched
    assert ducking._state_file().exists()                                 # recoverable after a crash
    assert d.restore() == 2
    assert {s.ProcessId: s.SimpleAudioVolume.v for s in fake_audio}[111] == 0.8
    assert not ducking._state_file().exists()


def test_restore_leaves_a_volume_the_user_changed_meanwhile(fake_audio):
    d = ducking.AudioDucker(level=0.2)
    d.duck()
    fake_audio[0].SimpleAudioVolume.v = 0.55         # user moved the slider while we were listening
    d.restore()
    assert fake_audio[0].SimpleAudioVolume.v == 0.55
    assert fake_audio[1].SimpleAudioVolume.v == 1.0


def test_crash_recovery_restores_saved_volumes(fake_audio):
    d = ducking.AudioDucker(level=0.2)
    d.duck()
    d._saved_copy = dict(d._saved)
    d._saved = {}                                     # simulate: process died, nothing in memory
    assert ducking.recover_after_crash() == 2
    assert fake_audio[1].SimpleAudioVolume.v == 1.0


def test_duck_twice_is_idempotent(fake_audio):
    d = ducking.AudioDucker(level=0.5)
    assert d.duck() == 2
    assert d.duck() == 0
    d.restore()
    assert fake_audio[0].SimpleAudioVolume.v == 0.8


# ------------------------------------------------------------------ guards around the Hinglish model
class _Seg:
    def __init__(self, text, lp=-0.3, nsp=0.0):
        self.text, self.avg_logprob, self.no_speech_prob = text, lp, nsp


def _stub(segs):
    calls = []

    class _Model:
        def transcribe(self, *_a, **_k):
            calls.append(1)
            return iter(segs), None

    return types.SimpleNamespace(_hinglish_model=_Model()), calls


_INFO = types.SimpleNamespace(language="en", language_probability=0.4)


def test_nothing_heard_never_runs_the_hinglish_model():
    stub, calls = _stub([_Seg("kahaan kahaan kahaan")])
    out = stt_engine.SpeechToText._maybe_use_hinglish(stub, None, "", [], _INFO, 2, 0.6)
    assert out == ("", []) and calls == []


def test_looped_hinglish_output_is_rejected_in_favour_of_the_main_text():
    stub, calls = _stub([_Seg("kahaan kahaan kahaan kahaan kahaan kahaan")])
    main_seg = _Seg("maybe a sentence", lp=-0.9)
    text, segs = stt_engine.SpeechToText._maybe_use_hinglish(stub, None, "maybe a sentence", [main_seg], _INFO, 2, 0.6)
    assert calls == [1] and text == "maybe a sentence" and segs == [main_seg]


def test_unsure_hinglish_output_is_rejected():
    stub, _ = _stub([_Seg("bahut kuch", lp=-1.4)])
    main_seg = _Seg("some words", lp=-0.9)
    text, _ = stt_engine.SpeechToText._maybe_use_hinglish(stub, None, "some words", [main_seg], _INFO, 2, 0.6)
    assert text == "some words"


def test_good_hinglish_output_replaces_an_unsure_main_text():
    good = _Seg("Chrome kholo.", lp=-0.2)
    stub, _ = _stub([good])
    main_seg = _Seg("Drum kolu.", lp=-1.2)
    text, segs = stt_engine.SpeechToText._maybe_use_hinglish(stub, None, "Drum kolu.", [main_seg], _INFO, 2, 0.6)
    assert text == "Chrome kholo." and segs == [good]


def test_debug_dump_saves_wav_and_index(tmp_path, monkeypatch):
    import json

    import numpy as np

    import config

    monkeypatch.setattr(config, "app_data_subdir", lambda name: tmp_path)
    stub = types.SimpleNamespace(SAMPLE_RATE=16000, energy_threshold=278.0)
    audio = (np.sin(np.arange(16000) / 20.0) * 0.3).astype(np.float32)
    stt_engine.SpeechToText._dump_utterance(stub, audio, "open chrome", 0.9)
    root = tmp_path / "stt_dumps"
    assert len(list(root.glob("*.wav"))) == 1
    rec = json.loads((root / "index.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["text"] == "open chrome" and rec["seconds"] == 1.0


def test_gpu_free_mb_never_raises(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError("no nvidia-smi")))
    assert stt_engine.SpeechToText._gpu_free_mb() == 0
