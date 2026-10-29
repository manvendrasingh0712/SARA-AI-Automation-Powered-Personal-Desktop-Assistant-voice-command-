"""
D72: the preview-latency instrument must report the real per-command cost,
measured against the base the endpoint ACTUALLY races against.

WHY THIS FILE EXISTS -- TWO WRONG INSTRUMENTS, ONE AFTER THE OTHER

  (1) D51 printed a binary verdict against a "budget" of
      ENDPOINT_MIN_MS - ENDPOINT_PREVIEW_AT_MS:

          [STT] preview took 313ms (budget 200ms ...) -- TOO SLOW

      and concluded that overshooting it meant the command "fell back to
      base" and the designed saving "was never collected".

      Wrong. `_endpoint_reached()` re-reads ep["partial"] on EVERY 32ms
      tick, so a LATE partial is still used the moment it lands. The
      endpoint fires at about ENDPOINT_PREVIEW_AT_MS + preview_ms. The
      budget is not a cliff; overshooting it costs the overshoot. The line
      said TOO SLOW on 7 of 7 measured previews while the fast path was
      firing on 7 of 7.

  (2) The replacement computed the cost against base = MIN + (MAX-MIN)/2
      = 625ms. Also wrong, and wrong in the same way D71 was: that is the
      `base_ms=None` FALLBACK in required_silence_ms, and the engine never
      passes None. `_endpoint_open()` passes
      base_ms = _SilenceGate.silence_limit * 1000, clamped to [MIN, MAX]:

          fresh engine            _DEFAULT 0.8s  -> base  800ms
          recent utterances <1.5s _FLOOR   0.45s -> base  450ms
          recent utterances >5.0s  _CEIL   1.2s  -> base  900ms (clamped)

      625ms never occurs in the running application.

WHAT THE REAL BASES MEAN -- the finding this file pins

  Against base 450ms, which is the steady state for short commands (the
  way this assistant is actually used), the endpoint can never fire later
  than 480ms. So:

      * a perfect preview saves at most 128ms, not 288ms
      * 4 of the 7 measured previews contribute NOTHING -- the base rule
        fires first

  The preview earns its keep on the FIRST utterance of a session (base
  800ms, up to 448ms) and after long dictation. Optimising preview latency
  has a small ceiling in the case that matters most, and that is a reason
  not to spend risk on it.

THE TESTING LESSON, kept because it nearly slipped through
  An earlier draft of the key test recomputed the instrument's formula
  inside the test. A mutation run -- breaking the formula in engine.py --
  left it PASSING. The test checked my arithmetic against itself. It is now
  behavioural: it runs the real `_spawn_preview_transcribe`, captures the
  line it prints, and checks those numbers against the shipped
  `decide_endpoint`.
"""
from __future__ import annotations

import inspect
import re
import threading
import time as _time

import pytest

from config import Config
from sara.audio.stt import endpoint as ep
from sara.audio.stt.buffers import _SilenceGate


# The measured distribution from the target machine's DEBUG_MODE trace.
# Not invented: these are the seven `preview took` values in the 14:45 log.
MEASURED_PREVIEW_MS = [218, 282, 313, 344, 375, 390, 485]

CHUNK_MS = 512 / 16000 * 1000.0  # 32.0, the shipped CHUNK_SIZE at 16kHz


def _real_base_ms(silence_limit_s: float) -> float:
    """The base the engine passes, clamped exactly as required_silence_ms does."""
    lo = float(Config.ENDPOINT_MIN_MS)
    hi = float(Config.ENDPOINT_MAX_MS)
    return min(hi, max(lo, float(silence_limit_s) * 1000.0))


# The three bases that actually occur, named by the gate state producing them.
BASE_FRESH = _real_base_ms(_SilenceGate._DEFAULT)   # 800ms
BASE_SHORT = _real_base_ms(_SilenceGate._FLOOR)     # 450ms  <- the common case
BASE_LONG = _real_base_ms(_SilenceGate._CEIL)       # 900ms


def _fire_ms(preview_ms, *, base_ms, text="open chrome", utt_ms=1150.0):
    """
    When the endpoint actually fires, driven by the REAL decide_endpoint at
    the real 32ms tick granularity, against a REAL base.

    `base_ms` is required rather than defaulted: defaulting it to the
    MIN+(MAX-MIN)/2 fallback is precisely the mistake this file documents,
    and a default would let the mistake back in silently.
    """
    at = float(Config.ENDPOINT_PREVIEW_AT_MS)
    lands = at + preview_ms if preview_ms is not None else float("inf")

    n = 0
    while True:
        n += 1
        silence = n * CHUNK_MS
        seen = text if silence >= lands else None
        if ep.decide_endpoint(
            seen, silence, 0.0, "en",
            utterance_ms=utt_ms, base_ms=base_ms, backoff_ms=0.0,
        ):
            rule = ep.required_silence_ms(
                seen, 0.0, "en",
                utterance_ms=utt_ms, base_ms=base_ms, backoff_ms=0.0,
            )[1]
            return silence, rule
        if silence > 3000:  # pragma: no cover - would mean a broken endpoint
            pytest.fail("the endpoint never fired")


def _instrument_src() -> str:
    """The preview timing block, comments stripped.

    Comments are stripped FIRST because this file's own explanation mentions
    the tokens being searched for, and so does the block's docstring -- a
    slicing bug this audit hit three separate times.
    """
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._spawn_preview_transcribe)
    return "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )


# ── behavioural: the printed numbers vs the shipped decision function ───
def _run_preview_and_capture(capsys, monkeypatch, fake_took_ms, silence_limit_s):
    """
    Drive the real preview path with a `_transcribe` that takes a known
    time and a `_silence_gate` reporting a known limit, then return the
    line it printed.
    """
    from sara.audio.stt.engine import SpeechToText

    monkeypatch.setattr(Config, "DEBUG_MODE", True, raising=False)

    class _Gate:
        silence_limit = silence_limit_s

    class _Stub:
        SAMPLE_RATE = 16000
        CHUNK_SIZE = 512
        _wake_whisper_model = object()
        _preview_generation = 7

        def __init__(self):
            self._preview_busy = threading.Event()
            self._silence_gate = _Gate()

        def _transcribe(self, *a, **kw):
            _time.sleep(fake_took_ms / 1000.0)
            return "open chrome"

    stub = _Stub()
    SpeechToText._spawn_preview_transcribe(
        stub, b"\x00\x00" * 8000, lambda _t: None, 7
    )
    for _ in range(600):           # wait for the daemon thread to finish
        if not stub._preview_busy.is_set():
            break
        _time.sleep(0.01)
    assert not stub._preview_busy.is_set(), "the preview thread never finished"

    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if "preview took" in l]
    assert lines, f"the preview timing line was not printed; stdout was {out!r}"
    return lines[-1]


_LINE_RE = re.compile(
    r"preview took (\d+)ms -> endpoint fires ~(\d+)ms "
    r"\(ideal (\d+)ms, \+(-?\d+)ms\); (.*)$"
)


@pytest.mark.parametrize(
    "silence_limit_s, base_label",
    [(_SilenceGate._FLOOR, "450ms / short commands"),
     (_SilenceGate._DEFAULT, "800ms / fresh engine")],
)
@pytest.mark.parametrize("fake_took_ms", [60, 300, 520])
def test_the_reported_fire_time_matches_the_real_endpoint(
    capsys, monkeypatch, fake_took_ms, silence_limit_s, base_label
) -> None:
    """
    The line claims the endpoint fires at ~N ms. Drive the real
    decide_endpoint with the SAME `took` the line reports, against the same
    base, and require the two to agree.

    Using the line's own reported `took` as the input is what makes this
    jitter-proof: time.sleep is approximate, but the question is whether
    the instrument's arithmetic matches the endpoint's.

    The 520ms x 450ms-base cell is the one that caught the second wrong
    instrument: there the partial lands at 670ms, after the 480ms base tick
    has already ended the turn, so the preview contributes nothing and any
    formula that just reports max(MIN, AT+took) is lying.
    """
    line = _run_preview_and_capture(
        capsys, monkeypatch, fake_took_ms, silence_limit_s
    )
    m = _LINE_RE.search(line)
    assert m, f"the preview line no longer has a readable shape: {line!r}"
    took, claimed_fires, claimed_ideal, claimed_cost = (
        int(g) for g in m.groups()[:4]
    )
    base = _real_base_ms(silence_limit_s)

    actual_fires, _rule = _fire_ms(took, base_ms=base)
    assert claimed_fires == pytest.approx(actual_fires, abs=1.0), (
        f"[{base_label}] the log claims the endpoint fires at "
        f"~{claimed_fires}ms but the real decide_endpoint fires at "
        f"{actual_fires:.0f}ms for the same {took}ms preview (D72)"
    )

    ideal_fires, _ = _fire_ms(0, base_ms=base)
    assert claimed_ideal == pytest.approx(ideal_fires, abs=1.0), (
        f"[{base_label}] the log calls {claimed_ideal}ms the ideal, but an "
        f"instant preview fires at {ideal_fires:.0f}ms (D72)"
    )
    assert claimed_cost == claimed_fires - claimed_ideal, (
        "the reported cost is not fires-minus-ideal, so the three numbers "
        "in the line disagree with each other (D72)"
    )


def test_a_wasted_preview_is_reported_as_wasted(capsys, monkeypatch) -> None:
    """
    The case the second instrument got wrong. Against the 450ms base a
    520ms preview lands at 670ms, long after the base rule ended the turn
    at 480ms. The line must say the preview changed nothing, not quietly
    attribute the 480ms to it.
    """
    line = _run_preview_and_capture(
        capsys, monkeypatch, 520, _SilenceGate._FLOOR
    )
    m = _LINE_RE.search(line)
    assert m, f"unreadable line: {line!r}"
    verdict = m.group(5)
    assert "changed nothing" in verdict, (
        f"a 520ms preview against a 450ms base contributed nothing, but the "
        f"line reports: {verdict!r} (D72)"
    )
    took = int(m.group(1))
    _fires, rule = _fire_ms(took, base_ms=BASE_SHORT)
    assert rule == "base", (
        "the fixture no longer produces a base-rule win; re-pick the "
        "preview latency so this test still exercises the wasted case"
    )


def test_a_preview_inside_endpoint_min_ms_is_reported_as_free(
    capsys, monkeypatch
) -> None:
    """
    The other half of dropping the binary verdict: a FAST preview must be
    reported as costing nothing, because the endpoint cannot fire before
    ENDPOINT_MIN_MS however quickly the partial arrives.
    """
    line = _run_preview_and_capture(
        capsys, monkeypatch, 40, _SilenceGate._FLOOR
    )
    m = _LINE_RE.search(line)
    assert m, f"unreadable line: {line!r}"
    assert int(m.group(4)) <= 0, (
        f"a 40ms preview was reported as costing {m.group(4)}ms; anything "
        f"inside ENDPOINT_MIN_MS is free (D72)"
    )
    assert "free" in m.group(5), (
        f"a preview inside ENDPOINT_MIN_MS should say so plainly: {line!r}"
    )


def test_the_instrument_reads_the_live_base_not_the_fallback(
    capsys, monkeypatch
) -> None:
    """
    The D71-class guard. The same preview latency against two different
    gate states must produce two different verdicts -- otherwise the
    instrument is using a constant base and the numbers are fiction.
    """
    short = _run_preview_and_capture(
        capsys, monkeypatch, 400, _SilenceGate._FLOOR
    )
    fresh = _run_preview_and_capture(
        capsys, monkeypatch, 400, _SilenceGate._DEFAULT
    )
    v_short = _LINE_RE.search(short).group(5)
    v_fresh = _LINE_RE.search(fresh).group(5)
    assert v_short != v_fresh, (
        "the same 400ms preview reports identically against a 450ms and an "
        "800ms base, so the instrument is not reading "
        "_SilenceGate.silence_limit (D72)"
    )
    assert "changed nothing" in v_short, (
        f"against the 450ms base a 400ms preview is wasted; got {v_short!r}"
    )
    assert "saved" in v_fresh, (
        f"against the 800ms base a 400ms preview saves time; got {v_fresh!r}"
    )


# ── the facts the instrument was corrected to reflect ───────────────────
def test_a_late_partial_is_still_used_rather_than_falling_back_to_base() -> None:
    """
    D51's specific error. Every measured preview overshot the 200ms budget,
    so if overshooting meant falling back to base, all seven would fire at
    base. Against the 800ms base, none of them do.
    """
    budget = float(Config.ENDPOINT_MIN_MS) - float(Config.ENDPOINT_PREVIEW_AT_MS)
    for took in MEASURED_PREVIEW_MS:
        assert took > budget, (
            f"{took}ms no longer overshoots the {budget:.0f}ms budget; this "
            f"test needs a new fixture to be meaningful"
        )
        fires, rule = _fire_ms(took, base_ms=BASE_FRESH)
        assert rule == "complete", (
            f"a {took}ms preview fell back to rule={rule!r} against the "
            f"{BASE_FRESH:.0f}ms base, which is the behaviour D51 wrongly "
            f"assumed (D72)"
        )
        assert fires < BASE_FRESH, (
            f"a {took}ms preview fired at {fires:.0f}ms, no earlier than the "
            f"{BASE_FRESH:.0f}ms base, so the fast path bought nothing (D72)"
        )


def test_the_steady_state_saving_ceiling_is_small() -> None:
    """
    The finding that matters for where to spend effort. Against the 450ms
    base -- the state repeated short commands produce -- even an
    instantaneous preview saves well under 200ms, and most of the measured
    previews save nothing at all.

    Written as bounds so retuning ENDPOINT_MIN_MS does not fail this for
    the wrong reason; what must hold is that the ceiling stays small and
    that some of the measured previews are wasted.
    """
    ideal, _ = _fire_ms(0, base_ms=BASE_SHORT)
    no_partial, rule = _fire_ms(None, base_ms=BASE_SHORT)
    assert rule == "base"
    ceiling = no_partial - ideal
    assert 0 < ceiling <= 200.0, (
        f"the steady-state saving ceiling moved to {ceiling:.0f}ms; "
        f"re-derive the figures in FINDINGS/MORNING rather than widening "
        f"this bound (D72)"
    )

    wasted = [
        t for t in MEASURED_PREVIEW_MS
        if _fire_ms(t, base_ms=BASE_SHORT)[1] != "complete"
    ]
    assert len(wasted) >= 3, (
        f"only {len(wasted)} of the measured previews are wasted against "
        f"the {BASE_SHORT:.0f}ms base; the published 4-of-7 figure needs "
        f"updating (D72)"
    )


def test_the_625ms_fallback_base_is_not_what_the_engine_passes() -> None:
    """
    Pins the D72b correction itself. If `_endpoint_open` ever stops passing
    a gate-derived base, the published cost figures silently become the
    fallback's again -- which is the error this finding is about.
    """
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._endpoint_open)
    src = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    assert "silence_limit" in src, (
        "_endpoint_open no longer derives limit_ms from the silence gate, "
        "so the endpoint base is not what this file measured (D72)"
    )
    lo = float(Config.ENDPOINT_MIN_MS)
    hi = float(Config.ENDPOINT_MAX_MS)
    fallback = lo + 0.5 * (hi - lo)
    assert fallback not in (BASE_FRESH, BASE_SHORT, BASE_LONG), (
        f"the {fallback:.0f}ms fallback now coincides with a real gate "
        f"state, so this file's central distinction has collapsed -- "
        f"re-derive before trusting any figure here (D72)"
    )


# ── the instrument's shape ──────────────────────────────────────────────
def test_the_misleading_binary_verdict_is_gone() -> None:
    src = _instrument_src()
    assert "TOO SLOW" not in src, (
        "the preview line prints TOO SLOW again. It fired on 7 of 7 "
        "measured previews while the fast path was also firing on 7 of 7, "
        "so it reports a cliff that does not exist (D72)"
    )
    assert "in budget" not in src, (
        "the in-budget/over-budget framing is back; the cost is "
        "proportional, not binary (D72)"
    )


def test_the_line_computes_cost_against_the_gate_derived_base() -> None:
    src = _instrument_src()
    assert "_silence_gate" in src and "silence_limit" in src, (
        "the preview line no longer reads the live silence gate, so its "
        "cost figure is computed against a base the endpoint does not use "
        "(D72)"
    )
    assert "_t_base" in src and "_t_done" in src, (
        "the line no longer races the two rules, so it cannot tell a "
        "wasted preview from a useful one (D72)"
    )
    assert re.search(r"math\.ceil", src), (
        "the prediction no longer rounds up to a tick boundary, so it "
        "reports a fire time the endpoint cannot produce (D72)"
    )
    assert "CHUNK_SIZE" in src and "SAMPLE_RATE" in src, (
        "the tick length is no longer derived from CHUNK_SIZE/SAMPLE_RATE "
        "and would be wrong at any other chunk size (D72)"
    )


def test_the_instrument_stays_debug_gated_and_logs_no_transcript() -> None:
    """
    Unchanged requirement from D51/D60: this runs on every silence run, so
    it must not print unless DEBUG_MODE, and must never log recognised text
    -- the standing constraint is that transcripts stay out of logs.
    """
    src = _instrument_src()
    i = src.index("preview took")
    assert "DEBUG_MODE" in src[max(0, i - 2000):i], (
        "the preview timing line is no longer DEBUG_MODE-gated (D51/D60)"
    )
    printed = src[src.rindex("print(", 0, i):src.index(")", src.index("_ideal:.0f", i))]
    for forbidden in ("result", "snapshot", "str(result)"):
        assert forbidden not in printed, (
            f"the preview timing line interpolates {forbidden!r}; recognised "
            f"text and raw audio must never reach the log"
        )
