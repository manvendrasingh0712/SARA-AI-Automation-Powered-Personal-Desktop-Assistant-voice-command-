"""
sara.orchestrator.history
Restoring conversation history and saved preferences into a fresh
SaraLLM/session at startup.
"""
from .lazy import _debug_log

from config import Config

from sara.orchestrator._constants import _KOKORO_SPEED_MIN, _KOKORO_SPEED_MAX


# ----------------------------------------------------------------------------
# History restore
# ----------------------------------------------------------------------------


def _row_role(r):
    return r.get("role", "") if isinstance(r, dict) else getattr(r, "role", "")


def _row_content(r):
    if isinstance(r, dict):
        return r.get("message") or r.get("content", "")
    return getattr(r, "message", getattr(r, "content", ""))


def _restore_history(db, brain) -> None:
    try:
        limit = Config.MAX_MEMORY_EXCHANGES * 2
        try:
            rows = db.get_recent_messages(limit)
        except TypeError:
            rows = db.get_recent_messages()
        if not rows:
            return

        pairs = []
        i = 0
        while i < len(rows) - 1:
            row_a, row_b = rows[i], rows[i + 1]
            if _row_role(row_a) == "user" and _row_role(row_b) == "assistant":
                pairs.append((_row_content(row_a), _row_content(row_b)))
                i += 2
            else:
                i += 1
        brain.load_history(pairs)
    except Exception as e:
        print(f"[Warning] Could not restore conversation history: {e}")


# ----------------------------------------------------------------------------
# Object construction
# ----------------------------------------------------------------------------


def _apply_saved_preferences(db, ears):
    """Re-apply Voice Control slider preferences saved by the GUI (mic
    sensitivity / speech speed) so they persist across app restarts.

    LANGUAGE-SYNC FEATURE: also restores a previously-chosen manual
    EN/HI language (saved by Api.set_language via the pref writer) so a
    manual choice survives an app restart instead of silently reverting
    to auto-detect. Returns (mode, lang).
    """
    try:
        _saved_mic_sens = db.get_preference("mic_sensitivity")
        if _saved_mic_sens is not None:
            _sens_val = max(0, min(100, int(_saved_mic_sens)))
            # D58: this preference OVERRIDES Config.BARGE_IN_ENERGY_THRESHOLD,
            # and the old message hid that. On the target machine the log read
            #
            #   [STT] Ready — ... threshold=300          <- from .env
            #   [Debug] Restored mic sensitivity: 23% (threshold=793)
            #
            # a few lines apart, with nothing to say the second wins. The
            # user had deliberately set BARGE_IN_ENERGY_THRESHOLD=300 in .env
            # to get past a quiet microphone; a GUI slider saved at 23% put
            # it back to 793, so the real start gate was 793 x 0.35 = 277
            # rather than the 105 they were expecting -- and both numbers
            # appeared in the same log, neither marked as the effective one.
            #
            # Precedence itself is correct: an explicit GUI action should beat
            # a file default. Only the silence was wrong. Note this uses the
            # plain setter, NOT set_manual_energy_threshold(), so it does not
            # suppress _run_one_recalibration().
            _previous = float(ears.energy_threshold)
            _restored = max(100, 1000 - (_sens_val * 9))
            ears.energy_threshold = _restored
            if abs(_restored - _previous) > 1.0:
                # D64-followup: the last sentence of this message used to
                # read "Change it with the GUI slider, not .env." That
                # became WRONG ADVICE the moment D65 enabled the
                # noise-relative gate: the startup calibration runs
                # STT_RECALIB_FIRST_FAST_S (1.5s) after the mic stream
                # opens and supersedes this restored value too. A live
                # slider move still sticks -- Api.set_mic_sensitivity goes
                # through set_manual_energy_threshold(), which suppresses
                # recalibration for 600s -- but a value merely RESTORED at
                # startup does not, and that is deliberate: a stale saved
                # number should yield to a fresh measurement of the actual
                # room. Saying so here is what stops this line and the
                # calibration line contradicting each other in the log.
                _gate_adaptive = bool(
                    getattr(Config, "VOICE_GATE_NOISE_RELATIVE", False)
                )
                _note = (
                    " Startup calibration will re-derive this from the "
                    "measured room noise shortly; move the GUI slider to "
                    "override it for 10 minutes."
                    if _gate_adaptive
                    else " Change it with the GUI slider, not .env."
                )
                print(
                    f"[Debug] Mic sensitivity {_sens_val}% (saved in the GUI) "
                    f"OVERRIDES BARGE_IN_ENERGY_THRESHOLD "
                    f"{_previous:.0f} -> {_restored:.0f}; "
                    f"speech must now exceed RMS {_restored * 0.35:.0f} to "
                    f"start a capture.{_note}"
                )
            else:
                _debug_log(
                    f"[Debug] Restored mic sensitivity: {_sens_val}% "
                    f"(threshold={_restored:.0f}, unchanged)"
                )
    except Exception as e:
        print(f"[Warning] Could not restore mic sensitivity: {e}")
    try:
        _saved_speed = db.get_preference("speech_speed")
        if _saved_speed is not None:
            _speed_val = max(0, min(100, int(_saved_speed)))
            span = _KOKORO_SPEED_MAX - _KOKORO_SPEED_MIN
            speed_val_mapped = round(_KOKORO_SPEED_MIN + (_speed_val / 100.0) * span, 3)
            Config.KOKORO_SPEED = speed_val_mapped
            Config.KOKORO_SPEED_EN = speed_val_mapped
            Config.KOKORO_SPEED_HI = speed_val_mapped
            _debug_log(
                f"[Debug] Restored speech speed: {_speed_val}% (kokoro_speed={speed_val_mapped:.2f})"
            )
    except Exception as e:
        print(f"[Warning] Could not restore speech speed: {e}")

    try:
        saved_mode = db.get_preference("language_mode")
        if saved_mode in ("en", "hi"):
            _debug_log(f"[Debug] Restored manual language preference: {saved_mode}")
            return "manual", saved_mode
    except Exception as e:
        print(f"[Warning] Could not restore language preference: {e}")
    return "auto", "en"


def _finish_brain_setup(db, brain) -> None:
    try:
        _restore_history(db, brain)
        saved_name = db.get_user_name()
        if saved_name:
            brain.set_user_name(saved_name)
    except Exception as e:
        print(f"[Warning] Deferred brain setup failed: {e}")
