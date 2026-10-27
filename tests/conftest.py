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

import sara.orchestrator.intent_handlers  # noqa: F401  -- warm-up, see above
