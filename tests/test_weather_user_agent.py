"""
wttr.in must be asked with a command-line User-Agent.

E2E user test 2026-10-29: wttr.in answered SARA's browser User-Agent with
its full HTML page, the HTML guard discarded it, and every weather request
said "Sorry, I couldn't fetch the weather right now."
"""
from types import SimpleNamespace

from sara.tools import web


def test_weather_request_uses_a_cli_user_agent(monkeypatch):
    seen = {}

    def fake_get(url, timeout=None, headers=None):
        seen["headers"] = headers or {}
        return SimpleNamespace(
            raise_for_status=lambda: None,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            text="Delhi: +22°C",
            encoding=None,
        )

    monkeypatch.setattr(web._session, "get", fake_get)
    monkeypatch.setattr(web, "_cache_get", lambda key: None)
    monkeypatch.setattr(web, "_cache_set", lambda *a, **k: None, raising=False)

    assert "22" in web.get_weather("delhi")
    ua = seen["headers"].get("User-Agent", "")
    assert ua.startswith("curl/"), ua
