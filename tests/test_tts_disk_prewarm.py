"""Tests for disk_cache.cache_dir() and the prewarm disk integration (temp dirs only)."""
import threading

import numpy as np
import pytest

import config as config_module
from sara.audio.tts import cache as cache_mod
from sara.audio.tts import disk_cache as disk
from sara.audio.tts import engine, prewarm
from sara.audio.voice_flags import reset_flags, set_flag


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()
    monkeypatch.setattr(prewarm, "_ack_phrases", lambda: ("On it!", "Sure thing!"))
    yield
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()


# ── cache_dir ────────────────────────────────────────────────────────────────

def test_cache_dir_uses_app_data_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "app_data_subdir", lambda name: tmp_path / name, raising=False)
    path = disk.cache_dir()
    assert path == tmp_path / "data" / "tts_cache"
    assert path.is_dir()


def test_cache_dir_failure_returns_none(monkeypatch):
    def boom(name):
        raise OSError("read-only")

    monkeypatch.setattr(config_module, "app_data_subdir", boom, raising=False)
    assert disk.cache_dir() is None


# ── prewarm integration ──────────────────────────────────────────────────────

class _Synth:
    def __init__(self):
        self.calls = 0

    def __call__(self, text, kokoro, params, lock):
        self.calls += 1
        return np.full(240, 0.25, dtype=np.float32)


def _make_tts():
    tts = engine.TextToSpeech.__new__(engine.TextToSpeech)
    tts._shutdown_done = False
    tts._speaking = threading.Event()
    tts._synth_lock = threading.Lock()
    tts._speed_lock = threading.Lock()
    tts._speed_override = None
    tts._disabled = False
    tts._kokoro = object()
    tts._warmup_done = threading.Event()
    tts._warmup_done.set()
    return tts


def _run(tts):
    ticks = iter(range(10**6))
    prewarm._run(tts, lambda s: None, lambda: next(ticks) * 0.05)


def test_disabled_flag_writes_nothing(tmp_path, monkeypatch):
    synth = _Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", synth)

    def must_not_be_called():
        raise AssertionError("disk cache touched while flag is off")

    monkeypatch.setattr(prewarm._disk, "cache_dir", must_not_be_called)
    set_flag("VOICE_ACK_CACHE_DISK", False)
    _run(_make_tts())
    assert synth.calls > 0
    assert list(tmp_path.iterdir()) == []


def test_enabled_flag_persists_and_second_run_loads_from_disk(tmp_path, monkeypatch, capsys):
    synth = _Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", synth)
    monkeypatch.setattr(prewarm._disk, "cache_dir", lambda: tmp_path)
    set_flag("VOICE_ACK_CACHE_DISK", True)
    _run(_make_tts())
    first_calls = synth.calls
    files = list(tmp_path.glob("*.npy"))
    assert first_calls > 0 and len(files) == first_calls
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()
    capsys.readouterr()
    _run(_make_tts())
    assert synth.calls == first_calls  # nothing synthesised the second time
    assert len(cache_mod._phrase_cache) == first_calls
    assert f"disk cache: {first_calls} loaded" in capsys.readouterr().out


def test_changed_speed_resynthesises_instead_of_loading(tmp_path, monkeypatch):
    synth = _Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", synth)
    monkeypatch.setattr(prewarm._disk, "cache_dir", lambda: tmp_path)
    set_flag("VOICE_ACK_CACHE_DISK", True)
    _run(_make_tts())
    first_calls = synth.calls
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    tts = _make_tts()
    tts._speed_override = 1.3
    _run(tts)
    assert synth.calls == first_calls * 2


def test_corrupt_disk_entry_is_resynthesised(tmp_path, monkeypatch):
    synth = _Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", synth)
    monkeypatch.setattr(prewarm._disk, "cache_dir", lambda: tmp_path)
    set_flag("VOICE_ACK_CACHE_DISK", True)
    _run(_make_tts())
    first_calls = synth.calls
    victim = next(tmp_path.glob("*.npy"))
    victim.write_bytes(b"garbage")
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    _run(_make_tts())
    assert synth.calls == first_calls + 1
    assert disk.load(tmp_path, victim.stem) is not None


def test_unavailable_cache_dir_falls_back_to_memory_only(monkeypatch):
    synth = _Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", synth)
    monkeypatch.setattr(prewarm._disk, "cache_dir", lambda: None)
    set_flag("VOICE_ACK_CACHE_DISK", True)
    _run(_make_tts())
    assert synth.calls > 0
    assert len(cache_mod._phrase_cache) == synth.calls