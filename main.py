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

# cuDNN/cuBLAS DLL loading (Windows) — onnxruntime-gpu's CUDA execution
# provider can't find cudnn_graph64_9.dll etc. at runtime otherwise, since
# nvidia-cudnn-cu12/nvidia-cublas-cu12 install their DLLs under the venv's
# site-packages rather than anywhere on the default search path. This MUST
# run before onnxruntime, kokoro-onnx, or faster-whisper get imported
# anywhere in the import chain below — hence right at the top of this file,
# before any sara.* import. Both os.add_dll_directory AND a manual PATH
# prepend are needed: onnxruntime's CUDA provider only respects PATH (not
# add_dll_directory) for some of its internal LoadLibrary calls.
_cuda_dll_dir_handles = []  # kept alive deliberately -- see comment below
try:
    import nvidia.cudnn
    _cudnn_bin = os.path.join(nvidia.cudnn.__path__[0], "bin")
    import nvidia.cublas
    _cublas_bin = os.path.join(nvidia.cublas.__path__[0], "bin")
    for _dll_dir in (_cudnn_bin, _cublas_bin):
        if os.path.isdir(_dll_dir):
            # os.add_dll_directory()'s return value MUST be kept alive
            # for the directory to remain part of the DLL search path --
            # if it's garbage-collected (which happens almost immediately
            # if the return value isn't stored anywhere), the directory
            # is silently removed again. Appending to this module-level
            # list keeps a permanent reference for the process's lifetime.
            _cuda_dll_dir_handles.append(os.add_dll_directory(_dll_dir))
            os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    pass

import re
import logging

from logging_config import setup_logging

from config import Config


# PRODUCTION-AUDIT ADDITION (Phase 2): long-term memory (RAG) and the
# LLM tool-calling fallback are both optional, additive features — if
# either module fails to import for any reason (e.g. numpy missing),
# the whole app must still start exactly as before, just without that
# one feature. Both are re-checked as None/False below wherever used.
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
_OLLAMA_MODEL = getattr(Config, "OLLAMA_MODEL", "qwen2.5")
_OLLAMA_READY_TIMEOUT_S = 60
_OLLAMA_POLL_INTERVAL_S = 0.25

_DEBUG = getattr(Config, "DEBUG_MODE", False)

# Kokoro speed range. Kokoro's `speed` parameter is DIRECTLY
# proportional to playback rate (1.0 = normal, >1.0 = faster).
_KOKORO_SPEED_MIN = 0.6
_KOKORO_SPEED_MAX = 1.4

_POST_TTS_SETTLE_WITH_AEC_S = 0.3

_THREAD_ERROR_BACKOFF_S = 0.5



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
    setup_logging()
    logger.info("Sara AI starting up (main.main).")

    from sara.gui.app import main as webview_main

    webview_main()


if __name__ == "__main__":
    main()