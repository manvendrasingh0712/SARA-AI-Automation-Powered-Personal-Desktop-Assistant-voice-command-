"""
D76: "close window" must close the active window, and "close windows
explorer" must not.

TWO DEFECTS IN ONE PATTERN

    r"close (?:this|the|current) window"

  1. MISSED PHRASINGS. One determiner was REQUIRED, out of exactly three.
     Everything else fell through to `close_app` -- which sits at route
     position 139, after this intent at 55 -- carrying a nonsense app name:

         "close window"             -> close_app['window']
         "close active window"      -> close_app['active window']
         "close the active window"  -> close_app['active window']
         "close my window"          -> close_app['my window']

     `close_app` then looks for a running process by that name. On Windows
     that is not a harmless no-op: "window" is a substring of real process
     names, so the wrong thing can be closed.
     `bench/golden/intents_en_noisy.jsonl` row 55 is "close window".

  2. OVER-MATCH, already live before this fix. With no boundary after
     "window", `pattern.search()` matched "close the window" INSIDE a
     longer name:

         "close the windows explorer"  -> close_active_window

     so asking to close Windows Explorer closed whatever happened to be in
     front. "close windows terminal" and "close windows security" escaped
     only because they have no determiner -- the bug was one word away from
     being much more visible.

THE FIX

    r"close (?:(?:this|the|my|current|active)\\s+)*window$"

  The determiners become optional and repeatable ("the active window"), and
  the `$` is what stops "windows <anything>" matching -- the same anchoring
  lesson as D73, where an unanchored alternative silently took over three
  sentences that the 590-row bench could not see.

  Trailing politeness ("close the window please") is deliberately NOT
  covered: it was not covered before either, and `_FILLER` absorbs only
  leading prefixes. Recorded rather than guessed at.
"""
from __future__ import annotations

import pytest

from sara.core.intent import engine as ie


@pytest.mark.parametrize(
    "text",
    [
        "close window",
        "close the window",
        "close this window",
        "close current window",
        "close the current window",
        "close active window",
        "close the active window",
        "close my window",
    ],
)
def test_the_active_window_phrasings_all_land_on_one_intent(text) -> None:
    intent, _m = ie.detect_intent(text)
    assert intent == "close_active_window", (
        f"{text!r} routed to {intent!r}. Anything that misses this intent "
        f"reaches close_app with a nonsense app name and can close the "
        f"wrong process (D76)"
    )


@pytest.mark.parametrize(
    "text, expected_target",
    [
        ("close windows explorer", "windows explorer"),
        ("close the windows explorer", "windows explorer"),
        ("close windows terminal", "windows terminal"),
        ("close windows security", "windows security"),
        ("close window manager", "window manager"),
        ("close chrome window", "chrome window"),
        ("close all windows", "all windows"),
    ],
)
def test_a_named_window_app_is_not_claimed_as_the_active_window(
    text, expected_target
) -> None:
    """
    The over-match half. `"close the windows explorer"` used to route to
    close_active_window, i.e. closing Windows Explorer closed whatever was
    focused instead.
    """
    intent, match = ie.detect_intent(text)
    assert intent == "close_app", (
        f"{text!r} routed to {intent!r}; a named app must reach close_app, "
        f"not be treated as the active window (D76)"
    )
    assert match is not None and match.group(1) == expected_target, (
        f"{text!r} captured {match.group(1)!r} rather than "
        f"{expected_target!r} (D76)"
    )


def test_the_english_pattern_is_boundary_anchored() -> None:
    """
    Structural backstop. The phrasings above are examples; this fails the
    moment the end anchor goes, which is what allowed "windows explorer" to
    be eaten.

    Scoped to the ENGLISH pattern. This intent also carries a Hinglish
    pattern registered from patterns_multilingual.py, and that one needs no
    `$`: it requires `window\\s+` followed by a closing verb
    ("ye window band kar do"), and "windows explorer" has an "s" where that
    whitespace must be, so it cannot over-match the same way. An earlier
    version of this test asserted over every pattern and failed on it --
    a guard that fires on correct code teaches people to edit the guard.
    """
    from sara.core.intent import patterns as P

    routes = dict(
        getattr(P, "INTENT_PATTERNS", None) or getattr(P, "_INTENT_PATTERNS")
    )
    pats = routes.get("close_active_window")
    assert pats, "close_active_window is no longer a registered intent"

    english = [p for p in pats if p.startswith("close ")]
    assert english, (
        f"no English close_active_window pattern found among {pats!r}; "
        f"re-scope this test (D76)"
    )
    for p in english:
        assert p.rstrip().endswith("$"), (
            f"the English close_active_window pattern is not end-anchored: "
            f"{p!r}. Unanchored, search() matches 'close the window' inside "
            f"'close the windows explorer' (D76)"
        )


def test_the_hinglish_pattern_cannot_over_match_a_named_window_app() -> None:
    """
    The claim the test above rests on: the Hinglish pattern is safe without
    a `$` because it requires whitespace after "window". Asserted
    behaviourally so the reasoning cannot quietly stop being true.
    """
    for text in (
        "windows explorer band kar do",
        "windows terminal band karo",
    ):
        intent, _m = ie.detect_intent(text)
        assert intent != "close_active_window", (
            f"{text!r} was claimed as the active window by the Hinglish "
            f"pattern; it needs the same end boundary the English one has "
            f"(D76)"
        )


def test_close_active_window_is_tried_before_close_app() -> None:
    """
    The fix relies on route ORDER: widening this pattern only helps because
    close_active_window (55) is tried before close_app (139). If close_app
    ever moved ahead of it, every phrasing above would be swallowed again
    and the tests above would be the only warning.
    """
    from sara.core.intent import patterns as P

    names = [
        n for n, _ in (
            getattr(P, "INTENT_PATTERNS", None) or getattr(P, "_INTENT_PATTERNS")
        )
    ]
    assert "close_active_window" in names and "close_app" in names
    assert names.index("close_active_window") < names.index("close_app"), (
        "close_app is now matched before close_active_window, so "
        "'close window' is claimed as an app name again (D76)"
    )


def test_negating_it_still_refuses() -> None:
    """
    D74's guard decides by matching the remainder, so a newly-reachable
    intent must also become newly-refusable. If this fails, "don't close
    window" closes the window.
    """
    for phrase in ("don't close window", "do not close the active window"):
        intent, _m = ie.detect_intent(phrase)
        assert intent == "chat", (
            f"{phrase!r} routed to {intent!r} -- a negated window close "
            f"would execute (D74/D76)"
        )
