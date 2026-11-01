"""Live-test finding (#102): "सुबह छह बजे का अलार्म लगा दो" answered "couldn't understand the time" because number WORDS were not read."""
import dateparser
import pytest

from sara.orchestrator.handlers.timers import _spoken_clock_time


def _hm(text):
    p = dateparser.parse(_spoken_clock_time(text), languages=["en"])
    return p.strftime("%H:%M") if p else None


@pytest.mark.parametrize("text,expected", [
    ("सुबह छह बजे", "06:00"), ("सुबह छः बजे", "06:00"), ("शाम पाँच बजे", "17:00"), ("रात दस बजे", "22:00"),
    ("दोपहर बारह बजे", "12:00"), ("subah chhe baje", "06:00"), ("सुबह साढ़े छह बजे", "06:30"), ("सवा सात बजे", "07:15"),
    ("पौने सात बजे", "06:45"), ("डेढ़ बजे", "01:30"), ("ढाई बजे", "02:30"), ("सुबह 6 बजे", "06:00"), ("शाम 5:30 बजे", "17:30"),
])
def test_spoken_hindi_and_hinglish_clock_times(text, expected):
    assert _hm(text) == expected


def test_tomorrow_prefix_is_kept():
    assert _spoken_clock_time("kal subah paanch baje") == "tomorrow 5 am"
    assert _spoken_clock_time("कल सुबह सात बजे") == "tomorrow 7 am"


def test_plain_english_times_are_untouched():
    assert _spoken_clock_time("5 am") == "5 am"
    assert _spoken_clock_time("7:45 pm") == "7:45 pm"
