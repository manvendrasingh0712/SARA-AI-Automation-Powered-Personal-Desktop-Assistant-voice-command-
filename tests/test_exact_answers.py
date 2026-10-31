"""Audit part 2, O3: exact answers by code, no language model."""
from datetime import date

import pytest

import sara.skills  # noqa: F401
from sara.core import exact
from sara.core.intent.engine import detect_intent
from sara.orchestrator.calc_utils import _safe_calc

TODAY = date(2026, 10, 31)   # a Saturday


@pytest.mark.parametrize("text,expected", [
    ("5 km kitne miles", "5 kilometres is 3.11 miles."),
    ("convert 5 km to miles", "5 kilometres is 3.11 miles."),
    ("10 kg in pounds", "10 kilograms is 22.05 pounds."),
    ("70 kg in pounds", "70 kilograms is 154.32 pounds."),
    ("100 fahrenheit to celsius", "100 degrees Fahrenheit is 37.78 degrees Celsius."),
    ("0 celsius to fahrenheit", "0 degrees Celsius is 32 degrees Fahrenheit."),
    ("3 hours in minutes", "3 hours is 180 minutes."),
    ("how many minutes in an hour", "1 hour is 60 minutes."),
    ("2 gb in mb", "2 gigabytes is 2048 megabytes."),
    ("60 mph to km/h", "60 miles per hour is 96.56 kilometres per hour."),
    ("37 ka square root", "The square root of 37 is 6.08."),
    ("square root of 144", "The square root of 144 is 12."),
    ("15 percent tip on 840", "15 percent tip on 840 is 126, so the total is 966."),
    ("percentage of 45 out of 60", "45 out of 60 is 75 percent."),
])
def test_exact_values(text, expected):
    assert exact.answer(text, TODAY) == expected


@pytest.mark.parametrize("text,expected", [
    ("kal kitni tarikh hogi", "Sunday, 1 November 2026."),
    ("kal kaun sa din hoga", "Sunday."),
    ("25 days from today", "That is Wednesday, 25 November 2026."),
    ("aaj se 10 din baad kya date hogi", "Woh Tuesday, 10 November 2026 hoga."),
    ("what day is 15 august 2026", "Saturday, 15 August 2026."),
    ("how many days in march", "March 2026 has 31 days."),
    ("february mein kitne din", "February 2026 has 28 days."),
])
def test_date_arithmetic(text, expected):
    assert exact.answer(text, TODAY) == expected


def test_leap_year_february():
    assert exact.answer("how many days in february 2028", TODAY) == "February 2028 has 29 days."


@pytest.mark.parametrize("text", ["what is photosynthesis", "5 km", "convert this file", "how many apples in a basket", "tell me about miles"])
def test_other_sentences_are_not_answered_here(text):
    assert exact.answer(text, TODAY) is None


def test_the_phrases_reach_the_skill_and_the_skill_answers_without_a_model():
    for t in ("5 km kitne miles", "37 ka square root", "25 days from today", "3 hours in minutes"):
        assert detect_intent(t)[0] == "exact_answer", t
    from sara.skills.exact_answers import handle_exact
    from sara.skills._framework import SkillContext

    assert handle_exact(None, SkillContext({"user_input": "5 km kitne miles"}, "t")) == "5 kilometres is 3.11 miles."


def test_calculator_power_phrase_regression():
    assert _safe_calc("2 to the power 8") == "The answer is 256."
    assert _safe_calc("2 to the power of 10") == "The answer is 1024."
