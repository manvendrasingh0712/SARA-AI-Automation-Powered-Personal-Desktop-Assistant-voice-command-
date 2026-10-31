"""Audit Task 2: questions and topics must not trigger actions; real commands must keep working."""
import pytest

from sara.core.intent import engine as E
from sara.core.intent.engine import detect_intent

FALSE_POSITIVES = [
    "what is the temperature at which water boils",
    "laptop ka temperature kitna hona chahiye",
    "tell me about dark mode benefits",
    "can you open my mind",
    "play it again sam movie ke baare me batao",
    "who is the news anchor ravish kumar",
    "i need to take a screenshot of myself, funny joke",
]

CHAT_PROBES = [
    "what does mute mean",
    "bluetooth kya hota hai",
    "shutdown ka matlab kya hai",
    "stop talking about politics",
    "close the deal with client email draft karo",
    "how do I lock my screen in windows",
    "what is the difference between wifi and bluetooth",
    "explain what dark mode does",
    "who is the weather presenter on ndtv",
    "tell me about the history of the news",
    "wifi kya hota hai samjhao",
    "why is my laptop temperature high",
    "open source software kya hota hai",
]

REAL_COMMANDS = [
    ("volume badha do", "set_volume"),
    ("open chrome", "open_app"),
    ("wifi band karo", "wifi_off"),
    ("kal subah 7 baje alarm laga do", "set_alarm"),
    ("brightness kam karo", "decrease_brightness"),
    ("pc lock kar do", "lock_pc"),
    ("spotify pe arijit singh chalao", "play_spotify"),
    ("mujhe kal ki meeting yaad dilana 4 baje", "reminder_add"),
    ("mausam kaisa hai", "weather"),
    ("jaipur ka mausam", "weather"),
    ("what is the weather today", "weather"),
    ("turn on dark mode", "dark_mode"),
    ("dark mode laga do", "dark_mode"),
    ("switch to light theme", "light_mode"),
    ("play despacito on youtube", "play_youtube"),
    ("cpu temperature batao", "temperature_status"),
    ("pc kitna garam hai", "temperature_status"),
    ("latest news", "news"),
    ("cricket ki latest news batao", "news"),
    ("take a screenshot", "screenshot_describe"),
    ("what's the weather in jaipur", "weather"),
    ("how's the weather", "weather"),
]


@pytest.mark.parametrize("text", FALSE_POSITIVES + CHAT_PROBES)
def test_questions_and_topics_stay_chat(text):
    assert detect_intent(text)[0] == "chat", detect_intent(text)[0]


@pytest.mark.parametrize("text,intent", REAL_COMMANDS)
def test_real_commands_keep_their_intent(text, intent):
    assert detect_intent(text)[0] == intent


def test_guarded_intents_exist_in_the_tables():
    known = {n if isinstance(n, str) else n[0] for n in E._INTENT_PATTERNS}
    assert E._GUARDED_INTENTS <= known, E._GUARDED_INTENTS - known


def test_question_helper_is_pure_and_sensible():
    f = E._looks_like_question_or_topic
    assert f("tell me about dark mode benefits") and f("bluetooth kya hota hai")
    assert not f("turn on dark mode") and not f("open chrome") and not f("wifi band karo")
