"""
The window must not wait for Ollama, and the brain must not give up on it.

E2E 2026-10-30: startup measured 41 s cold / 9 s warm, all spent in
main() waiting for `ollama serve` before the GUI was even created. Ollama
now starts on a background thread, so the brain may come up first: it
polls for the server (OLLAMA_STARTUP_WAIT_S) and then warms the model with
the real system prompt, instead of recording "Ollama unreachable" at once
and leaving the first question to pay a cold model load.
"""
import inspect
import threading
from types import SimpleNamespace
from unittest import mock

import main
from sara.core.llm import engine
from sara.core.llm.engine import SaraLLM


def _brain(wait_s=5.0):
    b = SaraLLM.__new__(SaraLLM)
    b._cfg = SimpleNamespace(OLLAMA_STARTUP_WAIT_S=wait_s, OLLAMA_KEEP_ALIVE="5m",
                             OLLAMA_NUM_CTX=4096, DEBUG_MODE=False)
    b._warm_event = threading.Event()
    b._warmup_error = None
    b._ollama_model_name = "m"
    b.system_instruction = "SYSTEM PROMPT"
    return b


def test_waits_for_a_starting_server_then_warms(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    client = mock.Mock()
    client.list.side_effect = [ConnectionError("down"), ConnectionError("down"), {"models": []}]
    monkeypatch.setattr(engine, "_get_ollama_client", lambda cfg: client)
    b = _brain()
    b._wait_for_ollama_then_warm(client)
    assert client.list.call_count == 3
    assert b._warmup_error is None and b._warm_event.is_set()
    msgs = client.chat.call_args.kwargs["messages"]
    assert msgs[0] == {"role": "system", "content": "SYSTEM PROMPT"}


def test_gives_up_after_the_wait(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    client = mock.Mock()
    client.list.side_effect = ConnectionError("down")
    b = _brain(wait_s=0.0)
    b._wait_for_ollama_then_warm(client)
    assert "unreachable" in b._warmup_error and b._warm_event.is_set()
    client.chat.assert_not_called()


def test_main_starts_ollama_in_the_background():
    src = inspect.getsource(main.main)
    assert "threading.Thread(target=_start_ollama" in src
    assert "ollama_ready = ollama_manager.start()" not in src
