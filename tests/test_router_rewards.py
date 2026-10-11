"""Reward signals: quality combinations, latency penalty, rephrase detection."""
from __future__ import annotations

import itertools

import pytest

from sara.core.router.rewards import compute_reward, is_rephrase, quality_from_signals


@pytest.mark.parametrize(
    "thumbs,rephrased,tool_failure,empty,escalated",
    list(itertools.product((-1, 0, 1), (False, True), (False, True), (False, True), (False, True))),
)
def test_quality_every_combination(thumbs, rephrased, tool_failure, empty, escalated):
    expected = 0.7 + {1: 0.3, 0: 0.0, -1: -0.7}[thumbs]
    if rephrased:
        expected -= 0.4
    if tool_failure or empty:
        expected -= 0.6
    if escalated:
        expected -= 0.3
    expected = min(1.0, max(0.0, expected))
    got = quality_from_signals(
        thumbs=thumbs, rephrased=rephrased, tool_failure=tool_failure,
        empty=empty, escalated=escalated,
    )
    assert got == pytest.approx(expected, abs=1e-9)


def test_default_quality():
    assert quality_from_signals() == pytest.approx(0.7)


def test_latency_penalty():
    assert compute_reward(0.7, 1000.0, 1.0, 0.15, 0.05) == pytest.approx(0.65)
    assert compute_reward(0.7, 4500.0, 1.0, 0.15, 0.05) == pytest.approx(0.575)
    assert compute_reward(0.7, 7500.0, 1.0, 0.15, 0.05) == pytest.approx(0.5)
    assert compute_reward(0.7, 60000.0, 1.0, 0.15, 0.05) == pytest.approx(0.5)
    assert compute_reward(0.7, float("nan"), 0.0, 0.15, 0.05) == pytest.approx(0.7)


def test_cost_penalty_prefers_cheaper_tier():
    cheap = compute_reward(0.7, 500.0, 1.0, 0.15, 0.05)
    dear = compute_reward(0.7, 500.0, 6.0, 0.15, 0.05)
    assert cheap > dear


def test_rephrase_english():
    assert is_rephrase("what is the weather today", "what is the weather today?", 5.0)
    assert not is_rephrase("what is the weather today", "play some music", 5.0)
    assert not is_rephrase("what is the weather today", "what is the weather today", 25.0)
    assert is_rephrase("what is the weather today", "what is the weather today", 20.0)


def test_rephrase_hinglish_and_hindi():
    assert is_rephrase("aaj ka mausam kaisa hai", "aaj ka mausam kaisa hai yaar", 8.0)
    assert is_rephrase("मुझे समय बताओ", "मुझे समय बताओ", 3.0)


@pytest.mark.parametrize("word", ["no", "nahi", "galat", "wrong", "nope", "No!", "नहीं", "nahi galat"])
def test_negation_counts_as_rephrase(word):
    assert is_rephrase("play some jazz", word, 4.0)


def test_negation_inside_a_new_request_is_not_a_rephrase():
    assert not is_rephrase("play some jazz", "no I want the weather report", 4.0)
    assert not is_rephrase("play some jazz", "", 4.0)
    assert not is_rephrase("", "something", 4.0)