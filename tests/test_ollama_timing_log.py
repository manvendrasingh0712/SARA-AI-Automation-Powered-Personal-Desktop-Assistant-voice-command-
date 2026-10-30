"""One [LLM] ollama timing line per chat turn, built from Ollama's own final chunk."""
import logging
from types import SimpleNamespace

from sara.core.llm.engine import _log_ollama_timing


def test_logs_prompt_load_and_generation(caplog):
    chunk = SimpleNamespace(
        prompt_eval_count=1400, prompt_eval_duration=520_000_000, load_duration=8_000_000,
        eval_count=40, eval_duration=1_000_000_000, total_duration=1_600_000_000,
    )
    with caplog.at_level(logging.INFO, logger="sara.llm"):
        _log_ollama_timing(chunk)
    msg = caplog.records[-1].getMessage()
    assert "prompt=1400 tok in 520 ms" in msg and "load=8 ms" in msg
    assert "gen=40 tok @ 40.0 tok/s" in msg and "total=1600 ms" in msg


def test_missing_fields_never_raise(caplog):
    with caplog.at_level(logging.INFO, logger="sara.llm"):
        _log_ollama_timing(SimpleNamespace())
        _log_ollama_timing(None)
