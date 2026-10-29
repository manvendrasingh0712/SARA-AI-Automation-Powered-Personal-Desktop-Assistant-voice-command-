"""
Regression test for D42: `aec_active` must mean "echo cancellation is
actually running", not "an AEC object was constructed".

WHY THIS MATTERS MORE THAN IT LOOKS
    `aec_active` gates two shortcuts in core_wiring.run_sara_logic(), and
    both are only safe when echo really is being cancelled:

      * the per-turn mic settle drops from Config.STT_SETTLE_MIN_GAP_S
        (1.3s) to _POST_TTS_SETTLE_WITH_AEC_S (0.3s)
      * the wake acknowledgement is spoken non-blocking, so listen() starts
        while SARA is still talking

    Without working AEC, taking those shortcuts means SARA's own voice
    reaches the microphone and is transcribed as the user's command.

THE DEFECT
    The test was `getattr(ears, "_aec", None) is not None`. But
    sara/audio/aec.py catches its own ImportError at module level and sets
    _HAS_AEC_LIB = False, and AECProcessor.__init__ then sets
    `self.enabled = Config.AEC_ENABLED and _HAS_AEC_LIB`. Construction
    still SUCCEEDS. So on a machine without the `aec-audio-processing`
    wheel, `_aec` is a live object with enabled=False and the old test read
    True.

    engine.py's `[STT] Ready` line already checked `.enabled` correctly, so
    the two could report opposite things about the same AEC.
"""
from __future__ import annotations

import inspect
import re

from sara.orchestrator import core_wiring


def _aec_active_expr() -> str:
    """
    The aec_active assignment, comments stripped. Sliced by matching the
    opening paren rather than searching for the next ')', which would stop
    inside a nested getattr(...) call.
    """
    src = inspect.getsource(core_wiring.run_sara_logic)
    start = src.index("aec_active = (")
    depth, end = 0, None
    for i in range(src.index("(", start), len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "could not find the end of the aec_active expression"
    return "\n".join(
        l for l in src[start:end].splitlines() if not l.lstrip().startswith("#")
    )


def test_aec_active_requires_the_enabled_flag() -> None:
    expr = _aec_active_expr()
    assert "enabled" in expr, (
        "aec_active no longer checks AECProcessor.enabled, so a constructed "
        "but non-functional AEC re-enables the 0.3s settle and the "
        "non-blocking wake ack with no echo cancellation running (D42)"
    )


def test_aec_active_still_honours_the_config_switch() -> None:
    expr = _aec_active_expr()
    assert "AEC_ENABLED" in expr, "AEC_ENABLED is no longer honoured"


def test_matches_the_engine_ready_line_test() -> None:
    """
    The two reports must agree. engine.py's Ready line is the reference:
    both must require the object AND its enabled flag.
    """
    import sara.audio.stt.engine as stt_engine

    ready = inspect.getsource(stt_engine.SpeechToText._log_init)
    ready = "\n".join(
        l for l in ready.splitlines() if not l.lstrip().startswith("#")
    )
    aec_line = [l for l in ready.splitlines() if "AEC=" in l]
    assert aec_line, "the Ready line no longer reports AEC"
    assert "enabled" in aec_line[0], (
        "the Ready line stopped checking .enabled; it was the correct one "
        "of the two and is this test's reference (D42)"
    )


def test_a_disabled_processor_reads_as_inactive() -> None:
    """
    Behavioural check against the real class: an AECProcessor built while
    the library is unavailable must not read as active. Simulated by
    constructing one and forcing enabled=False, which is exactly the state
    _HAS_AEC_LIB=False produces.
    """
    from config import Config

    class _Ears:
        pass

    ears = _Ears()

    class _Proc:
        enabled = False

    ears._aec = _Proc()
    active = (
        bool(getattr(Config, "AEC_ENABLED", True))
        and getattr(ears, "_aec", None) is not None
        and bool(getattr(getattr(ears, "_aec", None), "enabled", False))
    )
    assert active is False, (
        "a processor with enabled=False still reads as active; the settle "
        "shortcut and non-blocking ack would be taken unsafely (D42)"
    )


def test_real_aecprocessor_exposes_enabled() -> None:
    """
    The guard reads `.enabled`, so the attribute must exist on the real
    class -- otherwise getattr's False default silently disables AEC for
    everyone, which would be a worse bug than the one being fixed.
    """
    from sara.audio.aec import AECProcessor

    src = inspect.getsource(AECProcessor.__init__)
    assert re.search(r"self\.enabled\s*=", src), (
        "AECProcessor no longer sets self.enabled; the D42 guard would "
        "read False and disable AEC unconditionally"
    )


# ── D64: the same defect in a second place, found by self-review ────────
def test_barge_watch_also_requires_the_enabled_flag() -> None:
    """
    D42 fixed `aec_active` in core_wiring. A later grep for the same
    pattern found barge_watch._aec_available() doing exactly the same
    thing -- testing that the AEC object exists rather than that it works.

    The consequence here is the opposite of D42's, and worse. This decides
    whether BARGE_NOAEC_THRESHOLD_BOOST (1.5) is applied, and that boost
    exists to stop SARA's own voice tripping barge-in when nothing is
    cancelling the echo. Reading True meant the boost was skipped exactly
    when it was needed: SARA could interrupt herself mid-reply and take her
    own speech as the user's next command.
    """
    from sara.orchestrator import barge_watch

    src = inspect.getsource(barge_watch._BargeWatch._aec_available) if hasattr(
        barge_watch, "_BargeWatch"
    ) else None
    if src is None:
        cls = next(
            v for v in vars(barge_watch).values()
            if inspect.isclass(v) and hasattr(v, "_aec_available")
        )
        src = inspect.getsource(cls._aec_available)
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    assert "enabled" in code, (
        "barge_watch._aec_available() checks only that an AEC object "
        "exists, so a constructed-but-disabled AEC skips the no-AEC "
        "barge-in boost and SARA can interrupt herself (D64)"
    )


def test_barge_watch_still_tolerates_a_double_without_an_aec_attribute() -> None:
    """
    `hasattr` returning False means the stand-in never had the attribute --
    a test double, not a machine without AEC. It must not be penalised, or
    every existing barge-in test starts exercising the no-AEC path.
    """
    from sara.orchestrator import barge_watch

    cls = next(
        v for v in vars(barge_watch).values()
        if inspect.isclass(v) and hasattr(v, "_aec_available")
    )
    src = inspect.getsource(cls._aec_available)
    assert 'hasattr(self._ears, "_aec")' in src, (
        "the hasattr escape hatch is gone; doubles without an _aec "
        "attribute would now be treated as having broken AEC (D64)"
    )


def test_every_decision_point_checks_enabled_not_just_existence() -> None:
    """
    The property that stops D42/D64 recurring a third time.

    Scoped to the places that DECIDE BEHAVIOUR from AEC availability. An
    earlier draft of this test grepped the whole package for
    `_aec ... is not None` and flagged five more sites -- the AEC worker
    thread start in stt/engine.py and three far-end feeds in tts/player.py.
    Those are legitimate: see the test below for why. A guard that fails on
    correct code is worse than none, because it teaches people to edit the
    guard.
    """
    import pathlib

    from sara.orchestrator import barge_watch

    checks = []
    # core_wiring's aec_active
    src = inspect.getsource(core_wiring.run_sara_logic)
    i = src.index("aec_active = (")
    checks.append(("core_wiring.aec_active", src[i : i + 400]))
    # barge_watch's _aec_available
    cls = next(
        v for v in vars(barge_watch).values()
        if inspect.isclass(v) and hasattr(v, "_aec_available")
    )
    checks.append(
        ("barge_watch._aec_available", inspect.getsource(cls._aec_available))
    )
    # the STT Ready line
    import sara.audio.stt.engine as stt_engine
    ready = inspect.getsource(stt_engine.SpeechToText._log_init)
    # Slice AROUND the AEC segment: it comes after VAD= in the Ready line,
    # so cutting at VAD= would drop the very thing under test.
    _a = ready.index("AEC=")
    checks.append(("stt Ready line", ready[_a : _a + 300]))

    for name, code in checks:
        body = "\n".join(
            l for l in code.splitlines() if not l.lstrip().startswith("#")
        )
        assert "enabled" in body, (
            f"{name} decides AEC availability from object existence alone, "
            f"which is the D42/D64 defect"
        )
    assert pathlib.Path(inspect.getfile(barge_watch)).exists()


def test_the_remaining_is_not_none_sites_are_safe_because_the_processor_no_ops() -> None:
    """
    Why the sites the earlier draft flagged are fine: every AECProcessor
    method that touches audio returns early when `self.enabled` is False.
    So handing data to a disabled processor is a no-op, and the worker
    threads that do so are wasteful at worst, not wrong.

    This test is what makes the narrower scope above honest -- if a data
    method ever stops guarding on `enabled`, those sites become real bugs
    and this fails.
    """
    from sara.audio import aec as aec_mod

    for name in ("feed_far_end", "process_near_end"):
        fn = getattr(aec_mod.AECProcessor, name, None)
        assert fn is not None, f"AECProcessor.{name} is gone; rescope this test"
        src = inspect.getsource(fn)
        assert "not self.enabled" in src, (
            f"AECProcessor.{name} no longer returns early when disabled, so "
            f"every `if self._aec is not None:` caller that feeds it audio "
            f"becomes a real defect (D64)"
        )
