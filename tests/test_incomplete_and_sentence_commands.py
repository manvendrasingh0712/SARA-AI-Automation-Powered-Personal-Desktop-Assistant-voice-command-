import pytest

from sara.core.intent.engine import detect_intent


@pytest.mark.parametrize("text", ["search for", "look up for", "open source software is great"])
def test_incomplete_or_sentence_is_not_a_command(text):
    assert detect_intent(text)[0] == "chat"


@pytest.mark.parametrize("text", ["weather", "the weather"])
def test_bare_weather_uses_own_location(text):
    intent, m = detect_intent(text)
    assert intent == "weather" and m.group(1) == ""


def test_search_the_web_keeps_clean_query():
    intent, m = detect_intent("search the web for dogs")
    assert intent == "web_search" and m.group(1) == "dogs"


def test_normal_commands_still_work():
    assert detect_intent("search for cats")[1].group(1) == "cats"
    assert detect_intent("open notepad")[0] == "open_app"
