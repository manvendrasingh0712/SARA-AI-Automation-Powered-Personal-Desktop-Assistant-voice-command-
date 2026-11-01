"""'what is the date today' used to fall through to the language model; questions about other dates must still go there."""
import pytest

import sara.skills  # noqa: F401
from sara.core.intent.engine import detect_intent


@pytest.mark.parametrize("text", [
    "what is the date today", "What's the date today?", "what is the date", "tell me the date", "tell me the date today",
    "today's date", "what is today's date", "what's today's date",
])
def test_date_questions_are_answered_locally(text):
    assert detect_intent(text)[0] == "date_query"


@pytest.mark.parametrize("text", [
    "what is the date of independence day", "what is the date of birth of gandhi", "tell me the date of the exam",
])
def test_questions_about_some_other_date_stay_with_the_model(text):
    assert detect_intent(text)[0] != "date_query"
