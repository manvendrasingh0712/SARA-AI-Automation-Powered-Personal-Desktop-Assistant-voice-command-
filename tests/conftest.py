"""
Session-wide test setup.

WARM THE LAZY ORCHESTRATOR IMPORT (test determinism)
----------------------------------------------------
sara/core/planning/executor.py defers `from sara.orchestrator.intent_handlers
import _log_action` to call time, inside _audit_plan_results(), to break the
dispatcher <-> planning import cycle. The first time a plan executes in a
process, that statement imports the whole sara.orchestrator package, which
pulls in core_wiring -> vision / STT / TTS and probes a long list of optional
native dependencies (pyaudio, faster-whisper, webrtcvad, openwakeword,
sounddevice, kokoro-onnx, Pillow). That is slow, and it happens INSIDE the
plan's total-timeout budget.

In production this costs nothing: the app imports sara.orchestrator during
boot, long before any plan runs, so the import is already warm.

Under pytest it is a landmine. Whichever test happens to execute the first
plan pays the cost inside its own deadline, so
ExecutorTests::test_execute_plan_all_succeed passes or fails depending purely
on collection order -- it failed the moment tests/test_planning_gemini_backend.py
was added after it, with "Total plan timeout (6.0s) reached before step 1 of 2
could run", a message pointing nowhere near the real cause.

Doing the import once here, before any test runs, makes the cost a fixed
start-up charge and the suite order-independent. This changes no production
behaviour; it only moves an import that production already does at boot.
"""
from __future__ import annotations

# ── NEVER TOUCH THE USER'S REAL DATA (must run before `config` is imported) ──
# E2E audit 2026-10-30: the suite appended 66 junk lines ("todo 0b4a5616:
# water the plants", "call about invoice c972e89a", ...) to the user's REAL
# sara_todos.txt, one batch per run: tests redirected the notes file and
# Config.DB_PATH but not the to-do file, and anything that builds a
# PreferencesDB() / telemetry / memory store writes under %LOCALAPPDATA%.
# Pointing LOCALAPPDATA and the explicit file paths at a throw-away folder
# first makes every test hermetic by construction, whatever it forgets to patch.
import os as _os
import tempfile as _tempfile

_ISOLATED = _tempfile.mkdtemp(prefix="sara_tests_")
_os.environ["LOCALAPPDATA"] = _ISOLATED
_DATA = _os.path.join(_ISOLATED, "SARA-AI", "data")
_os.makedirs(_DATA, exist_ok=True)
_os.environ["DB_PATH"] = _os.path.join(_DATA, "sara_data.db")
_os.environ["NOTES_FILE_PATH"] = _os.path.join(_DATA, "sara_notes.txt")
_os.environ["TODO_FILE_PATH"] = _os.path.join(_DATA, "sara_todos.txt")

import sara.orchestrator.intent_handlers  # noqa: F401  -- warm-up, see above
