"""
The calculator must read the whole expression, or say it cannot.

E2E audit 2026-10-30: "what is 2400 divided by 8" -> "The answer is 2400."
and "calculate 7 times 8 plus 3" -> "The answer is 7." (first number only;
silently wrong).
"""
import pytest

from sara.core.intent import detect_intent
from sara.orchestrator.calc_utils import _safe_calc


def _answer(text):
    detect_intent.cache_clear()
    intent, match = detect_intent(text)
    assert intent == "calculator", (text, intent)
    return _safe_calc(match.group(1))


@pytest.mark.parametrize("text, answer", [
    ("what is 2400 divided by 8", "300"),
    ("calculate 7 times 8 plus 3", "59"),
    ("what is five plus three", "8"),
    ("what's 10 minus 4", "6"),
    ("twelve times twelve", "144"),
    ("what is 2 to the power of 10", "1024"),
    ("what is 100 over 4", "25"),
    ("calculate 7 x 8", "56"),
    ("what is 20 mod 6", "2"),
    ("what is the square root of 144", "12"),
    ("what is 9 squared", "81"),
    ("what is 3 cubed", "27"),
    ("what is 15 percent of 2400", "360"),
    ("what is 5 + 3", "8"),
])
def test_spoken_expressions(text, answer):
    assert _answer(text) == f"The answer is {answer}."


def test_long_decimals_are_rounded_for_speech():
    assert _answer("what's the square root of 2") == "The answer is 1.414214."


@pytest.mark.parametrize("text", [
    "room 12-15", "call me at 555-1234", "the score was 2-1", "what is the capital of france",
    "i have two plus points", "that costs ten times more than expected",
])
def test_ordinary_text_is_not_math(text):
    detect_intent.cache_clear()
    assert detect_intent(text)[0] != "calculator"
