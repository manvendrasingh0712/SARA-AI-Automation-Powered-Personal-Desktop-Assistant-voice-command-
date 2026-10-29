"""
D77: "set a timer for ten minutes" must set a timer.

THE DEFECT
    `_parse_duration_to_seconds` was digit-only:

        (r"(\\d+)\\s*(?:hour|hr)s?",   3600)
        (r"(\\d+)\\s*(?:minute|min)s?",  60)
        (r"(\\d+)\\s*(?:second|sec)s?",   1)

    so every duration said out loud failed outright:

        "set a timer for ten minutes"   -> "Sorry, I couldn't understand
                                            the duration 'ten minutes'."
        "set a timer for half an hour"  -> same

WHY THE BENCH NEVER SHOWED IT
    bench/golden checks the INTENT, not the slot. The router returned
    set_timer correctly in both cases, so both counted as passes.

    The clearest example is adversarial row 37, "long rambling sentence
    with one embedded command":

        "so i was talking to my brother yesterday about the trip and
         honestly it was a lot of fun but anyway can you please set a timer
         for fifteen minutes because the tea is on the stove"

    It is recorded as PASSING, and the timer it embeds did nothing. The
    number word is what defeated the parser, not the trailing words -- a
    slot containing a digit tolerates trailing text fine
    ("90 seconds and then remind me" -> 90).

    That is a structural blind spot, not a missing row: a whole class of
    "right intent, dead command" defects is invisible to an intent-only
    bench. These tests assert on the parsed SECONDS for that reason.

SCOPE
    The converter is called ONLY from `_parse_duration_to_seconds`. It is
    deliberately not wired into intent routing: it would rewrite
    "open one note" to "open 1 note", and the measured win there is one
    bench row ("set volume to fifty") against a real risk to app names. A
    test below pins that separation.
"""
from __future__ import annotations

import pytest

from sara.core.intent import engine as ie
from sara.orchestrator.calc_utils import (
    _digits_for_number_words,
    _parse_duration_to_seconds,
)


# ── the defect ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text, seconds",
    [
        ("ten minutes", 600),
        ("fifteen minutes", 900),
        ("twenty minutes", 1200),
        ("thirty minutes", 1800),
        ("five minutes", 300),
        ("one minute", 60),
        ("an hour", 3600),
        ("one hour", 3600),
        ("two hours", 7200),
        ("ninety seconds", 90),
        ("five seconds", 5),
        # compound forms
        ("forty five minutes", 2700),
        ("forty-five minutes", 2700),
        ("twenty two minutes", 1320),
        ("one hour thirty minutes", 5400),
        # "half" is not a count, so it is rewritten before anything else
        ("half an hour", 1800),
        ("half hour", 1800),
        ("half a minute", 30),
    ],
)
def test_spoken_durations_parse(text, seconds) -> None:
    got = _parse_duration_to_seconds(text)
    assert got == seconds, (
        f"{text!r} parsed to {got!r}, not {seconds}. A duration said out "
        f"loud must work -- this is the only way a voice assistant receives "
        f"one (D77)"
    )


def test_the_adversarial_golden_rows_timer_actually_fires() -> None:
    """
    The row the bench records as passing while the command did nothing.
    Asserted end to end: route it, take the slot the pattern captured, and
    parse that.
    """
    text = (
        "so i was talking to my brother yesterday about the trip and "
        "honestly it was a lot of fun but anyway can you please set a timer "
        "for fifteen minutes because the tea is on the stove"
    )
    intent, match = ie.detect_intent(text)
    assert intent == "set_timer", f"routed to {intent!r}"
    slot = match.group(1).strip()
    assert "fifteen" in slot, (
        f"this test assumes the slot still carries the number word; got "
        f"{slot!r}"
    )
    assert _parse_duration_to_seconds(slot) == 900, (
        f"the slot {slot!r} does not parse to 15 minutes, so the bench "
        f"counts this row as passing while no timer is set (D77)"
    )


# ── nothing that worked may break ───────────────────────────────────────
@pytest.mark.parametrize(
    "text, seconds",
    [
        ("10 minutes", 600),
        ("5 minutes please", 300),
        ("2 hours", 7200),
        ("30 seconds", 30),
        ("1 hour 30 minutes", 5400),
        ("90 seconds and then remind me", 90),
        ("45", 2700),  # a bare number has always meant minutes
    ],
)
def test_digit_durations_are_unchanged(text, seconds) -> None:
    got = _parse_duration_to_seconds(text)
    assert got == seconds, f"{text!r} regressed to {got!r} from {seconds} (D77)"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "banana", "soon", "a bit", "later", "whenever", "a lot of fun"],
)
def test_a_non_duration_still_returns_none(text) -> None:
    assert _parse_duration_to_seconds(text) is None, (
        f"{text!r} was read as a duration; the handler would set a timer "
        f"the user never asked for (D77)"
    )


def test_none_and_non_strings_do_not_raise() -> None:
    for junk in (None, 0, [], {}):
        assert _parse_duration_to_seconds(junk) is None


# ── the converter in isolation ──────────────────────────────────────────
@pytest.mark.parametrize(
    "text, expected",
    [
        ("ten minutes", "10 minutes"),
        ("forty five", "45"),
        ("forty-five", "45"),
        ("an hour", "1 hour"),
        ("half an hour", "30 minutes"),
        # untouched: not numbers, or numbers that are not counts
        ("a bit of text", "a bit of text"),
        ("an apple", "an apple"),
        ("a lot of fun", "a lot of fun"),
        ("play never gonna give you up", "play never gonna give you up"),
        ("10 minutes", "10 minutes"),
    ],
)
def test_the_converter_only_rewrites_actual_numbers(text, expected) -> None:
    got = _digits_for_number_words(text)
    assert got == expected, f"{text!r} became {got!r}, expected {expected!r} (D77)"


def test_a_and_an_only_count_in_front_of_a_unit() -> None:
    """
    "a"/"an" mean 1 in "an hour" but not in "a bit". Without that
    restriction the converter would turn "a lot of fun" into "1 lot of fun"
    -- and that string is a real slot, from the rambling adversarial row.
    """
    assert _digits_for_number_words("an hour") == "1 hour"
    assert _digits_for_number_words("a minute") == "1 minute"
    for untouched in ("a bit", "an apple", "a lot of fun", "a timer"):
        assert _digits_for_number_words(untouched) == untouched, (
            f"{untouched!r} had its article read as the number 1 (D77)"
        )


# ── scope: it must not reach intent routing ─────────────────────────────
def test_the_converter_is_not_wired_into_intent_routing() -> None:
    """
    Deliberate scope limit. Applied to intent text the converter would
    rewrite "open one note" to "open 1 note", and the measured gain in the
    router is a single bench row ("set volume to fifty") against a real
    risk to app names.

    Asserted two ways: the demonstration that it WOULD change that text,
    and that routing does not.
    """
    assert _digits_for_number_words("open one note") == "open 1 note", (
        "this test's premise is gone; re-derive it"
    )
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(ie)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_digits_for_number_words" not in called, (
        "the number-word converter is now called from intent routing. That "
        "rewrites app names ('open one note' -> 'open 1 note'); if it is "
        "wanted there, measure it with the golden row diff first (D77)"
    )


def test_it_does_not_contradict_the_memory_number_table() -> None:
    """
    sara/core/memory2/timeparse.py keeps its own smaller _NUMWORD (1-12,
    plus Hindi "ek"/"एक") for relative date windows. The tables stay
    separate -- merging them would couple memory2 to the orchestrator for
    no benefit -- but two tables that must not contradict each other is
    exactly the shape of the D74/D75 defects, so the overlap is pinned.
    """
    from sara.core.memory2 import timeparse
    from sara.orchestrator import calc_utils

    theirs = timeparse._NUMWORD
    mine = {**calc_utils._NUMBER_WORDS, **calc_utils._NUMBER_TENS}
    shared = set(theirs) & set(mine)
    assert shared, "the two number tables no longer overlap; re-scope this test"
    for word in sorted(shared):
        assert theirs[word] == mine[word], (
            f"the two number-word tables disagree on {word!r}: "
            f"timeparse says {theirs[word]}, calc_utils says {mine[word]} "
            f"(D77)"
        )
