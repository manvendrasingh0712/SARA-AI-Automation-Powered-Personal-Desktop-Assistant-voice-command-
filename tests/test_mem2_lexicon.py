"""Tests for the Memory 2.0 cue lexicon (English, Hinglish, Hindi)."""
from __future__ import annotations

from sara.core.memory2.lexicon import (
    KIN_GROUPS,
    has_history_cue,
    is_memory_question,
    predicates_in_query,
    tokens,
)


def test_tokens_keep_devanagari_words_whole():
    assert tokens("पढ़ता हूँ") == ["पढ़ता", "हूँ"]
    assert tokens("Mother's NAME?") == ["mother", "s", "name"]
    assert tokens(None) == []


def test_predicates_in_english_hinglish_hindi():
    assert predicates_in_query("where do I live now?") == ["lives_in"]
    assert "works_at" in predicates_in_query("mera kaam kya hai")
    assert "lives_in" in predicates_in_query("मैं कहाँ रहता हूँ")
    assert "name_is" in predicates_in_query("please call me Sam")
    assert "wake_time" in predicates_in_query("main subah kitne baje uthta hoon")
    assert "likes" in predicates_in_query("mujhe kaunsa khel pasand hai")


def test_unrelated_queries_have_no_predicates():
    assert predicates_in_query("what is my favorite movie?") == []
    assert predicates_in_query("") == []


def test_history_cue():
    for query in ("where did I live before?", "pehle main kahan rehta tha", "पहले मैं कहाँ रहता था"):
        assert has_history_cue(query)
    assert not has_history_cue("where do I live now?")


def test_memory_question_detection():
    for query in ("do you remember my phone number?", "yaad hai maine kya bataya", "मेरा नाम क्या है?"):
        assert is_memory_question(query)
    for query in ("tell me a joke", "what is the capital of France?", ""):
        assert not is_memory_question(query)


def test_kin_groups_cover_languages():
    flat = set().union(*KIN_GROUPS)
    assert {"sister", "dost", "भाई", "mother"} <= flat