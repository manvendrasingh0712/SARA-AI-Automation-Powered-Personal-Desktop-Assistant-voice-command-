"""
Mistyped commands must run the command, not reach the chat model.

E2E audit 2026-10-30: "opn chrmoe" -> the chat model said "Chrome opened!"
(nothing was opened) and "whats teh time" -> "8:45 AM" (invented; the real
time was ~11:45). The rescue pass ignored words under 5 letters and had no
app names in its vocabulary.
"""
import pytest

from sara.core.intent import detect_intent


def _i(text):
    detect_intent.cache_clear()
    return detect_intent(text)


@pytest.mark.parametrize("text, intent, group", [
    ("opn chrmoe", "open_app", "chrome"),
    ("opne chrome", "open_app", "chrome"),
    ("open spotfy", "open_app", "spotify"),
    ("whats teh time", "time_query", None),
    ("pley music on youtube", "play_youtube", "music"),
    ("set a timr for 5 minutes", "set_timer", "5 minutes"),
    ("clsoe notepad", "close_app", "notepad"),
    ("volum up", "set_volume", None),
])
def test_typos_reach_the_command(text, intent, group):
    got, match = _i(text)
    assert got == intent
    if group:
        assert match.group(1) == group


@pytest.mark.parametrize("text", [
    "I opened teh door", "tell me about the time machine", "I love to play the guitar", "my favourite song is on the radio",
    "what a nice day", "it is time to sleep",
])
def test_ordinary_sentences_are_left_alone(text):
    assert _i(text)[0] == "chat"
