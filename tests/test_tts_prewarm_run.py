"""Tests for start_prewarm / the prewarm worker (fakes only)."""
import re
import threading
import time

import numpy as np
import pytest

from sara.audio.tts import cache as cache_mod
from sara.audio.tts import prewarm
from sara.audio.voice_flags import reset_flags, set_flag

try:
    from tests.tts_fakes import Synth, make_tts, run
except ImportError:  # tests/ is not a package here
    from tts_fakes import Synth, make_tts, run


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()
    monkeypatch.setattr(prewarm, "_ack_phrases", lambda: ("On it!", "Sure thing!", "Ek second..."))
    yield
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()


@pytest.fixture
def synth(monkeypatch):
    fake = Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", fake)
    return fake


# ── start_prewarm ────────────────────────────────────────────────────────────

def test_flag_off_returns_none_and_never_synthesises(synth):
    set_flag("VOICE_ACK_CACHE", False)
    assert prewarm.start_prewarm(make_tts()) is None
    assert synth.calls == []


def test_disabled_or_modelless_tts_is_a_noop(synth):
    tts = make_tts()
    tts._disabled = True
    assert prewarm.start_prewarm(tts) is None
    tts = make_tts()
    tts._kokoro = None
    assert prewarm.start_prewarm(tts) is None
    assert prewarm.start_prewarm(object()) is None
    assert synth.calls == []


def test_runs_only_after_warmup_event(synth):
    tts = make_tts()
    tts._warmup_done = threading.Event()
    thread = prewarm.start_prewarm(tts, sleeper=lambda s: None)
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    time.sleep(0.3)
    assert synth.calls == []
    tts._warmup_done.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(synth.calls) == len(prewarm._plan())


def test_start_twice_returns_same_running_thread(synth):
    tts = make_tts()
    tts._warmup_done = threading.Event()
    first = prewarm.start_prewarm(tts, sleeper=lambda s: None)
    assert prewarm.start_prewarm(tts, sleeper=lambda s: None) is first
    tts._shutdown_done = True
    first.join(timeout=10)
    assert not first.is_alive()


def test_gives_up_when_warmup_never_finishes(synth, capsys):
    tts = make_tts()
    tts._warmup_done = threading.Event()
    jumps = iter([0.0, 100.0, 200.0, 300.0])
    prewarm._run(tts, lambda s: None, lambda: next(jumps, 400.0))
    assert synth.calls == []
    assert "prewarmed 0 phrases" in capsys.readouterr().out


def test_yields_while_speaking(synth):
    tts = make_tts()
    tts._speaking.set()
    seen_during_speech = []
    sleeps = {"n": 0}

    def sleeper(seconds):
        if tts._speaking.is_set():
            seen_during_speech.append(len(synth.calls))
            sleeps["n"] += 1
            if sleeps["n"] == 4:
                tts._speaking.clear()

    run(tts, sleeper)
    assert seen_during_speech == [0, 0, 0, 0]
    assert len(synth.calls) == len(prewarm._plan())


def test_pauses_again_when_speech_starts_midrun(synth):
    tts = make_tts()
    state = {"paused_calls": None, "sleeps": 0}

    def on_call(n):
        if n == 3:
            tts._speaking.set()

    synth.on_call = on_call

    def sleeper(seconds):
        if tts._speaking.is_set():
            state["sleeps"] += 1
            if state["paused_calls"] is None:
                state["paused_calls"] = len(synth.calls)
            if state["sleeps"] == 3:
                tts._speaking.clear()

    run(tts, sleeper)
    assert state["paused_calls"] == 3
    assert len(synth.calls) == len(prewarm._plan())


def test_gap_between_phrases_is_20_to_50_ms(synth):
    sleeps = []
    run(make_tts(), sleeps.append)
    assert sleeps
    assert all(0.02 <= s <= 0.05 for s in sleeps)


def test_aborts_on_shutdown(synth):
    tts = make_tts()

    def on_call(n):
        if n == 5:
            tts._shutdown_done = True

    synth.on_call = on_call
    run(tts)
    assert len(synth.calls) == 5


def test_aborts_when_flag_switched_off(synth):
    tts = make_tts()

    def on_call(n):
        if n == 4:
            set_flag("VOICE_ACK_CACHE", False)

    synth.on_call = on_call
    run(tts)
    assert len(synth.calls) == 4


def test_one_failing_phrase_does_not_stop_the_rest(monkeypatch, capsys):
    tts = make_tts()
    fake = Synth(fail_on={2})
    monkeypatch.setattr(prewarm, "_synth_kokoro", fake)
    run(tts)
    plan = prewarm._plan()
    assert len(fake.calls) == len(plan)
    cached = [k for k in (prewarm.cache_key_for(tts, t, l, f) for t, l, f in plan)
              if cache_mod._phrase_cache_get(k) is not None]
    assert len(cached) == len(plan) - 1
    assert f"({1} skipped)" in capsys.readouterr().out


def test_empty_audio_is_skipped_not_cached(monkeypatch):
    tts = make_tts()
    monkeypatch.setattr(prewarm, "_synth_kokoro", lambda *a: np.zeros(0, dtype=np.float32))
    run(tts)
    assert cache_mod._phrase_cache == {}


def test_speed_change_during_synthesis_discards_the_result(monkeypatch):
    tts = make_tts()
    fake = Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", fake)
    fake.on_call = lambda n: tts.set_speed(1.3) if n == 1 else None
    run(tts)
    first = prewarm._plan()[0]
    stale_key = (first[0], first[1], first[2], None)
    assert cache_mod._phrase_cache_get(stale_key) is None


def test_never_takes_the_speak_lock(synth):
    tts = make_tts()  # _lock raises on any use
    run(tts)
    plan = prewarm._plan()
    assert len(synth.calls) == len(plan)
    assert all(
        cache_mod._phrase_cache_get(prewarm.cache_key_for(tts, t, l, f)) is not None
        for t, l, f in plan
    )


def test_no_exception_escapes_the_thread(monkeypatch):
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args))
    tts = make_tts()
    monkeypatch.setattr(prewarm, "_synth_kokoro", Synth(fail_on=set(range(1, 500))))
    thread = prewarm.start_prewarm(tts, sleeper=lambda s: None)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    monkeypatch.setattr(prewarm, "_plan", lambda cfg=None: (_ for _ in ()).throw(KeyError("x")))
    tts2 = make_tts()
    thread2 = prewarm.start_prewarm(tts2, sleeper=lambda s: None)
    thread2.join(timeout=10)
    assert errors == []


def test_start_prewarm_swallows_internal_errors(monkeypatch, capsys):
    class Exploding:
        _disabled = False
        _kokoro = object()

        def __setattr__(self, name, value):
            raise RuntimeError("no attributes allowed")

    assert prewarm.start_prewarm(Exploding()) is None
    assert "prewarm could not start" in capsys.readouterr().out


def test_log_line_has_counts_and_timings_only(synth, capsys):
    run(make_tts())
    out = capsys.readouterr().out
    assert len(re.findall(r"\[TTS\] prewarmed \d+ phrases in \d+\.\d s \(\d+ skipped\)", out)) == 1
    assert out.count("\n") == 1
    for text, _, _ in prewarm._plan():
        assert text not in out


def test_prewarm_pins_its_entries(synth):
    tts = make_tts()
    run(tts)
    assert len(cache_mod._pinned) == len(prewarm._plan())


def test_existing_cache_entry_is_not_resynthesised_but_pinned(synth):
    tts = make_tts()
    first = prewarm._plan()[0]
    key = (first[0], first[1], first[2], None)
    cache_mod._phrase_cache_put(key, np.ones(10, dtype=np.float32))
    run(tts)
    assert len(synth.calls) == len(prewarm._plan()) - 1
    assert key in cache_mod._pinned