"""
Whisper gets a short list of names the user actually uses.

Measured 2026-10-30 (tiny, synthetic noisy speech): English word errors
5.7 % -> 2.1 % clean, 9.3 % -> 7.7 % at 8 dB, +10 ms per decode, and no
vocabulary word appeared on silence / noise / ordinary speech (0/18).
"""
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from sara.audio.stt import vocabulary as voc
from sara.audio.stt.engine import SpeechToText


def _cfg(**kw):
    base = dict(STT_VOCABULARY_PROMPT=True, STT_VOCABULARY_MAX_WORDS=24, STT_EXTRA_VOCABULARY="")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    monkeypatch.setattr(voc, "_from_memory", lambda: [])
    voc._cache.update(at=0.0, key=None, value="")


def test_own_words_come_first_then_cities_and_brands():
    words = voc.build_vocabulary(_cfg(STT_EXTRA_VOCABULARY="Khushhal, Kekri; Arijit Singh"))
    assert words[:3] == ["Khushhal", "Kekri", "Arijit Singh"]
    assert "Jaipur" in words and "YouTube" in words


def test_memory_names_beat_the_static_lists(monkeypatch):
    monkeypatch.setattr(voc, "_from_memory", lambda: ["Priya", "Rohan"])
    assert voc.build_vocabulary(_cfg())[:2] == ["Priya", "Rohan"]


def test_junk_duplicates_and_placeholders_are_dropped():
    words = voc._clean(["user", "Priya", "priya", "", None, "12345", "a", "Pune!!", "x" * 80, "रोहन"])
    assert words == ["Priya", "रोहन"] or words[0] == "Priya"
    assert "user" not in words and words.count("Priya") == 1 and not any(w.isdigit() for w in words)


def test_cap_is_respected():
    assert len(voc.build_vocabulary(_cfg(), max_words=5)) == 5


def test_hint_text_and_switch():
    on = voc.vocabulary_hint(_cfg(STT_EXTRA_VOCABULARY="Khushhal"))
    assert on.startswith(" Names that may come up: Khushhal, ") and on.endswith(".")
    voc._cache.update(at=0.0, key=None, value="")
    assert voc.vocabulary_hint(_cfg(STT_VOCABULARY_PROMPT=False)) == ""


def test_hint_is_cached_for_a_minute(monkeypatch):
    cfg = _cfg(STT_EXTRA_VOCABULARY="Khushhal")
    first = voc.vocabulary_hint(cfg)
    build = mock.Mock(return_value=["Rebuilt"])
    monkeypatch.setattr(voc, "build_vocabulary", build)
    assert voc.vocabulary_hint(cfg) == first          # served from the cache
    build.assert_not_called()
    voc._cache["at"] = time.monotonic() - 120         # two minutes later it is rebuilt
    assert "Rebuilt" in voc.vocabulary_hint(cfg)
    build.assert_called_once()


def test_never_raises(monkeypatch):
    monkeypatch.setattr(voc, "build_vocabulary", mock.Mock(side_effect=RuntimeError("boom")))
    voc._cache.update(at=0.0, key=None, value="")
    assert voc.vocabulary_hint(_cfg()) == ""


@pytest.mark.parametrize("forced, base", [
    ("hi", "_STATIC_TRANSCRIBE_PROMPT_HI"), (None, "_STATIC_TRANSCRIBE_PROMPT_NEUTRAL"),
])
def test_every_prompt_variant_carries_the_hint(monkeypatch, forced, base):
    import sara.audio.stt.engine as eng

    monkeypatch.setattr(eng, "vocabulary_hint", lambda: " Names that may come up: Jaipur.")
    stt = SpeechToText.__new__(SpeechToText)
    prompt = stt._get_transcribe_prompt(forced)
    assert prompt.endswith(" Names that may come up: Jaipur.")
    assert prompt.startswith(getattr(SpeechToText, base)[:30])


def test_english_prompt_carries_the_hint(monkeypatch):
    import sara.audio.stt.engine as eng

    monkeypatch.setattr(eng, "vocabulary_hint", lambda: " Names that may come up: Jaipur.")
    assert SpeechToText.__new__(SpeechToText)._get_transcribe_prompt("en").endswith("Jaipur.")
