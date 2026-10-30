"""wttr.in's "+22°C" must be spoken as words, not "+twenty-two°C"."""
import pytest

from sara.audio.tts.text_prep import _normalize_numbers_for_tts, clean_for_tts


def _say(text):
    return _normalize_numbers_for_tts(clean_for_tts(text), "en")


@pytest.mark.parametrize("text, spoken", [
    ("Delhi: \U0001F326️  +22°C", "Delhi: twenty-two degrees Celsius"),
    ("Pune: -3°C and windy", "Pune: minus three degrees Celsius and windy"),
    ("It is 98.6°F outside", "It is ninety-eight point six degrees Fahrenheit outside"),
    ("rotate 90° left", "rotate ninety degrees left"),
    ("Mumbai: 31 degrees, humidity 58 percent.", "Mumbai: thirty-one degrees, humidity fifty-eight percent."),
])
def test_degrees_are_spoken_in_words(text, spoken):
    assert _say(text) == spoken
