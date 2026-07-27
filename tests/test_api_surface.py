"""
tests/test_api_surface.py

Guards against the Api class (sara/gui/app/engine.py) silently losing or
gaining JS-bridge methods -- the exact failure mode that previously shipped
unnoticed when engine.py was accidentally replaced by an older version and
get_setup_wizard_seen / mark_setup_wizard_seen vanished (no import error,
no crash -- just a silent "(preview mode, no backend connected)"-style
failure in the GUI until someone manually opened DevTools).

Two invariants are enforced:
  1. Every name in EXPECTED_METHODS must exist as a callable on Api.
     If one is missing, the test fails and prints the exact missing
     name(s).
  2. Api must not expose any *additional* public callable beyond
     EXPECTED_METHODS. If a new method is added to any mixin, this test
     fails with an explicit "update this test's expected list" message
     -- so every new JS-bridge method is forced to show up here (and in
     docs) instead of silently growing unnoticed.

Hardware / platform / paid-API-dependent third-party modules are stubbed
out (only when not actually importable) before Api is imported, so this
test runs on any machine -- no GPU, no microphone, no Ollama server, no
pywebview runtime, no Windows-only SDKs required -- and in CI.

If importing Api fails for a reason that is NOT one of these stubbed
hardware modules, that is a real code problem: the test prints the exact
original exception instead of silently skipping.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Stub hardware / platform / paid-API dependent modules BEFORE importing Api.
# These are only ever touched as import-time dependencies of the mixins that
# engine.py combines; this test never exercises their real runtime behaviour.
# Real installs (if actually present on the machine running the test) are
# left completely alone -- we only stub what genuinely fails to import.
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "numpy",
    "psutil",
    "onnxruntime",
    "webview",
    "pywebview",
    "sounddevice",
    "pytz",
    "webrtcvad",
    "pyaudio",
    "faster_whisper",
    "onnx",
    "torch",
    "winsdk",
    "winsdk.windows",
    "winsdk.windows.media",
    "winsdk.windows.media.control",
    "winsdk.windows.storage",
    "winsdk.windows.storage.streams",
    "google",
    "google.genai",
    "google.genai.types",
    "nvidia",
    "nvidia.cudnn",
    "nvidia.cudnn.lib",
    "nvidia.cublas",
    "nvidia.cublas.lib",
]


def _install_stub(name: str) -> None:
    """Install a permissive fake module at sys.modules[name], but only if
    it isn't already genuinely importable on this machine."""
    if name in sys.modules:
        return
    try:
        __import__(name)
        return
    except Exception:
        pass

    module = types.ModuleType(name)
    _mock = MagicMock()
    # PEP 562 module-level __getattr__: any attribute (class, function,
    # constant) that real code pulls off this module at import time
    # (`numpy.array`, `from onnxruntime import InferenceSession`, ...)
    # resolves to a MagicMock instead of raising AttributeError.
    module.__getattr__ = lambda attr_name, _m=_mock: getattr(_m, attr_name)  # type: ignore[attr-defined]
    sys.modules[name] = module

    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        _install_stub(parent_name)
        setattr(sys.modules[parent_name], child_name, module)


for _name in _STUB_MODULES:
    _install_stub(_name)


# ---------------------------------------------------------------------------
# Expected public JS-bridge surface. Update this set (and only this set)
# whenever a method is intentionally added to or removed from Api.
# ---------------------------------------------------------------------------
EXPECTED_METHODS = {
    "add_reminder",
    "check_setup_status",
    "close_window",
    "cycle_repeat_mode",
    "delete_reminder",
    "export_memory",
    "get_assistant_active",
    "get_media_status",
    "get_memory_stats",
    "get_notes",
    "get_proactive_stats",
    "get_reminders",
    "get_setup_wizard_seen",
    "get_share_card_data",
    "get_system_stats",
    "get_ui_settings",
    "get_weather",
    "mark_setup_wizard_seen",
    "minimize_window",
    "run_action",
    "run_setup_fix",
    "save_note",
    "seek_media",
    "send_text_command",
    "set_assistant_active",
    "set_focus_mode",
    "set_language",
    "set_mic_sensitivity",
    "set_mute",
    "set_speech_speed",
    "skip_next_track",
    "skip_previous_track",
    "stop_music",
    "stop_sara",
    "toggle_maximize",
    "toggle_music_playback",
    "toggle_reminder",
    "toggle_shuffle",
    "toggle_wifi",
    "update_setting",
    "wake_now",
}

assert len(EXPECTED_METHODS) == 41, (
    "EXPECTED_METHODS must contain exactly 41 entries, found "
    f"{len(EXPECTED_METHODS)}. Fix the list in this test file itself."
)


def _import_api():
    try:
        from sara.gui.app.engine import Api
    except Exception as exc:  # want the real error surfaced, never swallowed
        raise AssertionError(
            "Failed to import sara.gui.app.engine.Api. This is either a "
            "genuine code problem or a hardware-dependent import this test "
            "does not stub yet (check _STUB_MODULES in this file). "
            f"Original error: {exc.__class__.__name__}: {exc}"
        ) from exc
    return Api


def _public_callable_names(cls) -> set:
    names = set()
    for name in dir(cls):
        if name.startswith("_"):
            continue
        member = getattr(cls, name)
        if callable(member):
            names.add(name)
    return names


class ApiSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Api = _import_api()
        cls.actual_methods = _public_callable_names(cls.Api)

    def test_no_expected_methods_missing(self):
        missing = sorted(EXPECTED_METHODS - self.actual_methods)
        self.assertFalse(
            missing,
            "Api class is MISSING expected JS-bridge method(s): "
            f"{missing}. This is exactly the failure mode that previously "
            "shipped silently (get_setup_wizard_seen / "
            "mark_setup_wizard_seen vanished when engine.py was replaced "
            "by an older version) -- check the mixin list in engine.py "
            "and the mixin source files (core.py / reminders.py / "
            "settings.py / notes.py / media.py / setup_wizard.py).",
        )

    def test_no_unexpected_extra_methods(self):
        extra = sorted(self.actual_methods - EXPECTED_METHODS)
        self.assertFalse(
            extra,
            f"Api class exposes {len(extra)} new method(s) not in this "
            f"test's expected list: {extra}. If this addition is "
            "intentional, update EXPECTED_METHODS in "
            "tests/test_api_surface.py to include it (and add matching "
            "docs/tests) -- do not just delete or ignore this failure.",
        )


if __name__ == "__main__":
    unittest.main()
