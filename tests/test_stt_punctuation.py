"""Speech-to-text delivers "Switch to Spotify." (capital + full stop). Handlers used to receive "Spotify." and answered
"I couldn't find an open window for 'Spotify.'."; "Close this window." was even routed to close_app('this window.')."""
import inspect

import pytest

from sara.core.intent.engine import detect_intent


@pytest.mark.parametrize("text,intent,group", [
    ("Switch to Spotify.", "switch_to_application", "Spotify"),
    ("Switch to VS Code.", "switch_to_application", "VS Code"),
    ("Close Notepad.", "close_app", "Notepad"),
    ("Restart Chrome.", "restart_application", "Chrome"),
    ("Search for best laptops.", "web_search", "best laptops"),
    ("What is the weather in Mumbai.", "weather", "Mumbai"),
    ("Take a note buy milk.", "take_note", "buy milk"),
    ("Switch to Spotify!", "switch_to_application", "Spotify"),
    ("Switch to Spotify . ", "switch_to_application", "Spotify"),
])
def test_trailing_sentence_punctuation_is_not_part_of_the_target(text, intent, group):
    i, m = detect_intent(text)
    assert i == intent and m.group(1) == group


def test_close_this_window_is_the_active_window_command():
    assert detect_intent("Close this window.")[0] == "close_active_window"


def test_question_marks_and_abbreviations_survive():
    assert detect_intent("Is it raining?")[0] == "chat"
    from sara.core.intent.engine import _strip_sentence_end

    assert _strip_sentence_end("Remind me at 5 p.m.") == "Remind me at 5 p.m."
    assert _strip_sentence_end("What time is it?") == "What time is it?"
    assert _strip_sentence_end("...") == "..."          # nothing but punctuation is left alone
    assert _strip_sentence_end("Hello, ") == "Hello"


def test_empty_reply_shows_no_placeholder_bubble():
    from sara.orchestrator import core_wiring

    src = inspect.getsource(core_wiring)
    assert 'reply_text or "(no response)"' not in src
