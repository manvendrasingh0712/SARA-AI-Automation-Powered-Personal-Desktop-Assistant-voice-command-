"""Tests for sara.gui.app.security_api (temp event DB, no network, no GUI)."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVENT_KEYS = {"ts", "kind", "tool", "source", "tier"}


def _load_module():
    path = ROOT / "sara" / "gui" / "app" / "security_api.py"
    spec = importlib.util.spec_from_file_location("security_api_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeWriter:
    def __init__(self):
        self.items = []

    def enqueue(self, key, value):
        self.items.append((key, value))


class FakeDb:
    def __init__(self, saved=None):
        self.saved = saved

    def get_preference(self, key):
        return self.saved


def make_host(mod, saved=None):
    host = type("Host", (mod.ApiSecurityMixin,), {})()
    host.db = FakeDb(saved)
    host._pref_writer = FakeWriter()
    return host


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from config import Config
    from sara.core.security import events, taint

    events.configure(db_path=tmp_path / "events.db")
    monkeypatch.setattr(Config, "SECURITY_MODE", "standard", raising=False)
    taint.clear_turn()
    yield events
    taint.clear_turn()
    events.flush()
    events.configure()


def _log_three(events):
    events.log_event("blocked", tool="shutdown_system", tier=3, origin="llm_tool", source="web_page")
    events.log_event("injection_detected", source="web_page", score=0.9)
    events.log_event("confirm_asked", tool="open_app", tier=1, origin="llm_tool")
    events.flush()


def test_summary_shape(env):
    mod = _load_module()
    host = make_host(mod)
    _log_three(env)
    res = host.get_security_summary()
    assert res["ok"] is True
    data = res["data"]
    assert data["mode"] == "standard"
    assert data["counts"]["total"] >= 3
    assert data["counts"]["blocked"] >= 2
    assert isinstance(data["counts"]["by_kind"], dict)
    assert data["tainted_now"] is False
    assert 1 <= len(data["last_events"]) <= 10
    assert all(set(row) == EVENT_KEYS for row in data["last_events"])


def test_tainted_now_follows_taint(env):
    from sara.core.security import taint

    mod = _load_module()
    host = make_host(mod)
    taint.mark_turn("web_page")
    assert host.get_security_summary()["data"]["tainted_now"] is True


def test_events_limit_is_clamped(env):
    mod = _load_module()
    host = make_host(mod)
    _log_three(env)
    assert len(host.get_security_events(limit=0)["data"]) == 1
    assert len(host.get_security_events(limit=10 ** 6)["data"]) <= 200
    assert len(host.get_security_events(limit="abc")["data"]) == 3
    assert all(set(row) == EVENT_KEYS for row in host.get_security_events()["data"])


def test_events_kind_filter_and_unknown_kind(env):
    mod = _load_module()
    host = make_host(mod)
    _log_three(env)
    rows = host.get_security_events(kind="blocked")["data"]
    assert rows and all(row["kind"] == "blocked" for row in rows)
    assert host.get_security_events(kind="no_such_kind") == {"ok": True, "data": []}


@pytest.mark.parametrize("bad", ["bogus", "", None, 3, "STANDARD2"])
def test_invalid_mode(env, bad):
    mod = _load_module()
    host = make_host(mod)
    assert host.set_security_mode(bad) == {"ok": False, "error": "invalid_mode"}
    assert host._pref_writer.items == []


def test_set_mode_persists_and_applies(env):
    from config import Config

    mod = _load_module()
    host = make_host(mod)
    assert host.set_security_mode(" Strict ") == {"ok": True, "mode": "strict"}
    assert Config.SECURITY_MODE == "strict"
    assert host._pref_writer.items == [("ui:security_mode", "strict")]


def test_saved_mode_applied_lazily_and_once(env):
    from config import Config

    mod = _load_module()
    host = make_host(mod, saved="strict")
    assert host.get_security_summary()["data"]["mode"] == "strict"
    Config.SECURITY_MODE = "standard"
    assert host.get_security_summary()["data"]["mode"] == "standard"


def test_invalid_saved_mode_is_ignored(env):
    mod = _load_module()
    host = make_host(mod, saved="weird")
    assert host.get_security_summary()["data"]["mode"] == "standard"


def test_missing_module_fallback(env, monkeypatch):
    mod = _load_module()
    host = make_host(mod)
    monkeypatch.setitem(sys.modules, "sara.core.security.events", None)
    assert host.get_security_summary() == {"ok": False, "data": None}
    assert host.get_security_events() == {"ok": False, "data": None}
    assert host.set_security_mode("strict") == {"ok": False, "data": None}


def test_mode_switch_changes_policy_decision(env):
    from sara.core.security import policy

    mod = _load_module()
    host = make_host(mod)
    host.set_security_mode("standard")
    assert policy.decide("shutdown_system", "llm_tool", tainted=True).decision == "deny"
    host.set_security_mode("off")
    assert policy.decide("shutdown_system", "llm_tool", tainted=True).decision == "allow"
    host.set_security_mode("standard")
    assert policy.decide("shutdown_system", "llm_tool", tainted=True).decision == "deny"