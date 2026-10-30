"""
An exit phrase quits SARA only when it is the whole request.

E2E user test 2026-10-29: "shutdown the computer" answered "Shutting down.
Goodbye!" and quit SARA. _matches_phrase_set() matches a phrase at the
start OR end of the sentence, so every command that begins with "quit"/
"exit"/"shutdown" or ends with "band karo" quit the app -- the same defect
the STOP-WORD FIX removed for "stop".
"""
import pytest

from sara.orchestrator._shared_state import _EXIT_WORDS
from sara.orchestrator.text_utils import _is_exit_command


@pytest.mark.parametrize("text", [
    "shutdown the computer",
    "spotify band karo",
    "gaana band karo",
    "wifi band karo",
    "computer band karo",
    "music band kar",
    "quit spotify",
    "exit full screen",
    "bye mode setting kya hai",
])
def test_commands_do_not_quit_sara(text):
    assert not _is_exit_command(text, _EXIT_WORDS)


@pytest.mark.parametrize("text", [
    "exit", "exit.", "bye", "ok bye", "goodbye sara", "bye bye sara",
    "band karo", "sara band karo", "alvida", "बंद करो", "thank you bye",
])
def test_real_goodbyes_still_quit(text):
    assert _is_exit_command(text, _EXIT_WORDS)


def test_dispatcher_uses_the_strict_check():
    import inspect

    from sara.orchestrator import dispatcher

    src = inspect.getsource(dispatcher._handle_command)
    assert "_is_exit_command(lowered, _EXIT_WORDS)" in src
    assert "_matches_phrase_set(lowered, _EXIT_WORDS)" not in src


# ── sleep / forget phrases follow the same rule (E2E audit 2026-10-30) ──────
from sara.orchestrator._constants import _FORGET_WORDS, _SLEEP_WORDS  # noqa: E402


@pytest.mark.parametrize("text", [
    "put the computer to sleep",        # sleep_system (needs a confirmation), not "SARA sleeps"
    "gaana bas karo", "spotify bas karo", "forget everything you know about me", "clear memory of the meeting",
])
def test_commands_are_not_session_controls(text):
    assert not (_is_exit_command(text, _SLEEP_WORDS) or _is_exit_command(text, _FORGET_WORDS))


@pytest.mark.parametrize("text", [
    "sleep", "go to sleep", "ok go to sleep", "sara go to sleep now please", "that's all", "no that's all thanks",
    "you can sleep now", "chalo so jao", "bas karo", "theek hai bas",
])
def test_real_sleep_phrases_still_work(text):
    assert _is_exit_command(text, _SLEEP_WORDS)


@pytest.mark.parametrize("text", ["forget everything", "clear memory", "please forget our conversation", "sab bhool jao"])
def test_real_forget_phrases_still_work(text):
    assert _is_exit_command(text, _FORGET_WORDS)


def test_the_router_now_gets_the_real_commands():
    from sara.core.intent import detect_intent

    detect_intent.cache_clear()
    assert detect_intent("put the computer to sleep")[0] == "sleep_system"
    assert detect_intent("forget everything you know about me")[0] == "memory_forget_all"
