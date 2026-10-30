"""
In auto language mode the reply follows the language of the message.

E2E test 2026-10-30: typed "hello sara, how are you?" and "why is the sky
blue?" were answered in Hinglish, because typed turns never changed the
brain's language from SARA_LANGUAGE=hinglish (and Whisper reports only
en/hi, so voice could never pick Hinglish).
"""
import inspect

import pytest

from sara.orchestrator import dispatcher


class _Brain:
    def __init__(self, lang="hinglish"):
        self.lang = lang
        self.calls = []

    def get_language(self):
        return self.lang

    def set_language(self, lang):
        self.calls.append(lang)
        self.lang = lang


@pytest.mark.parametrize("text, lang", [
    ("hello sara, how are you?", "english"),
    ("why is the sky blue?", "english"),
    ("mera mood kharab hai, kuch accha bolo", "hinglish"),
    ("मुझे एक कहानी सुनाओ", "hindi"),
])
def test_auto_mode_follows_the_message(text, lang):
    b = _Brain()
    dispatcher._match_reply_language(b, text, {"turn_lang_mode": "auto"})
    assert b.lang == lang


def test_manual_choice_is_left_alone():
    b = _Brain("hindi")
    dispatcher._match_reply_language(b, "why is the sky blue?", {"turn_lang_mode": "manual"})
    assert b.calls == []


def test_no_change_means_no_prompt_rebuild():
    b = _Brain("english")
    dispatcher._match_reply_language(b, "what is python", {"turn_lang_mode": "auto"})
    assert b.calls == []


def test_brain_without_language_api_is_fine():
    dispatcher._match_reply_language(object(), "hello", {"turn_lang_mode": "auto"})


# ── per-turn language: no system-prompt rebuild (prefix cache kept) ─────


class _TurnBrain(_Brain):
    def __init__(self, lang="hinglish"):
        super().__init__(lang)
        self.turn = "unset"

    def set_turn_language(self, lang):
        self.turn = lang


def test_auto_mode_uses_the_per_turn_language_not_a_rebuild():
    b = _TurnBrain()
    dispatcher._match_reply_language(b, "why is the sky blue?", {"turn_lang_mode": "auto"})
    assert b.turn == "english" and b.calls == []


def test_manual_mode_clears_a_leftover_turn_language():
    b = _TurnBrain()
    dispatcher._match_reply_language(b, "why is the sky blue?", {"turn_lang_mode": "manual"})
    assert b.turn is None and b.calls == []


def _engine_brain(base="hinglish"):
    from sara.core.llm.engine import SaraLLM

    b = SaraLLM.__new__(SaraLLM)
    b.system_instruction = "SYSTEM"
    b._lang = base
    b._turn_lang = None
    return b


def test_note_sits_after_history_right_before_the_message():
    b = _engine_brain()
    b.set_turn_language("english")
    msgs = b._build_messages_ollama("why is the sky blue?", [("q", "a")], memory_context="m")
    assert msgs[0]["content"] == "SYSTEM"
    assert [m["content"] for m in msgs[1:3]] == ["q", "a"]
    assert msgs[-2] == {"role": "system", "content": "Reply to this message in English only."}
    assert msgs[-1]["content"] == "why is the sky blue?"


def test_no_note_when_the_turn_matches_the_prompt_language():
    b = _engine_brain("english")
    b.set_turn_language("english")
    assert b._turn_language_note() is None
    b.set_turn_language(None)
    assert b._turn_language_note() is None


def test_set_turn_language_rejects_unknown_and_keeps_the_prompt():
    b = _engine_brain()
    b.set_turn_language("klingon")
    assert b._turn_lang is None and b.system_instruction == "SYSTEM"


def test_gemini_gets_the_note_too():
    b = _engine_brain()
    b.set_turn_language("hindi")
    contents = b._build_contents_gemini("कहानी सुनाओ", [])
    assert contents[-3]["parts"][0]["text"].startswith("[System] ")
    assert "देवनागरी" in contents[-3]["parts"][0]["text"]


def test_wired_into_the_chat_path_and_the_gui_text_path():
    assert "_match_reply_language(brain, user_input, context_state)" in inspect.getsource(
        dispatcher._handle_command
    )
    from sara.gui.app import core

    assert 'context_state["turn_lang_mode"]' in inspect.getsource(core)
