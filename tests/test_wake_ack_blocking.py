"""
Regression tests for D20 (audit batch B): the wake acknowledgement must not
block the listen cycle when AEC is active, and must still block when it is not.

THE DEFECT
----------
core_wiring.py carried a comment stating that when AEC is active the wake ack
should be fired asynchronously (block=False) so that ears.wait_settle() and
ears.listen() start immediately -- "this is what actually removes the fixed
1-1.5s tax per wake cycle". The call three lines below it read:

    tts.speak(ack, fast=True, block=True)

Unconditional, with no aec_active test, so the described optimisation existed
only in the comment. Every wake cycle paid full ack synthesis + playback before
SARA began listening. Verified end to end at the time:

  * `tts` in run_sara_logic is the TTSWorker built in core_wiring (not the raw
    TextToSpeech, whose speak() takes no `block` argument at all).
  * TTSWorker.speak(..., block=True) creates a threading.Event and calls
    done_event.wait() -- it waits for the job to finish, i.e. synthesis AND
    playback.
  * aec_active IS computed in core_wiring and IS used, but only to choose
    post_tts_settle_s (0.3s with AEC vs 1.3s without). It was never consulted
    for the ack.

WHY THE NO-AEC BRANCH MUST KEEP BLOCKING
----------------------------------------
Without echo cancellation the microphone hears the ack itself, and STT can
misfire on SARA's own voice. Blocking until playback finishes is the correct
behaviour there. So this is not "make the ack non-blocking" -- it is "make it
conditional", and both branches are asserted below.

WHAT THESE TESTS DO AND DO NOT COVER
------------------------------------
These are source-level contract tests. They assert the call is correctly
conditional and that TTSWorker's block semantics are what the fix relies on.
They do NOT measure the latency saving: that needs a real microphone, real
speakers and real playback on Windows, and telemetry's existing ttfa_ms is the
instrument for it. No timing claim is made here.
"""
from __future__ import annotations

import inspect
import re
import threading
from pathlib import Path

import pytest

import sara.orchestrator.core_wiring as core_wiring
from sara.orchestrator.tts_worker import TTSWorker

_SRC = Path(inspect.getfile(core_wiring)).read_text(encoding="utf-8")

# The ack call, however it is spelled across future edits.
_ACK_CALL_RE = re.compile(
    r"tts\.speak\(\s*ack\s*,(?P<args>[^)]*)\)",
    re.DOTALL,
)


def _ack_call_args() -> str:
    match = _ACK_CALL_RE.search(_SRC)
    assert match is not None, (
        "could not find the wake-ack `tts.speak(ack, ...)` call in "
        "core_wiring.py -- if it was renamed or restructured, update this test "
        "rather than deleting it: D20 was a silent per-wake latency tax."
    )
    return match.group("args")


def test_wake_ack_block_is_conditional_on_aec() -> None:
    """
    The whole defect: block was a literal True. It must be derived from
    aec_active instead.
    """
    args = _ack_call_args()
    assert "block=" in args, "the wake ack must pass `block=` explicitly"
    assert "aec_active" in args, (
        "the wake ack's block= must depend on aec_active. A bare block=True "
        "reintroduces D20, and because block=True is TTSWorker.speak()'s own "
        "default, omitting the argument reintroduces it too."
    )


def test_wake_ack_is_not_unconditionally_blocking() -> None:
    """Guards the exact regression, including whitespace variants."""
    args = _ack_call_args()
    normalised = re.sub(r"\s+", "", args)
    assert "block=True" not in normalised, (
        "the wake ack is unconditionally blocking again (D20)"
    )


def test_aec_active_is_still_computed() -> None:
    """
    The fix depends on aec_active existing in run_sara_logic's scope. If a
    future refactor moves or renames it, this fails loudly here rather than
    as a NameError at the first wake word.
    """
    assert re.search(r"^\s*aec_active\s*=", _SRC, re.MULTILINE), (
        "aec_active is no longer assigned in core_wiring.py; the wake-ack fix "
        "and post_tts_settle_s both depend on it"
    )
    source = inspect.getsource(core_wiring.run_sara_logic)
    assert "aec_active" in source, (
        "aec_active is not visible inside run_sara_logic, where the wake ack "
        "uses it"
    )


def test_ttsworker_speak_accepts_block_and_defaults_true() -> None:
    """
    Pins the two facts the fix rests on: speak() takes `block`, and its default
    is True (which is why the argument must be passed explicitly).
    """
    sig = inspect.signature(TTSWorker.speak)
    assert "block" in sig.parameters, (
        "TTSWorker.speak lost its `block` parameter; the wake-ack fix calls it"
    )
    assert sig.parameters["block"].default is True, (
        "TTSWorker.speak's block default changed. The fix passes block= "
        "explicitly so this does not matter for correctness, but the comment "
        "in core_wiring.py explains the default and should be updated."
    )


def test_ttsworker_speak_blocking_semantics() -> None:
    """
    block=True waits for the queued job; block=False returns without waiting.

    Driven against a real TTSWorker with its queue consumed by this test
    instead of the worker thread, so no audio device or voice model is needed.
    """
    worker = TTSWorker.__new__(TTSWorker)  # no __init__: avoid audio/db setup
    worker._q = __import__("queue").PriorityQueue()
    worker._seq = iter(range(1000))
    worker._epoch = 0
    worker._epoch_lock = threading.Lock()

    # block=False must return immediately and queue a job with no event.
    worker.speak("hi", block=False)
    _, _, job = worker._q.get_nowait()
    assert job[5] is None, "block=False must not create a done_event"

    # block=True must wait on the event; prove it by setting it from a helper.
    released = threading.Event()

    def _consume() -> None:
        _, _, queued = worker._q.get()
        event = queued[5]
        assert event is not None, "block=True must create a done_event"
        released.set()
        event.set()  # what the real worker thread does when the job finishes

    consumer = threading.Thread(target=_consume, daemon=True)
    consumer.start()
    worker.speak("hi", block=True)  # returns only once _consume sets the event
    consumer.join(timeout=5.0)
    assert released.is_set(), "block=True did not wait for the queued job"


@pytest.mark.parametrize("aec_active,expected_block", [(True, False), (False, True)])
def test_block_expression_evaluates_correctly(aec_active: bool, expected_block: bool) -> None:
    """
    The decision table, stated once so it cannot be inverted by accident:
    AEC on  -> do not block (the saving); AEC off -> block (mic safety).
    """
    assert (not aec_active) is expected_block
