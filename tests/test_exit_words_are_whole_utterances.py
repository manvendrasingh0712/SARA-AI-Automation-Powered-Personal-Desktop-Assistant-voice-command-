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
