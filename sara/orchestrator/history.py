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
            ears.energy_threshold = max(100, 1000 - (_sens_val * 9))
            _debug_log(
                f"[Debug] Restored mic sensitivity: {_sens_val}% (threshold={ears.energy_threshold:.0f})"
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
