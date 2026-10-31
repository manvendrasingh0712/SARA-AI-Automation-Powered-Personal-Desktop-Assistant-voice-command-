"""
Regression tests for D40: VAD_AGGRESSIVENESS must be a real Config field.

THE DEFECT
    Two comments asserted it already was one. buffers.py:

        aggressiveness now defaults to Config.VAD_AGGRESSIVENESS (0-3,
        WebRTC VAD's own scale) instead of a fixed 2, so it's tunable via
        .env without a code edit

    and engine.py said passing nothing "lets Config.VAD_AGGRESSIVENESS
    (default 2, same as before) actually take effect".

    A grep across all 304 Python files found the name only in those
    comments and in the `getattr(Config, "VAD_AGGRESSIVENESS", 2)` fallback
    itself. The field was declared NOWHERE, so the fallback always returned
    2 and the earlier "BUGFIX" that removed a hardcoded 2 had replaced it
    with a different hardcoded 2. The same shape as D31: it read as
    configurable and silently was not.

WHY THE CLAMP IS NOT COSMETIC
    webrtcvad.Vad() RAISES on a value outside 0-3, and it is constructed
    inside _VADFilter.__init__ during SpeechToText boot. Declaring the
    field without clamping it would turn a typo in .env into a startup
    crash, which is strictly worse than the silent default it replaced.

SCOPE, SO THIS IS NOT MISREAD
    After D39 and D45 the VAD no longer decides whether a capture starts or
    continues -- both gates are energy-only, because webrtcvad called 92% of
    near-silent audio speech on the target machine. It now only NARROWS an
    energy-passed decision. So this tunes barge-in sensitivity, not whether
    SARA hears a command.
"""
from __future__ import annotations

import inspect
import re

from config import Config


def _declaration() -> str:
    """
    The VAD_AGGRESSIVENESS declaration and its arguments.

    Sliced from the DECLARATION line, not the first mention of the name:
    the field carries a long explanatory comment that mentions it several
    times, and slicing from the first occurrence lands inside that prose.
    """
    src = inspect.getsource(Config)
    m = re.search(r"^\s+VAD_AGGRESSIVENESS\s*:\s*int.*$", src, re.MULTILINE)
    assert m, "VAD_AGGRESSIVENESS is not declared as a Config field (D40)"
    tail = src[m.start():]
    depth, end = 0, None
    for i, ch in enumerate(tail):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return tail[:end] if end else tail.splitlines()[0]


def test_it_is_a_real_config_field_not_just_a_getattr_default() -> None:
    assert isinstance(getattr(Config, "VAD_AGGRESSIVENESS", None), int), (
        "VAD_AGGRESSIVENESS is not an int attribute on Config"
    )
    src = inspect.getsource(Config)
    assert re.search(r"^\s+VAD_AGGRESSIVENESS\s*:\s*int", src, re.MULTILINE), (
        "VAD_AGGRESSIVENESS is not DECLARED as a Config field, so .env is "
        "silently ignored and the comments claiming otherwise are false (D40)"
    )


def test_it_reads_from_the_environment() -> None:
    assert 'os.getenv("VAD_AGGRESSIVENESS")' in _declaration(), (
        "the field no longer reads the environment variable, so it is a "
        "constant again (D40)"
    )


def test_the_default_is_unchanged() -> None:
    """
    D40 was about making the knob real, NOT about retuning it. 2 is what
    the hardcoded value and the getattr fallback both were, so the default
    must stay 2 or this fix silently changes barge-in behaviour.
    """
    assert "default=2" in _declaration(), (
        "the default moved away from 2; D40 declared the field, it did not "
        "license a behaviour change"
    )


def test_validate_clamps_out_of_range_values() -> None:
    """
    webrtcvad.Vad() raises outside 0-3 and is constructed during boot, so
    an unclamped .env typo would be a startup crash.

    Asserted on validate()'s source rather than by calling it: validate()
    also enforces unrelated startup invariants (it raises without a Gemini
    key), so invoking it here would test the wrong thing and fail for the
    wrong reason. The behavioural guarantee is covered by
    test_a_bad_config_value_cannot_crash_the_filter below, which exercises
    the path that would actually crash.
    """
    # The plain range clamps of validate() live in config._CLAMPS (one row per setting); the loop that applies them is in
    # validate() itself.
    import config as _config

    assert ("VAD_AGGRESSIVENESS", 0, 3) in _config._CLAMPS, (
        "validate() no longer clamps VAD_AGGRESSIVENESS to webrtcvad's own 0-3 range, so a value outside it in .env "
        "crashes SpeechToText at construction (D40)"
    )
    assert "_CLAMPS" in inspect.getsource(Config.validate)


def test_a_bad_config_value_cannot_crash_the_filter() -> None:
    """
    The behaviour the clamp exists for: whatever lands in the field,
    constructing the filter must not raise. This is the call that happens
    during SpeechToText.__init__.
    """
    from sara.audio.stt.buffers import _VADFilter

    saved = Config.VAD_AGGRESSIVENESS
    try:
        for bad in (9, -4, 0, 3, 2, 99):
            Config.VAD_AGGRESSIVENESS = bad
            _VADFilter(16000)  # must not raise
    finally:
        Config.VAD_AGGRESSIVENESS = saved


def test_vadfilter_clamps_independently_of_validate() -> None:
    """
    _VADFilter cannot assume Config.validate() has run, so it keeps its own
    clamp. Both layers are needed; neither is redundant.
    """
    from sara.audio.stt import buffers

    src = inspect.getsource(buffers._VADFilter.__init__)
    assert "max(0, min(3, aggressiveness))" in src, (
        "_VADFilter lost its own defensive clamp (D40)"
    )


def test_the_filter_actually_reads_the_field() -> None:
    from sara.audio.stt import buffers

    src = inspect.getsource(buffers._VADFilter.__init__)
    assert 'getattr(Config, "VAD_AGGRESSIVENESS"' in src, (
        "_VADFilter no longer consults Config.VAD_AGGRESSIVENESS, so the "
        "field is declared but unused (D40)"
    )
