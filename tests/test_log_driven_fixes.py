"""Regression tests built from the user's real sara_unmatched_queries.jsonl."""
import re
import sqlite3

import pytest

from sara.audio.stt.helpers import _is_degenerate_text, _is_known_hallucination, _is_prompt_echo
from sara.core import memory_cleanup
from sara.core.intent.engine import detect_intent
from sara.core.memory_consolidation import _is_durable_fact


@pytest.mark.parametrize("text", [
    "Transcribe exactly what is spoken, in whichever language or mix it was spoken in English, or a natural code-switched mix.",
    "Keep proper names and technical terms exactly as spoken.",
    "This conversation may be in English, or a natural code-switched mix of both.",
    "The word is written in English, and it is written in English.",
    "Thank you very much for watching.",
    "Copyright WDR 2021",
    ". .",
    ". . . . .",
    "nd nd nd nd nd",
    "ndndndndndndndndndndndndndndndndndndndndnd",
    "vay,vay,vay,vay,vay,vay,vay,vay,vay,vay,vay,vay,vay,vay,vay,vay",
    "ṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭṭ",
    "hello hello hello hello",
])
def test_junk_transcripts_are_dropped(text):
    assert _is_prompt_echo(text) or _is_degenerate_text(text) or _is_known_hallucination(text)


@pytest.mark.parametrize("text", [
    "open chrome", "Who is the Prime Minister of India?", "no no no", "Thank you.", "Can you hear me clearly?",
    "set volume to 50", "I want to go on a trip to Jaipur", "please say it again", "play it again and again",
])
def test_real_speech_is_kept(text):
    assert not (_is_prompt_echo(text) or _is_degenerate_text(text) or _is_known_hallucination(text))


@pytest.mark.parametrize("text, intent", [
    ("panihari song chalao spotify per", "play_spotify"),
    ("panihari song chalao on spotify", "play_spotify"),
    ("kesariya gaana chalao youtube par", "play_youtube"),
    ("Offen Chrome.", "open_app"),
    ("nest track", "next_track"),
    ("Maximize this video.", "maximize_active_window"),
    ("type : hello , how are you", "typing_text"),
    ("screen vision", "screenshot_describe"),
    ("What do you know about me?", "memory_recall"),
    ("tum mere baare me kya kya janti ho", "memory_recall"),
    ("mere baare me kya kya jaante ho", "memory_recall"),
    ("Is there any event today?", "calendar_today"),
    ("do i have any plans today", "calendar_today"),
])
def test_queries_from_the_log_reach_their_tools(text, intent):
    assert detect_intent(text)[0] == intent


def test_daily_brief_phrases_match_the_skill():
    import importlib

    from sara.skills import daily_briefing
    from sara.skills._framework import _REGISTRY

    importlib.reload(daily_briefing)  # re-runs @skill so the spec is in the registry again
    spec = next(s for s in _REGISTRY if s.name == "daily_briefing")
    try:
        for phrase in ["Now tell me about my dailys brief", "tell me about my daily brief", "daily briefing", "my daily summary"]:
            assert any(re.search(p, phrase, re.IGNORECASE) for p in spec.patterns), phrase
    finally:
        _REGISTRY[:] = [s for s in _REGISTRY if s.name != "daily_briefing"]


@pytest.mark.parametrize("fact", [
    "User toggled play and pause several times.",
    "User adjusted volume from mute to 50%.",
    "User frequently pauses and toggles music playback.",
    "User resumed music twice.",
    "User identifies as \"Just More John\". They express deep emotional connection to music.",
    "A Fool likes Jalea,",
    "Your User has been writing in English for",
    "They use VS Code and often maximize or minimize windows.",
])
def test_activity_logs_and_garbage_are_not_facts(fact):
    assert not _is_durable_fact(fact)


@pytest.mark.parametrize("fact", [
    "User likes Arijit Singh songs on Spotify.",
    "User's sister lives in Pune.",
    "User works at Infosys.",
    "User prefers dark mode.",
])
def test_real_facts_are_kept(fact):
    assert _is_durable_fact(fact)


def test_memory_cleanup_dry_run_and_apply(tmp_path):
    db = tmp_path / "sara_data.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE long_term_memory (id INTEGER PRIMARY KEY, text TEXT, embedding BLOB, source TEXT, timestamp REAL)")
    rows = [
        (1, "User's sister lives in Pune.", "consolidation"),
        (2, "User toggled play and pause several times.", "consolidation"),
        (3, "User's name is Kya H.", "consolidation"),
        (4, "My sister lives in Pune", "fact"),
        (5, "The user's name is just more John.", "fact"),
        (6, "toggled the volume", "conversation"),
    ]
    con.executemany("INSERT INTO long_term_memory (id, text, source) VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()

    noise = memory_cleanup.find_noise(db)
    assert sorted(i for i, _, _ in noise) == [2, 3, 5]

    backup = memory_cleanup.apply_cleanup(db, [i for i, _, _ in noise])
    assert backup.exists()
    left = sqlite3.connect(db).execute("SELECT id FROM long_term_memory ORDER BY id").fetchall()
    assert [r[0] for r in left] == [1, 4, 6]
    # the backup still has everything
    assert len(sqlite3.connect(backup).execute("SELECT id FROM long_term_memory").fetchall()) == 6


@pytest.mark.parametrize("text, intent", [
    ("Turn the volume down a bit.", "set_volume"),
    ("Increase the brightness.", "increase_brightness"),
    ("Volume down.", "set_volume"),
    ("can you open chrome?", "open_app"),
    ("open chrome!", "open_app"),
    ("क्रोम खोलो।", "open_app"),
    ("गाना पॉज़ करो।", "play_pause_media"),
    ("Okay, sleep.", "chat"),
])
def test_whisper_style_trailing_punctuation_does_not_break_commands(text, intent):
    assert detect_intent(text)[0] == intent


def test_dictation_keeps_its_own_punctuation():
    intent, m = detect_intent("type: hello how are you?")
    assert intent == "typing_text" and m.group(1) == "hello how are you?"


@pytest.mark.parametrize("text, intent", [
    ("Aavaaz kam karo.", "set_volume"),
    ("Gaana pahunch karo.", "play_pause_media"),
    ("Mute karo.", "mute"),
])
def test_hinglish_recogniser_spellings_reach_their_tools(text, intent):
    assert detect_intent(text)[0] == intent


@pytest.mark.parametrize("text", ["awaaz band karo", "volume band karo", "sound band karo"])
def test_awaaz_band_karo_is_mute_not_close_app(text):
    assert detect_intent(text)[0] == "mute"


def test_band_karo_still_closes_apps():
    assert detect_intent("notepad band karo")[0] == "close_app"
