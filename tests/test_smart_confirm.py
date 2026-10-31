"""Audit part 2, O9: confirmation by risk tier."""
import sara.skills  # noqa: F401
from sara.orchestrator import dispatcher


class Tts:
    def __init__(self):
        self.said = []

    def speak(self, text, fast=False, block=True, **kw):
        self.said.append(text)

    def is_interrupted(self):
        return False


class Brain:
    def record_exchange(self, u, a):
        pass

    def get_history_snapshot(self):
        return []


def _run(text, conf, state, confirm):
    tts = Tts()
    reply = dispatcher._handle_command(text, Brain(), tts, None, None, None, None, lambda *a: None, {},
                                       confirm_state=confirm, context_state=state, stt_confidence=conf)
    return reply, tts


def test_low_confidence_read_only_command_just_runs():
    confirm = {}
    reply, _ = _run("what time is it", 0.2, {}, confirm)
    assert "pending" not in confirm and "Did you say" not in reply


def test_low_confidence_reversible_command_just_runs(monkeypatch):
    confirm = {}
    reply, _ = _run("what is today's date", 0.2, {}, confirm)
    assert "Did you say" not in reply


def test_low_confidence_impactful_command_is_read_back_then_runs_on_yes(monkeypatch):
    ran = []
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "lock_pc", lambda m, ctx: (ran.append(1), dispatcher._quick(ctx, "Locked."))[1])
    confirm, state = {}, {}
    reply, _ = _run("lock my pc", 0.2, state, confirm)
    assert reply.startswith('Did you say "lock my pc"?') and not ran
    reply, _ = _run("yes", 0.9, state, confirm)
    assert ran == [1] and "Locked" in reply


def test_confident_impactful_command_is_not_read_back(monkeypatch):
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "lock_pc", lambda m, ctx: dispatcher._quick(ctx, "Locked."))
    confirm = {}
    reply, _ = _run("lock my pc", 0.95, {}, confirm)
    assert reply == "Locked." and "pending" not in confirm


def test_cancel_drops_the_readback(monkeypatch):
    ran = []
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "lock_pc", lambda m, ctx: ran.append(1))
    confirm, state = {}, {}
    _run("lock my pc", 0.2, state, confirm)
    reply, _ = _run("cancel", 0.9, state, confirm)
    assert not ran and "cancel" in reply.lower()
