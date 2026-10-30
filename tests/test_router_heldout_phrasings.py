"""
Everyday phrasings that the router missed in the E2E held-out test
(2026-10-29: 74.5 % on 98 fresh utterances while the tuned golden set said
99.3 %). The second block was written AFTER the fix and never used to tune
it, so it guards generalisation, not memorisation.
"""
import pytest

from sara.core.intent import detect_intent
from sara.orchestrator.handlers.system import _volume_direction


def _intent(text):
    detect_intent.cache_clear()
    return detect_intent(text)


TUNED = [
    ("turn the volume down a bit", "set_volume"),
    ("make it louder", "set_volume"),
    ("volume thoda kam karo", "set_volume"),
    ("awaaz badha do", "set_volume"),
    ("आवाज़ कम करो", "set_volume"),
    ("remind me in 20 minutes to check the oven", "reminder_add"),
    ("mujhe 7 baje yaad dilana ki dawai leni hai", "reminder_add"),
    ("timer 90 seconds", "set_timer"),
    ("will it rain today", "weather"),
    ("aaj mausam kaisa hai", "weather"),
    ("मौसम कैसा है", "weather"),
    ("how much battery do i have", "system_info"),
    ("press enter", "press_key"),
    ("hey can you open chrome for me", "open_app"),
    ("screenshot le lo", "screenshot_describe"),
    ("स्क्रीनशॉट लो", "screenshot_describe"),
    ("add finish resume to my todo list", "add_todo"),
    ("find my resume pdf", "find_file"),
    ("exit full screen", "toggle_fullscreen"),
]

UNSEEN = [
    ("can you make the sound quieter", "set_volume"),
    ("turn the sound up", "set_volume"),
    ("awaz thodi badhao", "set_volume"),
    ("वॉल्यूम थोड़ा बढ़ाओ", "set_volume"),
    ("remind me after 10 minutes to call rahul", "reminder_add"),
    ("remind me at 9 pm to lock the door", "reminder_add"),
    ("mujhe 6 baje yaad dilana ki gym jana hai", "reminder_add"),
    ("timer 5 minutes", "set_timer"),
    ("is it going to rain tomorrow", "weather"),
    ("mausam kya hai", "weather"),
    ("kitni battery bachi hai", "system_info"),
    ("hit escape", "press_key"),
    ("add buy groceries to my to-do list", "add_todo"),
    ("find the budget xlsx", "find_file"),
    ("ok open notepad please", "open_app"),
    ("exit fullscreen", "toggle_fullscreen"),
]

STAY_CHAT = [
    "what does mute mean in hindi",
    "how to take a screenshot on mac",
    "how do i increase the volume on my phone",
    "what does fullscreen mean",
    "the movie was louder than i expected",
    "my sister will remind me later",
    "how to press enter without a keyboard",
    "what is the meaning of mausam",
    "sound engineering is a cool career",
    "type hello world",  # D79: colon-free dictation stays unsupported on purpose
    # older patterns, fixed 2026-10-30
    "i want to find my purpose in life",
    "kal ka mausam bahut accha tha",
]


@pytest.mark.parametrize("text, query", [
    ("search for python tutorials", "python tutorials"),
    ("can you find me a good recipe", "a good recipe"),
    ("find out who won the match", "who won the match"),
    ("google best phones", "best phones"),
])
def test_web_search_query_has_no_leftover_words(text, query):
    intent, match = _intent(text)
    assert intent == "web_search" and match.group(1) == query


@pytest.mark.parametrize("text, place", [
    ("kal ka mausam kaisa hoga", ""),
    ("jaipur ka mausam", "jaipur"),
    ("aaj delhi ka mausam", "delhi"),
])
def test_time_words_are_not_places(text, place):
    intent, match = _intent(text)
    assert intent == "weather" and match.group(1) == place


@pytest.mark.parametrize("text, intent", TUNED + UNSEEN)
def test_command_routes(text, intent):
    assert _intent(text)[0] == intent


@pytest.mark.parametrize("text", STAY_CHAT)
def test_questions_and_prose_stay_chat(text):
    assert _intent(text)[0] == "chat"


def test_meta_questions_are_still_commands():
    assert _intent("why did you say that")[0] == "why_proactive"
    assert _intent("why did i change the volume")[0] == "why_decision"


def test_reminder_groups_keep_task_then_time():
    m = _intent("remind me in 20 minutes to check the oven")[1]
    assert (m.group(1), m.group(2)) == ("check the oven", "20 minutes")
    m = _intent("mujhe 7 baje yaad dilana ki dawai leni hai")[1]
    assert (m.group(1), m.group(2)) == ("dawai leni hai", "7 baje")


def test_weather_without_a_place_asks_for_the_local_one():
    assert _intent("aaj mausam kaisa hai")[1].group(1) == ""


@pytest.mark.parametrize("text, sign", [
    ("volume thoda kam karo", -1), ("awaaz badha do", 1),
    ("आवाज़ कम करो", -1), ("वॉल्यूम थोड़ा बढ़ाओ", 1),
    ("turn the volume down a bit", -1), ("make it louder", 1),
])
def test_volume_direction_understands_hindi(text, sign):
    assert _volume_direction(text) == sign
