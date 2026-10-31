"""Audit part 2, O5: the shared context remembers the last intent, user input and reply for follow-ups."""
import sara.skills  # noqa: F401
from sara.orchestrator import dispatcher
from sara.orchestrator.command_helpers import _quick


class Tts:
    def __init__(self):
        self.said = []

    def speak(self, text, fast=False, block=True, **kw):
        self.said.append(text)

    def speak_stream(self, gen, **kw):
        out = list(gen)
        self.said.extend(out)
        return out

    def is_interrupted(self):
        return False


class Brain:
    def __init__(self):
        self.history = []

    def record_exchange(self, u, a):
        self.history.append((u, a))

    def get_history_snapshot(self):
        return list(self.history)

    def generate_response_stream(self, prompt, reference_context=None):
        yield "Delhi hai."

    def set_turn_language(self, lang):
        pass


def test_quick_records_the_last_reply():
    ctx = {"ui_update": lambda *a: None, "tts": Tts(), "context_state": {}}
    _quick(ctx, "Timer set for 5 minutes.")
    assert ctx["context_state"]["last_reply"] == "Timer set for 5 minutes."


def test_quick_works_without_a_context_state():
    _quick({"ui_update": lambda *a: None, "tts": Tts()}, "ok")      # must not raise


def test_dispatcher_keeps_last_intent_and_previous_intent_across_turns():
    state = {}
    brain, tts = Brain(), Tts()
    for text in ("what time is it", "what is today's date"):
        dispatcher._handle_command(text, brain, tts, None, None, None, None, lambda *a: None, {}, context_state=state)
    assert state["last_intent"] == "date_query" and state["prev_intent"] == "time_query"
    assert state["last_user_input"] == "what is today's date" and state["last_reply"]


def test_news_warmer_starts_once_and_fills_the_cache(monkeypatch):
    import time

    from sara.tools import web

    calls = []
    monkeypatch.setattr(web, "get_news", lambda topic="", max_results=3: calls.append(topic) or "x")
    monkeypatch.setattr(web, "_news_warmer_started", False)
    assert web.start_news_warmer(first_delay_s=0.0, interval_s=3600) is True
    assert web.start_news_warmer(first_delay_s=0.0, interval_s=3600) is False
    time.sleep(0.3)
    assert calls == [""]


def test_slow_memory_search_does_not_hold_back_the_reply():
    import threading
    import time
    from types import SimpleNamespace

    from sara.core.llm import engine as E

    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = SimpleNamespace(RAG_DEADLINE_S=0.15)
    release = threading.Event()
    b._memory = SimpleNamespace(search=lambda p: (release.wait(2), ["late"])[1])
    t0 = time.perf_counter()
    assert b._search_within_deadline("q") == []
    assert time.perf_counter() - t0 < 0.5
    release.set()
    b._memory = SimpleNamespace(search=lambda p: ["hit"])
    assert b._search_within_deadline("q") == ["hit"]
    b._cfg = SimpleNamespace(RAG_DEADLINE_S=0)
    assert b._search_within_deadline("q") == ["hit"]
