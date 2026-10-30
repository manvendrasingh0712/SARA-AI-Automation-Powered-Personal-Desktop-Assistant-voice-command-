import types

from sara.core.llm import clients


def test_embedding_cooldown_skips_repeat_calls_after_failure(monkeypatch):
    calls = []

    class Down:
        def embed(self, **kw):
            calls.append(1)
            raise ConnectionError("refused")

    monkeypatch.setattr(clients, "_get_ollama_client", lambda cfg: Down())
    monkeypatch.setattr(clients, "_embed_down_until", 0.0)
    cfg = types.SimpleNamespace(EMBEDDING_BACKEND="ollama")
    assert clients._get_embedding_vector(cfg, "a") is None
    assert clients._get_embedding_vector(cfg, "b") is None
    assert len(calls) == 1
