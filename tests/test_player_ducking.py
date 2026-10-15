import math
import os
import sys
import threading
import types

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sara.audio.tts import player as pm

FRAMES = pm._BLOCK_SIZE


@pytest.fixture
def player(monkeypatch):
    monkeypatch.setattr(pm, "_SD_OK", False)
    p = pm._PersistentPlayer()
    yield p
    p.close()


def _feed(p, value=1000, blocks=40):
    for _ in range(blocks):
        p._chunk_q.put(np.full(FRAMES, value, dtype=np.int16))


def _run(p, n):
    out = None
    for _ in range(n):
        out = np.zeros((FRAMES, 1), dtype=np.int16)
        p._callback(out, FRAMES, None, None)
    return out[:, 0]


def test_no_duck_is_byte_identical_and_fast_path(player, monkeypatch):
    rng = np.random.default_rng(1)
    pcm = rng.integers(-20000, 20000, size=FRAMES * 2, dtype=np.int16)
    player._chunk_q.put(pcm)
    monkeypatch.setattr(player, "_apply_duck", lambda *a: (_ for _ in ()).throw(AssertionError))
    first = _run(player, 1)
    second = _run(player, 1)
    assert np.array_equal(first, pcm[:FRAMES])
    assert np.array_equal(second, pcm[FRAMES:])
    assert player.duck_active is False


def test_gain_ramps_to_target_within_ramp(player):
    _feed(player)
    ramp_ms = 200
    player.set_duck_gain(-12.0, ramp_ms)
    blocks = max(1.0, ramp_ms / 1000.0 * pm._SAMPLE_RATE / FRAMES)
    target = 10 ** (-12.0 / 20.0)
    assert player.duck_active is True
    if blocks > 1:
        _run(player, int(blocks) - 1)
        assert player._duck_gain > target
    _run(player, int(math.ceil(blocks)) + 1)
    assert player._duck_gain == pytest.approx(target)
    out = _run(player, 1)
    assert abs(int(out[0]) - int(1000 * target)) <= 1


def test_unduck_ramps_back_and_returns_to_legacy(player):
    _feed(player)
    player.set_duck_gain(-12.0, 40)
    _run(player, 3)
    player.set_duck_gain(0.0, 200)
    assert player.duck_active is False
    _run(player, 8)
    assert player._duck_gain == 1.0
    assert np.all(_run(player, 1) == 1000)


def test_target_writes_are_thread_safe(player):
    _feed(player, blocks=200)
    errors = []

    def writer(seed):
        try:
            rng = np.random.default_rng(seed)
            for _ in range(500):
                player.set_duck_gain(float(-rng.integers(0, 30)), int(rng.integers(1, 300)))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for _ in range(150):
        _run(player, 1)
    for t in threads:
        t.join()
    assert not errors
    assert 0.0 < player._duck_gain <= 1.0


def test_invalid_values_ignored(player):
    player.set_duck_gain(float("nan"), 40)
    player.set_duck_gain("x", 40)
    assert player.duck_active is False


def test_clear_releases_duck(player):
    player.set_duck_gain(-12.0, 40)
    player.clear()
    assert player.duck_active is False


def test_pygame_fallback_ignores_duck(player, monkeypatch):
    captured = {}
    sound = types.SimpleNamespace(play=lambda: None)

    def make_sound(arr):
        captured["arr"] = arr
        return sound

    fake = types.SimpleNamespace(sndarray=types.SimpleNamespace(make_sound=make_sound))
    monkeypatch.setattr(pm, "_PG_OK", True)
    monkeypatch.setattr(pm, "pygame", fake)
    pcm = np.full(100, 1000, dtype=np.int16)
    player.set_duck_gain(-12.0, 40)
    assert player._pygame_play_and_wait(pcm, threading.Event()) is False
    assert np.array_equal(captured["arr"], np.column_stack([pcm, pcm]))