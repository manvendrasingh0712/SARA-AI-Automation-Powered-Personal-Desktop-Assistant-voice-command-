"""tests/test_perf_api.py -- ApiPerfMixin: clamping, fallback, shapes, persistence, registration."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("perf_api_under_test", _ROOT / "sara" / "gui" / "app" / "perf_api.py")
perf_api = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(perf_api)


class FakeTelemetry(types.ModuleType):
    def __init__(self):
        super().__init__("sara.core.telemetry")
        self.enabled = True
        self.summary_args = []
        self.recent_args = []

    def get_summary(self, window_turns=50):
        self.summary_args.append(window_turns)
        return {"count": 7, "ttfa_p50": 1200.0, "ttfa_p95": 2500.0, "by_route": {}, "stage_avg": {}, "dropped_traces": 0}

    def get_recent(self, n=10):
        self.recent_args.append(n)
        return [{"turn_id": "abc", "route": "regex", "outcome": "ok"}]

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, on):
        self.enabled = bool(on)


class FakeDb:
    def __init__(self, saved=None):
        self.saved = saved

    def get_preference(self, key):
        return self.saved


class FakeWriter:
    def __init__(self):
        self.items = []

    def enqueue(self, key, value):
        self.items.append((key, value))


class Host(perf_api.ApiPerfMixin):
    def __init__(self, saved=None):
        self.db = FakeDb(saved)
        self._pref_writer = FakeWriter()


@pytest.fixture
def tel(monkeypatch):
    fake = FakeTelemetry()
    monkeypatch.setitem(sys.modules, "sara.core.telemetry", fake)
    return fake


@pytest.fixture
def missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "sara.core.telemetry", None)


@pytest.mark.parametrize("raw,expected", [(1, 5), (10_000, 500), ("40", 40), (None, 50), ("abc", 50), (float("inf"), 50)])
def test_window_is_clamped(tel, raw, expected):
    Host().get_perf_summary(raw)
    assert tel.summary_args == [expected]


@pytest.mark.parametrize("raw,expected", [(0, 1), (999, 50), ("12", 12), (None, 10), ("x", 10)])
def test_recent_count_is_clamped(tel, raw, expected):
    Host().get_recent_turns(raw)
    assert tel.recent_args == [expected]


def test_response_shapes(tel):
    host = Host()
    summary = host.get_perf_summary()
    assert summary["ok"] is True and summary["data"]["enabled"] is True and summary["data"]["count"] == 7
    recent = host.get_recent_turns()
    assert recent == {"ok": True, "data": [{"turn_id": "abc", "route": "regex", "outcome": "ok"}]}


def test_telemetry_missing_falls_back(missing):
    host = Host()
    assert host.get_perf_summary() == {"ok": False, "data": None}
    assert host.get_recent_turns() == {"ok": False, "data": None}
    result = host.set_telemetry_enabled(True)
    assert result["ok"] is False and result["enabled"] is False


def test_telemetry_errors_never_raise(tel):
    def boom(*_a, **_k):
        raise RuntimeError("secret detail")

    tel.get_summary = boom
    tel.get_recent = boom
    host = Host()
    assert host.get_perf_summary() == {"ok": False, "data": None}
    assert host.get_recent_turns() == {"ok": False, "data": None}


def test_set_enabled_persists_and_toggles(tel):
    host = Host()
    assert host.set_telemetry_enabled(False) == {"ok": True, "enabled": False}
    assert tel.enabled is False
    assert host._pref_writer.items[-1] == ("ui:telemetry_enabled", "0")
    assert host.set_telemetry_enabled("true") == {"ok": True, "enabled": True}
    assert host._pref_writer.items[-1] == ("ui:telemetry_enabled", "1")


def test_saved_pref_applied_lazily_once(tel):
    host = Host(saved="0")
    host.get_perf_summary()
    assert tel.enabled is False
    tel.enabled = True
    host.get_perf_summary()
    assert tel.enabled is True


def test_unset_pref_leaves_default(tel):
    Host(saved=None).get_perf_summary()
    assert tel.enabled is True


def test_mixin_registered_on_api():
    source = (_ROOT / "sara" / "gui" / "app" / "engine.py").read_text(encoding="utf-8")
    assert "from .perf_api import ApiPerfMixin" in source
    assert "    ApiPerfMixin,\n)" in source