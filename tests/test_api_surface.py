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

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

# Make sure the project root (the parent of this tests/ folder, where the
# `sara` package and config.py live) is importable regardless of HOW this
# file is run:
#   - `python tests/test_api_surface.py` directly -> Python only puts this
#     file's own directory (tests/) on sys.path, not the project root.
#   - `pytest tests/test_api_surface.py` / `pytest tests/` -> pytest's
#     default "prepend" import mode (no tests/__init__.py here) has the
#     same limitation.
#   - `python -m pytest ...` happened to work by accident in earlier
#     testing because `-m` itself adds the current working directory to
#     sys.path -- but that only holds if you happen to run it from the
#     project root. This makes it work unconditionally, from any cwd.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


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
    "google.auth",
    "google.auth.transport",
    "google.auth.transport.requests",
    "google.oauth2",
    "google.oauth2.credentials",
    "google_auth_oauthlib",
    "google_auth_oauthlib.flow",
    "googleapiclient",
    "googleapiclient.discovery",
    "googleapiclient.errors",
    "nvidia",
    "nvidia.cudnn",
    "nvidia.cudnn.lib",
    "nvidia.cublas",
    "nvidia.cublas.lib",
]


# Bookkeeping so every stub installed by this file can be undone. Nothing
# installed here may survive past ApiSurfaceTests (see _restore_stubs()).
_MISSING = object()
_STUB_SYS_MODULES_PREV = {}   # sys.modules key -> previous value (or _MISSING)
_STUB_PARENT_ATTRS = []       # (parent_module, child_name, previous attr or _MISSING)
_MODULES_BEFORE_STUBS = set() # sys.modules keys present before any stub/Api import


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
    module.__getattr__ = lambda attr_name, _m=_mock: getattr(_m, attr_name)  
    # type: ignore[attr-defined]
    _STUB_SYS_MODULES_PREV.setdefault(name, sys.modules.get(name, _MISSING))
    sys.modules[name] = module

    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        _install_stub(parent_name)
        parent = sys.modules[parent_name]
        # vars() (not getattr) so a stubbed parent's permissive __getattr__
        # can't hand back a MagicMock as the "previous" attribute.
        _STUB_PARENT_ATTRS.append((parent, child_name, vars(parent).get(child_name, _MISSING)))
        setattr(parent, child_name, module)


def _install_all_stubs() -> None:
    """Snapshot sys.modules, then install every stub. Called from
    ApiSurfaceTests.setUpClass (not at import time), so nothing is stubbed
    while pytest collects or runs any other test file."""
    _MODULES_BEFORE_STUBS.clear()
    _MODULES_BEFORE_STUBS.update(sys.modules.keys())
    for _name in _STUB_MODULES:
        _install_stub(_name)


def _restore_stubs() -> None:
    """Undo everything _install_all_stubs() and the Api import did:
      - restore/remove parent-module attributes set for dotted stubs,
      - drop every sara.* module first imported while stubs were active
        (they hold references to MagicMocks and must be re-imported fresh by
        later tests),
      - delete stubs this file added and put back anything it overwrote.
    Idempotent."""
    if not _MODULES_BEFORE_STUBS:
        return

    for parent, child, prev in reversed(_STUB_PARENT_ATTRS):
        if prev is _MISSING:
            vars(parent).pop(child, None)
        else:
            setattr(parent, child, prev)
    _STUB_PARENT_ATTRS.clear()

    for mod_name in list(sys.modules):
        if mod_name in _MODULES_BEFORE_STUBS:
            continue
        if mod_name == "sara" or mod_name.startswith("sara."):
            sys.modules.pop(mod_name, None)
            parent_name, _, child_name = mod_name.rpartition(".")
            parent_mod = sys.modules.get(parent_name) if parent_name else None
            if parent_mod is not None:
                vars(parent_mod).pop(child_name, None)

    for mod_name, prev in reversed(list(_STUB_SYS_MODULES_PREV.items())):
        if prev is _MISSING:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = prev
    _STUB_SYS_MODULES_PREV.clear()
    _MODULES_BEFORE_STUBS.clear()


# ---------------------------------------------------------------------------
# Expected public JS-bridge surface. Update this set (and only this set)
# whenever a method is intentionally added to or removed from Api.
# ---------------------------------------------------------------------------
EXPECTED_METHODS = {
    "add_reminder",
    "apply_mode",
    "check_setup_status",
    "close_window",
    "cycle_repeat_mode",
    "delete_reminder",
    "delete_routine",
    "get_action_timeline",
    "get_analytics_dashboard",
    "get_calendar_status",
    "get_today_calendar_events",
    "get_display_name",
    "get_frequent_misses",
    "export_memory",
    "get_assistant_active",
    "get_master_volume",
    "get_media_status",
    "get_media_volume",
    "get_memory_stats",
    "list_media_sessions",
    "select_media_session",
    "get_modes_status",
    "get_notes",
    "get_proactive_stats",
    "get_reminders",
    "get_routine",
    "get_setup_wizard_seen",
    "get_notes_status",
    "get_share_card_data",
    "get_skills_list",
    "get_system_stats",
    "get_ui_settings",
    "get_weather",
    "list_routines",
    "mark_setup_wizard_seen",
    "minimize_window",
    "record_command_usage",
    "run_action",
    "run_routine_now",
    "run_setup_fix",
    "save_note",
    "save_routine",
    "seek_media",
    "send_text_command",
    "set_assistant_active",
    "set_display_name",
    "set_focus_mode",
    "set_language",
    "set_master_volume",
    "set_media_volume",
    "set_mic_sensitivity",
    "set_mute",
    "set_skill_enabled",
    "set_speech_speed",
    "skip_next_track",
    "skip_previous_track",
    "stop_music",
    "stop_sara",
    "toggle_maximize",
    "toggle_master_mute",
    "toggle_music_playback",
    "toggle_reminder",
    "toggle_session_mute",
    "toggle_shuffle",
    "toggle_wifi",
    "update_setting",
    "wake_now",
}

assert len(EXPECTED_METHODS) == 67, (
    "EXPECTED_METHODS must contain exactly 67 entries, found "
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
        _install_all_stubs()
        try:
            cls.Api = _import_api()
            cls.actual_methods = _public_callable_names(cls.Api)
        except BaseException:
            # tearDownClass is not called when setUpClass fails.
            _restore_stubs()
            raise

    @classmethod
    def tearDownClass(cls):
        _restore_stubs()

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
            "settings.py / notes.py / media.py / setup_wizard.py / "
            "calendar_api.py / routines_api.py).",
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