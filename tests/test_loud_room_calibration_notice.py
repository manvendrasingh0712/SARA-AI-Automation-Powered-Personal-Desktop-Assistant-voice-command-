"""
Regression tests for D70: when calibration declines because the room is too
loud, it must say so.

WHY THIS MATTERS MORE THAN A LOG LINE USUALLY DOES
    `_run_one_recalibration()` bails when the sampled average exceeds half
    the current threshold. That guard is correct and must stay: without it
    the gate could be derived from the user's own voice, which would raise
    it until SARA went deaf.

    But it fires identically for sustained ROOM audio, and then returned in
    silence. On the target machine that hid a whole session: a YouTube track
    was playing and reached the mic at RMS ~980 against a start gate of 278,
    so captures opened on the music -- a 5.58s capture transcribed as
    'Yes.', two Thai hallucinations from a Hindi song (both correctly
    discarded by D43), a 0.12s capture, and a wake score of 0.6586 barely
    over its 0.6 threshold. Every one of those traces to the music, and
    nothing in the log said the room was simply too loud to measure.

THE LIMITATION THIS DOCUMENTS (deliberately not fixed)
    Because the guard bails, VOICE_GATE_NOISE_RELATIVE can LOWER the gate in
    a quiet room but can never RAISE it in a loud one -- the loud-room
    measurement is the one it refuses to take. Telling sustained noise apart
    from a person talking needs evidence across several attempts, and
    getting it wrong raises the gate until SARA goes deaf. That wants its
    own batch and a real measurement.
"""
from __future__ import annotations

import inspect

from sara.audio.stt.engine import SpeechToText


def _recalib_src() -> str:
    """Raw source, comments included."""
    return inspect.getsource(SpeechToText._run_one_recalibration)


def _recalib_code() -> str:
    """
    Source with comments stripped.

    Needed for any test that SLICES: the explanatory comment above the
    notice legitimately contains the same phrases as the log line, so
    slicing the raw source lands inside the prose. This is the third time
    tonight that trap has been hit, so the helper now owns the fix.
    """
    return "\n".join(
        l for l in _recalib_src().splitlines()
        if not l.lstrip().startswith("#")
    )


def test_the_guard_itself_is_still_there() -> None:
    """
    The notice must not have replaced the protection. Calibrating on speech
    is the failure this guard prevents.
    """
    src = _recalib_src()
    assert "avg > thr * 0.5" in src, (
        "the too-loud guard is gone, so the gate can now be derived from "
        "the user's own voice (D70 was about reporting, not removing it)"
    )


def test_declining_is_reported_not_silent() -> None:
    src = _recalib_src()
    assert "calibration declined" in src, (
        "calibration returns in silence again when the room is too loud, "
        "which is what hid an entire session of music-induced captures (D70)"
    )


def test_the_notice_carries_the_numbers_needed_to_act() -> None:
    """
    'Too loud' alone is not actionable. The measured level, the threshold it
    is being compared against, and the resulting start gate are what let a
    reader decide between turning the music off and moving the slider.
    """
    src = _recalib_code()
    window = src[src.index("calibration declined"):]
    window = window[: window.index("return")]
    assert "avg" in window, "the measured room level is not reported"
    assert "thr" in window, "the threshold it is compared against is not reported"
    assert "0.35" in window, (
        "the resulting START GATE is not reported, so the reader cannot tell "
        "what level will open a capture (D70)"
    )


def test_the_notice_is_rate_limited() -> None:
    """
    This runs on a timer for the whole session. Unrated, a loud room would
    fill the log -- which is the D60 defect (unbounded printing) in a new
    place.
    """
    src = _recalib_code()
    window = src[max(0, src.index("calibration declined") - 400) :]
    assert "_log_rate_limited" in window, (
        "the too-loud notice is not rate-limited, so a persistently loud "
        "room floods the log (D70)"
    )


def test_it_is_debug_gated_like_its_neighbours() -> None:
    src = _recalib_code()
    window = src[: src.index("calibration declined")]
    assert 'DEBUG_MODE' in window, (
        "the notice is not DEBUG_MODE-gated, unlike the other calibration "
        "reporting in this function"
    )


# ── the arithmetic that made this a finding ─────────────────────────────
def test_the_measured_session_would_have_been_explained() -> None:
    """
    Replays the target machine's numbers: with the slider's threshold of
    793, music at RMS 980 both clears the start gate AND trips the guard --
    so captures open on it while calibration refuses to measure it.
    """
    thr = 793.0
    music = 980.1          # [WakeDiag] RAW mic stream ... rms=980.1
    start_gate = thr * 0.35
    assert music > start_gate, (
        "this test no longer reproduces the condition: the music level no "
        "longer clears the start gate"
    )
    assert music > thr * 0.5, (
        "the music level no longer trips the too-loud guard, so it would "
        "not have been silently declined"
    )
    # and the noise floor never even samples it
    assert music >= thr * 0.4, (
        "_NoiseFloor.update() would have sampled the music as room noise; "
        "the point of this finding is that it does not"
    )


def test_noise_floor_still_ignores_chunks_above_its_sampling_bar() -> None:
    """
    Pins the mechanism the finding rests on: the floor estimate only sees
    quiet chunks, which is why loud continuous audio cannot raise the gate.
    """
    from sara.audio.stt.buffers import _NoiseFloor

    nf = _NoiseFloor()
    thr = 793.0
    for _ in range(20):
        nf.update(980.1, thr)      # music: above thr * 0.4, must be ignored
    assert nf.floor is None, (
        "the noise floor now samples chunks above threshold * 0.4, so a "
        "loud room would be measured as its own floor (D70)"
    )
    for _ in range(10):
        nf.update(30.0, thr)       # genuine quiet
    assert nf.floor == 30.0
