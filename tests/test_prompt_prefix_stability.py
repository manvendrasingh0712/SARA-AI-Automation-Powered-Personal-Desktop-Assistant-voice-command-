"""Audit part 2, O15: everything up to and including the history is byte-identical between consecutive turns, and every
per-turn note comes after it, so Ollama's prefix cache keeps hitting."""
import threading
from collections import deque

from sara.core.llm import engine as E
from sara.core.llm.prompt import _build_base_prompt


class Cfg:
    LLM_BACKEND = "ollama"
    OLLAMA_NUM_CTX = 4096
    OLLAMA_NUM_PREDICT = 300
    LLM_HISTORY_MAX_TOKENS = 700

    def __getattr__(self, name):
        raise AttributeError(name)


def _brain():
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = Cfg()
    b._sys_prompt_tokens = 640
    b.system_instruction = _build_base_prompt("Sara", "It is evening.", "hinglish", "Manvendra")
    b._history_lock = threading.Lock()
    b._history = deque()
    b._turn_lang = "english"
    b._lang = "hinglish"
    b._gloss = None
    return b


def _turn(b, question, memory=None, ref=None):
    hist = b._trim_history_to_budget(question)
    return hist, b._build_messages_ollama(question, hist, memory, ref)


def test_prefix_through_history_is_identical_across_turns_and_notes_come_after():
    b = _brain()
    prev = None
    for i in range(12):
        q = f"question number {i}"
        hist, msgs = _turn(b, q, memory=f"- fact {i}" if i % 2 else None, ref="(a hint)" if i % 3 == 0 else None)
        n_prefix = 1 + 2 * len(hist)
        if prev is not None:
            prev_msgs, prev_hist_len = prev
            shared = min(n_prefix, 1 + 2 * prev_hist_len)
            # the window may advance in a big step, but while it does not, the shared part must be byte-identical
            if len(hist) >= prev_hist_len:
                assert msgs[:shared] == prev_msgs[:shared]
        # per-turn context (memory, hint, time, language) is strictly after the history, the user message is last
        assert all(m["role"] != "system" or i_ >= n_prefix or i_ == 0 for i_, m in enumerate(msgs))
        assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == q
        assert msgs[0]["content"] == b.system_instruction
        b._history.append((q, f"answer {i}"))
        prev = (msgs, len(hist))


def test_window_only_moves_in_big_steps_so_the_prefix_survives_several_turns():
    b = _brain()
    starts = []
    for i in range(40):
        hist, _ = _turn(b, "q")
        starts.append(hist[0] if hist else None)
        b._history.append((f"user message number {i} " + "x" * 90, f"assistant answer {i} " + "y" * 90))
    changes = sum(1 for a, c in zip(starts, starts[1:]) if a != c)
    assert changes <= 10          # a window sliding on every turn would change 39 times


def test_embedding_lru_hits_for_a_repeated_question(monkeypatch):
    from sara.core.llm import clients

    calls = []

    class Client:
        def embed(self, model, input, **kw):
            calls.append(input)
            return type("R", (), {"embeddings": [[0.1, 0.2, float(len(calls))]]})()

    monkeypatch.setattr(clients, "_get_ollama_client", lambda cfg: Client())
    clients._embed_cache.clear()

    class C:
        EMBEDDING_BACKEND = "ollama"
        OLLAMA_EMBEDDING_MODEL = "m"
        OLLAMA_KEEP_ALIVE = "5m"
        OLLAMA_EMBEDDING_NUM_GPU = 0

    a = clients._get_embedding_vector(C, "What is Photosynthesis?")
    b = clients._get_embedding_vector(C, "  what is photosynthesis? ")
    assert a == b and len(calls) == 1
    clients._get_embedding_vector(C, "another question")
    assert len(calls) == 2
