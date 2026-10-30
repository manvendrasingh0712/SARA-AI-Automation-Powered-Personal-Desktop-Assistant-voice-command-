"""
The chat model must not claim actions it did not take, or guess the time.

E2E audit 2026-10-30: "opn chrmoe" -> "Chrome opened!" (nothing opened);
"whats teh time" -> "8:45 AM" at 11:45; "weather in" -> "I can't check live weather".
"""
import re

import pytest

from sara.core.llm import prompt as P
from sara.core.llm.engine import SaraLLM, _current_time_note


@pytest.mark.parametrize("lang", ["english", "hindi", "hinglish"])
def test_every_language_prompt_carries_the_action_rules(lang):
    text = P._build_base_prompt("Sara", "It is morning.", lang, None)
    assert "never say or imply that you did one of them" in text
    assert "SARA can do all of these" in text and "do not know" in text


def test_clock_note_is_the_real_time():
    from datetime import datetime

    note = _current_time_note()
    assert re.fullmatch(r"Current local date and time: \w+day, \d{1,2} \w+ \d{4}, \d{1,2}:\d{2} [AP]M\.", note), note
    assert datetime.now().strftime("%Y") in note


def test_clock_note_goes_after_the_history_in_both_backends():
    b = SaraLLM.__new__(SaraLLM)
    b.system_instruction = "SYSTEM"
    b._lang, b._turn_lang = "english", None
    msgs = b._build_messages_ollama("what time is it", [("q", "a")])
    assert msgs[1]["content"] == "q" and msgs[2]["content"] == "a"          # cacheable prefix first
    assert msgs[-2]["content"].startswith("Current local date and time") and msgs[-1]["content"] == "what time is it"
    contents = b._build_contents_gemini("what time is it", [("q", "a")])
    assert any("Current local date and time" in c["parts"][0]["text"] for c in contents)
    assert contents[-1]["parts"][0]["text"] == "what time is it"


def test_old_history_messages_are_clipped_but_not_the_current_one():
    from sara.core.llm.engine import _HISTORY_CLIP_CHARS

    b = SaraLLM.__new__(SaraLLM)
    b.system_instruction = "SYSTEM"
    b._lang, b._turn_lang = "english", None
    huge = "a" * 3000
    msgs = b._build_messages_ollama(huge, [(huge, huge), ("short", "ok")])
    assert len(msgs[1]["content"]) <= _HISTORY_CLIP_CHARS + 20 and msgs[1]["content"].endswith("[shortened]")
    assert msgs[3]["content"] == "short" and msgs[4]["content"] == "ok"
    assert msgs[-1]["content"] == huge                                   # the question being asked is untouched
    contents = b._build_contents_gemini(huge, [(huge, huge)])
    assert contents[0]["parts"][0]["text"].endswith("[shortened]") and contents[-1]["parts"][0]["text"] == huge
