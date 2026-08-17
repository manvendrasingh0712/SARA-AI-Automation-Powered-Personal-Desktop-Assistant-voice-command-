"""
health_check.py — lightweight, non-blocking startup diagnostics for
Sara AI.

run_startup_diagnostics(ui_update) is called as the VERY FIRST thing
inside gui_main.build_core_objects(), before any expensive model/audio
object is constructed. It never raises and never blocks for long — its
only job is to catch obviously-missing prerequisites (no mic/speaker,
missing model files, Ollama unreachable, etc.) and report them LOUD and
EARLY via the logger + a GUI notification, instead of the app failing
silently/confusingly deep inside TTS/STT/LLM construction later.

Every individual check is wrapped in its own try/except so one failing
check (e.g. sounddevice not installed) can never stop the rest of the
diagnostics — or startup itself — from proceeding.

REFACTOR (on-demand diagnostics skill support):
Each _check_X(ui_update) function now ADDITIONALLY returns a list of
structured result dicts: {"name": str, "friendly_name": str, "ok": bool,
"detail": str}. This is purely additive — every existing _fail()/_ok()
call (and therefore every log line and every GUI notification) fires
in EXACTLY the same place, with EXACTLY the same message, as before.
run_startup_diagnostics() now returns the combined list instead of
None; since nothing at any existing call site used its return value,
this cannot change observable startup behavior. The new on-demand
sara/skills/self_diagnostics.py skill calls run_startup_diagnostics()
directly (passing ui_update=None so no duplicate GUI toasts fire) and
turns the structured list into a single spoken sentence.
"""

import os
import shutil
import logging
import urllib.request

logger = logging.getLogger("sara.health_check")


def _fail(ui_update, message: str) -> None:
    logger.warning(f"[HealthCheck] FAIL: {message}")
    if ui_update is not None:
        try:
            ui_update("notification", "ti-alert-triangle", "#f87171", message)
        except Exception:
            pass


def _ok(message: str) -> None:
    logger.info(f"[HealthCheck] OK: {message}")


def _check_audio_devices(ui_update) -> list:
    """Best-effort mic/speaker presence check. Uses sounddevice if it's
    installed; otherwise skipped entirely (not a hard dependency of
    this module — sara/audio/* import their own audio libs)."""
    results = []
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        has_input = any(d.get("max_input_channels", 0) > 0 for d in devices)
        has_output = any(d.get("max_output_channels", 0) > 0 for d in devices)

        if not has_input:
            msg = "No microphone detected — voice input will not work."
            _fail(ui_update, msg)
            results.append(
                {"name": "microphone", "friendly_name": "your microphone", "ok": False, "detail": msg}
            )
        else:
            _ok("Microphone detected.")
            results.append(
                {"name": "microphone", "friendly_name": "your microphone", "ok": True, "detail": "Microphone detected."}
            )

        if not has_output:
            msg = "No speaker/output device detected — Sara won't be heard."
            _fail(ui_update, msg)
            results.append(
                {"name": "audio_output", "friendly_name": "your audio output", "ok": False, "detail": msg}
            )
        else:
            _ok("Audio output device detected.")
            results.append(
                {"name": "audio_output", "friendly_name": "your audio output", "ok": True, "detail": "Audio output device detected."}
            )
    except ImportError:
        logger.info(
            "[HealthCheck] sounddevice not installed — skipping audio device check."
        )
    except Exception as e:
        logger.info(f"[HealthCheck] Audio device check skipped (non-fatal): {e}")
    return results


def _check_config(ui_update) -> list:
    """Confirms config.py imports cleanly and has the attributes the
    rest of the app assumes exist, without hard-failing if a few
    optional ones are missing (getattr defaults are used everywhere
    else in the codebase anyway)."""
    try:
        from config import Config

        required_soft = ["WAKE_WORD", "MAX_MEMORY_EXCHANGES"]
        missing = [name for name in required_soft if not hasattr(Config, name)]
        if missing:
            msg = (
                f"config.py is missing expected settings: {', '.join(missing)}. "
                f"Using built-in defaults where possible."
            )
            _fail(ui_update, msg)
            return [{"name": "config", "friendly_name": "your configuration", "ok": False, "detail": msg}]
        else:
            _ok("config.py loaded successfully.")
            return [{"name": "config", "friendly_name": "your configuration", "ok": True, "detail": "config.py loaded successfully."}]
    except Exception as e:
        msg = f"Could not import config.py: {e}"
        _fail(ui_update, msg)
        return [{"name": "config", "friendly_name": "your configuration", "ok": False, "detail": msg}]


def _check_ollama_reachable(ui_update) -> list:
    """Non-blocking, short-timeout ping of the Ollama server. Ollama is
    started/warmed up properly later in build_core_objects() — this is
    only an early heads-up if it looks like it will need a cold start
    (e.g. 'ollama' isn't even on PATH), not a hard requirement here."""
    try:
        from config import Config

        host = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")
    except Exception:
        host = "http://localhost:11434"

    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=1.5) as resp:
            if resp.status == 200:
                _ok("Ollama server already running.")
                return [{"name": "ollama", "friendly_name": "Ollama", "ok": True, "detail": "Ollama server already running."}]
    except Exception:
        pass

    if shutil.which("ollama") is None:
        msg = (
            "Ollama not found on PATH — the AI brain will not be able to start. "
            "Install Ollama from https://ollama.com and make sure it's on PATH."
        )
        _fail(ui_update, msg)
        return [{"name": "ollama", "friendly_name": "Ollama", "ok": False, "detail": msg}]
    else:
        logger.info(
            "[HealthCheck] Ollama server not running yet — it will be auto-started "
            "in the background during boot."
        )
        return [{
            "name": "ollama",
            "friendly_name": "Ollama",
            "ok": True,
            "detail": "Ollama isn't running yet, but it will auto-start in the background.",
        }]


def _check_model_files(ui_update) -> list:
    """Best-effort check for common local model directories (Kokoro TTS
    weights, Vosk/Whisper STT models, etc.) if their expected paths are
    exposed on Config. Silently skipped for any attribute that isn't
    defined, since not every install uses every backend."""
    results = []
    try:
        from config import Config
    except Exception:
        return results

    path_attrs = [
        "KOKORO_MODEL_PATH",
        "STT_MODEL_PATH",
        "VOSK_MODEL_PATH",
        "WHISPER_MODEL_PATH",
    ]
    for attr in path_attrs:
        path = getattr(Config, attr, None)
        if not path:
            continue
        if os.path.exists(path):
            _ok(f"{attr} found at {path}")
            results.append({
                "name": f"model_file:{attr}",
                "friendly_name": attr.replace("_", " ").title(),
                "ok": True,
                "detail": f"{attr} found.",
            })
        else:
            msg = f"{attr} points to a missing path: {path}"
            _fail(ui_update, msg)
            results.append({
                "name": f"model_file:{attr}",
                "friendly_name": attr.replace("_", " ").title(),
                "ok": False,
                "detail": msg,
            })
    return results


def run_startup_diagnostics(ui_update=None) -> list:
    """
    Runs every check below, each isolated so a single failure can't
    take down the others or block startup. Intentionally fast — this
    runs before any heavy model is loaded, so the user sees feedback
    within a second or two of launching the app.

    Returns the combined list of structured per-check results (see
    module docstring) for callers that want to build their own summary
    (currently: sara/skills/self_diagnostics.py). Startup itself still
    ignores this return value, exactly as before.
    """
    logger.info("[HealthCheck] Running startup diagnostics...")

    all_results = []
    for check in (
        _check_config,
        _check_audio_devices,
        _check_ollama_reachable,
        _check_model_files,
    ):
        try:
            result = check(ui_update)
            if result:
                all_results.extend(result)
        except Exception as e:
            logger.exception(
                f"[HealthCheck] Diagnostic check crashed (continuing): {e}"
            )

    logger.info("[HealthCheck] Startup diagnostics complete.")
    return all_results