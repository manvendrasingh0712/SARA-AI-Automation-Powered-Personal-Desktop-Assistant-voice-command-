"""Tests for the deterministic temporal parser (fixed now: Wednesday 2026-03-18 10:00)."""
from __future__ import annotations

from datetime import datetime

from sara.core.memory2.timeparse import parse_window

NOW = datetime(2026, 3, 18, 10, 0).timestamp()


def day(text: str):
    """Start / end labels (MM-DD) of the window for ``text``."""
    window = parse_window(text, NOW)
    assert window is not None, text
    return (datetime.fromtimestamp(window[0]).strftime("%m-%d"),
            datetime.fromtimestamp(window[1]).strftime("%m-%d"))


def test_single_days_english():
    assert day("what did I do today") == ("03-18", "03-19")
    assert day("what did I do yesterday") == ("03-17", "03-18")
    assert day("what did I do the day before yesterday") == ("03-16", "03-17")
    assert day("what is happening tomorrow") == ("03-19", "03-20")
    assert day("the day after tomorrow") == ("03-20", "03-21")


def test_weekdays():
    assert day("what did I tell you last Monday") == ("03-16", "03-17")
    assert day("what happened on Monday last week") == ("03-09", "03-10")
    assert day("what did I do two Mondays ago") == ("03-02", "03-03")
    assert day("what happened on Wednesday") == ("03-11", "03-12")
    assert day("what is on next Friday") == ("03-20", "03-21")


def test_relative_spans():
    assert day("3 days ago") == ("03-15", "03-16")
    assert day("last week") == ("03-09", "03-16")
    assert day("this week") == ("03-16", "03-23")
    assert day("2 weeks ago") == ("03-02", "03-09")
    assert day("last month") == ("02-01", "03-01")
    assert day("2 months ago") == ("01-01", "02-01")


def test_hinglish():
    assert day("aaj kya hua") == ("03-18", "03-19")
    assert day("kal maine kya kharida tha") == ("03-17", "03-18")
    assert day("kal meri meeting hogi") == ("03-19", "03-20")
    assert day("parso maine kya kiya tha") == ("03-16", "03-17")
    assert day("pichle hafte kya hua") == ("03-09", "03-16")
    assert day("2 din pehle kya hua") == ("03-16", "03-17")


def test_hindi():
    assert day("आज क्या हुआ") == ("03-18", "03-19")
    assert day("कल मैंने क्या किया था") == ("03-17", "03-18")
    assert day("कल मेरी मीटिंग होगी") == ("03-19", "03-20")
    assert day("परसों क्या हुआ था") == ("03-16", "03-17")
    assert day("पिछले हफ्ते क्या हुआ") == ("03-09", "03-16")


def test_no_expression_and_bad_input():
    assert parse_window("what is my name", NOW) is None
    assert parse_window("", NOW) is None
    assert parse_window(None, NOW) is None  # type: ignore[arg-type]