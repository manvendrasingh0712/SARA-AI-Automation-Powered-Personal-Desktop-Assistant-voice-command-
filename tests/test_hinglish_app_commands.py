"""
Regression tests for D23 and D24 (audit batch D-local): mixed-script app
commands, and a Spotify catch-all that turned "close it" into "play it".

Both sit directly on the project's Hindi/English/Hinglish code-switching
requirement, and both were reproducible from bench's own golden set.

D23 -- no generic Devanagari open_app / close_app existed
--------------------------------------------------------
H_OPEN and H_CLOSE were already defined in patterns_multilingual.py, but only
ever wired to SPECIFIC named targets (wifi, bluetooth, tabs, downloads, task
manager, file explorer). There was no generic Devanagari open_app/close_app, so
these all fell through to chat:

    "chrome खोलो"       (ASCII app name + Devanagari verb -- the common case)
    "क्रोम खोलो"
    "नोटपैड बंद करो"
    "chrome खोल दो"

while the romanised equivalents worked, because the romanised pattern's
app-name capture is ASCII-only and its verb alternation has no Devanagari.

D24 -- `^spotify (.+)$` captured command verbs as track names
-------------------------------------------------------------
play_spotify carried a bare catch-all, so anything beginning "spotify " had the
remainder captured as a song:

    "spotify close karo"  -> play_spotify, track "close karo"
    "spotify band kar do" -> play_spotify, track "band kar do"
    "spotify बंद करो"      -> play_spotify, track "बंद करो"

play_spotify is at index 30 in _INTENT_PATTERNS and close_app at 139, so it
always won. This is a wrong ACTION, not a mislabel: asking SARA to close
Spotify started music. The same defect shape is already documented as removed
from `press_key` ("a bare catch-all with no end marker at all"); here the
shorthand is worth keeping, so the pattern now refuses command verbs instead.

NOTE ON APP-NAME NORMALISATION
------------------------------
"vs code खोलो" captures "vscode", not "vs code". That is pre-existing,
script-independent behaviour -- "open vs code" and "vs code kholo" both capture
"vscode" too -- so it is asserted here as the contract rather than treated as a
defect of these patterns.
"""
from __future__ import annotations

import pytest

from sara.core.intent import detect_intent
from sara.core.intent.patterns import _INTENT_PATTERNS, _NON_APP_TOKENS


def _intent_index(name: str) -> int:
    return [n for n, _ in _INTENT_PATTERNS].index(name)


# ── D23: mixed-script and pure-Devanagari app commands ──────────────────
@pytest.mark.parametrize(
    "text,intent,app",
    [
        ("chrome खोलो", "open_app", "chrome"),
        ("क्रोम खोलो", "open_app", "क्रोम"),
        ("नोटपैड खोलो", "open_app", "नोटपैड"),
        ("chrome खोल दो", "open_app", "chrome"),
        ("ज़रा chrome खोलो", "open_app", "chrome"),
        ("नोटपैड बंद करो", "close_app", "नोटपैड"),
        ("chrome बंद करो", "close_app", "chrome"),
        ("spotify बन्द कर दो", "close_app", "spotify"),
    ],
)
def test_devanagari_app_commands_resolve(text: str, intent: str, app: str) -> None:
    got, match = detect_intent(text)
    assert got == intent, f"{text!r} routed to {got!r}, expected {intent!r}"
    assert match is not None and match.groups(), "app-name capture group missing"
    assert match.group(1).strip() == app, (
        f"captured {match.group(1)!r}, expected {app!r}"
    )


def test_app_name_normalisation_is_script_independent() -> None:
    """Pre-existing contract, pinned so a future change is a deliberate one."""
    for text in ("open vs code", "vs code kholo", "vs code खोलो"):
        intent, match = detect_intent(text)
        assert intent == "open_app", f"{text!r} -> {intent!r}"
        assert match.group(1).strip() == "vscode"


@pytest.mark.parametrize(
    "text,intent",
    [
        ("टास्क मैनेजर खोलो", "open_task_manager"),
        ("फाइल एक्सप्लोरर खोलो", "open_file_explorer"),
        ("नया टैब खोलो", "new_tab"),
        ("यह विंडो बंद करो", "close_active_window"),
        ("ब्लूटूथ बंद करो", "bluetooth_off"),
    ],
)
def test_specific_devanagari_targets_still_win(text: str, intent: str) -> None:
    """
    The generic open_app/close_app must never shadow a specific target. This
    holds because open_app (138) and close_app (139) sit after every specific
    intent; test_generic_app_intents_come_last pins that ordering directly.
    """
    got, _ = detect_intent(text)
    assert got == intent, (
        f"{text!r} routed to {got!r} instead of {intent!r} -- the generic "
        f"Devanagari app pattern is shadowing a specific target"
    )


def test_generic_app_intents_come_last() -> None:
    """The ordering the test above depends on, asserted rather than assumed."""
    generic = max(_intent_index("open_app"), _intent_index("close_app"))
    for specific in (
        "close_active_window", "new_tab", "close_tab", "open_downloads",
        "open_file_explorer", "open_task_manager",
    ):
        assert _intent_index(specific) < generic, (
            f"{specific} must be matched before the generic app intents"
        )


@pytest.mark.parametrize(
    "text",
    [
        "कुछ काम करना है",
        "मुझे क्या करना चाहिए",
        "दरवाज़ा मत खोलो अभी",
        "मेरा नाम क्या है",
        "आज मौसम कैसा है",
        "यह काम बंद नहीं हुआ",
    ],
)
def test_ordinary_devanagari_chat_is_not_an_app_command(text: str) -> None:
    """
    A free app-name capture is the D19 hazard in another script, so every
    widening is paired with the sentences it could have swallowed. The ^...$
    anchor is what does most of the work here.
    """
    got, _ = detect_intent(text)
    assert got == "chat", f"{text!r} false-triggered {got!r}"


def test_non_app_tokens_covers_devanagari() -> None:
    """
    engine._captured_app_name_is_rejected() is layer 4 for the app-name
    intents, and it only ever held romanised function words. The Devanagari
    capture needs the same vocabulary or the guard is blind in that script.
    """
    for token in ("करना", "है", "कुछ", "को", "में", "मेरा", "काम"):
        assert token in _NON_APP_TOKENS, (
            f"{token!r} missing from _NON_APP_TOKENS; the Devanagari app-name "
            f"capture is no longer protected by layer 4"
        )


# ── D24: Spotify must not read a command verb as a track name ───────────
@pytest.mark.parametrize(
    "text",
    [
        "spotify close karo",
        "spotify band kar do",
        "spotify बंद करो",
        "spotify बन्द कर दो",
        "spotify kholo",
        "spotify खोलो",
    ],
)
def test_spotify_command_verbs_do_not_play_music(text: str) -> None:
    got, _ = detect_intent(text)
    assert got != "play_spotify", (
        f"{text!r} resolved to play_spotify -- asking to open/close Spotify "
        f"would start music instead (D24)"
    )


@pytest.mark.parametrize(
    "text,track",
    [
        ("spotify bohemian rhapsody", "bohemian rhapsody"),
        ("spotify despacito", "despacito"),
        ("play shape of you on spotify", "shape of you"),
        ("open spotify and play jazz", "jazz"),
    ],
)
def test_spotify_shorthand_still_plays(text: str, track: str) -> None:
    """The fix must not cost the shorthand it was protecting."""
    got, match = detect_intent(text)
    assert got == "play_spotify", f"{text!r} no longer plays (got {got!r})"
    assert match.group(1).strip() == track, (
        f"captured {match.group(1)!r}, expected {track!r}"
    )
