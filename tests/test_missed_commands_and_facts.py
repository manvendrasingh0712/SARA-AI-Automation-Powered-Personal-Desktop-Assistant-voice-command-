import pytest

from sara.core.memory_consolidation import _is_durable_fact
from sara.core.unmatched_log import is_not_a_command


@pytest.mark.parametrize("text", [
    "thank you.", "thank you very much.", "yes.", "okay", "hello sara",
    "can you hear me clearly?", "who is the prime minister of india?", "kya haal hai",
])
def test_small_talk_and_plain_questions_are_not_missed_commands(text):
    assert is_not_a_command(text)


@pytest.mark.parametrize("text", [
    "turn up the brightness", "lights off kar do", "can you turn on the wifi",
    "mere phone ko charge pe lagao", "dim the screen a bit",
])
def test_real_command_shapes_are_kept(text):
    assert not is_not_a_command(text)


def test_log_unmatched_skips_small_talk(tmp_path, monkeypatch):
    from config import Config
    from sara.core import unmatched_log

    path = tmp_path / "unmatched.jsonl"
    monkeypatch.setattr(Config, "UNMATCHED_LOG_PATH", str(path), raising=False)
    unmatched_log.log_unmatched("thank you very much")
    unmatched_log.log_unmatched("dim the screen a bit")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and "dim the screen" in lines[0]


@pytest.mark.parametrize("fact", [
    "User prefers dark mode.", "User works at Infosys in Pune.", "User's sister lives in Pune.",
])
def test_plain_facts_about_the_user_are_kept(fact):
    assert _is_durable_fact(fact)


@pytest.mark.parametrize("fact", [
    'User mentioned "Rajni and Ravi" as story names.',
    "They likely confused a TV show with a fictional story.",
    "User is probably tired today.",
    "The assistant said it was sunny.",
])
def test_guesses_one_off_remarks_and_assistant_text_are_rejected(fact):
    assert not _is_durable_fact(fact)


@pytest.mark.parametrize("text", ["thank you very much for watching.", "nd nd nd nd nd"])
def test_whisper_silence_hallucinations_are_not_missed_commands(text):
    assert is_not_a_command(text)
