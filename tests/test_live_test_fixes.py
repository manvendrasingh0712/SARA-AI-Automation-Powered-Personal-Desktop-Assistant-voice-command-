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


def test_typed_commands_count_as_activity_for_the_idle_sleep_clock():
    import time

    from sara.orchestrator.state import last_typed_activity, note_typed_activity

    before = last_typed_activity()
    time.sleep(0.01)
    note_typed_activity()
    assert last_typed_activity() > before


def test_hinglish_news_topics_and_opinions():
    from sara.core.intent.engine import detect_intent, is_negated_action_request

    name, m = detect_intent("cricket ki latest news batao")
    assert name == "news" and m.group(1) == "cricket"
    assert detect_intent("technology ke baare mein khabar")[1].group(1) == "technology"
    assert not is_negated_action_request("not funny")
    assert is_negated_action_request("don't open chrome")


def test_routine_name_filler_removed():
    from sara.orchestrator.handlers.automation import _clean_routine_name

    assert _clean_routine_name("meri evening") == "evening"
    assert _clean_routine_name("सुबह का") == "morning"


def test_news_rss_headlines_parsed(monkeypatch):
    from sara.tools import web

    xml = b"<rss><channel><item><title>Big story - The Hindu</title></item><item><title>Other - NDTV</title></item></channel></rss>"

    class R:
        content = xml

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web._session, "get", lambda *a, **k: R())
    assert web._news_google_rss("", 3) == ["1. Big story (The Hindu)", "2. Other (NDTV)"]
