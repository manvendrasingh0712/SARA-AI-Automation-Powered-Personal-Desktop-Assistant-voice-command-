"""
D78: "take screenshot" must take a screenshot.

THE GAP
    The only screenshot-taking pattern was

        r"take a screenshot"

    with the article REQUIRED and the compound spelling not accepted, so
    three of the four ways this is actually said went to chat:

        "take screenshot"    -> chat
        "take screen shot"   -> chat
        "capture screen"     -> chat

    Dropping an unstressed "a" is one of the most common things an STT
    engine does, and Whisper writes "screen shot" as two words at least as
    often as one. `bench/golden/adversarial.jsonl` row 32 records exactly
    this, annotated "dropped article in speech-to-text".

    The three `describe my screen` style patterns were unaffected, which is
    why the intent looked covered.

THE ANCHORING, which is the part worth reviewing
    The bare noun form has to be bound to the whole utterance. Unanchored,
    `search()` would fire on any sentence containing the word:

        "where do screenshots go"
        "that screenshot was blurry"
        "i sent you a screenshot yesterday"

    so it uses `_FILLER` (^ plus the wake/politeness prefixes) and `$` --
    the same shape D73 and D76 needed, for the same reason.

    The "capture screen" form likewise has a negative lookahead so it does
    not eat "screen settings" or "screen resolution".
"""
from __future__ import annotations

import pytest

from sara.core.intent import engine as ie


@pytest.mark.parametrize(
    "text",
    [
        "take screenshot",
        "take a screenshot",
        "take screen shot",
        "take a screen shot",
        "grab a screenshot",
        "grab screenshot",
        "capture screenshot",
        "capture screen",
        "capture the screen",
        "take my screen",
        # the bare noun, with the prefixes _FILLER absorbs
        "screenshot",
        "screen shot",
        "sara screenshot",
        "please take screenshot",
    ],
)
def test_the_screenshot_phrasings_all_land_on_one_intent(text) -> None:
    intent, _m = ie.detect_intent(text)
    assert intent == "screenshot_describe", (
        f"{text!r} routed to {intent!r}; only 'take a screenshot' used to "
        f"work and the dropped article is the commonest STT variation "
        f"(D78)"
    )


@pytest.mark.parametrize(
    "text",
    [
        "where do screenshots go",
        "that screenshot was blurry",
        "i sent you a screenshot yesterday",
        "change screen resolution",
        "share my screen",
    ],
)
def test_talking_about_a_screenshot_is_not_taking_one(text) -> None:
    intent, _m = ie.detect_intent(text)
    assert intent != "screenshot_describe", (
        f"{text!r} routed to screenshot_describe. The bare-noun pattern "
        f"must stay bound by _FILLER and $, or search() fires on any "
        f"sentence containing the word (D78)"
    )


@pytest.mark.parametrize(
    "text, want",
    [
        ("open display settings", "open_display_settings"),
        ("lock screen", "lock_pc"),
        # the three original patterns must be untouched
        ("what's on my screen", "screenshot_describe"),
        ("describe my screen", "screenshot_describe"),
        ("look at my screen", "screenshot_describe"),
    ],
)
def test_neighbouring_screen_intents_are_unchanged(text, want) -> None:
    intent, _m = ie.detect_intent(text)
    assert intent == want, f"{text!r} moved from {want!r} to {intent!r} (D78)"


def test_the_bare_noun_pattern_is_bound_at_both_ends() -> None:
    """
    Structural backstop for the phrasings above: they are examples, and a
    future edit could satisfy them while leaving the bare form unanchored.
    """
    from sara.core.intent import patterns as P

    routes = dict(
        getattr(P, "INTENT_PATTERNS", None) or getattr(P, "_INTENT_PATTERNS")
    )
    pats = routes.get("screenshot_describe")
    assert pats, "screenshot_describe is no longer a registered intent"

    # Selected by the shape the fix uses: the bare-noun pattern is the only
    # one built from _FILLER, which expands to `^[\s,]*(?:please |sara |...)*`.
    #
    # An earlier selector took "every pattern mentioning screen without an
    # English verb" and picked up the Hinglish pattern registered from
    # patterns_multilingual.py ("screen pe kya chal raha hai"), which is
    # start-anchored by \b and a required Hindi postposition instead. That
    # is the same mis-scoping D76's structural test hit -- selecting
    # patterns by what they lack finds the wrong ones.
    bare = [p for p in pats if p.startswith("^[\\s,]*")]
    assert bare, (
        f"no _FILLER-anchored screenshot pattern found among {pats!r}; the "
        f"bare-noun form is gone or no longer uses _FILLER (D78)"
    )
    for p in bare:
        assert p.rstrip().endswith("$"), (
            f"the bare screenshot pattern is not anchored at the end: "
            f"{p!r} -- it would match 'that screenshot was blurry' (D78)"
        )


def test_capture_screen_does_not_eat_screen_settings() -> None:
    """
    The `capture ... screen` alternative carries a negative lookahead so it
    stops at the bare noun. Without it, "capture screen settings" and
    "capture screen resolution" would be claimed.
    """
    for text in ("capture screen settings", "capture screen resolution"):
        intent, _m = ie.detect_intent(text)
        assert intent != "screenshot_describe", (
            f"{text!r} was claimed as a screenshot; the lookahead after "
            f"`screen\\b` is gone (D78)"
        )


def test_negating_it_still_refuses() -> None:
    """D74's guard works off the remainder, so a newly-reachable intent
    must also be newly refusable."""
    for phrase in ("don't take screenshot", "do not take a screenshot"):
        intent, _m = ie.detect_intent(phrase)
        assert intent == "chat", (
            f"{phrase!r} routed to {intent!r} -- a negated screenshot would "
            f"be taken (D74/D78)"
        )
