"""Router features: determinism, dimension, language handling, speed."""
from __future__ import annotations

import time

import pytest

from sara.core.router import features
from sara.core.router.features import FEATURE_DIM, FEATURE_NAMES, extract_features

PROMPTS = [
    "what is the capital of France",
    "set volume to 50",
    "kya haal hai yaar, mujhe samjhao kaise kaam karta hai",
    "मुझे हिंदी में जवाब दो",
    "compare these two plans and then explain why one is better",
]


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(features, "_detect_fn", None)
    monkeypatch.setattr(features, "_tool_fn", lambda text: "weather" in text.lower())
    monkeypatch.setattr(features, "_resolved", True)


def _idx(name: str) -> int:
    return FEATURE_NAMES.index(name)


def test_dimension_and_range():
    assert FEATURE_DIM <= 12
    assert len(FEATURE_NAMES) == FEATURE_DIM
    for text in PROMPTS:
        vec = extract_features(text).vector
        assert vec.shape == (FEATURE_DIM,)
        assert vec[0] == 1.0
        assert ((vec >= 0.0) & (vec <= 1.0)).all()


def test_deterministic_and_stateless():
    first = [extract_features(t).vector.copy() for t in PROMPTS]
    second = [extract_features(t).vector.copy() for t in reversed(PROMPTS)]
    for a, b in zip(first, reversed(second)):
        assert (a == b).all()


def test_language_detection():
    assert extract_features("what is the capital of France").lang == "en"
    assert extract_features("kya haal hai yaar").lang == "hinglish"
    assert extract_features("मुझे हिंदी में जवाब दो").lang == "hi"


def test_flags():
    assert extract_features("set volume to 50").simple is True
    assert extract_features("tell me about black holes").simple is False
    assert extract_features("what does it do").followup is True
    assert extract_features("explain this step by step").think_cue is True
    assert extract_features("यह समझाओ").think_cue is True
    assert extract_features("what is the weather").needs_tools is True
    assert extract_features("tell me a story").needs_tools is False


def test_complexity_grows_with_structure():
    low = extract_features("hello there")
    high = extract_features(
        "compare these two plans and then explain why one is better " + " ".join(["detail"] * 30)
    )
    assert low.complexity < 0.2
    assert high.complexity > low.complexity
    assert high.complexity >= 0.55


def test_ctx_features():
    base = extract_features("tell me a story")
    ctx = extract_features("tell me a story", {"memory_injected": True, "depth": 5})
    assert base.vector[_idx("memory_injected")] == 0.0
    assert ctx.vector[_idx("memory_injected")] == 1.0
    assert ctx.vector[_idx("convo_depth")] == pytest.approx(0.5)


def test_never_raises():
    for bad in (None, "", "   ", 42):
        feat = extract_features(bad)
        assert feat.vector.shape == (FEATURE_DIM,)
    assert extract_features("hi", {"depth": "x"}).vector.shape == (FEATURE_DIM,)


def test_mean_extraction_time_under_one_millisecond():
    start = time.perf_counter()
    for i in range(2000):
        extract_features(PROMPTS[i % len(PROMPTS)])
    mean = (time.perf_counter() - start) / 2000
    assert mean < 1e-3