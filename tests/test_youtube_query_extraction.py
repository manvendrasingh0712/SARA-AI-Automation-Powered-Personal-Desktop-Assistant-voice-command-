"""Live-test finding (#305): "youtube pe arijit singh ke gaane chalao" searched YouTube for "pe arijit singh ke gaane chalao"."""
import pytest

from sara.core.intent.engine import detect_intent


@pytest.mark.parametrize("text,query", [
    ("youtube pe arijit singh ke gaane chalao", "arijit singh ke gaane"),
    ("youtube par badshah ke gaane bajao", "badshah ke gaane"),
    ("youtube me python tutorial", "python tutorial"),
    ("youtube lofi beats", "lofi beats"),
    ("search youtube for cats", "cats"),
    ("play despacito on youtube", "despacito"),
    ("यूट्यूब पर भजन चलाओ", "भजन"),
])
def test_query_has_no_filler_words(text, query):
    intent, m = detect_intent(text)
    assert intent == "play_youtube" and m is not None and m.group(1).strip() == query


def test_plain_open_youtube_is_still_open_app():
    assert detect_intent("open youtube")[0] == "open_app"
