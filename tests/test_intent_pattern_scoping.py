"""
Regression tests for D19 and D21 (audit batch C): intent patterns whose
optional groups were scoped wrongly.

Both defects came from the same mistake in opposite directions, which is why
they are tested together: an optional group that was too permissive (D19) and
optional groups that were too restrictive (D21).

D19 -- unmute swallowed three unrelated intents
-----------------------------------------------
The pattern was

    r"(?:unmute|restore|enable)(?: (?:the )?(?:volume|sound|audio|mic|microphone))?"

with the audio object OPTIONAL for every alternative, so it reduced to a bare
match on "unmute", "restore" OR "enable". "restore" and "enable" are generic
verbs, so unmute quietly captured other intents. Reproduced before the fix:

    "enable the bluetooth"          -> unmute   (want bluetooth_on)
    "uh enable the bluetooth"       -> unmute   (want bluetooth_on)
    "enable airplane mode"          -> unmute   (want open_airplane_mode_settings)
    "uh enable airplane mode"       -> unmute   (want open_airplane_mode_settings)
    "restore my minimized windows"  -> unmute   (want restore_windows)
    "restore minimized windows"     -> unmute   (want restore_windows)

This was also a measurable SAFETY problem, not only an accuracy one: generic
verbs are exactly what adversarial input contains, and fixing this one pattern
moved `false_trigger_adversarial` from 20.83% to 12.50% on bench's 590 rows.

D21 -- three commands missed because a group accepted only one word
-------------------------------------------------------------------
    paste_clipboard  r"paste(?: (?:it|this|that|here))?$"
        accepted ONE of it/this/that/here, so "paste it here" missed.

    minimize_active_window  r"minimize (?:this|the|current) window"
        accepted ONE determiner, so "minimize the current window" missed.

    typing_text  r"type (?:this )?(?:for me)?[:\\s]+(.+)"
        the literal "type " already consumed the only space, so [:\\s]+
        demanded a SECOND separator. "type this: X" missed because
        `(?:this )?` needs "this" plus a space and the text has "this:".
        Only "type: X" or "type  X" (double space) ever matched.

All three fell through to `chat`, and for these three
`has_probable_tool_intent()` is False, so no later stage recovered them.

WHY typing_text STILL REQUIRES A COLON
--------------------------------------
The obvious repair -- matching bare "type <text>" -- would be a new D19. "type"
occurs in ordinary chat ("what type of file is this", "this type of problem"),
and a pattern matching bare "type <anything>" captures the rest of such a
sentence as dictation. The sibling "write" pattern already requires a colon for
precisely this reason, and typing_text now follows it. So "type X" without a
colon is deliberately NOT matched, and
test_ordinary_chat_is_not_a_typing_command pins that.
"""
from __future__ import annotations

import pytest

from sara.core.intent import detect_intent


# ── D19: the collision ──────────────────────────────────────────────────
# Every one of these was observed returning "unmute" before the fix.
D19_CASES = [
    ("enable the bluetooth", "bluetooth_on"),
    ("uh enable the bluetooth", "bluetooth_on"),
    ("enable airplane mode", "open_airplane_mode_settings"),
    ("uh enable airplane mode", "open_airplane_mode_settings"),
    ("restore my minimized windows", "restore_windows"),
    ("restore minimized windows", "restore_windows"),
]

# unmute must keep working -- the fix must not simply narrow it to nothing.
UNMUTE_STILL_WORKS = [
    "unmute",
    "unmute sound",
    "unmute the sound",
    "unmute mic",
    "restore the volume",
    "restore audio",
    "enable sound",
    "enable the microphone",
]


@pytest.mark.parametrize("text,expected", D19_CASES)
def test_generic_verbs_do_not_route_to_unmute(text: str, expected: str) -> None:
    """D19: 'enable'/'restore' with a non-audio object are not unmute."""
    got, _ = detect_intent(text)
    assert got == expected, (
        f"{text!r} routed to {got!r}; D19 is back -- check that the audio "
        f"object is still REQUIRED for the restore/enable alternatives"
    )


@pytest.mark.parametrize("text", UNMUTE_STILL_WORKS)
def test_real_unmute_phrasings_still_match(text: str) -> None:
    got, _ = detect_intent(text)
    assert got == "unmute", f"{text!r} no longer reaches unmute (got {got!r})"


def test_unmute_pattern_has_no_capturing_groups() -> None:
    """
    engine._merge_groupless() only merges an intent's patterns when every one
    compiles with zero capturing groups. The D19 fix kept that property, which
    is what preserves the startup-cost win from D11; a future edit that adds a
    capture group here would silently cost it.
    """
    from sara.core.intent.patterns import _INTENT_PATTERNS

    patterns = dict(_INTENT_PATTERNS)["unmute"]
    import re

    for raw in patterns:
        assert re.compile(raw).groups == 0, (
            f"unmute pattern {raw!r} now has capturing groups, which stops "
            f"_merge_groupless() from merging it"
        )


# ── D21: the three missed commands ──────────────────────────────────────
D21_CASES = [
    # paste_clipboard -- two optional words, not one
    ("paste it here", "paste_clipboard"),
    ("paste here", "paste_clipboard"),
    ("paste it", "paste_clipboard"),
    ("paste", "paste_clipboard"),
    ("paste this", "paste_clipboard"),
    # minimize_active_window -- determiner AND qualifier
    ("minimize the current window", "minimize_active_window"),
    ("minimize this window", "minimize_active_window"),
    ("minimize the active window", "minimize_active_window"),
    ("minimize current window", "minimize_active_window"),
    ("minimize window", "minimize_active_window"),
    # typing_text -- the colon forms
    ("type this: see you tomorrow", "typing_text"),
    ("type: hello world", "typing_text"),
    ("type this for me: hi", "typing_text"),
    ("type out: abc", "typing_text"),
]


@pytest.mark.parametrize("text,expected", D21_CASES)
def test_missed_commands_now_match(text: str, expected: str) -> None:
    got, _ = detect_intent(text)
    assert got == expected, f"{text!r} routed to {got!r}, expected {expected!r}"


@pytest.mark.parametrize(
    "text,expected_capture",
    [
        ("type this: see you tomorrow", "see you tomorrow"),
        ("type: hello world", "hello world"),
        ("type this for me: hi", "hi"),
        ("type out: abc", "abc"),
    ],
)
def test_typing_text_captures_only_the_dictated_text(text: str, expected_capture: str) -> None:
    """
    The handler types group(1) verbatim. A pattern that matches but captures
    the wrong span would type the command words too, which is worse than a
    miss because it is silent.
    """
    intent, match = detect_intent(text)
    assert intent == "typing_text"
    assert match is not None and match.groups(), "typing_text lost its capture group"
    assert match.group(1) == expected_capture, (
        f"captured {match.group(1)!r}, expected {expected_capture!r}"
    )


# ── The over-match guard: these must all stay chat ──────────────────────
# Widening a pattern is exactly how D19 was created, so every widening in
# batch C is paired with the sentences it could plausibly have swallowed.
MUST_STAY_CHAT = [
    # "type" in ordinary use -- the reason typing_text still needs a colon
    "what type of file is this",
    "this type of problem is hard",
    "what type of person says that",
    "type of thing i like",
    # "paste" used as an ordinary verb -- the $ anchor is what saves these
    "paste my essay into the document",
    "i will paste the data tomorrow",
    # "minimize" used figuratively -- the literal "window" is what saves these
    "minimize your exposure to risk",
    "how do i minimize the risk here",
    # "write" must still require its colon
    "write my essay for me",
]


@pytest.mark.parametrize("text", MUST_STAY_CHAT)
def test_ordinary_chat_is_not_a_typing_command(text: str) -> None:
    got, _ = detect_intent(text)
    assert got == "chat", (
        f"{text!r} false-triggered {got!r}. A batch-C widening over-matched; "
        f"this is the D19 failure mode repeating."
    )
