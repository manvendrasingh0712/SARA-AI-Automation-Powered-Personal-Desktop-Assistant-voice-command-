"""
sara.orchestrator
Everything the old 1729-line gui_main.py used to do in one file, now split
by concern. The project-root main.py imports from here and stays a thin
entry point (setup_logging -> build_core_objects -> launch GUI).

    lazy.py            - _Lazy background construction wrapper
    state.py           - LanguageState, AssistantState (GUI-driven toggles)
    ollama_manager.py   - start/stop/health-check the local Ollama server
    ui_bridge.py          - ui_update(kind, *args) wrapper + event coalescing
    tts_worker.py           - TTSWorker (speak/barge-in coordination)
    db_writer.py              - AsyncDBWriter (fire-and-forget conversation log)
    calc_utils.py               - safe calculator eval + duration parsing
    network_utils.py              - bounded-timeout wrapper for network tools
    text_utils.py                   - name-extraction / phrase-matching helpers
    history.py                        - restore conversation history + preferences
    intent_handlers.py                  - one handler per fast-path regex intent
    core_wiring.py                        - build_core_objects() + run_sara_logic()
    proactive.py                             - ProactiveEngine (agentic background nudges)
"""
from .core_wiring import build_core_objects, run_sara_logic
from .state import LanguageState, AssistantState
from .tts_worker import TTSWorker
from .db_writer import AsyncDBWriter
from .proactive import ProactiveEngine, ActivityTracker

__all__ = [
    "build_core_objects", "run_sara_logic",
    "LanguageState", "AssistantState", "TTSWorker", "AsyncDBWriter",
    "ProactiveEngine", "ActivityTracker",
]