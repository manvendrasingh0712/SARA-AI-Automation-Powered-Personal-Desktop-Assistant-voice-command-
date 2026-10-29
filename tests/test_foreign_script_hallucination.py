"""
Regression tests for D43: Whisper hallucinations in a script SARA does not
support must not reach the command router.

THE OBSERVED FAILURE (target machine, bench/stt_doctor.py stage 7)
    [STTDiag] listen completed | mode=command | audio=12.26s | text='こんにちは!'

    The user said "open chrome" and stopped. Both existing filters passed
    it through -- _is_hallucinated_repetition() only matches repeated loops,
    and _HALLUCINATION_PHRASES is a list of English YouTube boilerplate --
    so the phantom transcript reached detect_intent(), matched no command
    pattern, and was dispatched to the LLM as conversation. The user's
    command was lost with no error logged anywhere, which is the worst
    shape of failure: silent.

WHY A SCRIPT TEST IS SOUND AND NOT A GUESS
    Config.validate() clamps SARA_LANGUAGE to exactly
    ("english", "hindi", "hinglish"). Everything SARA is built to hear is
    written in Latin or Devanagari, so CJK/Hangul/Arabic/Cyrillic output is
    invented text by construction -- the same failure mode the phrase list
    exists for, in scripts that list could never enumerate.

WHAT KEEPS IT SAFE
    * inert whenever Config.STT_LANGUAGE pins a language, since a user who
      explicitly selects Japanese means it
    * letters only -- digits, punctuation and spaces never decide it
    * needs a 60% majority, so one borrowed glyph in a real command cannot
      discard the command
    * a minimum letter count, so a 1-character transcript is not trivially
      "100% foreign"
"""
from __future__ import annotations

import inspect

import pytest

from sara.audio.stt.helpers import _is_foreign_script_hallucination as _foreign


# ── the exact observed failure ──────────────────────────────────────────
def test_the_observed_japanese_hallucination_is_caught() -> None:
    assert _foreign("こんにちは!", None) is True, (
        "the transcript that actually cost the user a command still passes"
    )


@pytest.mark.parametrize(
    "text",
    [
        "こんにちは!",      # Japanese (the observed one)
        "안녕하세요",        # Hangul
        "привет",           # Cyrillic
        "مرحبا",            # Arabic
        "你好世界",          # CJK
        "สวัสดี",            # Thai
        "Γειά σου",         # Greek
        "שלום",             # Hebrew
    ],
)
def test_unsupported_scripts_are_discarded(text: str) -> None:
    assert _foreign(text, None) is True, f"{text!r} was not caught"


# ── and must never touch what SARA actually hears ───────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "open chrome",
        "Open Chrome.",
        "volume up",
        "क्रोम खोलो",                 # Hindi, Devanagari
        "नमस्ते",
        "open chrome karo",            # Hinglish, Latin
        "Chrome खोल do",               # mixed script, the hardest real case
        "wifi band karo",
        "mera naam Khush hai",
        "what's the time?",
        "screenshot le lo",
    ],
)
def test_supported_languages_are_never_discarded(text: str) -> None:
    assert _foreign(text, None) is False, (
        f"{text!r} was discarded as a hallucination; this is a real command "
        f"in one of SARA's three supported languages"
    )


def test_mixed_script_hinglish_survives() -> None:
    """
    The case most likely to break a naive implementation: a genuine
    Hinglish command that mixes Devanagari and Latin in one utterance.
    Both scripts are allowed, so the foreign ratio must be zero.
    """
    for text in ("Chrome खोल do", "volume कम karo", "YouTube पर गाना chalao"):
        assert _foreign(text, None) is False, f"{text!r} discarded"


# ── the escape hatch ────────────────────────────────────────────────────
@pytest.mark.parametrize("lang", ["ja", "ko", "ru", "ar", "zh"])
def test_pinned_stt_language_disables_the_filter(lang: str) -> None:
    """
    Config.STT_LANGUAGE is free-form by design. A user who pins Japanese
    intends Japanese, and this filter must not override that.
    """
    assert _foreign("こんにちは!", lang) is False, (
        f"the filter still fired with STT_LANGUAGE={lang!r}; an explicitly "
        f"pinned language must be trusted (D43)"
    )


# ── the guards that stop over-matching ──────────────────────────────────
def test_digits_and_punctuation_never_decide() -> None:
    for text in ("99", "!!", "...", "42!", "-", "  "):
        assert _foreign(text, None) is False, f"{text!r} judged on non-letters"


def test_too_few_letters_is_not_judged() -> None:
    """A single letter would trivially be 100% foreign."""
    assert _foreign("あ", None) is False
    assert _foreign("", None) is False


def test_one_stray_glyph_does_not_discard_a_command() -> None:
    """
    The majority threshold is what makes this safe: a real command with a
    stray hallucinated character must survive, because losing a command is
    worse than passing one odd character to the router.
    """
    assert _foreign("open chrome あ", None) is False
    assert _foreign("क्रोम खोलो あ", None) is False


def test_threshold_is_a_clear_majority_not_a_hair_trigger() -> None:
    src = inspect.getsource(
        __import__("sara.audio.stt.helpers", fromlist=["x"])
    )
    assert "_FOREIGN_SCRIPT_RATIO = 0.6" in src, (
        "the foreign-script ratio moved; below ~0.5 a mixed-script Hinglish "
        "command starts being discarded, which loses real commands (D43)"
    )


# ── wired into the real path ────────────────────────────────────────────
def test_filter_is_actually_called_by_transcribe() -> None:
    """
    A filter that exists but is not wired in is worse than none: it reads
    as handled. This pins it to _transcribe, after the other two.
    """
    import sara.audio.stt.engine as stt_engine

    src = inspect.getsource(stt_engine.SpeechToText._transcribe)
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    assert "_is_foreign_script_hallucination(" in code, (
        "the foreign-script filter is no longer called from _transcribe, so "
        "hallucinations like 'こんにちは!' reach the router again (D43)"
    )


def test_filter_reads_the_config_language_not_a_hardcoded_none() -> None:
    """
    Passing None unconditionally would make the escape hatch dead code and
    silently override a user's pinned STT_LANGUAGE.
    """
    import sara.audio.stt.engine as stt_engine

    src = inspect.getsource(stt_engine.SpeechToText._transcribe)
    call = src[src.index("_is_foreign_script_hallucination("):]
    call = call[: call.index(")") + 1]
    assert "STT_LANGUAGE" in call, (
        "the filter is not being passed Config.STT_LANGUAGE, so a pinned "
        "language no longer disables it (D43)"
    )
