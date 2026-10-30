"""
D73: bare "volume up" / "volume down" route to set_volume, and the pattern
that does it must not match inside a longer sentence.

THE GAP
    All four original set_volume patterns require the VERB first:

        (?:set|change|put) (?:the )?volume (?:to |at )?(\\d{1,3})%?
        volume (?:to |at )?(\\d{1,3})%?
        (?:increase|raise|turn up) (?:the )?volume(?: by (\\d+)%?)?
        (?:decrease|lower|turn down|reduce) (?:the )?volume(?: by (\\d+)%?)?

    So "turn up the volume" worked and bare "volume up" -- object first,
    which is how people say it out loud -- fell through to `chat`.
    bench/golden/adversarial.jsonl row 41 records it as
    "known miss: bare 'volume up' means set_volume, not chat".

WHY MOST OF THIS FILE IS ABOUT OVER-MATCHING
    `_detect_intent_cached` matches with `pattern.search()`, so the obvious
    fix

        r"(?:volume|sound) (?:up|down)(?: by (\\d+)%?)?"

    matches the substring "volume up" anywhere in the input. Shipped
    unanchored it took over three phrases that correctly went to chat:

        "don't turn the volume up"    -> set_volume   (a NEGATED command)
        "volume up mat karo"          -> set_volume   (negated, Hinglish)
        "what is the volume up to"    -> set_volume   (a question)

    The negation guard does NOT catch the first two: _ACTION_VERBS has no
    "turn up", so is_negated_action_request() returns False and the match
    stands. A user saying "don't turn the volume up" would have had the
    volume changed.

    **None of the three is in bench/golden**, so the row-level diff over
    all 590 golden rows reported zero regressions while all three were
    broken. They were found by probing adjacent phrasings by hand. That is
    the reason this file leans on negative cases: the golden set is the
    regression net for the rows it contains, and nothing else.

    The fix anchors with the file's existing `_FILLER` idiom (^ plus the
    wake/politeness prefixes) and a trailing $, the same shape the D19 fix
    used on `unmute` for the same class of over-match.
"""
from __future__ import annotations

import pytest

from sara.core.intent import engine as ie


# ── the gap that was closed ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "volume up",
        "volume down",
        "volume up by 10",
        "volume down by 5",
        # _FILLER absorbs the wake-word and politeness prefixes
        "sara volume up",
        "hey sara volume up",
        "please volume down",
        "  volume up  ",
    ],
)
def test_object_first_volume_commands_route_to_set_volume(text) -> None:
    got, _m = ie.detect_intent(text)
    assert got == "set_volume", (
        f"{text!r} routed to {got!r}; object-first word order is how this is "
        f"actually spoken and bench/golden records it as a known miss (D73)"
    )


# ── the over-match cases, which the golden set does not cover ───────────
@pytest.mark.parametrize(
    "text, why",
    [
        ("don't turn the volume up",
         "a NEGATED command -- the worst possible false positive here, and "
         "is_negated_action_request() does not catch it because _ACTION_VERBS "
         "has no 'turn up'"),
        ("volume up mat karo", "negated in Hinglish, same gap"),
        ("do not turn the volume up", "negated, spelled out"),
        ("what is the volume up to", "a question, not a command"),
        ("i need the volume up", "a statement about wanting it"),
        ("volume up the music and open chrome",
         "a compound utterance -- matching the prefix would run the wrong half"),
        ("can you tell me how to turn volume up",
         "a request for instructions, not for the action"),
    ],
)
def test_the_pattern_does_not_match_inside_a_longer_sentence(text, why) -> None:
    got, _m = ie.detect_intent(text)
    assert got != "set_volume", (
        f"{text!r} routed to set_volume. This is {why}. The D73 pattern must "
        f"stay anchored (_FILLER ... $) -- an unanchored search() form "
        f"regresses exactly these (D73)"
    )


def test_the_pattern_is_actually_anchored() -> None:
    """
    Structural backstop for the cases above. They are phrasings, and a
    future edit could satisfy them by accident while leaving the pattern
    unanchored; this fails the moment the anchors go.
    """
    from sara.core.intent import patterns as P

    routes = {name: pats for name, pats in P.INTENT_PATTERNS} if hasattr(
        P, "INTENT_PATTERNS"
    ) else None
    if routes is None:
        routes = {
            name: pats
            for name, pats in getattr(P, "_INTENT_PATTERNS", [])
        }
    assert "set_volume" in routes, "set_volume is no longer a registered intent"

    object_first = [
        p for p in routes["set_volume"]
        if "up|down" in p or "up)|" in p
    ]
    assert object_first, (
        "the object-first volume alternative is gone; bare 'volume up' is "
        "back to routing to chat (D73)"
    )
    for p in object_first:
        assert p.startswith("^") or "^[\\s,]*" in p, (
            f"the object-first volume pattern is not anchored at the start: "
            f"{p!r}. Unanchored, search() matches 'volume up' inside "
            f"\"don't turn the volume up\" (D73)"
        )
        assert p.rstrip().endswith("$"), (
            f"the object-first volume pattern is not anchored at the end: "
            f"{p!r}. Unanchored, it matches the prefix of 'volume up the "
            f"music and open chrome' (D73)"
        )


# ── nothing that already worked may move ────────────────────────────────
@pytest.mark.parametrize(
    "text, want",
    [
        ("turn up the volume", "set_volume"),
        ("turn down the volume", "set_volume"),
        ("increase the volume", "set_volume"),
        ("lower the volume", "set_volume"),
        ("set volume to 40", "set_volume"),
        ("set the volume to 100", "set_volume"),
        ("volume 70", "set_volume"),
        ("volume to 30", "set_volume"),
        ("mute", "mute"),
        ("unmute", "unmute"),
        ("open sound settings", "open_sound_settings"),
    ],
)
def test_the_existing_volume_forms_are_unchanged(text, want) -> None:
    got, _m = ie.detect_intent(text)
    assert got == want, f"{text!r} moved from {want!r} to {got!r} (D73)"


def test_brightness_is_not_swallowed() -> None:
    """The nearest neighbouring intent with the same verb shapes."""
    got, _m = ie.detect_intent("turn up the brightness")
    assert got != "set_volume", (
        f"'turn up the brightness' routed to set_volume (got {got!r}); the "
        f"volume patterns are reaching past their own object (D73)"
    )


def test_sound_gate_widening_was_measured() -> None:
    """
    The set_volume gate was ("volume",) on purpose: widening it was not
    measured (D73). It now admits "sound"/"audio"/"louder"/"quieter"/
    "softer" for "turn the sound up" and "make it louder" (E2E held-out
    test 2026-10-29), measured against the D73 requirement: golden router
    bench row diff unchanged (590 rows, 99.3 %, 0/60 false triggers) and
    the over-match probes below still stay chat. The pre-existing patterns
    all spell "volume" literally, so the wider gate only reaches the new
    anchored ones.
    """
    from sara.core.intent import detect_intent
    from sara.core.intent import patterns as P

    gates = getattr(P, "INTENT_GATES", None) or getattr(P, "_INTENT_GATES", {})
    gate = gates.get("set_volume")
    assert gate is not None, "set_volume lost its keyword gate"
    for probe in (
        "sound engineering is a cool career",
        "the movie was louder than i expected",
        "that sounds good",
        "can you tell me how to turn the sound up",
        "audio quality of this mic is bad",
    ):
        detect_intent.cache_clear()
        assert detect_intent(probe)[0] == "chat", probe
    got, _m = ie.detect_intent("sound up")
    assert got != "set_volume", (
        "'sound up' now routes to set_volume, which means the gate changed; "
        "see the comment above (D73)"
    )
