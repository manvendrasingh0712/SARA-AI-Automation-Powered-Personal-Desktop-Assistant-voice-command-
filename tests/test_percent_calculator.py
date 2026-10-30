"""
Percent questions must be calculated, not truncated.

E2E user test 2026-10-29 (real pipeline): "what is 15 percent of 2400"
routed to the calculator with only "15" captured and answered
"The answer is 15." "15% of 2400" and "2400 ka 15 percent" went to chat.
"""
import pytest

from sara.core.intent import detect_intent
from sara.orchestrator.calc_utils import _safe_calc


@pytest.mark.parametrize("text, answer", [
    ("what is 15 percent of 2400", "360"),
    ("15% of 2400", "360"),
    ("calculate 12.5 percent of 80", "10"),
    ("2400 ka 15 percent", "360"),
    ("what is 20 percent of 500", "100"),
    ("what is 5 + 3", "8"),
    ("50%20", "10"),
])
def test_percent_and_plain_math(text, answer):
    detect_intent.cache_clear()
    intent, match = detect_intent(text)
    assert intent == "calculator"
    assert _safe_calc(match.group(1)) == f"The answer is {answer}."
