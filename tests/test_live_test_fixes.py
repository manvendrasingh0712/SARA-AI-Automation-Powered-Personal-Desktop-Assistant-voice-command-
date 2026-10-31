"""Fixes found by the live chat test on the real PC (2026-10-31)."""
from datetime import datetime

import dateparser

from sara.core.intent.engine import detect_intent
from sara.orchestrator.calc_utils import _parse_duration_to_seconds, _safe_calc
from sara.orchestrator.handlers.timers import _spoken_clock_time


def test_hindi_number_words_in_calculator_and_timer():
    assert _safe_calc("बारह गुणा चार") == "The answer is 48."
    assert _parse_duration_to_seconds("दो घंटे") == 7200


def test_hinglish_alarm_times_parse():
    t = dateparser.parse(_spoken_clock_time("kal subah 7 baje"), languages=["en"], settings={"RELATIVE_BASE": datetime(2026, 10, 31, 12)})
    assert t is not None and t.hour == 7 and t.day == 1
    assert _spoken_clock_time("शाम 5:30 बजे") == "5:30 pm"


def test_water_is_not_weather_and_focus_mode_routes():
    assert detect_intent("what is the boiling point of water in fahrenheit")[0] != "weather"
    assert detect_intent("weather in delhi")[0] == "weather"
    assert detect_intent("switch to focus mode")[0] == "switch_mode"


def test_timer_message_has_no_float_digits():
    from sara.tools.system import timers

    msg = timers.set_timer(29 * 3600 + 660.0, "alarm", lambda m: None)
    timers.cancel_timer() if hasattr(timers, "cancel_timer") else None
    assert "29 hours and 11 minutes" in msg
