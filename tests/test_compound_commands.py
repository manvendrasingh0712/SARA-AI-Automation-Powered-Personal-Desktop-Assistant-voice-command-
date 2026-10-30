"""
"X and then Y" must run both X and Y.

E2E audit 2026-10-30: "open notepad and then search for python tutorials on
google" ran only the search; the first action was silently dropped because the
fast regex route matched one half and the planner is only consulted when
nothing matched.
"""
from types import SimpleNamespace

import pytest

from sara.orchestrator import dispatcher
from sara.orchestrator.dispatcher import _split_compound


@pytest.mark.parametrize("text, parts", [
    ("open notepad and then search for python tutorials on google", ["open notepad", "search for python tutorials on google"]),
    ("set a timer for 5 minutes and then open spotify", ["set a timer for 5 minutes", "open spotify"]),
    ("open notepad, then open calculator", ["open notepad", "open calculator"]),
    ("chrome kholo aur phir youtube pe gaana chalao", ["chrome kholo", "youtube pe gaana chalao"]),
    ("mute the volume and lock the pc", ["mute the volume", "lock the pc"]),
    ("turn the volume up and open notepad", ["turn the volume up", "open notepad"]),
    ("set volume to 50 and brightness to 70", ["set volume to 50", "brightness to 70"]),
])
def test_real_chains_are_split(text, parts):
    assert _split_compound(text) == parts


@pytest.mark.parametrize("text", [
    "search for salt and pepper", "play tom and jerry", "take a note buy milk and eggs", "open chrome",
    "phir se chalao", "what is 2 and 3", "tell me about cats and dogs", "remind me to call mom and buy milk at 5 pm",
    "shutdown the computer and then open chrome",        # destructive: needs its own confirmation
    "delete all my notes and then open notepad", "go to sleep and then shut down", "", "x" * 400,
])
def test_single_commands_and_destructive_chains_stay_whole(text):
    assert _split_compound(text) is None


def _run(text, monkeypatch, handlers=("open_app", "web_search", "set_volume", "play_youtube")):
    calls = []
    for name in handlers:
        monkeypatch.setitem(dispatcher._INTENT_HANDLERS, name,
                            lambda match, ctx, _n=name: calls.append((_n, tuple(match.groups()))) or f"did {_n}")
    brain = SimpleNamespace(model_name="m", record_exchange=lambda *a: None)
    tts = SimpleNamespace(speak=lambda *a, **k: None)
    reply = dispatcher._handle_command(text, brain, tts, None, None, None, None, lambda *a: None, {},
                                       confirm_state={}, context_state={})
    return calls, reply


def test_both_halves_run_in_order(monkeypatch):
    calls, reply = _run("open notepad and then search for python tutorials on google", monkeypatch)
    assert [c[0] for c in calls] == ["open_app", "web_search"]
    assert calls[0][1] == ("notepad",) and calls[1][1][0].startswith("python tutorials")
    assert reply == "did open_app did web_search"


def test_a_single_command_is_untouched(monkeypatch):
    calls, _ = _run("open notepad", monkeypatch)
    assert [c[0] for c in calls] == ["open_app"]


def test_a_confirmation_stops_the_chain(monkeypatch):
    # first half asks a question -> the second half must not run behind its back
    calls = []
    def asks(match, ctx):
        ctx["confirm_state"]["pending"] = {"action": "x", "expires_at": 9e12}
        return "sure?"
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "open_app", asks)
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "web_search", lambda m, c: calls.append(1) or "searched")
    brain = SimpleNamespace(model_name="m", record_exchange=lambda *a: None)
    reply = dispatcher._handle_command("open notepad and then search for cats", brain,
                                       SimpleNamespace(speak=lambda *a, **k: None), None, None, None, None,
                                       lambda *a: None, {}, confirm_state={}, context_state={})
    assert calls == [] and reply == "sure?"
