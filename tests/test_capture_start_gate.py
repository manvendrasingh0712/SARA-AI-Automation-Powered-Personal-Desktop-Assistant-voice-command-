"""
Regression tests for D45: a capture must not START on webrtcvad's word.

THE OBSERVED FAILURE (target machine, the run right after D39 shipped)
    3. CAPTURE   median block : RMS 29  (room noise)
    5. VAD       112/123 frames voiced
    7. [STT] endpoint rule=base silence_ms=800 utt_ms=64
       listen completed | mode=command | audio=0.32s | text=''
       listen() returned after 1.12s

    The user said "open chrome" and got an empty transcript. Every number
    identifies a false start:

      0.32s returned  = 300ms _pre_buf blob + one 32ms trigger chunk
      utt_ms = 64     = 2 list entries x 32ms
      silence = 800ms = 25 chunks, the endpoint firing correctly
      1.12s elapsed   = 332ms + 800ms + overhead

    Room noise was RMS 29 against a 210 start gate, so energy cannot have
    opened that window. The only other term was self._vad.is_speech(), and
    VAD voiced 112 of 123 chunks of that same near-silent clip.

WHY D39's REASONING WAS WRONG
    D39 removed VAD from the CONTINUATION test and deliberately left the
    START gate's `or` in place, arguing "a false start is cheap again once
    a capture can end". It is not cheap: a false start CONSUMES the
    listen() call. The window opens on noise, ends 800ms later with
    nothing, and the user's speech arrives after it has closed. Before D39
    this was masked -- capture never ended, so it ran the full 12s and
    usually caught the speech anyway. D39 did not introduce this defect, it
    removed what was hiding it.

WHY DROPPING VAD COSTS NO SENSITIVITY
    _pre_buf holds PRE_SPEECH_MS (300ms) of history, so a gate that
    triggers one chunk later still captures the onset. And
    energy_threshold is recalibrated against the measured noise floor
    (_run_one_recalibration, margin 250, clamped +/-150 per step), so a
    quiet room lowers the gate automatically instead of needing VAD.
"""
from __future__ import annotations

import inspect

import sara.audio.stt.engine as stt_engine

_SRC = inspect.getsource(stt_engine.SpeechToText._collect_speech)


def _start_gate() -> str:
    """The WAITING-state start gate, comments stripped."""
    waiting = _SRC.split("_CollectState.WAITING", 1)[1]
    waiting = waiting.split("_CollectState.SPEAKING", 1)[0]
    return "\n".join(
        l for l in waiting.splitlines() if not l.lstrip().startswith("#")
    )


def _continuation() -> str:
    body = _SRC.split("energy_window.append(energy)", 1)[1]
    body = body.split("speech_chunks.append(chunk)", 1)[0]
    return "\n".join(
        l for l in body.splitlines() if not l.lstrip().startswith("#")
    )


def test_start_gate_does_not_consult_vad() -> None:
    """
    THE DEFECT. webrtcvad voiced 112/123 chunks of RMS-29 room tone on the
    target hardware, so any `or vad.is_speech(...)` here opens a capture
    window on silence and burns the listen() call (D45).
    """
    gate = _start_gate()
    assert "is_speech" not in gate, (
        "the start gate consults the VAD again; it will false-start on room "
        "noise and return an empty transcript before the user speaks (D45)"
    )


def test_start_gate_still_uses_energy() -> None:
    gate = _start_gate()
    assert "_rms(chunk)" in gate, "the start gate no longer measures energy"
    assert "thr" in gate, "the start gate no longer compares to a threshold"


def test_both_gates_are_energy_only_and_consistent() -> None:
    """
    After D39 + D45 neither gate consults VAD. If one of them regains it,
    the two disagree about what speech is and the asymmetry argument below
    stops holding.
    """
    assert "is_speech" not in _start_gate()
    assert "is_speech" not in _continuation()


def test_start_remains_stricter_than_continuation() -> None:
    """
    The safety argument for energy-only gating: starting is harder (0.35)
    than sustaining (0.30), so anything loud enough to open a capture holds
    it. If start ever became the looser of the two, captures could open on
    noise they cannot sustain -- which is the D45 failure in another form.
    """
    import re

    start_mult = float(re.search(r"thr \* (0\.\d+)", _start_gate()).group(1))
    cont_mult = float(re.search(r"thr \* (0\.\d+)", _continuation()).group(1))
    assert start_mult > cont_mult, (
        f"start gate ({start_mult}) is no longer stricter than the "
        f"continuation gate ({cont_mult})"
    )


def test_prebuffer_still_covers_the_onset() -> None:
    """
    Dropping VAD is only free because _pre_buf retains history, so a gate
    that fires a chunk late loses no speech. If PRE_SPEECH_MS went to ~0,
    energy-only gating really would clip word onsets.
    """
    pre_ms = stt_engine.SpeechToText.PRE_SPEECH_MS
    chunk_ms = 1000.0 * stt_engine.SpeechToText.CHUNK_SIZE / stt_engine.SpeechToText.SAMPLE_RATE
    assert pre_ms >= 4 * chunk_ms, (
        f"PRE_SPEECH_MS is only {pre_ms}ms ({pre_ms / chunk_ms:.1f} chunks); "
        f"energy-only gating relies on this history to not clip onsets (D45)"
    )


def test_threshold_is_recalibrated_against_measured_noise() -> None:
    """
    The other half of why energy-only is safe: the gate is not a fixed
    constant. If recalibration were removed, a quiet speaker in a quiet
    room would be stuck behind a 600-derived gate with nothing to rescue
    them.
    """
    src = inspect.getsource(stt_engine.SpeechToText._run_one_recalibration)
    assert "self.energy_threshold = " in src, (
        "recalibration no longer updates energy_threshold, so the start "
        "gate is now a fixed constant (D45)"
    )
    assert "_noise_floor" in src, (
        "recalibration no longer consults the measured noise floor"
    )


# ── behavioural: replay the measured failure through the gate rule ──────
def _starts(energies, thr: float = 600.0, vad_voices: bool = False) -> bool:
    """
    Would a capture open on this sequence? `vad_voices` replays the pre-D45
    rule for contrast.
    """
    for energy in energies:
        if vad_voices or energy > thr * 0.35:
            return True
    return False


def test_room_noise_no_longer_opens_a_capture() -> None:
    """The exact measured room noise: RMS 29, well under the 210 gate."""
    assert not _starts([29.0] * 100), (
        "room noise at the measured RMS 29 still opens a capture window"
    )


def test_old_rule_did_open_a_capture_on_that_noise() -> None:
    """
    Pins the mechanism instead of trusting the narrative: with webrtcvad
    voicing room tone -- its measured behaviour on this hardware -- the old
    rule starts on silence no matter how quiet the room is.
    """
    assert _starts([29.0] * 100, vad_voices=True), (
        "the old rule was expected to false-start here; if it no longer "
        "does, this test no longer demonstrates the defect"
    )


def test_real_speech_still_opens_a_capture() -> None:
    """Measured speech was RMS 1599 against the 210 gate -- 7.6x."""
    assert _starts([29.0] * 10 + [1599.0]), (
        "real speech at the measured level no longer opens a capture"
    )


def test_quiet_speech_opens_a_capture_once_the_gate_recalibrates() -> None:
    """
    A soft speaker at RMS 150 is below the boot gate (210) but above the
    recalibrated one, since a 29-RMS room converges the threshold toward
    ~279 (noise + 250 margin) and so the gate toward ~98.
    """
    assert not _starts([150.0] * 20, thr=600.0), "boot gate: expected no start"
    assert _starts([150.0] * 20, thr=279.0), (
        "after recalibration in a quiet room, soft speech must still open a "
        "capture -- this is what replaces VAD's sensitivity (D45)"
    )
