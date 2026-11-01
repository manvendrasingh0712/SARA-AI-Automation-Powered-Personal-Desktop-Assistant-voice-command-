"""Tests for prewarm.phrases() and cache-key equality with speak()."""
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from sara.audio.tts import cache as cache_mod
from sara.audio.tts import engine, prewarm
from sara.audio.tts.text_prep import clean_for_tts
from sara.audio.tts.voice_params import _detect_lang
from sara.audio.voice_flags import reset_flags, set_flag

try:
    from tests.tts_fakes import Synth, cfg, make_tts, run
except ImportError:  # tests/ is not a package here
    from tts_fakes import Synth, cfg, make_tts, run


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    reset_flags()
    set_flag("VOICE_ACK_CACHE_DISK", False)      # count real syntheses; never read the user's on-disk phrase cache
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


# ── phrases() ────────────────────────────────────────────────────────────────

def test_phrases_respect_cache_max_length():
    pairs = prewarm.phrases(cfg(TTS_PHRASE_CACHE_MAXLEN=12))
    assert pairs
    assert all(len(clean_for_tts(t)) <= 12 for t, _ in pairs)
    assert all(t != "Sorry, something went wrong." for t, _ in pairs)


def test_phrases_are_deduplicated_after_cleaning():
    pairs = prewarm.phrases(cfg(WAKE_ACK_PHRASE="  Done.  "))
    texts = [t for t, _ in pairs]
    assert len(texts) == len(set(texts))
    assert texts.count("Done.") == 1


def test_phrases_are_capped_and_cap_is_clamped():
    assert len(prewarm.phrases(cfg(VOICE_PREWARM_MAX_PHRASES=8))) <= 8
    assert len(prewarm._plan(cfg(VOICE_PREWARM_MAX_PHRASES=8))) == 8
    assert len(prewarm._plan(cfg(VOICE_PREWARM_MAX_PHRASES=1))) == 8
    assert len(prewarm._plan(cfg(VOICE_PREWARM_MAX_PHRASES=99999))) <= 256


def test_phrases_priority_order_and_content():
    pairs = prewarm.phrases(cfg())
    texts = [t for t, _ in pairs]
    assert texts[:3] == ["On it!", "Sure thing!", "Ek second..."]
    assert "Yes?" in texts and "Done." in texts
    assert "ठीक है।" in texts and "रद्द कर दिया।" in texts
    assert dict(pairs)["ठीक है।"] == _detect_lang("ठीक है।")


def test_phrases_ack_variants_are_fast_only():
    plan = prewarm._plan(cfg())
    assert ("On it!", _detect_lang("On it!"), True) in plan
    assert not any(t == "On it!" and not fast for t, _, fast in plan)
    assert any(t == "Done." and not fast for t, _, fast in plan)
    assert any(t == "Done." and fast for t, _, fast in plan)


def test_phrases_survive_missing_command_helpers(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setitem(sys.modules, "sara.orchestrator.command_helpers", None)
    pairs = prewarm.phrases(cfg())
    texts = [t for t, _ in pairs]
    assert "Done." in texts and "Yes?" in texts


def test_phrases_use_real_ack_tuple_when_available(monkeypatch):
    monkeypatch.undo()
    fake = types.ModuleType("sara.orchestrator.command_helpers")
    fake._ACK_PHRASES = ("Right away!", "Working on it!")
    monkeypatch.setitem(sys.modules, "sara.orchestrator.command_helpers", fake)
    texts = [t for t, _ in prewarm.phrases(cfg())]
    assert texts[:2] == ["Right away!", "Working on it!"]


def test_phrases_never_raise_on_bad_config():
    bad = SimpleNamespace(TTS_PHRASE_CACHE_MAXLEN="x", VOICE_PREWARM_MAX_PHRASES=None)
    assert isinstance(prewarm.phrases(bad), list)


# ── keys: prewarmed entries are real hits for speak() ────────────────────────

def _prewarm_then_speaker(monkeypatch):
    tts = make_tts()
    pre = Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", pre)
    live = Synth()
    monkeypatch.setattr(engine, "_synth_kokoro", live)
    run(tts)
    return tts, pre, live


def test_prewarmed_entry_is_a_hit_for_speak_fast_and_normal(monkeypatch):
    tts, pre, live = _prewarm_then_speaker(monkeypatch)
    assert pre.calls
    tts._lock = threading.Lock()  # speak() legitimately uses its lock
    tts.speak("On it!", fast=True)
    tts.speak("Done.")
    tts.speak("Done.", fast=True)
    assert live.calls == []
    assert len(tts._player.played) == 3


def test_key_matches_speak_key_exactly(monkeypatch):
    tts, _, _ = _prewarm_then_speaker(monkeypatch)
    for text, lang, fast in prewarm._plan():
        key = prewarm.cache_key_for(tts, text, lang, fast)
        assert key == (text, lang, fast, None)
        assert cache_mod._phrase_cache_get(key) is not None


def test_speed_change_is_a_miss_and_matching_speed_hits(monkeypatch):
    tts, _, live = _prewarm_then_speaker(monkeypatch)
    tts._lock = threading.Lock()
    tts.set_speed(1.2)
    tts.speak("Done.")
    assert len(live.calls) == 1
    tts.speak("Done.")
    assert len(live.calls) == 1  # now cached at the new speed
    tts.set_speed(None)
    tts.speak("Done.")
    assert len(live.calls) == 1  # original prewarmed entry still valid


def test_prewarm_uses_speak_params(monkeypatch):
    tts = make_tts()
    pre = Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", pre)
    tts.set_speed(1.1)
    run(tts)
    speeds = {round(s, 3) for _, _, s in pre.calls}
    assert speeds == {1.1}
    key = prewarm.cache_key_for(tts, "Done.", _detect_lang("Done."), False)
    assert key[3] == tts.get_speed()
    assert cache_mod._phrase_cache_get(key) is not None


def test_fast_variant_changes_params(monkeypatch):
    tts = make_tts()
    pre = Synth()
    monkeypatch.setattr(prewarm, "_synth_kokoro", pre)
    run(tts)
    by_text = {}
    for text, _, speed in pre.calls:
        by_text.setdefault(text, []).append(speed)
    assert len(set(by_text["Done."])) == 2  # normal and fast speeds differ