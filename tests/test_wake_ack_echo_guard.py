"""
SARA must not answer her own wake acknowledgement.

E2E log 2026-10-30: right after each wake word the 0.3 s tail of her own
"Yes?" was transcribed as the user's command "Yes." and answered
("Got it! Ready for next step?").
"""
import pytest

from sara.orchestrator.core_wiring import _is_ack_echo


@pytest.mark.parametrize("text", ["Yes.", "yes", "Yes?", " YES! "])
def test_the_ack_heard_back_just_after_wake_is_an_echo(text):
    assert _is_ack_echo(text, "Yes?", 1.2, False)


def test_a_real_yes_to_a_question_is_kept():
    assert not _is_ack_echo("Yes.", "Yes?", 1.2, True)           # confirmation pending


def test_later_in_the_session_it_is_not_an_echo():
    assert not _is_ack_echo("Yes.", "Yes?", 12.0, False)


@pytest.mark.parametrize("text", ["yes open chrome", "open chrome", "", "Yes I do", "no"])
def test_anything_else_is_a_real_command(text):
    assert not _is_ack_echo(text, "Yes?", 1.0, False)


def test_empty_ack_phrase_never_matches():
    assert not _is_ack_echo("", "", 1.0, False)
