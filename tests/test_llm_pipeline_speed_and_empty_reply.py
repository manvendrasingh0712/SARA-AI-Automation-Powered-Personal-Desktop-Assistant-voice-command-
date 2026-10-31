"""LLM pipeline fixes from the live chat test (2026-10-31): empty replies, cache-friendly history, no emoji, prompt."""
from types import SimpleNamespace

from sara.core.llm import engine as E
from sara.core.llm.prompt import _build_base_prompt
from sara.core.llm.streaming import _clean_markdown


class _Cfg(SimpleNamespace):
    def __getattr__(self, name):  # unknown settings behave like "unset"
        raise AttributeError(name)


def _brain(history, **cfg):
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = _Cfg(LLM_BACKEND="ollama", OLLAMA_NUM_CTX=4096, OLLAMA_NUM_PREDICT=300, **cfg)
    b._sys_prompt_tokens = 450
    import threading
    from collections import deque

    b._history_lock = threading.Lock()
    b._history = deque(history)
    return b


def test_history_window_start_is_stable_between_turns():
    pairs = [(f"question number {i} " + "x" * 120, f"answer {i} " + "y" * 120) for i in range(30)]
    b = _brain(pairs[:12], LLM_HISTORY_MAX_TOKENS=500)
    first = b._trim_history_to_budget("next")
    starts = []
    for i in range(12, 30):
        b._history.append(pairs[i])
        w = b._trim_history_to_budget("next")
        starts.append(w[0])
    assert first
    # the first message of the window changes only a few times (big steps), not on every turn
    assert len(set(starts)) <= 7  # a window that slides every turn would give 18
    assert sum(E._estimate_tokens(u) + E._estimate_tokens(a) for u, a in w) <= 500


def test_history_window_respects_cap():
    pairs = [("u" * 400, "a" * 400)] * 20
    b = _brain(pairs, LLM_HISTORY_MAX_TOKENS=300)
    w = b._trim_history_to_budget("hi")
    assert len(w) == 1


def test_emoji_removed_but_text_kept():
    assert _clean_markdown("Paris \U0001F1EB\U0001F1F7") == "Paris"
    assert _clean_markdown("\U0001F3AF✨") == ""
    assert _clean_markdown("25°C and ₹500 **bold** दिल्ली") == "25°C and ₹500 bold दिल्ली"


def test_history_sent_to_model_has_no_emoji():
    assert E._clip_history("Octopuses have three hearts \U0001F419❤️") == "Octopuses have three hearts "


def test_hinglish_prompt_is_compact_and_keeps_safety_rules():
    text = _build_base_prompt("Sara", "It is evening.", "hinglish", "Manvendra")
    assert len(text) < 3000
    assert "no emojis" in text and "Manvendra" in text
    assert "UNTRUSTED_DATA" in text or "untrusted" in text.lower()
    assert "never say or imply that you did one of them" in text


def test_empty_stream_is_retried_then_reported(monkeypatch):
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = _Cfg(LLM_MAX_RETRIES=1, LLM_RETRY_BASE_DELAY_S=1.5, LLM_RETRY_MAX_DELAY_S=8.0, LLM_FALLBACK_MAX_RETRIES=1, DEBUG_MODE=False)
    import threading
    from collections import deque

    b._lang = "english"
    b._history = deque()
    b._history_lock = threading.Lock()
    b._last_stage_lock = threading.Lock()
    b._last_stage_used = None
    b._circuit_breaker_active = lambda: False
    b._circuit_breaker_record_success = lambda: None
    b._circuit_breaker_record_failure = lambda: None
    sleeps = []
    monkeypatch.setattr(E.time, "sleep", lambda s: sleeps.append(s))
    calls = []

    def opener(attempt):
        calls.append(attempt)
        return iter([None, ""]) if attempt == 0 else iter(["Pacific Ocean."])

    out = list(b._stream_generic("largest ocean", [("ollama", opener)]))
    assert out == ["Pacific Ocean."] and calls == [0, 1]
    assert sleeps and max(sleeps) <= 0.2

    calls.clear()
    out = list(b._stream_generic("x", [("ollama", lambda a: iter([]))]))
    assert len(out) == 1 and "try that again" in out[0].lower()


def _gloss_brain(**cfg):
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = _Cfg(**cfg)
    b._primary_backend = "ollama"
    b._gloss = None
    return b


def test_gloss_only_for_roman_hinglish_questions():
    b = _gloss_brain()
    assert b._needs_gloss("pani kitne degree par ubalta hai")
    assert b._needs_gloss("india ki rajdhani kya hai")
    assert not b._needs_gloss("what is the capital of India")  # English
    assert not b._needs_gloss("भारत की राजधानी क्या है")  # Devanagari
    assert not _gloss_brain(LLM_HINGLISH_GLOSS=False)._needs_gloss("india ki rajdhani kya hai")
    b._primary_backend = "gemini"
    assert not b._needs_gloss("india ki rajdhani kya hai")


def test_gloss_is_added_to_the_prompt_only_for_its_own_question():
    b = _gloss_brain()
    b.system_instruction = "SYSTEM"
    b._turn_language_note = lambda: None
    b._gloss = ("india ki rajdhani kya hai", "What is the capital of India?")
    msgs = b._build_messages_ollama("india ki rajdhani kya hai", [], None, None)
    assert any("What is the capital of India?" in m["content"] for m in msgs if m["role"] == "system")
    assert msgs[-1] == {"role": "user", "content": "india ki rajdhani kya hai"}
    other = b._build_messages_ollama("something else", [], None, None)
    assert not any("means in English" in m["content"] for m in other)


def test_old_conversation_memories_do_not_quote_the_models_earlier_answer():
    from sara.core.rag import MemoryHit

    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = _Cfg(RAG_MAX_CONTEXT_TOKENS=300)
    hits = [
        MemoryHit(1, "User said: first president of india\nSara replied: Jawaharlal Nehru", 0.9, "conversation", "t"),
        MemoryHit(2, "User likes green tea", 0.8, "fact", "t"),
    ]
    ctx, _ = b._build_capped_memory_context(hits)
    assert "first president of india" in ctx and "Nehru" not in ctx and "green tea" in ctx
