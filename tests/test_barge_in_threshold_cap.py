"""
Regression tests for D63: enabling the noise-relative gate (D65) must not
make interrupting SARA harder -- in one measured room it made it impossible.

THE DEFECT I INTRODUCED
    D65 made `energy_threshold` track the room noise, which is right for the
    CAPTURE gate: that gate has to sit above room noise or capture
    false-starts. Barge-in then inherited it, because both
    _passes_barge_in_gate() and is_user_speaking() multiplied
    `self.energy_threshold` by TTS_BLEED_GUARD_MULTIPLIER (1.6) -- a
    multiplier tuned against a FIXED threshold.

    Those are different quantities. Barge-in must clear SARA's own TTS ECHO
    BLEED, which is bounded by playback level and has nothing to do with the
    room. Against the seven floor/speech pairs measured on the target
    machine, with BARGE_IN_ENERGY_THRESHOLD at its 600 default:

        floor  speech   D65 gate   margin
          164    2337       2971    0.79x   <- gate ABOVE the user's speech
          203    3082       2971    1.04x
          125    4996       2286    2.19x

    At floor 164 the user could not have interrupted SARA at all.

THE FIX
    Cap it: barge-in may become EASIER than Config.BARGE_IN_ENERGY_THRESHOLD
    when calibration lowers the threshold in a quiet room, but never HARDER.
    That restores the pre-D65 ceiling -- the value that was observably
    working, since telemetry recorded barge_in outcomes -- while leaving
    D65's capture-gate fix untouched.

        floor  speech   capped gate   margin
          164    2337           960    2.43x
          203    3082           960    3.21x
"""
from __future__ import annotations

import inspect

import pytest

from config import Config
from sara.audio.stt.engine import SpeechToText

# (noise floor, loudest speech RMS) measured on the target machine, each
# pair from ONE session so the levels are comparable.
_MEASURED = [(24, 2202), (25, 3131), (29, 1599), (33, 607),
             (125, 4996), (164, 2337), (203, 3082)]


def _ears(calibrated: float) -> SpeechToText:
    ears = SpeechToText.__new__(SpeechToText)
    ears._energy_threshold = float(calibrated)
    import threading
    ears._threshold_lock = threading.Lock()
    ears._manual_threshold_until = 0.0
    return ears


def _calibrated_for(floor: float) -> float:
    from sara.audio.stt.buffers import _NoiseFloor

    nf = _NoiseFloor()
    for _ in range(10):
        nf._samples.append(float(floor))
    return float(nf.suggested_threshold_relative())


# ── the cap itself ──────────────────────────────────────────────────────
def test_barge_in_is_never_harder_than_the_configured_base() -> None:
    """THE PROPERTY. Interruption must not become impossible in a noisy room."""
    base = float(Config.BARGE_IN_ENERGY_THRESHOLD)
    for floor, _speech in _MEASURED:
        calibrated = _calibrated_for(floor)
        got = _ears(calibrated)._barge_in_threshold()
        assert got <= base + 1e-9, (
            f"at noise floor {floor} the calibrated threshold is "
            f"{calibrated:.0f}, and barge-in would require {got:.0f} -- "
            f"above the configured base {base:.0f}. D65 must not make "
            f"interrupting SARA harder (D63)"
        )


def test_barge_in_may_become_easier_in_a_quiet_room() -> None:
    """
    The cap is one-sided on purpose: a quiet room lowering the threshold
    SHOULD make interruption more sensitive. Clamping both ways would throw
    that away.
    """
    base = float(Config.BARGE_IN_ENERGY_THRESHOLD)
    quiet = _ears(base / 3.0)._barge_in_threshold()
    assert quiet < base, "a lowered threshold no longer eases barge-in (D63)"
    assert quiet == pytest.approx(base / 3.0)


def test_the_impossible_case_is_repaired() -> None:
    """
    Floor 164 / speech 2337 is the pair where the D65 gate sat above the
    user's own voice. With the cap it must sit comfortably below it.
    """
    mult = float(getattr(Config, "TTS_BLEED_GUARD_MULTIPLIER", 1.6))
    calibrated = _calibrated_for(164)
    uncapped = calibrated * mult
    capped = _ears(calibrated)._barge_in_threshold() * mult
    assert uncapped > 2337, (
        "this test no longer demonstrates the defect: the uncapped gate is "
        "not above the measured speech level any more"
    )
    assert capped < 2337 / 1.5, (
        f"capped barge-in gate {capped:.0f} still leaves less than a 1.5x "
        f"margin under the measured speech level 2337 (D63)"
    )


@pytest.mark.parametrize("floor,speech", _MEASURED)
def test_every_measured_room_can_still_interrupt(floor: int, speech: int) -> None:
    mult = float(getattr(Config, "TTS_BLEED_GUARD_MULTIPLIER", 1.6))
    gate = _ears(_calibrated_for(floor))._barge_in_threshold() * mult
    assert speech > gate, (
        f"at floor {floor}, speech measured at RMS {speech} cannot clear a "
        f"barge-in gate of {gate:.0f} -- interruption is impossible there"
    )


# ── both call sites must agree ──────────────────────────────────────────
def test_both_barge_in_call_sites_use_the_helper() -> None:
    """
    If one site keeps reading energy_threshold directly, the per-chunk gate
    and the sustained-speech vote disagree about how loud an interruption
    has to be.
    """
    for fn in (SpeechToText._passes_barge_in_gate, SpeechToText.is_user_speaking):
        src = inspect.getsource(fn)
        body = "\n".join(
            l for l in src.splitlines() if not l.lstrip().startswith("#")
        )
        assert "TTS_BLEED_GUARD_MULTIPLIER" in body, (
            f"{fn.__name__} no longer applies the bleed multiplier"
        )
        idx = body.index("TTS_BLEED_GUARD_MULTIPLIER")
        after = body[idx:]
        assert "_barge_in_threshold()" in after, (
            f"{fn.__name__} multiplies something other than "
            f"_barge_in_threshold(), so the two barge-in gates can drift "
            f"and D65's noise-scaled value can leak back in (D63)"
        )


def test_is_user_speaking_keeps_the_calibrated_threshold_when_tts_is_idle() -> None:
    """
    The cap applies ONLY to barge-in. With TTS silent, "is the user
    speaking" is a capture-side question and must use the calibrated,
    noise-tracking threshold -- otherwise D65's fix is partly undone.
    """
    src = inspect.getsource(SpeechToText.is_user_speaking)
    head = src[: src.index("TTS_BLEED_GUARD_MULTIPLIER")]
    assert "thr = self.energy_threshold" in head
    assert "_rms(c) > thr" in head, (
        "the TTS-idle branch no longer compares against the calibrated "
        "threshold (D63 was only about the barge-in branch)"
    )


def test_the_helper_reads_the_field_named_for_the_job() -> None:
    src = inspect.getsource(SpeechToText._barge_in_threshold)
    assert 'getattr(Config, "BARGE_IN_ENERGY_THRESHOLD"' in src
    assert "min(" in src, "the cap is gone (D63)"
