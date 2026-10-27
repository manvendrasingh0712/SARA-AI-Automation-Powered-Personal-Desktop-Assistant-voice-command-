"""Router JS-bridge methods: validation, clamping, fallbacks, mode persistence, feedback."""

import pytest

from sara.gui.app import router_api as ra
from sara.gui.app.router_api import ApiRouterMixin


class FakeRouter:
    def __init__(self, mode="off", accept_on=True):
        self._mode = mode
        self.accept_on = accept_on
        self.calls = []
        self.reset_called = False
        self.fb = None

    @property
    def mode(self):
        return self._mode

    def set_mode(self, mode):
        self.calls.append(mode)
        if mode == "on" and not self.accept_on:
            return False, "The offline benchmark has not passed yet."
        self._mode = mode
        return True, "Mode saved."

    def stats(self):
        return {"mode": self._mode, "samples": 0}

    def reset(self):
        self.reset_called = True

    def feedback(self, turn_id, value):
        self.fb = (turn_id, value)
        return turn_id != "unknown"


class _Prefs:
    def __init__(self, value=None):
        self.value = value

    def get_preference(self, key):
        return self.value


class _Writer:
    def __init__(self):
        self.items = []

    def enqueue(self, key, value):
        self.items.append((key, value))


class Host(ApiRouterMixin):
    def __init__(self, saved=None):
        self.db = _Prefs(saved)
        self._pref_writer = _Writer()


@pytest.fixture
def router(monkeypatch):
    fake = FakeRouter()
    monkeypatch.setattr(ra, "_load_router", lambda: fake)
    return fake


def test_stats_returns_router_data(router):
    result = Host().get_router_stats()
    assert result["ok"] is True
    assert result["data"]["mode"] == "off"


def test_stats_without_module(monkeypatch):
    monkeypatch.setattr(ra, "_load_router", lambda: None)
    assert Host().get_router_stats() == {"ok": False, "data": None}


def test_saved_mode_applied_once(router):
    host = Host(saved="shadow")
    host.get_router_stats()
    host.get_router_stats()
    assert router.calls == ["shadow"]


def test_set_mode_rejects_unknown_value(router):
    result = Host().set_router_mode("turbo")
    assert result["ok"] is False
    assert router.calls == []


def test_set_mode_persists_accepted_mode(router):
    host = Host()
    result = host.set_router_mode(" SHADOW ")
    assert result["ok"] is True and result["mode"] == "shadow"
    assert host._pref_writer.items == [("ui:router_mode", "shadow")]


def test_on_refused_by_router_is_not_persisted(monkeypatch):
    fake = FakeRouter(accept_on=False)
    monkeypatch.setattr(ra, "_load_router", lambda: fake)
    host = Host()
    result = host.set_router_mode("on")
    assert result["ok"] is False
    assert "benchmark" in result["message"]
    assert host._pref_writer.items == []


def test_set_mode_without_module(monkeypatch):
    monkeypatch.setattr(ra, "_load_router", lambda: None)
    assert Host().set_router_mode("shadow")["ok"] is False


def test_reset_calls_router(router):
    assert Host().reset_router() == {"ok": True}
    assert router.reset_called is True


@pytest.mark.parametrize("turn_id, value", [
    ("", 1), ("t1", 2), ("t1", True), ("x" * 41, 1), (None, 1), ("t1", "1"),
])
def test_feedback_rejects_bad_input(router, turn_id, value):
    assert Host().submit_feedback(turn_id, value) == {"ok": False}
    assert router.fb is None


def test_feedback_accepts_thumbs(router):
    assert Host().submit_feedback("t1", 1.0) == {"ok": True}
    assert router.fb == ("t1", 1)
    assert Host().submit_feedback("t1", -1) == {"ok": True}


def test_feedback_unknown_turn(router):
    assert Host().submit_feedback("unknown", 1) == {"ok": False}


def test_feedback_without_module(monkeypatch):
    monkeypatch.setattr(ra, "_load_router", lambda: None)
    assert Host().submit_feedback("t1", 1) == {"ok": False}


def test_exceptions_never_leak(monkeypatch):
    class Broken(FakeRouter):
        def feedback(self, turn_id, value):
            raise RuntimeError("secret reply text")

    monkeypatch.setattr(ra, "_load_router", lambda: Broken())
    result = Host().submit_feedback("t1", 1)
    assert result == {"ok": False}
    assert "secret" not in str(result)