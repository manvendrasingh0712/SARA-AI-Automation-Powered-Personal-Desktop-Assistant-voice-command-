"""
Weather must still answer when wttr.in hiccups.

E2E audit 2026-10-30: one lookup answered "taking longer than expected" after
6 s: two attempts of up to 8 s each on wttr.in with nothing behind them.
Now one quick attempt (4 s), then Open-Meteo as an independent backup.
"""
from types import SimpleNamespace

import pytest
import requests

from sara.tools import web


def _resp(text="", status=200, ctype="text/plain", payload=None):
    r = SimpleNamespace(status_code=status, text=text, headers={"Content-Type": ctype}, encoding=None)
    r.raise_for_status = lambda: None if status < 400 else (_ for _ in ()).throw(requests.exceptions.HTTPError("bad"))
    r.json = lambda: payload
    return r


GEO = {"results": [{"name": "Jaipur", "latitude": 26.9, "longitude": 75.8}]}
FC = {"current": {"temperature_2m": 23.8, "apparent_temperature": 24.1, "relative_humidity_2m": 65, "weather_code": 1}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(web, "_cache_get", lambda key: None)
    monkeypatch.setattr(web, "_cache_set", lambda *a, **k: None)


def _session(wttr, meteo_ok=True):
    def get(url, **kw):
        if "wttr.in" in url:
            if isinstance(wttr, Exception):
                raise wttr
            return wttr
        if not meteo_ok:
            raise requests.exceptions.ConnectionError("down")
        return _resp(payload=GEO if "geocoding" in url else FC)
    return get


def test_normal_case_uses_wttr(monkeypatch):
    monkeypatch.setattr(web._session, "get", _session(_resp("Jaipur: +24C")))
    assert web.get_weather("jaipur") == "Jaipur: +24C"


@pytest.mark.parametrize("failure", [
    requests.exceptions.Timeout("slow"),
    requests.exceptions.ConnectionError("down"),
    _resp("<!DOCTYPE html><html>page</html>", ctype="text/html"),
])
def test_backup_answers_when_wttr_fails(monkeypatch, failure):
    monkeypatch.setattr(web._session, "get", _session(failure))
    assert web.get_weather("jaipur") == "Jaipur: mostly clear, 24 degrees, feels like 24, humidity 65 percent."


def test_original_messages_survive_when_both_fail(monkeypatch):
    monkeypatch.setattr(web._session, "get", _session(requests.exceptions.Timeout("x"), meteo_ok=False))
    assert "timed out" in web.get_weather("jaipur")
    monkeypatch.setattr(web._session, "get", _session(requests.exceptions.ConnectionError("x"), meteo_ok=False))
    assert web.get_weather("jaipur") == web._WEATHER_FALLBACK_MSG


def test_no_backup_without_a_place_name(monkeypatch):
    monkeypatch.setattr(web._session, "get", _session(requests.exceptions.Timeout("x")))
    assert "timed out" in web.get_weather("")        # wttr.in can use the IP, Open-Meteo cannot


def test_only_one_quick_attempt_on_the_primary(monkeypatch):
    calls = []
    def get(url, **kw):
        calls.append(url)
        if "wttr.in" in url:
            raise requests.exceptions.Timeout("slow")
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(web._session, "get", get)
    web.get_weather("jaipur")
    assert sum("wttr.in" in c for c in calls) == 1 and web._WEATHER_TIMEOUT_SECONDS <= 5


def test_handler_gives_weather_enough_time_for_the_backup():
    import inspect
    from sara.orchestrator.handlers import info

    assert "timeout=10.0" in inspect.getsource(info._h_weather)
