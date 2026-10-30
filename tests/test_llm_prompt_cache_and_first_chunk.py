"""
Two chat-latency fixes from the E2E user test (2026-10-29).

1. Prompt-cache order. Ollama reuses the processed prefix of the previous
   request. The per-turn memory/context messages used to sit at position 2,
   ahead of the history, so every turn re-read ~1,400 tokens (0.5-0.9 s).
   Stable parts (system instruction, history) must come first.
2. Early first chunk. The engine yielded only whole sentences, so the TTS
   v2 chunker could never cut early; a long first sentence meant ~2 s of
   silence. With VOICE_CHUNKER_V2 on, the first chunk uses the same
   first_chunk_end() rule as the TTS.
"""
import inspect

from sara.core.llm.engine import SaraLLM, _first_chunk_cut


def _brain():
    b = SaraLLM.__new__(SaraLLM)
    b.system_instruction = "SYSTEM"
    return b


def test_history_comes_before_per_turn_context():
    msgs = _brain()._build_messages_ollama(
        "new question",
        [("q1", "a1"), ("q2", "a2")],
        memory_context="likes blue",
        reference_context="it = chrome",
    )
    roles = [(m["role"], m["content"][:12]) for m in msgs]
    assert roles[0] == ("system", "SYSTEM")
    assert [m["content"] for m in msgs[1:5]] == ["q1", "a1", "q2", "a2"]
    assert msgs[5]["role"] == "system" and "likes blue" in msgs[5]["content"]
    assert msgs[6]["role"] == "system" and "it = chrome" in msgs[6]["content"]
    assert msgs[-1] == {"role": "user", "content": "new question"}


def test_without_context_it_is_system_history_user():
    msgs = _brain()._build_messages_ollama("hi", [("q", "a")])
    # system prompt, history, then the per-turn clock note, then the message
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "system", "user"]
    assert msgs[3]["content"].startswith("Current local date and time: ")


def test_first_chunk_cut_uses_the_tts_rule():
    long_sentence = ("Fun fact: A single grain of sand from Earth could take "
                     "millions of years to drift into space but")
    cut = _first_chunk_cut(long_sentence)
    assert cut and len(long_sentence[:cut]) < len(long_sentence)
    assert _first_chunk_cut("Dr. Sharma said the total is 1,234. ok").__index__() == len(
        "Dr. Sharma said the total is 1,234."
    )
    assert _first_chunk_cut(" Sky looks blue because air") is None


def test_first_chunk_cut_never_raises():
    assert _first_chunk_cut(None) is None


def test_stream_loop_cuts_the_first_chunk_behind_the_flag():
    src = inspect.getsource(SaraLLM)
    assert 'getattr(self._cfg, "VOICE_CHUNKER_V2", False)' in src
    assert "_first_chunk_cut(buffer_str)" in src
