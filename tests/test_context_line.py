"""Audit part 2, O7: the opt-in PC context note, privacy filtered, after the history."""
from types import SimpleNamespace

from sara.core import context_line as C


def _cfg(**kw):
    return SimpleNamespace(CONTEXT_AWARE_ENABLED=True, **kw)


def test_off_by_default_and_when_disabled():
    C.reset_cache()
    assert C.build(SimpleNamespace(), title_fn=lambda: "Spotify", battery_fn=lambda: (50, True)) == ""


def test_note_has_window_and_battery():
    C.reset_cache()
    note = C.build(_cfg(), title_fn=lambda: "Spotify - Arijit Singh", battery_fn=lambda: (61, False))
    assert 'active window "Spotify - Arijit Singh"' in note and "battery 61% (on battery)" in note


def test_private_titles_are_dropped():
    for title in ("HDFC NetBanking - Google Chrome", "Bitwarden", "Login to PayPal", "New Incognito tab"):
        C.reset_cache()
        note = C.build(_cfg(), title_fn=lambda t=title: t, battery_fn=lambda: None)
        assert note == "", title


def test_custom_deny_pattern_and_cache():
    C.reset_cache()
    calls = []
    note = C.build(_cfg(CONTEXT_DENY_PATTERN="secretproject"), title_fn=lambda: calls.append(1) or "SecretProject notes", battery_fn=lambda: None)
    assert note == ""
    C.build(_cfg(), title_fn=lambda: calls.append(1) or "x", battery_fn=lambda: None)
    assert len(calls) == 1          # second call within the TTL came from the cache


def test_the_note_is_added_after_the_history_not_before(monkeypatch):
    import threading
    from collections import deque

    from sara.core.llm import engine as E

    monkeypatch.setattr(C, "build", lambda cfg: "What is on the PC right now: battery 50% (charging).")
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = SimpleNamespace()
    b.system_instruction = "SYS"
    b._turn_language_note = lambda: None
    b._gloss = None
    msgs = b._build_messages_ollama("hello", [("q1", "a1")], None, None)
    idx = [i for i, m in enumerate(msgs) if "PC right now" in m["content"]][0]
    assert idx > 2 and msgs[-1]["role"] == "user"       # after system + the history pair
