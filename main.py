"""
Sara AI — main.py
==================
Project entry point. Renamed from the original gui_main.py.

This file stays intentionally thin: it owns the module-level imports,
optional-feature flags (RAG / tool-router), shared constants, and the
main() entry point. All the actual orchestration logic (building every
subsystem, the always-on conversation loop, intent handlers, etc.) that
used to live here directly now lives in sara/orchestrator/ as focused
modules — see sara/orchestrator/__init__.py for the full breakdown.

Full revision history prior to this restructure is preserved in
CHANGELOG.md.
"""

import os
import sys

# ----------------------------------------------------------------------------
# CUDA DLL setup (must run BEFORE anything imports onnxruntime / ctranslate2)
# ----------------------------------------------------------------------------
# The pip-installed nvidia-*-cu12 wheels (cudnn, cublas, cufft, curand,
# cuda_runtime, cuda_nvrtc, nvjitlink, ...) each ship their DLLs in
# site-packages/nvidia/<pkg>/bin. Windows will not find them on its own, so
# every such bin dir is registered here. Previously only cudnn + cublas were
# added, which left cufft64_11.dll unresolved and made onnxruntime silently
# fall back to CPU for Kokoro TTS.
_cuda_dll_dir_handles = []  # kept alive deliberately -- if GC'd, the dirs are removed
try:
    import nvidia  # namespace package

    for _nv_root in list(getattr(nvidia, "__path__", [])):
        if not os.path.isdir(_nv_root):
            continue
        for _pkg in sorted(os.listdir(_nv_root)):
            _dll_dir = os.path.join(_nv_root, _pkg, "bin")
            if os.path.isdir(_dll_dir):
                try:
                    _cuda_dll_dir_handles.append(os.add_dll_directory(_dll_dir))
                except OSError:
                    pass
                os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    pass

# Let onnxruntime-gpu itself preload the CUDA/cuDNN DLLs it needs. This is
# what made the standalone InferenceSession test succeed. Best-effort: older
# onnxruntime builds don't have preload_dlls(), and CPU-only installs are fine.
try:
    import onnxruntime as _ort

    if hasattr(_ort, "preload_dlls"):
        _ort.preload_dlls()
except Exception:  # noqa: BLE001
    pass

import re
import logging

import win32api
import win32con
import win32event
import winerror

from logging_config import setup_logging

from config import Config



try:
    from sara.core.rag import LongTermMemory

    _HAS_RAG = True
except Exception as _rag_import_err:  # noqa: BLE001
    LongTermMemory = None
    _HAS_RAG = False
    print(
        f"[Core] sara.core.rag unavailable, long-term memory disabled: {_rag_import_err}"
    )

try:
    from sara.core.tool_router import (
        resolve_tool_call,
        build_fake_match,
        TOOL_NAME_TO_INTENT,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    _HAS_TOOL_ROUTER = False
    print(
        f"[Core] sara.core.tool_router unavailable, LLM tool-calling fallback "
        f"disabled: {_tool_router_import_err}"
    )

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_EXIT_WORDS = {
    "exit",
    "quit",
    "stop",
    "goodbye",
    "bye",
    "shutdown",
    "band karo",
    "band kar",
    "alvida",
    "phir milenge",
    "bye bye",
    "बंद करो",
    "अलविदा",
}
_SLEEP_WORDS = {
    "sleep",
    "go to sleep",
    "that's all",
    "nothing else",
    "nevermind",
    "so jao",
    "so ja",
    "bas karo",
    "bas kar",
    "theek hai bas",
    "ठीक है बस",
    "सो जाओ",
}
_FORGET_WORDS = {
    "forget our conversation",
    "clear memory",
    "forget everything",
    "clear our conversation",
    "reset memory",
    "sab bhool jao",
    "memory clear karo",
    "history delete karo",
    "conversation bhool jao",
    "सब भूल जाओ",
}

_STRONG_NAME_PHRASES = (
    "my name is ",
    "call me ",
    "mera naam hai ",
    "mera naam ",
    "mujhe bulao ",
    "main hoon ",
)
_WEAK_NAME_PHRASES = ("i am ", "i'm ")

_WEAK_NAME_BLOCKLIST = {
    "sorry",
    "sure",
    "fine",
    "okay",
    "ok",
    "going",
    "not",
    "just",
    "here",
    "still",
    "really",
    "so",
    "very",
    "trying",
    "about",
    "done",
    "ready",
    "afraid",
    "glad",
    "happy",
    "sad",
    "tired",
    "busy",
    "confused",
    "lost",
    "good",
    "great",
    "alright",
    "kidding",
    "joking",
    "serious",
    "curious",
    "worried",
    "excited",
    "bored",
    "annoyed",
    "stressed",
    "hungry",
}

_MAX_EMPTY_RETRIES = 3
_EMPTY_RETRY_GRACE_S = 8.0
_IDLE_SLEEP_TIMEOUT_S = 180

_WAKE_POLL_INTERVAL_S = 0.05
_WAKE_WAIT_TIMEOUT_S = 0.3

_BARGE_IN_POLL_S = 0.05
_BARGE_IN_GRACE_S = 0.2
_TTS_IDLE_POLL_S = 0.5
_WATCH_IDLE_POLL_S = 0.5
_DB_WRITER_IDLE_POLL_S = 1.0

_NETWORK_TOOL_TIMEOUT_S = 6.0

_CALC_EXPR_RE = re.compile(r"^[\d\s\+\-\*\/\(\)\.\%]+$")
_CALC_MAX_LEN = 200
_CALC_MAX_NUMBER_DIGITS = 12
_CALC_MAX_POW_OPS = 1
_CALC_MAX_EXPONENT_VALUE = 1000
_CALC_EXPONENT_RE = re.compile(r"\*\*\s*([+-]?\d+)")

_OLLAMA_HOST = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = getattr(Config, "OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
_OLLAMA_READY_TIMEOUT_S = 60
_OLLAMA_POLL_INTERVAL_S = 0.25

_DEBUG = getattr(Config, "DEBUG_MODE", False)

# Kokoro speed range. Kokoro's `speed` parameter is DIRECTLY
# proportional to playback rate (1.0 = normal, >1.0 = faster).
_KOKORO_SPEED_MIN = 0.6
_KOKORO_SPEED_MAX = 1.4

_POST_TTS_SETTLE_WITH_AEC_S = 0.3

_THREAD_ERROR_BACKOFF_S = 0.5

# ----------------------------------------------------------------------------
# Single-instance enforcement
# ----------------------------------------------------------------------------

# "Global\" scopes it to the whole machine (not just this login session),
# so two launches under different Windows sessions still collide correctly.
_SINGLE_INSTANCE_MUTEX_NAME = r"Global\SaraAI_SingleInstance_Mutex"

# Kept alive deliberately for the process's lifetime — if this handle were
# GC'd, Windows would release the mutex early and a second launch would
# wrongly succeed. OS releases it automatically on process exit/crash.
_single_instance_mutex_handle = None


def _acquire_single_instance_lock() -> bool:
    """True if this process now owns the Sara AI mutex (no other instance
    running). False if another process already holds it — caller must
    exit without doing any further init.

    Fails open on any OS-level error (e.g. a locked-down/Terminal-Services
    session refusing Global\\ objects): a broken lock should never trap a
    legitimate single launch out of the app.
    """
    global _single_instance_mutex_handle
    try:
        _single_instance_mutex_handle = win32event.CreateMutex(
            None, False, _SINGLE_INSTANCE_MUTEX_NAME
        )
        return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
    except Exception as exc:  # noqa: BLE001
        print(f"[Core] Single-instance check unavailable ({exc}); continuing.")
        return True


def _notify_already_running() -> None:
    """User-visible heads-up for a second launch. print() alone is
    invisible once Sara AI runs without an attached console (double-click,
    shortcut, packaged .exe), so also pop a native, always-on-top message
    box — the whole point of this check is that a real person sees it."""
    print("Sara AI is already running.")
    try:
        win32api.MessageBox(
            0,
            "Sara AI is already running.\n\nCheck your taskbar / system tray for the existing window.",
            "Sara AI",
            win32con.MB_OK
            | win32con.MB_ICONINFORMATION
            | win32con.MB_TOPMOST
            | win32con.MB_SETFOREGROUND,
        )
    except Exception:
        pass  # console print above already covers headless launches


# Re-exported so sara/gui/app/bootstrap.py's `import main as sara_main;
# sara_main.build_core_objects(...)` / `sara_main.run_sara_logic(...)`
# keeps working unchanged — those functions now live in
# sara.orchestrator.core_wiring, this is just the public alias.
from sara.orchestrator import build_core_objects, run_sara_logic

# BUGFIX (root cause of the "preview mode, no backend connected" bug):
# sara/gui/app/core.py's Api.send_text_command() calls
# self.gui_main._handle_command(...) — where self.gui_main is this very
# module, imported as `import main as gui_main`. _handle_command() itself
# now lives in sara.orchestrator.intent_handlers, but nothing re-exported
# it here, so every real chat message raised AttributeError inside the
# send_text_command() background thread (silently, since that thread has
# no try/except and nothing joins it) and no reply was ever pushed back
# to the frontend.
from sara.orchestrator.intent_handlers import _handle_command


def main() -> None:
    # Must be the very first thing — before logging setup, before any
    # heavy init. Second launch exits here, no partial init happens.
    if not _acquire_single_instance_lock():
        _notify_already_running()
        sys.exit(0)

    Config.validate()

    setup_logging()
    logger.info("Sara AI starting up (main.main).")

    from sara.gui.app import main as webview_main

    webview_main()


if __name__ == "__main__":
    main()