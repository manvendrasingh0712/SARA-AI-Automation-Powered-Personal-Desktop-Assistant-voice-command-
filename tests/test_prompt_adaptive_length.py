"""Audit Task 4: adaptive reply length, few-shot examples in the cached system prompt, honesty rules intact."""
import pytest

from sara.core.llm import prompt as P

LANGS = ["english", "hindi", "hinglish"]


@pytest.mark.parametrize("lang", LANGS)
def test_action_rules_and_voice_constraints_in_every_language(lang):
    text = P._build_base_prompt("Sara", "It is evening.", lang, "Manvendra")
    assert P._ACTION_RULES.strip() in text
    assert "never say or imply that you did one of them" in text
    if lang != "hindi":
        assert "emoji" in text.lower() or "markdown" in text.lower()


@pytest.mark.parametrize("lang,marker", [("english", "exactly 1 sentence"), ("hinglish", "exactly 1 sentence"), ("hindi", "exactly 1 sentence")])
def test_every_language_has_the_length_tiers(lang, marker):
    text = P._build_base_prompt("Sara", "x", lang, None)
    assert marker in text
    low = text.lower()
    assert "exactly 4 short spoken sentences" in text and "up to 8 sentences" in text


def test_hinglish_prompt_has_three_examples_and_stays_compact():
    text = P._build_base_prompt("Sara", "It is evening.", "hinglish", "Manvendra")
    assert text.count("User:") == 3 and "Sara:" in text and "guess nahi karungi" in text
    assert len(text) // 4 < 800  # the measured size is ~640 tokens, of which ~280 are the mandatory security + action rules


def test_examples_are_in_the_cached_system_instruction_not_per_turn():
    from sara.core.llm.engine import SaraLLM

    b = SaraLLM.__new__(SaraLLM)
    b.system_instruction = P._build_base_prompt("Sara", "x", "hinglish", None)
    b._turn_language_note = lambda: None
    msgs = b._build_messages_ollama("hello", [("a", "b")], None, None)
    assert "Examples." in msgs[0]["content"]
    assert all("Examples." not in m["content"] for m in msgs[1:])


def test_d6_all_three_prompts_are_compact_and_close_in_size():
    sizes = {l: len(P._build_base_prompt("Sara", "x", l, "Manvendra")) // 4 for l in LANGS}
    assert max(sizes.values()) < 800, sizes          # English was ~880, Hindi ~1000 tokens before
    assert max(sizes.values()) - min(sizes.values()) < 220, sizes
