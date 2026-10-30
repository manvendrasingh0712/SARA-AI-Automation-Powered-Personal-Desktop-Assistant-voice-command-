"""
Every Ollama call must load the model the same way.

Ollama unloads and reloads a model whenever two calls differ in a load
option (num_ctx, num_gpu). Chat, tool routing, planning and the short
generators all talk to the same local model, so they must all send the
same values -- from one helper. E2E test 2026-10-29: forcing every layer
onto the RTX 3050 measured 55 tok/s vs 28 with Ollama's own split.
"""
from __future__ import annotations

import inspect
import os
from types import SimpleNamespace
from unittest import mock

import pytest

from sara.core.llm.clients import ollama_load_options


def test_default_is_just_the_context_size() -> None:
    assert ollama_load_options(SimpleNamespace(OLLAMA_NUM_CTX=4096)) == {"num_ctx": 4096}


def test_num_gpu_is_sent_only_when_configured() -> None:
    cfg = SimpleNamespace(OLLAMA_NUM_CTX=2048, OLLAMA_NUM_GPU=99)
    assert ollama_load_options(cfg) == {"num_ctx": 2048, "num_gpu": 99}
    assert "num_gpu" not in ollama_load_options(SimpleNamespace(OLLAMA_NUM_GPU=None))


@pytest.mark.parametrize(
    "module_name, func_name",
    [
        ("sara.core.llm.engine", "SaraLLM"),
        ("sara.core.llm.provider_runtime", "_ollama_call"),
        ("sara.core.tool_router", "_ollama_tool_call"),
        ("sara.core.planning.planner", None),
        ("sara.core.planning.executor", None),
    ],
)
def test_every_ollama_call_site_uses_the_helper(module_name, func_name) -> None:
    import importlib

    mod = importlib.import_module(module_name)
    src = inspect.getsource(getattr(mod, func_name) if func_name else mod)
    assert "ollama_load_options(" in src, (
        f"{module_name} builds Ollama options without ollama_load_options(); "
        "a differing num_ctx/num_gpu makes Ollama reload the model"
    )


def test_spawned_ollama_gets_the_fast_kv_cache(monkeypatch) -> None:
    import ollama_manager

    monkeypatch.delenv("OLLAMA_FLASH_ATTENTION", raising=False)
    monkeypatch.delenv("OLLAMA_KV_CACHE_TYPE", raising=False)
    monkeypatch.delenv("SARA_OLLAMA_FAST_KV", raising=False)
    mgr = ollama_manager.OllamaManager.__new__(ollama_manager.OllamaManager)
    mgr._ollama_bind_host = lambda: "127.0.0.1:11434"
    with mock.patch.object(ollama_manager.subprocess, "Popen") as popen:
        mgr._spawn("ollama.exe")
    env = popen.call_args.kwargs["env"]
    assert env["OLLAMA_FLASH_ATTENTION"] == "1"
    assert env["OLLAMA_KV_CACHE_TYPE"] == "q8_0"


def test_user_values_win_and_the_switch_turns_it_off(monkeypatch) -> None:
    import ollama_manager

    mgr = ollama_manager.OllamaManager.__new__(ollama_manager.OllamaManager)
    mgr._ollama_bind_host = lambda: "127.0.0.1:11434"

    monkeypatch.setenv("OLLAMA_KV_CACHE_TYPE", "f16")
    with mock.patch.object(ollama_manager.subprocess, "Popen") as popen:
        mgr._spawn("ollama.exe")
    assert popen.call_args.kwargs["env"]["OLLAMA_KV_CACHE_TYPE"] == "f16"

    monkeypatch.delenv("OLLAMA_KV_CACHE_TYPE")
    monkeypatch.delenv("OLLAMA_FLASH_ATTENTION", raising=False)
    monkeypatch.setenv("SARA_OLLAMA_FAST_KV", "0")
    with mock.patch.object(ollama_manager.subprocess, "Popen") as popen:
        mgr._spawn("ollama.exe")
    env = popen.call_args.kwargs["env"]
    assert "OLLAMA_FLASH_ATTENTION" not in env and "OLLAMA_KV_CACHE_TYPE" not in env
