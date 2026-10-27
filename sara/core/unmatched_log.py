"""
sara.core.unmatched_log
Lightweight, append-only log of user inputs that fell all the way
through detect_intent(), the multi-step planner, AND the single-tool
LLM resolver without being handled by any real tool -- i.e. genuine
fast-path misses that ended up answered by plain chat.

This is the mechanism that turns real usage into future
_INTENT_PATTERNS / _INTENT_GATES entries: periodically read
Config.UNMATCHED_LOG_PATH and look for the same phrase showing up more
than once (e.g. several people typing "whether" instead of "weather")
to find the next small pattern fix, the same way the weather/whether
bug was found from a real transcript rather than guessed at.

Deliberately dumb and defensive -- logging must NEVER be able to affect
the user's actual response. Every failure mode here is swallowed.
"""
import json
import logging
import threading
import time
from pathlib import Path

from config import Config

logger = logging.getLogger("sara.core_logic")

_LOCK = threading.Lock()

# Guards against ever logging something absurdly long (e.g. a pasted
# document read aloud) -- this log is for short command-shaped misses,
# not a transcript dump.
_MAX_TEXT_LEN = 500


def log_unmatched(user_input: str) -> None:
    """
    Append one JSON line ({"ts": <epoch seconds>, "text": <input>}) to
    Config.UNMATCHED_LOG_PATH.

    Best-effort only: catches and swallows every exception (missing
    directory, disk full, permissions, ...) after a single debug-level
    log line, exactly like the RAG/tool_router optional-feature guards
    elsewhere in this codebase -- one broken write here must never
    interrupt or slow down the actual voice loop.
    """
    if not user_input:
        return
    text = user_input.strip()
    if not text:
        return
    if len(text) > _MAX_TEXT_LEN:
        text = text[:_MAX_TEXT_LEN]

    record = {"ts": time.time(), "text": text}
    try:
        path = Path(Config.UNMATCHED_LOG_PATH)
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 -- logging must never break the app
        logger.debug(f"[UnmatchedLog] could not write to log: {e}")