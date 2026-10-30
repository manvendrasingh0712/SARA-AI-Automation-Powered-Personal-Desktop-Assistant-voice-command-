"""
Filler turns must not become long-term memories.

E2E log 2026-10-30: a 0.3 s noise blip was heard as "Yes." / "Thank you.",
answered, and stored ("User said: Yes. / Sara replied: Got it! ..."), 9
junk memories in a few minutes; "Yes." then matched later queries (top
score 0.47 for the query 'Yes.').
"""
import pytest

from sara.core.llm.engine import SaraLLM


@pytest.mark.parametrize("text", [
    "Yes.", "yeah", "Yep!", "Thank you.", "thank you so much", "Sure", "cool", "You", "Okay", "haan ji",
    "Thanks for watching", "Oh ok",
])
def test_fillers_are_trivial(text):
    assert SaraLLM._is_trivial_prompt(text)


@pytest.mark.parametrize("text", [
    "I'm vegetarian", "yes I live in Jaipur", "my name is Manav", "thank you for the recipe of paneer",
    "remind me to call mom", "no I don't like spicy food", "what is the capital of france",
])
def test_real_statements_are_not(text):
    assert not SaraLLM._is_trivial_prompt(text)
