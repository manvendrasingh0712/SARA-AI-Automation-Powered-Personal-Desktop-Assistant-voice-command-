"""Audit Task 1: one Gemini config builder; sampling params dropped for models that reject them."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from sara.core.llm.clients import _build_gemini_generate_config, _model_rejects_sampling

ROOT = Path(__file__).resolve().parent.parent


class FakeTypes:
    class GenerateContentConfig:
        def __init__(self, **kw):
            self.kw = kw

    class HttpOptions:
        def __init__(self, timeout):
            self.timeout = timeout

    class ThinkingConfig:
        def __init__(self, thinking_budget):
            self.thinking_budget = thinking_budget


def _cfg(model, **kw):
    return _build_gemini_generate_config(FakeTypes, 10, model=model, temperature=0.7, top_p=0.9, top_k=40, max_output_tokens=300, **kw).kw


@pytest.mark.parametrize("model", ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash", "gemini-4.0-pro", "models/gemini-3.5-flash"])
def test_sampling_dropped_for_new_models(model):
    kw = _cfg(model)
    assert not {"temperature", "top_p", "top_k"} & set(kw) and kw["max_output_tokens"] == 300
    assert _model_rejects_sampling(model)


@pytest.mark.parametrize("model", ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-3.0-flash", "gemini-1.5-pro", None])
def test_sampling_kept_for_older_models(model):
    kw = _cfg(model)
    assert kw["temperature"] == 0.7 and kw["top_p"] == 0.9 and kw["top_k"] == 40


def test_timeout_and_thinking_budget_and_lite_clamp():
    kw = _cfg("gemini-2.5-flash", thinking_budget=0)
    assert kw["http_options"].timeout == 10000 and kw["thinking_config"].thinking_budget == 0
    assert _cfg("gemini-3.5-flash-lite", thinking_budget=0)["thinking_config"].thinking_budget == 128  # 0 gave HTTP 400
    assert "thinking_config" not in _cfg("gemini-2.5-flash")


def test_open_gemini_stream_sends_no_temperature_for_a_new_model(monkeypatch):
    from sara.core.llm import engine as E

    seen = {}

    class Models:
        def generate_content_stream(self, **kw):
            seen.update(kw)
            return iter([])

    monkeypatch.setattr(E, "_get_gemini_client", lambda cfg: SimpleNamespace(models=Models()))
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = SimpleNamespace(GEMINI_TEMPERATURE=0.7, GEMINI_TOP_P=0.9, GEMINI_TOP_K=40, GEMINI_MAX_OUTPUT_TOKENS=400)
    b.system_instruction = "S"
    b._secure_reference = lambda r: r
    b._build_contents_gemini = lambda *a, **k: []
    b._gemini_model_name = b._gemini_fast_model_name = "gemini-3.5-flash-lite"
    b._open_gemini_stream("hello", [], None, None)
    cfg = seen["config"]
    assert getattr(cfg, "temperature", None) is None and getattr(cfg, "top_k", None) is None
    assert cfg.max_output_tokens == 400


def test_no_direct_generatecontentconfig_outside_clients():
    offenders = []
    for p in (ROOT / "sara").rglob("*.py"):
        if p.name in ("clients.py",):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "GenerateContentConfig(" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{p.relative_to(ROOT)}:{n}")
    assert not offenders, offenders
