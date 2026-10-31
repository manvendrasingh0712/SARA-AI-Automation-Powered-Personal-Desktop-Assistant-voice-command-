"""Audit Task 3: Hindi/Hinglish number words next to a unit, duration timers, music phrases."""
import pytest

from sara.core.intent.engine import detect_intent
from sara.core.text.spoken_numbers import words_to_digits as w
from sara.orchestrator.calc_utils import _parse_duration_to_seconds


@pytest.mark.parametrize("text,expected", [
    ("paanch minute", "5 minute"),
    ("ek ghante", "1 ghante"),
    ("aadha ghanta", "30 minute"),
    ("pandrah minute", "15 minute"),
    ("volume pachaas", "volume 50"),
    ("do minute baad", "2 minute baad"),
    ("dedh ghanta", "90 minute"),
    ("dhai ghante", "150 minute"),
    ("sade teen ghante", "210 minute"),
    ("brightness saath percent", "brightness 60 percent"),
    ("fifty percent", "50 percent"),
    ("half an hour", "30 minutes"),
    ("forty five minutes", "45 minutes"),
    ("पाँच मिनट", "5 मिनट"),
    ("१० मिनट", "10 मिनट"),
])
def test_words_to_digits(text, expected):
    assert w(text) == expected


@pytest.mark.parametrize("text", ["laga do", "kar do", "ek ghar", "ek aur joke", "open one note", "a bit", "hum dono", "saath chalo"])
def test_ambiguous_words_stay_when_no_unit_follows(text):
    assert w(text) == text


def test_non_aware_mode_converts_english_but_not_the_hindi_ambiguous_words():
    assert w("open one note", unit_aware=False) == "open 1 note"
    assert w("kar do", unit_aware=False) == "kar do"


PHRASES = [
    ("paanch minute ka timer", "set_timer", 300),
    ("ek ghante ka timer laga do", "set_timer", 3600),
    ("dus minute ka timer set karo", "set_timer", 600),
    ("pandrah minute ka alarm", "set_timer", 900),
    ("aadha ghanta timer", "set_timer", 1800),
    ("10 minute timer", "set_timer", 600),
    ("do minute baad yaad dilao", "set_timer", 120),
    ("volume pachaas kar do", "set_volume", None),
    ("volume fifty percent", "set_volume", None),
    ("brightness saath percent", "set_brightness", None),
    ("agla gaana", "next_track", None),
    ("pichla gaana", "previous_track", None),
    ("music pause", "play_pause_media", None),
]


@pytest.mark.parametrize("text,intent,seconds", PHRASES)
def test_phrases_resolve_without_the_llm(text, intent, seconds):
    name, m = detect_intent(text)
    assert name == intent
    if seconds is not None:
        assert _parse_duration_to_seconds(m.group(1)) == seconds


def test_values_for_volume_and_brightness():
    assert detect_intent("volume pachaas kar do")[1].group(1) == "50"
    assert detect_intent("brightness saath percent")[1].group(1) == "60"


@pytest.mark.parametrize("text", ["laga do", "kar do", "ek aur", "ek ghar dikhao", "do"])
def test_do_and_ek_are_not_commands_on_their_own(text):
    assert detect_intent(text)[0] in ("chat", "joke_again")


def test_the_number_tables_are_shared():
    from sara.core.memory2 import timeparse
    from sara.core.text import spoken_numbers
    from sara.orchestrator import calc_utils

    assert calc_utils._NUMBER_WORDS is spoken_numbers.ENGLISH_ONES
    assert timeparse._NUMWORD["paanch"] == 5 and timeparse._NUMWORD["ek"] == 1
