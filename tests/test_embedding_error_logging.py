"""
Regression tests for D56: an upstream 5xx must not print a traceback.

THE OBSERVED NOISE (target machine, real session)
    gemini-embedding-001 returned HTTP 500 on most turns. Each turn logged
    two retry warnings, then a logger.error with exc_info=True -- a ~40 line
    Python traceback whose frames are always identical (google.genai's
    _api_client, tenacity's retry machinery, concurrent.futures). The user's
    console had four of them in a two-minute session, and they buried the
    things actually worth reading, such as the 429 quota failures.

THE DISTINCTION THAT MATTERS
    A 500 from a remote service is an expected operating condition, not a
    bug in this code: the stack says nothing we do not already know. An
    exception we could NOT classify is different -- there, not knowing the
    call path IS the problem, so the traceback stays.

    The one-line message carries status, model, text length and exception
    type either way, so nothing diagnostic is lost for the 5xx case.
"""
from __future__ import annotations

import inspect
import re

from sara.core.llm import clients


def _failure_log_src() -> str:
    src = inspect.getsource(clients)
    start = src.index("Gemini embed_content() FAILED after")
    # Back up to the logger.error call, forward to its closing paren.
    call_start = src.rindex("logger.error", 0, start)
    return src[call_start : src.index("return None", start)]


def test_server_errors_do_not_get_a_traceback() -> None:
    block = _failure_log_src()
    assert "exc_info=True" not in block, (
        "the embedding failure log prints a traceback unconditionally again; "
        "an upstream 5xx then dumps ~40 identical frames per turn (D56)"
    )
    assert "exc_info=not server_side" in block, (
        "exc_info is no longer conditional on whether the failure was "
        "classified as server-side (D56)"
    )


def test_unclassified_failures_still_get_a_traceback() -> None:
    """
    The guard must be `not server_side`, not a blanket False: an exception
    with no HTTP status is exactly the case where the stack is the useful
    part.
    """
    src = inspect.getsource(clients)
    assert re.search(r"server_side\s*=\s*isinstance\(status, int\)", src), (
        "server_side is no longer derived from the HTTP status, so the "
        "traceback decision no longer tracks whether we classified the error"
    )
    assert "500 <= status <= 599" in src, (
        "the server-side range check is gone; a 4xx or an unclassified "
        "exception could now be logged without its traceback (D56)"
    )


def test_the_one_line_message_still_carries_the_diagnostics() -> None:
    """Dropping the traceback must not drop the facts."""
    block = _failure_log_src()
    for field in ("model=%s", "chars=%d", "status=%s"):
        assert field in block, f"the failure log no longer reports {field}"
    assert "type(e).__name__" in block, (
        "the failure log no longer names the exception type, which is the "
        "one thing the traceback was still adding (D56)"
    )


def test_retries_are_unchanged() -> None:
    """
    This was a logging fix only. The backoff is what actually gives the
    remote service a chance to recover, and it must survive.
    """
    src = inspect.getsource(clients)
    assert "EMBEDDING_MAX_RETRIES" in src
    assert "base_delay_s * (2 ** attempt)" in src, (
        "the exponential backoff was removed; D56 was about log noise, not "
        "about retrying less"
    )
