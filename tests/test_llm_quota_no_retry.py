"""
Regression tests for D54: SARA must not retry a quota error the server has
already said cannot succeed yet.

THE OBSERVED WASTE (target machine, real session)
    [LLM:gemini] stream error on attempt 1: 429 RESOURCE_EXHAUSTED
      ... limit: 20, model: gemini-2.5-flash
      ... Generate content requests per day limit exceeded on Free Tier
      ... Please retry in 6h35m46.05359687s
      ... 'retryDelay': '23746s'
    [LLM:gemini] Retry 1/1 in 1.5s: 429 RESOURCE_EXHAUSTED ...
    [LLM:gemini] stream error on attempt 2: 429 RESOURCE_EXHAUSTED ...
    [LLM] 'gemini' unavailable -- falling back to next backend.

    Every single LLM turn paid two full round trips plus a 1.5s sleep
    against a DAILY quota that resets in six and a half hours. The second
    attempt could not possibly succeed -- the server said so in the first
    response -- so that was roughly 4s of pure latency per turn, on top of
    the ollama fallback that was going to run anyway.

THE RULE
    If the server asks us to wait longer than max_delay -- the longest this
    retry loop would ever sleep -- another attempt is guaranteed to fail.
    Treat it as non-transient and spend the time on the fallback instead.

    "No advice given" and "wait zero seconds" must stay distinguishable,
    hence Optional[float] rather than 0.0 for absent.
"""
from __future__ import annotations

import inspect

import pytest

from sara.core.llm.engine import _server_retry_after_s, _looks_rate_limited


# The real payload from the target machine, trimmed to what matters.
_REAL_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
    "your current quota... Please retry in 6h35m46.05359687s', 'status': "
    "'RESOURCE_EXHAUSTED', 'details': [{'@type': "
    "'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaValue': "
    "'20'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
    "'retryDelay': '23746s'}]}}"
)


class _Exc(Exception):
    def __init__(self, msg, details=None):
        super().__init__(msg)
        if details is not None:
            self.details = details


def test_extracts_the_real_retry_delay() -> None:
    assert _server_retry_after_s(_Exc(_REAL_429)) == pytest.approx(23746.0)


def test_extracts_from_structured_details() -> None:
    """Prefer the structured detail over string scraping when present."""
    exc = _Exc(
        "429 RESOURCE_EXHAUSTED",
        details={
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "23746s",
                    },
                ]
            }
        },
    )
    assert _server_retry_after_s(exc) == pytest.approx(23746.0)


def test_absent_advice_is_none_not_zero() -> None:
    """
    None and 0.0 mean different things: "the server said nothing" must not
    be read as "wait no time", which would make every error non-transient.
    """
    assert _server_retry_after_s(_Exc("500 INTERNAL")) is None
    assert _server_retry_after_s(_Exc("")) is None
    assert _server_retry_after_s(Exception("connection reset")) is None


def test_a_short_delay_is_still_retryable() -> None:
    """
    A per-MINUTE rate limit asking for a couple of seconds SHOULD be
    retried -- that is what the backoff is for. Only a wait longer than
    max_delay is hopeless.
    """
    short = _server_retry_after_s(
        _Exc("429 {'retryDelay': '2s'}")
    )
    assert short == pytest.approx(2.0)
    assert short < 8.0, "8.0 is LLM_RETRY_MAX_DELAY_S; 2s must stay retryable"


def test_never_raises_on_junk() -> None:
    """A parser on an error path must not add a second error."""
    for junk in (
        _Exc("retryDelay: not-a-number-s"),
        _Exc("{'retryDelay': ''}"),
        _Exc("429", details="not-a-list"),
        _Exc("429", details=[None, 42, {"@type": "RetryInfo"}]),
    ):
        assert _server_retry_after_s(junk) is None


def test_the_real_payload_is_also_recognised_as_rate_limited() -> None:
    """Both guards must agree about this error, or the fallback never runs."""
    assert _looks_rate_limited(_Exc(_REAL_429)) is True


# ── the decision site ───────────────────────────────────────────────────
def _retry_decision_src() -> str:
    from sara.core.llm.engine import SaraLLM

    for name, fn in inspect.getmembers(SaraLLM, predicate=inspect.isfunction):
        try:
            src = inspect.getsource(fn)
        except Exception:
            continue
        if "non_transient = isinstance(e, _ClientUnavailableError)" in src:
            return src
    raise AssertionError("retry decision site not found; this test is stale")


def test_the_retry_loop_consults_the_server_delay() -> None:
    src = _retry_decision_src()
    assert "_server_retry_after_s(" in src, (
        "the retry loop no longer asks how long the server wants to wait, so "
        "a daily-quota 429 is retried again at ~4s per turn (D54)"
    )


def test_the_comparison_is_against_max_delay() -> None:
    """
    The threshold must be the loop's own ceiling. Hardcoding a number here
    would drift from LLM_RETRY_MAX_DELAY_S and either retry the hopeless or
    give up on the recoverable.
    """
    src = _retry_decision_src()
    window = src[src.index("_server_retry_after_s("):]
    window = window[: window.index("if attempt <")]
    assert "max_delay" in window, (
        "the server-delay check is not compared against max_delay (D54)"
    )


def test_client_unavailable_stays_non_transient() -> None:
    """The pre-existing fail-fast must survive this change."""
    src = _retry_decision_src()
    assert "isinstance(e, _ClientUnavailableError)" in src
