"""
A regression gate on STREAMING LATENCY for the first TTS chunk.

WHAT WAS MISSING
    tests/test_tts_first_chunk_latency.py guards SAFETY -- that v2 never
    cuts mid-token, mid-number, inside a URL or after an abbreviation. It
    does not guard LATENCY: nothing pinned how much of the stream has to
    arrive before any audio can start. So a chunker change could push first
    audio later on every reply and the suite would stay green.

    These tests pin that budget.

WHAT IS AND IS NOT MEASURED
    The unit here is CHARACTERS OF STREAM, not milliseconds. Kokoro is not
    installable in this environment, so synthesis time is NOT measured and
    is not claimed anywhere. Characters-to-first-cut is the part of
    time-to-first-audio the chunker controls, and it is fully determined by
    pure text logic -- so it is exactly the part that can be pinned here.
    The other half (how long Kokoro then takes) is telemetry's
    `tts_start_ms` and has to be read on the Windows machine.

THE ACKNOWLEDGEMENT PATTERN, AND WHY IT IS NOT A BUG
    Measured across realistic SARA replies, the worst cases all share a
    shape: a short leading acknowledgement followed by the real content.

        'Done! Screenshot saved to your Pictures folder.'   -> 47 chars
        'Yes. The volume is now at 60 percent.'             -> 37 chars
        'Sure! Opening Chrome for you now.'                 -> 33 chars
        'No. That file does not exist.'                     -> 29 chars

    In each one the FIRST terminator ("Done!", "Yes.") is rejected by
    MIN_FIRST_CHARS (8), so the cut lands on the second sentence end and
    the chunk contains both sentences.

    That is deliberate, not an oversight. MIN_FIRST_CHARS mirrors
    engine._MIN_CHUNK and exists because a 5-character first chunk means
    audio starts sooner and is then followed by an audible gap while the
    rest synthesises. Whether that trade is worth taking depends entirely
    on synthesis speed -- and on the target machine Kokoro currently runs
    on the CPU at `synthesis -> first audio p50 = 1250 ms` (D55), where the
    gap would be plainly audible. Merging to the next sentence end also
    keeps prosody intact, which cutting after "Done!" would not.

    So the floor is left alone, and these tests pin the consequence so that
    if D55's onnxruntime-gpu change lands and synthesis gets several times
    faster, the decision can be revisited against a recorded baseline
    rather than from scratch.
"""
from __future__ import annotations

import pytest

from sara.audio.tts.chunker import (
    CLAUSE_MIN_CHARS,
    MIN_FIRST_CHARS,
    WORD_MIN_CHARS,
    first_chunk_end,
)


def chars_to_first_audio(reply: str) -> int | None:
    """
    How many characters of a word-by-word stream must arrive before the
    chunker releases a first chunk. None means it never released one and
    would wait for the stream to end.
    """
    buf = ""
    for word in reply.split(" "):
        buf += word + " "
        if first_chunk_end(buf, False) is not None:
            return len(buf.rstrip())
    return None


# ── the thresholds these budgets derive from ────────────────────────────
def test_the_governing_constants_are_unchanged() -> None:
    """
    Every budget below is a consequence of these three numbers. If one
    moves, the budgets are stale and must be re-measured rather than
    nudged -- so fail loudly here instead of further down.
    """
    assert MIN_FIRST_CHARS == 8, (
        "MIN_FIRST_CHARS moved. Lower means earlier audio but an audible "
        "gap on a slow synthesiser; higher means later audio on every "
        "acknowledgement-first reply. Re-measure the budgets below."
    )
    assert CLAUSE_MIN_CHARS == 25
    assert WORD_MIN_CHARS == 60


# ── short confirmations must start almost immediately ───────────────────
@pytest.mark.parametrize(
    "reply,budget",
    [
        ("Opened chrome.", 16),
        ("Theek hai! Chrome khol diya.", 12),
        ("Okay, Chrome is open.", 23),
        ("Haan, main sun raha hoon.", 27),
    ],
)
def test_short_confirmations_start_fast(reply: str, budget: int) -> None:
    got = chars_to_first_audio(reply)
    assert got is not None, f"{reply!r} never released a first chunk"
    assert got <= budget, (
        f"{reply!r} now needs {got} chars before any audio (budget {budget}). "
        f"These are SARA's most common replies; a regression here is felt on "
        f"every command."
    )


# ── the acknowledgement-first shape: bounded, and understood ────────────
@pytest.mark.parametrize(
    "reply,budget",
    [
        ("No. That file does not exist.", 31),
        ("Hmm. Let me check that for you.", 33),
        ("Sure! Opening Chrome for you now.", 35),
        ("Yes. The volume is now at 60 percent.", 39),
        ("Got it. Setting a reminder for 5 PM today.", 44),
        ("Done! Screenshot saved to your Pictures folder.", 49),
    ],
)
def test_acknowledgement_first_replies_stay_within_budget(reply: str, budget: int) -> None:
    """
    These wait for the SECOND sentence because MIN_FIRST_CHARS rejects the
    first. That is the intended trade (see the module docstring). What must
    not happen is it getting worse.
    """
    got = chars_to_first_audio(reply)
    assert got is not None, f"{reply!r} never released a first chunk"
    assert got <= budget, (
        f"{reply!r} now needs {got} chars (budget {budget}); first audio on "
        f"acknowledgement-first replies has regressed"
    )


def test_the_short_leading_sentence_is_merged_not_dropped() -> None:
    """
    The acknowledgement must still be SPOKEN. Rejecting it as a cut point
    is fine; losing it from the chunk would not be.
    """
    reply = "Done! Screenshot saved to your Pictures folder."
    buf = ""
    for word in reply.split(" "):
        buf += word + " "
        cut = first_chunk_end(buf, False)
        if cut is not None:
            part = buf[:cut].strip()
            assert part.startswith("Done!"), (
                f"the leading acknowledgement was dropped from the first "
                f"chunk: {part!r}"
            )
            assert "Screenshot" in part, (
                "the short sentence was cut on its own after all, which "
                "reintroduces the micro-chunk gap MIN_FIRST_CHARS prevents"
            )
            return
    raise AssertionError("no first chunk was ever released")


# ── nothing may wait unboundedly ────────────────────────────────────────
@pytest.mark.parametrize(
    "reply",
    [
        "Sorry, I couldn't find an app called youtube on this computer.",
        "The current Prime Minister of India is Narendra Modi.",
        "Sure here is a long answer with no punctuation at all so the word rule has to carry it",
        # Devanagari with no punctuation at all: the word rule must carry
        # it, and _safe_cut's conjunct guard must not block every position.
        # (An earlier version of this case was 59 chars -- one short of
        # WORD_MIN_CHARS -- and "failed" for the right reason: nothing had
        # fired yet. Kept long enough to actually exercise the rule.)
        "आपका नाम खुश है और मैं आपकी मदद कर सकती हूँ बिलकुल आसानी से और भी बहुत कुछ बता सकती हूँ",
    ],
)
def test_no_reply_waits_past_the_word_rule(reply: str) -> None:
    """
    WORD_MIN_CHARS (60) plus one token is the hard ceiling: beyond that the
    word rule must fire regardless of punctuation, or a reply with no
    commas or full stops would produce no audio until the stream ended.
    """
    got = chars_to_first_audio(reply)
    assert got is not None, (
        f"{reply!r} produced NO early audio at all -- the word rule failed "
        f"to fire, so the user hears nothing until the whole reply is done"
    )
    assert got <= WORD_MIN_CHARS + 25, (
        f"{reply!r} needed {got} chars; the word rule should have fired "
        f"near {WORD_MIN_CHARS}"
    )


def test_an_unpunctuated_reply_under_the_word_rule_correctly_waits() -> None:
    """
    The other side of the ceiling: below WORD_MIN_CHARS, with no sentence
    or clause boundary, there is genuinely nothing safe to cut on, so
    buffering is right. Pinned so the word rule is never "fixed" by
    letting it fire early and chop mid-phrase.
    """
    short_unpunctuated = "आपका नाम खुश है और मैं आपकी मदद कर सकती हूँ बिलकुल"
    assert len(short_unpunctuated) < WORD_MIN_CHARS, "premise stale"
    assert chars_to_first_audio(short_unpunctuated) is None, (
        "a sub-WORD_MIN_CHARS reply with no boundary now cuts anyway, which "
        "means the first chunk can land mid-phrase"
    )


def test_devanagari_positions_are_cuttable_at_all() -> None:
    """
    The conjunct guard must reject SOME positions, not all of them. If
    _safe_cut refused every Devanagari position, Hindi replies would never
    stream -- which is what this file first appeared to show before the
    59-character premise was corrected.
    """
    from sara.audio.tts.chunker import _safe_cut

    text = "आपका नाम खुश है और मैं आपकी मदद कर सकती हूँ बिलकुल आसानी से और भी बहुत कुछ"
    spaces = [i for i, ch in enumerate(text) if ch.isspace() and i >= WORD_MIN_CHARS]
    assert spaces, "premise stale: no spaces past the word rule"
    assert any(_safe_cut(text, i) for i in spaces), (
        "no Devanagari word boundary is considered safe to cut, so Hindi "
        "replies cannot stream at all"
    )


def test_a_single_long_word_cannot_stall_the_stream() -> None:
    """A pathological token must not hold audio hostage forever."""
    got = chars_to_first_audio("x" * 200 + " then some normal words follow here")
    assert got is not None, "a single 200-char token stalled the stream"


# ── the budget table itself must stay honest ─────────────────────────────
def test_budgets_are_not_so_loose_that_they_guard_nothing() -> None:
    """
    A ceiling far above the real value would pass through any regression.
    Each budget is within 5 characters of what is actually measured, so it
    is a gate rather than decoration.
    """
    cases = {
        "Opened chrome.": 16,
        "Okay, Chrome is open.": 23,
        "No. That file does not exist.": 31,
        "Done! Screenshot saved to your Pictures folder.": 49,
    }
    for reply, budget in cases.items():
        got = chars_to_first_audio(reply)
        assert got is not None
        assert budget - got <= 5, (
            f"the budget for {reply!r} is {budget} but it actually needs "
            f"{got}; that slack would hide a regression. Tighten it."
        )
