"""
Tests for D47 + D49: a capture gate that holds its margin at every noise
floor, and a calibration that reaches it before the user's first command.

D49 — THE ADDITIVE FORMULA CANNOT HOLD A RATIO
    _NoiseFloor.suggested_threshold() returns `floor + margin`, and the
    capture start gate is `threshold * 0.35`. So

        gate = 0.35 * (floor + 250) = 0.35*floor + 87.5
        gate > floor   requires   87.5 > 0.65*floor   ->   floor < 134.6

    Above a floor of ~135 the gate sits BELOW the room noise it was derived
    from -- the D45 false-start condition, arriving through the calibration
    path instead of through the VAD. The target machine measured floors of
    164 and 203, so this is not hypothetical for that hardware.

D47 — AND IT NEVER GOT THERE ANYWAY
    The +/-150 step clamp plus _RECALIB_FIRST_INTERVAL (15s) and
    _RECALIB_INTERVAL (300s) means moving 600 down to a floor-derived ~283
    takes three cycles: about ten minutes. Inside a bench/stt_doctor.py run
    the engine does not live long enough for even the first cycle. So
    within any session a user experiences, the gate was simply
    BARGE_IN_ENERGY_THRESHOLD -- which is why that .env value was the only
    thing that helped a quiet microphone.

THE REPLACEMENT, AND WHERE ITS NUMBERS COME FROM
        gate      = clamp(multiplier * floor, gate_min, gate_max)
        threshold = gate / 0.35

    multiplier=4.0 was chosen against seven floor/speech pairs measured on
    the target machine, each pair from ONE session so the levels are
    comparable. The worst speech/floor ratio observed was 14.2x, which is
    what leaves room for a 4x noise margin and a >=3.6x speech margin at
    the same time.

WHY IT SHIPS OFF BY DEFAULT
    It changes the gate on every capture, on a pipeline that had only just
    started working end to end, for a user who could not test it at the
    time. Enabling it is one .env line and the startup log states the
    result. These tests are what make that switch safe to flip.
"""
from __future__ import annotations

import inspect
import re

import pytest

from config import Config
from sara.audio.stt.buffers import _NoiseFloor

START_GATE_FRACTION = 0.35          # _collect_speech's `thr * 0.35`
CONT_GATE_FRACTION = 0.30           # and its `thr * 0.30`

# (noise floor, loudest speech RMS) measured on the target machine. Each
# pair is from ONE session, so the two levels are directly comparable --
# pairing across sessions is meaningless because mic gain drifts and moves
# both together. (An earlier analysis did exactly that and wrongly
# concluded the rule failed at floor 203.)
MEASURED = [
    (24, 2202),
    (25, 3131),
    (29, 1599),
    (33, 607),
    (125, 4996),
    (164, 2337),
    (203, 3082),
]


def _floor_with(value: float) -> _NoiseFloor:
    nf = _NoiseFloor()
    for _ in range(10):
        nf.update(value, threshold=value * 100)   # well under thr*0.4
    assert nf.floor is not None
    return nf


# ── D49: the defect, pinned ─────────────────────────────────────────────
@pytest.mark.parametrize("floor", [140, 164, 203, 300])
def test_the_additive_formula_really_does_invert_above_135(floor: int) -> None:
    """
    Pins the mechanism rather than trusting the narrative. If this ever
    stops being true, the D49 rationale is stale and must be re-derived.
    """
    thr = _floor_with(floor).suggested_threshold(margin=250.0)
    gate = thr * START_GATE_FRACTION
    assert gate < floor, (
        f"floor {floor}: additive formula gave gate {gate:.0f}, which is no "
        f"longer below the noise floor -- re-check the D49 arithmetic"
    )


@pytest.mark.parametrize("floor", [24, 33, 100, 134])
def test_the_additive_formula_is_fine_below_135(floor: int) -> None:
    """The defect is specifically at high floors, not everywhere."""
    thr = _floor_with(floor).suggested_threshold(margin=250.0)
    assert thr * START_GATE_FRACTION > floor


# ── D49: the replacement, against every measured session ────────────────
@pytest.mark.parametrize("floor,speech", MEASURED)
def test_relative_gate_clears_noise_and_stays_under_speech(floor, speech) -> None:
    thr = _floor_with(floor).suggested_threshold_relative(
        gate_multiplier=4.0, start_gate_fraction=START_GATE_FRACTION,
        gate_min=90.0, gate_max=650.0,
    )
    gate = thr * START_GATE_FRACTION
    assert gate / floor >= 3.0, (
        f"floor {floor}: gate {gate:.0f} is only {gate/floor:.1f}x over room "
        f"noise -- too close, captures will false-start (D45)"
    )
    assert speech / gate >= 3.0, (
        f"floor {floor}: gate {gate:.0f} is only {speech/gate:.1f}x below the "
        f"quietest speech measured in that same session ({speech}) -- too "
        f"high, commands will not start a capture"
    )


@pytest.mark.parametrize("floor,speech", MEASURED)
def test_the_continuation_gate_also_clears_noise(floor, speech) -> None:
    """
    D39 made the continuation test energy-only, so `thr * 0.30` must also
    stay above the floor -- otherwise captures never end (the original
    12-second hang).
    """
    thr = _floor_with(floor).suggested_threshold_relative(
        gate_multiplier=4.0, start_gate_fraction=START_GATE_FRACTION,
        gate_min=90.0, gate_max=650.0,
    )
    cont = thr * CONT_GATE_FRACTION
    assert cont > floor, (
        f"floor {floor}: continuation gate {cont:.0f} is at or below room "
        f"noise, so speech would never be detected as ended"
    )


def test_start_gate_stays_stricter_than_continuation() -> None:
    """The asymmetry D39/D45 rely on must survive any gate formula."""
    assert START_GATE_FRACTION > CONT_GATE_FRACTION


# ── the clamps, which are the safety rails ──────────────────────────────
def test_a_silent_microphone_cannot_drive_the_gate_to_zero() -> None:
    """
    A muted or near-dead mic reports a floor near 0. Without gate_min the
    gate would go with it and every chunk would false-start a capture --
    the D45 failure, caused by the fix for it.
    """
    thr = _floor_with(0.5).suggested_threshold_relative(
        gate_multiplier=4.0, gate_min=90.0, gate_max=650.0
    )
    assert thr * START_GATE_FRACTION >= 90.0 - 1e-6


def test_a_very_noisy_room_cannot_make_sara_deaf() -> None:
    thr = _floor_with(5000).suggested_threshold_relative(
        gate_multiplier=4.0, gate_min=90.0, gate_max=650.0
    )
    assert thr * START_GATE_FRACTION <= 650.0 + 1e-6


def test_uncalibrated_returns_none_not_a_guess() -> None:
    """
    "No floor yet" and "a floor of zero" must stay distinguishable, or a
    fresh start would calibrate against nothing.
    """
    nf = _NoiseFloor()
    assert nf.floor is None
    assert nf.suggested_threshold_relative() is None
    nf.update(30.0, threshold=1000.0)
    assert nf.suggested_threshold_relative() is None, (
        "a floor was reported from fewer than 5 samples"
    )


def test_a_zero_fraction_cannot_divide_by_zero() -> None:
    thr = _floor_with(30).suggested_threshold_relative(start_gate_fraction=0.0)
    assert thr is not None and thr > 0


# ── D47: the calibration actually reaches the value ─────────────────────
def test_the_startup_pass_skips_the_step_clamp() -> None:
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._run_one_recalibration)
    assert "if not first:" in src, (
        "the startup calibration is clamped to +/-150 again, so reaching a "
        "floor-derived threshold takes ~10 minutes (D47)"
    )
    assert "thr - 150.0" in src, (
        "the steady-state step clamp was removed entirely; ordinary drift "
        "should stay gradual, only the first pass jumps"
    )


def test_a_fast_first_pass_runs_before_the_fifteen_second_one() -> None:
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._recalib_loop)
    assert "STT_RECALIB_FIRST_FAST_S" in src, (
        "the fast startup calibration is gone, so the first pass waits "
        "_RECALIB_FIRST_INTERVAL (15s) -- later than a doctor run lives "
        "and later than the user's first command (D47)"
    )
    fast_at = src.index("STT_RECALIB_FIRST_FAST_S")
    slow_at = src.index("_RECALIB_FIRST_INTERVAL")
    assert fast_at < slow_at, "the fast pass no longer runs first"
    assert "first=True" in src, (
        "the startup pass does not identify itself, so it gets the step "
        "clamp and gains nothing"
    )


def _recalib_code_before_steady_state() -> str:
    """
    The fast-pass CODE, with comments stripped before slicing.

    Slicing the raw source at `_RECALIB_FIRST_INTERVAL` broke when a later
    comment in that function came to mention the name: `str.index` found the
    mention first and truncated the window before the code it was meant to
    cover. Stripping comments makes the slice depend on the code only.
    """
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._recalib_loop)
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    return code[: code.index("_RECALIB_FIRST_INTERVAL")]


def test_the_fast_pass_does_not_run_while_listening_or_speaking() -> None:
    """
    Calibrating off the user's own voice, or off SARA's, would raise the
    gate instead of lowering it. The steady-state loop already guards this
    and the fast pass must too.
    """
    window = _recalib_code_before_steady_state()
    assert "_is_listening.is_set()" in window and "_tts_active.is_set()" in window, (
        "the fast startup calibration can run while the mic is live or TTS "
        "is speaking, so it would calibrate against speech (D47)"
    )


# ── the switch ──────────────────────────────────────────────────────────
def test_every_knob_is_a_real_config_field() -> None:
    """D31/D40: flags that looked tunable via .env and silently were not."""
    from pathlib import Path

    cfg = Path(inspect.getfile(Config)).read_text(encoding="utf-8")
    import re

    for name, kind in (
        ("VOICE_GATE_NOISE_RELATIVE", bool),
        ("VOICE_GATE_NOISE_MULTIPLIER", float),
        ("VOICE_GATE_MIN_RMS", float),
        ("VOICE_GATE_MAX_RMS", float),
        ("STT_RECALIB_FIRST_FAST_S", float),
    ):
        assert isinstance(getattr(Config, name, None), kind), f"{name} missing"
        assert re.search(rf"^\s+{name}\s*:", cfg, re.MULTILINE), (
            f"{name} is not declared as a Config FIELD, so .env would be "
            f"silently ignored"
        )


def test_it_ships_off_again_after_a_heard_latency_regression() -> None:
    """
    D71: reverted to False the same day D65 turned it on, because the
    person who can hear it reported the regression -- the previous batch
    "felt fast", and after the batch that made this take effect it felt
    slower. That outranks the arithmetic.

    AND THE ARITHMETIC HAD A SPECIFIC ERROR. D65 argued the change was safe
    because the rule derives 96-132 in their room, "within a few percent of
    the gate 105 their successful run used". But gate 105 came from
    BARGE_IN_ENERGY_THRESHOLD=300 as seen by bench/stt_doctor.py, and THE
    DOCTOR DOES NOT LOAD GUI PREFERENCES. The application ran at 793 (the
    saved slider) -- a start gate of 278 -- and the application is what gets
    judged by feel. So the app's gate was cut 2.6x on a reference point
    taken from a different program.

    WHY A LOWER GATE FEELS LIKE LATENCY: captures open on typing, a mouse
    click, a chair, a breath. Each false start consumes a listen() window
    (480-800ms of silence budget), returns empty and retries; speak during
    one and the command is clipped and has to be repeated.

    The rule itself is not in question -- its margin table against seven
    measured floor/speech pairs is still the evidence, and every test above
    still passes. Only the DEFAULT was unjustified. Re-enable with
    VOICE_GATE_NOISE_RELATIVE=True once there is a telemetry baseline to
    compare against, which needs TELEMETRY_ENABLED=True first (D68).
    """
    assert Config.VOICE_GATE_NOISE_RELATIVE is False, (
        "the noise-relative gate is on by default again. It was reverted "
        "because a measured-by-ear latency regression was reported and the "
        "justification for enabling it used the wrong reference gate -- if "
        "there is now a telemetry baseline showing it is better, update "
        "this test and record the numbers (D71)"
    )


def test_the_unclamped_fast_first_pass_is_also_off_by_default() -> None:
    """
    Turning the rule off is not enough on its own, and this is the half
    that is easy to miss.

    The fast first pass runs with `first=True`, which deliberately skips
    the +/-150 step clamp -- so even on the ADDITIVE formula it jumps
    straight to `floor + 250` (about 280 in their room, a start gate of
    98). That is the same 2.6x drop by the other route, which is why D71
    had to change both defaults to restore what they described as fast.

    0 disables it (`_recalib_loop` guards on `fast_s > 0`). D47's point
    still stands, so this is a default and not a deletion -- the retry loop
    from D69 remains for when it is switched back on.
    """
    assert float(Config.STT_RECALIB_FIRST_FAST_S) == 0.0, (
        "the unclamped fast first calibration is enabled by default again, "
        "which drops the start gate on the first second of every session "
        "even with VOICE_GATE_NOISE_RELATIVE off (D71)"
    )


def test_zero_actually_disables_the_fast_pass() -> None:
    """
    The test above asserts the default is 0.0. This one asserts 0.0 MEANS
    something -- that `_recalib_loop` still guards on `fast_s > 0` rather
    than treating 0 as "wait no time, then calibrate immediately", which is
    what an unguarded `wait(timeout=fast_s)` would do.

    Without this, D71's revert would be a no-op that reads as a fix: the
    config would say disabled and the fast pass would run anyway, sooner
    than before.
    """
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._recalib_loop)
    src = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    assert re.search(r"fast_s\s*>\s*0", src), (
        "_recalib_loop no longer guards on `fast_s > 0`, so "
        "STT_RECALIB_FIRST_FAST_S=0 makes the fast pass fire IMMEDIATELY "
        "instead of disabling it, and D71's revert silently does nothing "
        "(D71)"
    )


def test_the_getattr_fallback_agrees_with_the_config_default() -> None:
    """
    The fallback in `getattr(Config, "STT_RECALIB_FIRST_FAST_S", X)` must
    equal the Config default. It read 1.5 while Config said 0.0, so any
    stand-in Config without the field ran a fast pass production does not
    run -- a test asserting "the fast pass is off" could pass while a test
    double exercised it.

    Only doubles reach the fallback, so this is small. It is also exactly
    how a switched-off behaviour creeps back: nobody updates the fallback
    when the default moves.
    """
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._recalib_loop)
    m = re.search(
        r'getattr\(\s*Config,\s*"STT_RECALIB_FIRST_FAST_S"\s*,\s*([0-9.]+)\s*\)',
        src,
    )
    assert m, "the fast-pass lookup is no longer a getattr with a fallback"
    assert float(m.group(1)) == float(Config.STT_RECALIB_FIRST_FAST_S), (
        f"the getattr fallback ({m.group(1)}) disagrees with the Config "
        f"default ({Config.STT_RECALIB_FIRST_FAST_S}); a Config stand-in "
        f"without the field would behave unlike production (D71)"
    )


def test_the_old_formula_is_still_what_runs_while_the_flag_is_off() -> None:
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._run_one_recalibration)
    assert "suggested_threshold(margin=250.0)" in src, (
        "the additive formula is no longer the else-branch, so turning the "
        "flag off would not restore the previous behaviour"
    )
    assert "VOICE_GATE_NOISE_RELATIVE" in src
