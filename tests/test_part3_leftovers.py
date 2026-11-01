"""Part 3 leftovers: 'Forget cached answers' bridge method (P7) and the Gemini usage meter in the perf summary (P15)."""
from sara.core import response_cache
from sara.core.llm import gemini_gate as G


def test_clear_response_cache_empties_the_shared_cache():
    from config import Config
    from sara.gui.app.notes import ApiNotesMixin

    cache = response_cache.shared_cache(Config.DB_PATH)
    cache.clear()
    assert cache.put("define machine learning", "Machine learning lets computers learn patterns from data.", "en", "p", "m")
    assert cache.size() == 1
    res = ApiNotesMixin.clear_response_cache(object())
    assert res == {"ok": True, "cleared": 1} and cache.size() == 0
    assert ApiNotesMixin.clear_response_cache(object()) == {"ok": True, "cleared": 0}


def test_latest_usage_only_lists_models_that_were_used():
    g = G.register(G.GeminiGate(rpd=250))
    assert G.latest_usage() == {}
    g.note_request("gemini-2.5-flash")
    g.note_request("gemini-2.5-flash")
    assert G.latest_usage() == {"gemini-2.5-flash": (2, 250)}


def test_perf_summary_carries_the_meter_only_when_gemini_was_used(monkeypatch):
    import types

    from sara.gui.app import perf_api

    class Host(perf_api.ApiPerfMixin):
        pass

    fake = types.SimpleNamespace(get_summary=lambda n: {"count": 0}, is_enabled=lambda: True)
    monkeypatch.setattr(perf_api, "_load_telemetry", lambda: fake)
    monkeypatch.setattr(perf_api, "_apply_saved", lambda host, t: None)
    G.register(G.GeminiGate())
    assert "gemini_usage" not in Host().get_perf_summary()["data"]
    g = G.register(G.GeminiGate(rpd=100))
    g.note_request("m")
    assert Host().get_perf_summary()["data"]["gemini_usage"] == {"m": [1, 100]}
