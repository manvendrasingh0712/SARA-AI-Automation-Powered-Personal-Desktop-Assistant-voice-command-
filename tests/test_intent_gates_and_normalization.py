"""
Regression tests for D26-D29 (audit batch D22-a).

Four defects, grouped because three of them share one root shape: a matcher
narrower or broader than it was documented to be.

D26 -- scroll_top / scroll_bottom / time_query fired on ordinary prose
----------------------------------------------------------------------
    scroll_top     r"(?:go to |scroll to )?(?:the )?\\btop\\b(?: of (?:the )?page)?"
    scroll_bottom  r"(?:go to |scroll to )?(?:the )?\\bbottom\\b(?: of (?:the )?page)?"

Verb optional AND object optional, so each reduced to a bare keyword:

    "bottom line of the report is we lost money" -> scroll_bottom
    "top of the morning to you"                  -> scroll_top

    time_query     r"what(?:'?s| is) (?:the )?(?:current )?time"

no end anchor, so:

    "what is the time complexity of quicksort"   -> time_query

All three are false triggers DURING ORDINARY CONVERSATION -- SARA scrolled
or answered the clock mid-sentence. The golden set carried all three with
notes saying they must not fire, so this was known-wrong behaviour. Fixing
them took bench's `false_trigger_adversarial` to 0.0000 (from 20.83% at the
start of the audit) and `false_trigger_rate` to 0.0000.

D27 -- a multilingual gate silently disabled the whole English calculator
-------------------------------------------------------------------------
`calculator` is DELIBERATELY ungated. engine.register_intent()'s docstring:
a gate may be omitted "only if no safe substring gate exists for this intent
(matches the existing `calculator` convention in patterns.py -- the one
remaining intent with no gate)" -- because no substring can express
"contains arithmetic".

patterns_multilingual.apply() then did:

    elif gate:
        intent_gates[intent] = tuple(g.lower() for g in gate)

so adding Hinglish calculator rows with a ("kitna", "kya") gate CREATED a
gate where the design requires none, and every English form stopped routing:

    "calculate 245 * 12" -> chat      "25 * 4 kitna hota hai" -> calculator
    "what is 5 + 3"      -> chat      "5 + 3 kitna hai"       -> calculator

The regexes matched throughout; only the pre-filter blocked them, which is
why this looked like a pattern problem and was not. An AST diff of the base
_INTENT_GATES literal against the post-merge dict confirmed `calculator` is
the ONLY intent that gained a gate this way.

D28 -- filler words
-------------------
_FILLER carried politeness words but no disfluencies, and was applied to
only six intents. Fixed narrowly: disfluencies added to _FILLER, an optional
post-verb slot added so an interior filler does not land in the app-name
capture, and _FILLER applied to the two intents in the evidence
(`open_this_pc`, `followup_query`). `open_this_pc` also needed its gate
widened -- same shape as D27, a gate narrower than its own patterns.

D29 -- homophones and split compounds
-------------------------------------
_correct_typos()'s docstring already claims homophone correction, but could
not perform it: _TYPO_MIN_WORD_LEN = 5 skips "two"/"hear"/"paws"/"no", and
difflib compares spelling while homophones differ in spelling by definition.
An explicit map now runs in the same pass, plus whole-string repair for
compounds ASR splits ("wi fi" -> "wifi").

The safety property is structural, not a matter of the map being cautious:
_correct_typos runs ONLY as a second attempt after the exact-spelling pass
found no intent, so no command that works today can reach it.
"""
from __future__ import annotations

import pytest

from sara.core.intent import detect_intent
from sara.core.intent.patterns import _INTENT_GATES


# ── D26: must stay chat ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "bottom line of the report is we lost money",
        "the bottom line is simple",
        "top of the morning to you",
        "he is at the top of his game",
        "what is the time complexity of quicksort",
        "what is the time complexity here",
    ],
)
def test_prose_does_not_trigger_scroll_or_time(text: str) -> None:
    got, _ = detect_intent(text)
    assert got == "chat", (
        f"{text!r} false-triggered {got!r} during ordinary conversation (D26)"
    )


@pytest.mark.parametrize(
    "text,intent",
    [
        ("scroll to the top of the page", "scroll_top"),
        ("scroll to top of the page", "scroll_top"),
        ("go to the top", "scroll_top"),
        ("top of the page", "scroll_top"),
        ("go to the bottom of the page", "scroll_bottom"),
        # a to/two homophone row that passed on the old bare-keyword match
        # and must keep passing on the new object-bearing pattern
        ("go two bottom of the page", "scroll_bottom"),
        ("scroll to the bottom", "scroll_bottom"),
        ("bottom of the page", "scroll_bottom"),
        ("what's the time", "time_query"),
        ("what's time", "time_query"),
        ("what is the current time", "time_query"),
        ("what is the time now", "time_query"),
        ("what time is it", "time_query"),
        ("tell me the time", "time_query"),
    ],
)
def test_real_scroll_and_time_commands_still_work(text: str, intent: str) -> None:
    got, _ = detect_intent(text)
    assert got == intent, f"{text!r} -> {got!r}, expected {intent!r}"


# ── D27: calculator must stay ungated ───────────────────────────────────
def test_calculator_has_no_gate() -> None:
    """
    The documented convention. If a future multilingual addition gives
    calculator a gate again, every English arithmetic form dies silently --
    the regexes keep matching and the pre-filter keeps skipping them.
    """
    assert _INTENT_GATES.get("calculator") is None, (
        "calculator gained a pre-filter gate; English arithmetic will stop "
        "routing (D27). See _BASE_INTENTS_WITHOUT_GATE in "
        "patterns_multilingual.py"
    )


@pytest.mark.parametrize(
    "text",
    [
        "calculate 245 * 12",
        "um calculate 245 * 12",
        "what is 5 + 3",
        "compute 100 / 4",
        "solve 12 * 12",
        "evaluate 2 ^ 8",
        "25 * 4 kitna hota hai",
        "5 + 3 kitna hai",
    ],
)
def test_calculator_routes_in_every_language(text: str) -> None:
    got, _ = detect_intent(text)
    assert got == "calculator", f"{text!r} -> {got!r}"


@pytest.mark.parametrize(
    "text",
    [
        "room 12-15 is free",
        "i work a 9-5 job",
        "call me on 555-1234",
        "version 2.5 is out",
        "i scored 7 out of 10",
    ],
)
def test_ungated_calculator_does_not_false_trigger(text: str) -> None:
    """
    Running ungated means these patterns are tested against every utterance,
    which is the design. The digit/operator shape requirements are what keep
    ordinary number-bearing text out.
    """
    got, _ = detect_intent(text)
    assert got != "calculator", f"{text!r} false-triggered calculator"


# ── D28: fillers ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,intent,capture",
    [
        ("uh open notepad", "open_app", "notepad"),
        # interior filler must not land in the app-name capture
        ("uh open um notepad", "open_app", "notepad"),
        ("open um notepad", "open_app", "notepad"),
        ("um close uh chrome", "close_app", "chrome"),
        ("uh restart um chrome", "restart_application", "chrome"),
        ("um restart chrome", "restart_application", "chrome"),
        ("uh switch to spotify", "switch_to_application", "spotify"),
        # a real app name that merely starts like a filler must survive
        ("open umbrella", "open_app", "umbrella"),
    ],
)
def test_filler_words_are_stripped_not_captured(text: str, intent: str, capture: str) -> None:
    got, match = detect_intent(text)
    assert got == intent, f"{text!r} -> {got!r}, expected {intent!r}"
    assert match is not None and match.groups()
    assert match.group(1).strip() == capture, (
        f"captured {match.group(1)!r}, expected {capture!r} -- a filler leaked "
        f"into the capture"
    )


@pytest.mark.parametrize(
    "text,intent",
    [
        ("uh open pc", "open_this_pc"),
        ("open pc", "open_this_pc"),
        ("open computer", "open_this_pc"),
        ("open this pc", "open_this_pc"),
        ("open my computer", "open_this_pc"),
        ("please what about tomorrow", "followup_query"),
        ("what about tomorrow", "followup_query"),
        ("uh how about friday", "followup_query"),
    ],
)
def test_filler_tolerant_intents(text: str, intent: str) -> None:
    got, _ = detect_intent(text)
    assert got == intent, f"{text!r} -> {got!r}, expected {intent!r}"


@pytest.mark.parametrize("text", ["my computer is slow", "this pc has a problem"])
def test_widened_pc_gate_does_not_false_trigger(text: str) -> None:
    got, _ = detect_intent(text)
    assert got == "chat", f"{text!r} false-triggered {got!r}"


# ── D29: homophones and split compounds ─────────────────────────────────
@pytest.mark.parametrize(
    "text,intent",
    [
        ("switch two study mode", "switch_mode"),
        ("set brightness two 70", "set_brightness"),
        ("snap window two the left", "snap_window_left"),
        ("snap the window to the write", "snap_window_right"),
        ("paste it hear", "paste_clipboard"),
        ("paws the music", "play_pause_media"),
        ("forget everything you no about me", "memory_forget_all"),
        ("turn off wi fi", "wifi_off"),
    ],
)
def test_homophones_are_rescued(text: str, intent: str) -> None:
    got, _ = detect_intent(text)
    assert got == intent, f"{text!r} -> {got!r}, expected {intent!r}"


@pytest.mark.parametrize(
    "text",
    [
        "i have two options",
        "there are two reasons",
        "write me a poem",
        "i will write it down",
        "did you hear that",
        "the dog has big paws",
        "no i dont want that",
        "no thanks",
    ],
)
def test_homophone_map_does_not_turn_chat_into_commands(text: str) -> None:
    """
    The rescue pass only runs when the exact pass found nothing, so these
    sentences DO reach the homophone map. They must still fail every
    pattern's structural requirements afterwards.
    """
    got, _ = detect_intent(text)
    assert got == "chat", (
        f"{text!r} became {got!r} after homophone rescue -- the map is "
        f"rewriting ordinary conversation into a command"
    )


def test_rescue_pass_cannot_change_a_working_command() -> None:
    """
    The structural safety property the whole D29 design rests on: a command
    that already matches never reaches _correct_typos. Asserted here via a
    word that IS in the homophone map and IS a command word: "write".
    """
    intent, match = detect_intent("write: hello there")
    assert intent == "typing_text", (
        "'write: hello there' no longer matches on the exact pass; if it now "
        "falls through to the rescue pass, 'write' would be rewritten to "
        "'right' and the command would be lost"
    )
    assert match.group(1).strip() == "hello there"
