"""Live-test finding: "spotify volume 100" opened a track called "volume 100" (control words were taken as a track name)."""
import pytest

from sara.core.intent.engine import detect_intent


@pytest.mark.parametrize("text", ["spotify volume 100", "spotify volume up", "spotify next", "spotify mute", "spotify ka volume 30 kar do"])
def test_control_words_are_never_a_track_name(text):
    intent, m = detect_intent(text)
    assert not (intent == "play_spotify" and m is not None and m.group(1).strip() in {"volume 100", "volume up", "next", "mute", "ka volume 30 kar do"})


@pytest.mark.parametrize("text,query", [
    ("spotify volume 100", None), ("spotify bohemian rhapsody", "bohemian rhapsody"), ("spotify despacito", "despacito"),
    ("spotify pe despacito chalao", "despacito"), ("spotify par arijit singh ke gaane bajao", "arijit singh ke gaane"),
    ("play despacito on spotify", "despacito"),
])
def test_real_track_requests_still_play(text, query):
    intent, m = detect_intent(text)
    if query is None:
        assert intent != "play_spotify"
    else:
        assert intent == "play_spotify" and m.group(1).strip() == query


def test_spotify_close_and_open_unchanged():
    assert detect_intent("spotify close karo")[0] == "close_app"
    assert detect_intent("open spotify")[0] == "open_app"
