"""
Regression tests for D69: the startup calibration must not lose a race with
the TTS warm-up and then give up.

THE OBSERVED FAILURE (target machine, 14:45 session)
    The log has no `[STT] startup calibration:` line at all, and the gate
    stayed at the GUI slider's value:

        [Debug] Mic sensitivity 23% ... -> 793; speech must now exceed RMS 278
        [TTS] warm-up complete in 3.310s

    The fast pass was a SINGLE attempt at STT_RECALIB_FIRST_FAST_S (1.5s),
    skipped silently when `_is_listening` or `_tts_active` was set. TTS
    warm-up takes ~3.3s on that machine, so the attempt lost that race every
    time. The next chance is _RECALIB_FIRST_INTERVAL (15s), by which point
    the user is mid-command, and after that _RECALIB_INTERVAL (300s) -- so
    in a 50-second session the calibration never ran and the whole benefit
    of VOICE_GATE_NOISE_RELATIVE (D65) was lost to a one-shot.

WHY RETRYING IS THE RIGHT SHAPE
    The fast pass exists to calibrate BEFORE the first command. Polling for
    a bounded window keeps that intent without depending on winning a race
    against an unrelated warm-up. The bound matters too: a permanently busy
    engine must fall through to the steady-state loop rather than spin.
"""
from __future__ import annotations

import inspect
import re

from config import Config
from sara.audio.stt.engine import SpeechToText


def _loop_src() -> str:
    src = inspect.getsource(SpeechToText._recalib_loop)
    return "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )


def test_the_fast_pass_retries_rather_than_giving_up() -> None:
    """THE DEFECT: a single attempt that can be skipped is no attempt."""
    body = _loop_src()
    head = body[: body.index("first_wait")]
    assert "while" in head, (
        "the startup calibration is a one-shot again; it loses the race "
        "against the TTS warm-up and never runs (D69)"
    )


def test_the_retry_window_is_bounded() -> None:
    """A permanently busy engine must not spin here."""
    body = _loop_src()
    head = body[: body.index("first_wait")]
    assert "deadline" in head, "the retry has no deadline (D69)"
    assert "STT_RECALIB_FIRST_FAST_WINDOW_S" in head, (
        "the retry window is not configurable, so it cannot be shortened "
        "or disabled without a code edit (D69)"
    )


def test_the_window_covers_a_realistic_tts_warmup() -> None:
    """
    3.3s of warm-up was measured on the target machine, and the first wake
    cycle follows it. A window that does not clear both reintroduces the
    race it was added to remove.
    """
    window = float(Config.STT_RECALIB_FIRST_FAST_WINDOW_S)
    assert window >= 2 * 3.3, (
        f"STT_RECALIB_FIRST_FAST_WINDOW_S is {window}s, which does not "
        f"comfortably clear the 3.3s TTS warm-up measured on the target "
        f"machine plus the first wake cycle (D69)"
    )


def test_the_window_is_a_declared_config_field() -> None:
    cfg = inspect.getsource(Config)
    assert re.search(
        r"^\s+STT_RECALIB_FIRST_FAST_WINDOW_S\s*:\s*float", cfg, re.MULTILINE
    ), "the window is not a declared Config field, so .env is ignored (D69)"
    assert 'os.getenv("STT_RECALIB_FIRST_FAST_WINDOW_S")' in cfg


def test_it_still_waits_for_the_engine_to_be_idle() -> None:
    """
    The guard is the point: calibrating while a listen is in flight would
    measure the user's own voice as the noise floor. Retrying must not
    become 'calibrate regardless'.
    """
    body = _loop_src()
    head = body[: body.index("first_wait")]
    assert "_is_listening.is_set()" in head and "_tts_active.is_set()" in head, (
        "the retry no longer waits for an idle engine, so it can sample the "
        "user's own speech as room noise (D69)"
    )


def test_a_skipped_calibration_is_reported() -> None:
    """
    The silent skip is why this went unnoticed for a whole session. If it
    gives up, it must say so.
    """
    src = inspect.getsource(SpeechToText._recalib_loop)
    assert "startup calibration skipped" in src, (
        "giving up is silent again; a session with no calibration looks "
        "identical to one that calibrated (D69)"
    )


def test_the_steady_state_loop_still_runs_afterwards() -> None:
    """
    The retry must not swallow the thread: the 15s/300s cycles are what keep
    the gate following the room over a long session.
    """
    body = _loop_src()
    assert "first_wait" in body
    assert "_RECALIB_INTERVAL" in body
    assert "while not self._closed:" in body, (
        "the steady-state loop is gone, so the threshold freezes after "
        "startup (D69)"
    )
