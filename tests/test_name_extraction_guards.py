"""
"my name is X" detection must not turn questions or idioms into a name.

E2E user test 2026-10-29 (real pipeline): asking "mera naam kya hai"
answered "Nice to meet you, Kya Hai!" and saved "Kya Hai" as the user's
name. "mera naam Rahul hai" saved "Rahul Hai".
"""
import pytest

from sara.orchestrator.text_utils import _extract_name


@pytest.mark.parametrize("text", [
    "mera naam kya hai",
    "mera naam kya hai?",
    "my name is what?",
    "my name is what",
    "call me later",
    "call me a taxi",
    "call me back tomorrow",
    "main hoon na",
    "what is my name",
    "i am going home",
])
def test_not_an_introduction(text):
    assert _extract_name(text) is None


@pytest.mark.parametrize("text, name", [
    ("my name is Priya", "Priya"),
    ("mera naam Rahul hai", "Rahul"),
    ("mera naam Khushhal Sahu hai", "Khushhal Sahu"),
    ("mujhe bulao Raj", "Raj"),
    ("call me Khushi", "Khushi"),
    ("I am Sam", "Sam"),
    ("main hoon Aman", "Aman"),
])
def test_real_introductions_still_work(text, name):
    assert _extract_name(text) == name
