"""Audit part 2, O4: recency questions are answered from one fresh search."""
import time

import pytest

from sara.core.grounding import ground, needs_grounding


@pytest.mark.parametrize("text", [
    "what is the latest iphone price", "who is the current prime minister of india", "aaj ka petrol ka rate kya hai",
    "what is the live score of india vs australia", "latest version of python kya hai", "who won the match yesterday",
])
def test_recency_questions(text):
    assert needs_grounding(text)


@pytest.mark.parametrize("text", [
    "what is photosynthesis", "explain recursion", "open chrome", "play the latest songs on youtube", "hello",
    "kaise ho", "tell me a joke", "search latest news", "x",
])
def test_everything_else_is_left_to_the_model(text):
    assert not needs_grounding(text)


def test_block_is_built_from_results_and_failures_give_none():
    block = ground("latest iphone price", lambda q: "1. Apple: iPhone 17 starts at 79,900 rupees")
    assert "iPhone 17" in block and "Fresh web search results" in block
    assert ground("q", lambda q: "Sorry, I couldn't complete that search right now.") is None
    assert ground("q", lambda q: "No results found for 'q'.") is None

    def boom(q):
        raise RuntimeError("offline")

    assert ground("q", boom) is None


def test_a_slow_search_is_abandoned_at_the_deadline():
    t = time.perf_counter()
    assert ground("q", lambda q: (time.sleep(2), "late")[1], timeout_s=0.2) is None
    assert time.perf_counter() - t < 1.0
