"""
Tests for the open_app Windows Search fallback.

Pure-logic tests: the keyboard, clipboard and foreground-window probes are
faked, so nothing here touches a real desktop. They do NOT prove the key
sequence behaves correctly on a real Windows Search UI.
"""
import sys
import types

import pytest

from config import Config
from sara.core.planning import schema
from sara.core.planning.schema import PlanValidationError, validate_tool_arguments
from sara.tools.system import apps

ALLOWED = frozenset({"chrome", "notepad", "calc", "spotify"})

UNSAFE_TARGETS = [
    "test & calc",
    "a|b",
    "C:\\something",
    "ms-settings:",
    "foo; bar",
    "a<b",
    "a>b",
    "a^b",
    'say "hi"',
    "..\\..\\evil",
    "bad\x00name",
]


def _open(target, **kw):
    return validate_tool_arguments(
        "open_app", {"target": target}, allowed_apps=ALLOWED, app_allowlist_enabled=True, **kw
    )


def _close(target):
    return validate_tool_arguments(
        "close_app", {"target": target}, allowed_apps=ALLOWED, app_allowlist_enabled=True
    )


# ---------------- schema / validation ----------------

@pytest.mark.parametrize("target", ["notepad", "chrome", "the chrome app", "chrome!"])
def test_allowlisted_open_app_still_valid(target):
    assert _open(target)["target"]


@pytest.mark.parametrize("target", ["blender", "Android Studio", "DaVinci Resolve", "notion", "IntelliJ IDEA"])
def test_unlisted_safe_open_app_is_valid(target):
    assert _open(target)["target"] == " ".join(target.lower().split())


@pytest.mark.parametrize("target", UNSAFE_TARGETS)
def test_unsafe_open_app_is_rejected(target):
    with pytest.raises(PlanValidationError):
        _open(target)


def test_fallback_disabled_restores_old_behaviour():
    with pytest.raises(PlanValidationError):
        _open("blender", app_unlisted_search_fallback=False)
    assert _open("chrome", app_unlisted_search_fallback=False)["target"] == "chrome"


def test_config_flag_controls_fallback(monkeypatch):
    monkeypatch.setattr(Config, "APP_UNLISTED_SEARCH_FALLBACK_ENABLED", False, raising=False)
    with pytest.raises(PlanValidationError):
        _open("blender")


def test_close_app_unlisted_still_rejected():
    with pytest.raises(PlanValidationError):
        _close("blender")
    assert _close("chrome")["target"] == "chrome"


def test_planner_accepts_unlisted_safe_open_app_and_drops_unsafe():
    plan = schema.parse_plan_from_llm(
        [
            {"tool": "open_app", "arguments": {"target": "blender"}},
            {"tool": "open_app", "arguments": {"target": "x & calc"}},
            {"tool": "close_app", "arguments": {"target": "blender"}},
        ],
        allowed_tools=frozenset({"open_app", "close_app"}),
        max_steps=5,
        allowed_apps=ALLOWED,
    )
    assert [(s.tool, s.arguments["target"]) for s in plan.steps] == [("open_app", "blender")]


def test_retry_correction_uses_same_policy():
    # The executor re-validates corrected arguments through this exact call.
    assert _open("figma")["target"] == "figma"
    with pytest.raises(PlanValidationError):
        _open("figma | calc")
    with pytest.raises(PlanValidationError):
        _close("figma")


# ---------------- apps.open_application routing ----------------

@pytest.fixture
def routed(monkeypatch):
    calls = {"search": [], "direct": []}
    monkeypatch.setattr(apps, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        apps, "_drive_windows_search", lambda q: calls["search"].append(q) or True
    )

    def fake_launch(target):
        calls["direct"].append(target)
        return apps._LAUNCH_OK, None

    monkeypatch.setattr(apps, "_try_launch", fake_launch)
    return calls


def test_alias_uses_direct_launch(routed):
    assert apps.open_application("chrome") == "Opened chrome."
    assert routed == {"search": [], "direct": ["chrome"]}


def test_config_allowlisted_raw_name_uses_direct_launch(routed, monkeypatch):
    monkeypatch.setattr(Config, "APP_LAUNCH_ALLOWLIST", ["blender"])
    apps.open_application("blender")
    assert routed == {"search": [], "direct": ["blender"]}


@pytest.mark.parametrize("name", ["blender", "android studio", "The Figma App."])
def test_unlisted_name_uses_search(routed, name):
    result = apps.open_application(name)
    assert result.startswith("Opened ")
    assert routed["direct"] == []
    assert routed["search"] == [apps._normalize_app_name(name)]


@pytest.mark.parametrize("name", UNSAFE_TARGETS)
def test_unsafe_name_never_reaches_search_or_launch(routed, name):
    assert "couldn't" in apps.open_application(name)
    assert routed == {"search": [], "direct": []}


def test_fallback_disabled_uses_old_path(routed, monkeypatch):
    monkeypatch.setattr(Config, "APP_UNLISTED_SEARCH_FALLBACK_ENABLED", False, raising=False)
    apps.open_application("blender")
    assert routed == {"search": [], "direct": ["blender"]}


def test_restart_not_running_never_uses_search(routed, monkeypatch):
    monkeypatch.setattr(apps, "_terminate_matching", lambda exe: apps._TerminateResult([], None, 0))
    apps.restart_application("blender")
    assert routed["search"] == []
    assert routed["direct"] == ["blender"]


def test_close_app_code_path_has_no_search():
    import inspect
    assert "_open_via_windows_search" not in inspect.getsource(apps.close_application)
    assert "_open_via_windows_search" not in inspect.getsource(apps.restart_application)


# ---------------- Windows Search driver ----------------

class FakeKeyboard:
    def __init__(self, pressed=()):
        self.sent, self.released, self._pressed = [], [], set(pressed)

    def send(self, combo):
        self.sent.append(combo)

    def is_pressed(self, name):
        return name in self._pressed

    def release(self, name):
        self.released.append(name)
        self._pressed.discard(name)


class FakePyperclip:
    class PyperclipException(RuntimeError):
        pass

    def __init__(self, initial="old clip"):
        self.value = initial

    def paste(self):
        return self.value

    def copy(self, text):
        self.value = text


@pytest.fixture
def fakes(monkeypatch):
    kb, clip = FakeKeyboard(pressed={"ctrl"}), FakePyperclip()
    monkeypatch.setitem(sys.modules, "keyboard", kb)
    monkeypatch.setitem(sys.modules, "pyperclip", clip)
    monkeypatch.setattr(apps.time, "sleep", lambda s: None)
    return kb, clip


def _script_foreground(monkeypatch, names):
    seq = iter(names)
    last = {"v": names[-1]}

    def probe():
        try:
            last["v"] = next(seq)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(apps, "_foreground_process_name", probe)


def test_driver_happy_path(fakes, monkeypatch):
    kb, clip = fakes
    # focus wait -> search ; pre-Enter check -> search ; close wait -> closed
    _script_foreground(monkeypatch, ["searchhost.exe", "searchhost.exe", "blender.exe"])
    assert apps._drive_windows_search("blender") is True
    assert kb.sent == ["windows+s", "ctrl+a", "ctrl+v", "enter"]
    assert "ctrl" in kb.released          # stuck modifier released
    assert clip.value == "old clip"       # clipboard restored


def test_driver_never_types_if_search_does_not_open(fakes, monkeypatch):
    kb, _ = fakes
    clock = iter(range(0, 1000))
    monkeypatch.setattr(apps.time, "monotonic", lambda: next(clock) * 0.5)
    monkeypatch.setattr(apps, "_foreground_process_name", lambda: "sara.exe")
    assert apps._drive_windows_search("blender") is False
    assert kb.sent == ["windows+s"]


def test_driver_escapes_when_nothing_opened(fakes, monkeypatch):
    kb, _ = fakes
    clock = iter(range(0, 1000))
    monkeypatch.setattr(apps.time, "monotonic", lambda: next(clock) * 0.5)
    monkeypatch.setattr(apps, "_foreground_process_name", lambda: "searchhost.exe")
    assert apps._drive_windows_search("zzzz") is False
    assert kb.sent[-2:] == ["enter", "esc"]


def test_driver_failure_returns_clean_message(monkeypatch):
    monkeypatch.setattr(apps, "_IS_WINDOWS", True)

    def boom(_q):
        raise OSError("no keyboard access")

    monkeypatch.setattr(apps, "_drive_windows_search", boom)
    assert apps.open_application("blender") == "Sorry, I couldn't open 'blender'."
