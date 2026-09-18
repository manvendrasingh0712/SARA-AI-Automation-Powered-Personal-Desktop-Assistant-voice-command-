"""
Sara AI — main.py
==================
Project entry point. Renamed from the original gui_main.py.

This file stays intentionally thin: it owns the module-level imports
and the main() entry point. All the actual orchestration logic
(building every subsystem, the always-on conversation loop, intent
handlers, etc.) that used to live here directly now lives in
sara/orchestrator/ as focused modules — see
sara/orchestrator/__init__.py for the full breakdown.

Full revision history prior to this restructure is preserved in
CHANGELOG.md.

NOTE (cleanup pass): this file previously carried a full copy of the
sara/orchestrator constants block and the sara.core.rag /
sara.core.tool_router availability probes, left over from the
monolith split. Neither was referenced anywhere in this module, so
both were removed. If some other module does `from main import
_HAS_RAG` (or similar) instead of importing directly from
sara.core.rag / sara.core.tool_router, that import will now break —
grep the repo for `_HAS_RAG`, `_HAS_TOOL_ROUTER`, `LongTermMemory`,
`resolve_tool_call`, `build_fake_match`, `TOOL_NAME_TO_INTENT`
imported *from main* specifically before shipping this.
"""

import os

_cuda_dll_dir_handles = []  # kept alive deliberately -- see comment below
try:
    import nvidia.cudnn
    _cudnn_bin = os.path.join(nvidia.cudnn.__path__[0], "bin")
    import nvidia.cublas
    _cublas_bin = os.path.join(nvidia.cublas.__path__[0], "bin")
    for _dll_dir in (_cudnn_bin, _cublas_bin):
        if os.path.isdir(_dll_dir):
            _cuda_dll_dir_handles.append(os.add_dll_directory(_dll_dir))
            os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    pass

import logging

from logging_config import setup_logging

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

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
