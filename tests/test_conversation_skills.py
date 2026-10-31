"""Audit part 2, D1-D3, D8, D9: deterministic cancel / repeat / continue / rate / language / stop / honest refusals."""
import re
import time

import pytest

import sara.skills  # noqa: F401  (registers the skill intents)
from sara.core.intent.engine import detect_intent
from sara.skills import conversation as C
from sara.skills._framework import SkillContext


@pytest.mark.parametrize("text", ["alarm band karo", "cancel timer", "timer cancel karo", "alarm hata do", "kal ka alarm cancel"])
def test_d1_cancel_phrases(text):
    assert detect_intent(text)[0] == "cancel_timer_alarm"


def test_d1_other_phrases_keep_their_meaning():
    assert detect_intent("last reminder delete karo")[0] == "reminder_cancel"
    assert detect_intent("close alarm app")[0] == "close_app"
    assert detect_intent("set alarm for 7 am")[0] == "set_alarm"


@pytest.mark.parametrize("text,lang", [("switch to hindi", "hindi"), ("switch to english", "english"), ("hindi me bolo", "hindi"),
                                       ("english me bolo", "english"), ("hinglish me baat karo", "hinglish")])
def test_d2_d3_language_is_not_an_app_switch(text, lang):
    name, m = detect_intent(text)
    assert name == "set_reply_language" and m.group(1).lower() == lang


@pytest.mark.parametrize("text,intent", [
    ("repeat that", "repeat_last_reply"), ("dobara bolo", "repeat_last_reply"), ("phir se batao", "repeat_last_reply"),
    ("ek baar aur bolo", "repeat_last_reply"), ("aage batao", "continue_last_reply"), ("go on", "continue_last_reply"),
    ("tez bolo", "set_speech_rate"), ("dheere bolo", "set_speech_rate"), ("slowly", "set_speech_rate"), ("speak faster", "set_speech_rate"),
    ("yeh kya hai screen par", "screenshot_describe"), ("screen par kya dikh raha hai", "screenshot_describe"),
    ("stop", "stop_speaking"), ("stop it", "stop_speaking"), ("ruko", "stop_speaking"), ("chup ho ja", "stop_speaking"),
    ("volume", "incomplete_command"), ("timer", "incomplete_command"),
    ("fan chalao", "unsupported_device"), ("tv chalao", "unsupported_device"), ("torch chalao", "unsupported_device"),
    ("ac chalao", "unsupported_device"), ("pc chalao", "unsupported_device"),
])
def test_phrases_resolve(text, intent):
    assert detect_intent(text)[0] == intent


def test_real_commands_are_not_stolen():
    assert detect_intent("open chrome")[0] == "open_app"
    assert detect_intent("stop the music")[0] == "stop_media"
    assert detect_intent("volume 50 kar do")[0] == "set_volume"
    assert detect_intent("turn on dark mode")[0] == "dark_mode"
    assert detect_intent("pause")[0] == "play_pause_media"


class Brain:
    def __init__(self, history):
        self.history = history
        self.llm_calls = 0

    def get_history_snapshot(self):
        return list(self.history)

    def generate_response(self, prompt):
        self.llm_calls += 1
        return "More details."


class Api:
    def __init__(self):
        self.speed = None
        self.lang = None

    def set_speech_speed(self, v):
        self.speed = v

    def set_language(self, v):
        self.lang = v


class Db:
    def __init__(self, prefs=None):
        self.prefs = prefs or {}

    def get_preference(self, k):
        return self.prefs.get(k)

    def set_preference(self, k, v):
        self.prefs[k] = v


def _ctx(text, brain=None, api=None, db=None, tts=None):
    raw = {"user_input": text, "brain": brain, "db": db or Db(), "tts": tts, "context_state": {"control_api": api} if api else {}}
    return SkillContext(raw, "t")


def test_repeat_makes_no_llm_call_and_says_the_last_reply():
    brain = Brain([("q1", "first"), ("q2", "Delhi is the capital.")])
    out = C.handle_repeat(None, _ctx("repeat that", brain))
    assert out == "Delhi is the capital." and brain.llm_calls == 0
    assert re.search(r"haven't said|nahi bola", C.handle_repeat(None, _ctx("repeat that", Brain([]))).lower())


def test_continue_uses_one_llm_call_with_an_explicit_instruction():
    brain = Brain([("q", "a")])
    seen = []
    brain.generate_response = lambda p: (seen.append(p), "Next part.")[1]
    assert C.handle_continue(None, _ctx("aage batao", brain)) == "Next part."
    assert "do not repeat" in seen[0].lower()
    assert re.search(r"nothing to continue|continue karne ko kuch nahi", C.handle_continue(None, _ctx("go on", Brain([]))).lower())


def test_speech_rate_steps_and_clamps():
    api = Api()
    C.handle_rate(None, _ctx("tez bolo", api=api, db=Db({"speech_speed": "50"})))
    assert api.speed == 70
    C.handle_rate(None, _ctx("dheere bolo", api=api, db=Db({"speech_speed": "10"})))
    assert api.speed == 0
    C.handle_rate(None, _ctx("normal speed", api=api, db=Db({"speech_speed": "90"})))
    assert api.speed == 50
    assert re.search(r"can't change|nahi badal", C.handle_rate(None, _ctx("tez bolo")).lower())


def test_language_setter_maps_names_to_the_existing_gui_codes():
    for text, code in (("hindi me bolo", "hi"), ("english me bolo", "en"), ("hinglish me baat karo", "auto")):
        api = Api()
        m = re.search(r"(hindi|english|hinglish)", text)
        C.handle_language(m, _ctx(text, api=api))
        assert api.lang == code


def test_unsupported_devices_and_bare_verbs_answer_without_the_model():
    m = detect_intent("fan chalao")[1]
    assert re.search(r"can't control the fan|fan control nahi", C.handle_device(m, _ctx("fan chalao")).lower())
    m = detect_intent("pc chalao")[1]
    assert re.search(r"already on|pehle se chalu", C.handle_device(m, _ctx("pc chalao")).lower())
    m = detect_intent("volume")[1]
    assert "volume" in C.handle_incomplete(m, _ctx("volume")).lower()


def test_cancel_handler_reports_honestly(monkeypatch):
    from sara.tools import system as S

    monkeypatch.setattr(S, "cancel_timer", lambda: "Timer cancelled.")
    assert re.search(r"cancelled|band kar diya", C.handle_cancel(None, _ctx("cancel timer")).lower())
    monkeypatch.setattr(S, "cancel_timer", lambda: "No active timer to cancel.")
    assert re.search(r"no timer|koi timer", C.handle_cancel(None, _ctx("cancel timer")).lower())


@pytest.mark.parametrize("text", ["stop", "ruko", "chup", "bas karo", "stop it", "quiet", "shut up"])
def test_d9_stop_words_interrupt_the_reply_fast(text):
    from sara.audio.tts.barge_classify import INTERRUPT, classify_barge

    assert classify_barge(text, 400) == INTERRUPT
    from tests.tts_fakes import make_tts

    tts = make_tts()
    t0 = time.perf_counter()
    tts.stop()
    assert (time.perf_counter() - t0) * 1000 < 150 and tts.is_interrupted()


def test_backchannels_still_do_not_interrupt():
    from sara.audio.tts.barge_classify import BACKCHANNEL, classify_barge

    assert classify_barge("hmm", 400) == BACKCHANNEL and classify_barge("okay", 400) == BACKCHANNEL


def test_aur_batao_outside_a_briefing_continues_the_last_answer():
    from sara.skills import daily_briefing as DB

    DB._last_briefing["at"] = 0           # no briefing just now
    brain = Brain([("q", "a")])
    seen = []
    brain.generate_response = lambda p: (seen.append(p), "More.")[1]
    assert DB.handle_more(None, _ctx("aur batao", brain)) == "More." and seen
