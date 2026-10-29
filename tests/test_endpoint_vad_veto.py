"""
Regression tests for D39: a permissive VAD could hold a capture open forever.

THE OBSERVED FAILURE (target machine, bench/stt_doctor.py stage 7)
    [STTDiag] listen completed | mode=command | audio=12.26s | text='こんにちは!'
    listen() returned after 14.83s

    The user said "open chrome" and stopped. Capture ran the full
    max_duration=12s, and Whisper hallucinated Japanese out of the trailing
    silence. The transcript then routed to 'chat' instead of open_app, so the
    command never executed -- which is what "SARA doesn't hear me" looked
    like from outside.

THE MECHANISM
    _collect_speech's continuation test was:

        is_voice = self._vad.is_speech(chunk) or (avg_energy > thr * 0.3)

    webrtcvad at aggressiveness 2 calls a quiet room speech most of the time.
    Measured with the same _VADFilter over the same 1024-byte chunks this
    loop uses: 74/123 and 112/123 chunks voiced on 4s clips holding roughly
    1s of speech.

    Under `or` that is fatal and silent: is_voice never goes False, so
    `silence_count += 1` is unreachable, so `silence_count >= silence_limit_n`
    is unreachable, so the only remaining exit is the 12s ceiling.

    The energy term was HEALTHY in the failing run -- stage 4 measured room
    noise 125 against this gate's 180, a 1.4x margin, and passed. It simply
    never got a vote. That is the whole defect: not a bad threshold, but a
    correct threshold placed where a noisier signal could always override it.

WHY ENERGY-ONLY IS SAFE HERE
    The START gate is `thr * 0.35` (210) and this continuation gate is
    `thr * 0.30` (180), so anything loud enough to begin a capture sustains
    one with margin to spare. Measured speech was RMS 2337-2660 against 180:
    13x of headroom. And where webrtcvad is absent, is_speech() already
    returned False unconditionally, so that configuration was energy-only
    before this change and is unaffected by it.
"""
from __future__ import annotations

import collections
import inspect
import re

import sara.audio.stt.engine as stt_engine

_SRC = inspect.getsource(stt_engine.SpeechToText._collect_speech)


def _continuation_expr() -> str:
    """
    The continuation test, with comments stripped. The fix's own comment
    quotes the old `or` expression to explain what changed, so matching raw
    source would fail these tests on their own documentation.
    """
    # The SPEAKING branch begins after the start gate; take everything from
    # the energy_window append onwards.
    body = _SRC.split("energy_window.append(energy)", 1)[1]
    body = body.split("speech_chunks.append(chunk)", 1)[0]
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def test_continuation_does_not_consult_vad() -> None:
    """
    THE DEFECT. If is_speech() reappears in the continuation test, a
    permissive VAD can again out-vote the energy gate and every command
    goes back to running the full 12s ceiling (D39).
    """
    expr = _continuation_expr()
    assert "is_voice" in expr, "the continuation test moved; this test is stale"
    assert "is_speech" not in expr, (
        "the continuation test consults the VAD again. webrtcvad reports a "
        "quiet room as speech 60-91% of the time on the target hardware, so "
        "under `or` the endpoint can never fire (D39)"
    )


def test_continuation_still_uses_the_energy_window() -> None:
    """Removing VAD must not have removed the decision itself."""
    expr = _continuation_expr()
    assert "energy_window" in expr, (
        "the continuation test no longer averages the energy window"
    )
    assert "thr" in expr, "the continuation test no longer compares to a threshold"


def test_start_gate_is_deliberately_left_sensitive() -> None:
    """
    The start gate keeps its `or`: it is meant to catch the first syllable,
    and a false start is cheap once a capture can actually end. D39 changed
    one gate on purpose -- if someone "tidies up" by making both match, the
    assistant goes deaf to quiet openings.
    """
    start = _SRC.split("_CollectState.WAITING", 1)[1].split(
        "_CollectState.SPEAKING", 1
    )[0]
    assert "is_speech" in start, (
        "the START gate lost its VAD term; quiet speech onsets will now be "
        "missed. D39 was only ever about ENDING a capture"
    )


def test_the_two_gates_keep_their_asymmetric_thresholds() -> None:
    """
    The safety argument for dropping VAD from continuation is that starting
    is harder than continuing: 0.35 to start, 0.30 to sustain. If
    continuation ever becomes the stricter of the two, speech that was loud
    enough to start a capture could be cut mid-word.
    """
    start_mult = float(re.search(r"thr \* (0\.\d+)", _SRC).group(1))
    cont_mult = float(
        re.search(r"thr \* (0\.\d+)", _continuation_expr()).group(1)
    )
    assert cont_mult < start_mult, (
        f"continuation gate ({cont_mult}) is no longer looser than the start "
        f"gate ({start_mult}); captures can now be cut mid-utterance (D39)"
    )


# ── the behavioural half: prove the endpoint can fire again ──────────────
def _endpoint_fires(energies, thr: float = 600.0) -> bool:
    """
    Replays the FIXED continuation decision over a sequence of per-chunk
    energies and reports whether the endpoint would ever fire.

    `silence_limit_n` is 25 chunks (0.8s at 512 samples / 16kHz).
    """
    window: collections.deque = collections.deque(maxlen=3)
    silence_count = 0
    for energy in energies:
        window.append(energy)
        # The FIXED rule: energy decides.
        is_voice = sum(window) / len(window) > thr * 0.3
        if is_voice:
            silence_count = 0
        else:
            silence_count += 1
            if silence_count >= 25:
                return True
    return False


def _endpoint_fires_old_rule(voiced, energies, thr: float = 600.0) -> bool:
    """The pre-D39 rule, for the contrast the fix rests on."""
    window: collections.deque = collections.deque(maxlen=3)
    silence_count = 0
    for i, energy in enumerate(energies):
        window.append(energy)
        is_voice = (i in voiced) or (sum(window) / len(window) > thr * 0.3)
        if is_voice:
            silence_count = 0
        else:
            silence_count += 1
            if silence_count >= 25:
                return True
    return False


def test_endpoint_fires_on_the_measured_failing_input() -> None:
    """
    The target machine's numbers: ~1s of speech at RMS 2500, then silence at
    room noise 125, inside a 12s window (375 chunks at 32ms).
    """
    energies = [2500.0] * 31 + [125.0] * 344
    assert _endpoint_fires(energies), (
        "the endpoint still cannot fire on the exact input that failed"
    )


def test_old_rule_could_not_fire_on_that_input() -> None:
    """
    Pins the mechanism rather than trusting the narrative: with webrtcvad
    voicing 91% of chunks -- its measured rate on this hardware -- the old
    `or` rule never reaches the endpoint, whatever the energy says.
    """
    energies = [2500.0] * 31 + [125.0] * 344
    voiced = {i for i in range(len(energies)) if i % 100 < 91}  # 91%, spread
    assert not _endpoint_fires_old_rule(voiced, energies), (
        "the old rule was expected to hang on this input; if it no longer "
        "does, this test no longer demonstrates the defect"
    )
    assert _endpoint_fires(energies), "the new rule must fire here"


def test_real_speech_is_not_cut_short() -> None:
    """
    The counter-risk of dropping VAD: cutting someone off mid-sentence. A
    natural inter-word pause is ~200ms (6 chunks), far short of the 25-chunk
    (0.8s) silence limit, so normal speech with gaps must survive.
    """
    energies: list = []
    for _ in range(6):  # six words with 200ms gaps between them
        energies += [2500.0] * 15 + [125.0] * 6
    assert not _endpoint_fires(energies), (
        "ordinary inter-word pauses now end the capture; speech will be cut "
        "off mid-sentence"
    )


def test_quiet_speaker_still_sustains_a_capture() -> None:
    """
    Someone who only just clears the 210 start gate must still hold a
    capture at the 180 continuation gate -- the asymmetry that makes
    energy-only endpointing safe.
    """
    energies = [215.0] * 60
    assert not _endpoint_fires(energies), (
        "a speaker just above the start gate is being cut off; the "
        "start/continuation asymmetry is broken"
    )


def test_trailing_silence_is_bounded_not_twelve_seconds() -> None:
    """
    The user-visible payoff: the capture ends about 0.8s after speech stops,
    not at the 12s ceiling, which is what stopped Whisper hallucinating on
    11 seconds of room noise.
    """
    energies = [2500.0] * 31 + [125.0] * 344
    window: collections.deque = collections.deque(maxlen=3)
    silence_count = 0
    for i, energy in enumerate(energies):
        window.append(energy)
        if sum(window) / len(window) > 180.0:
            silence_count = 0
        else:
            silence_count += 1
            if silence_count >= 25:
                captured_s = (i + 1) * 512 / 16000
                assert captured_s < 3.0, (
                    f"capture ran {captured_s:.2f}s; the endpoint fires but "
                    f"far too late to stop silence hallucinations"
                )
                return
    raise AssertionError("endpoint never fired")
