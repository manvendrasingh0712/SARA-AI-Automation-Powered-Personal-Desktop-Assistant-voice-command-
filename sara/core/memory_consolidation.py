"""
sara.core.memory_consolidation
Periodic background summarizer that reads the most recent raw
conversation_log entries and asks the LOCAL Ollama LLM (via the existing
brain.generate_response() call, sara/core/llm/engine.py -- never a new
cloud client) to extract a handful of short, durable "facts worth
remembering long-term", storing them into the existing RAG long-term
memory store (sara/core/rag.py's LongTermMemory.add_memory()).

DESIGN
------
Follows the exact same background-daemon-thread pattern as
sara/orchestrator/proactive.py:
  - config-gated at the top (Config.MEMORY_CONSOLIDATION_ENABLED)
  - runs on its own daemon thread
  - sleeps in a loop checking a stop-event (never a busy-loop)
  - every tick is wrapped in its own try/except so one bad pass can
    never kill the thread
  - if Ollama is unreachable, this SKIPS the tick silently (a logged
    warning, never a crash) and tries again next interval -- per this
    feature's spec ("must never run if Ollama is unreachable").

100% LOCAL / FREE: the only network call this module ever makes is to
Config.OLLAMA_HOST (the same local Ollama server the rest of Sara
already depends on for chat) -- no paid API, no new cloud service.

WIRING (NOT done automatically by this file)
---------------------------------------------
This module does not know about your application's startup sequence,
so it must be wired in manually, ONE time, at startup -- in the same
file where sara/orchestrator/proactive.py's background thread is
already started (that file was not provided, so this file cannot edit
it for you):

    from sara.core.memory_consolidation import start_memory_consolidation
    start_memory_consolidation(db, brain, rag_memory)

  - db:         the PreferencesDB instance (sara/core/memory.py)
  - brain:      the SaraLLM instance (sara/core/llm/engine.py) --
                must expose .generate_response(prompt: str) -> str
  - rag_memory: the LongTermMemory instance (sara/core/rag.py) --
                same instance the rest of the app already uses (e.g.
                exposed as brain.rag_memory elsewhere in this codebase)

Call this once. It returns the background Thread object (already
started, daemon=True) or None if consolidation is disabled/unavailable.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.request
from typing import List, Optional

from config import Config

logger = logging.getLogger(__name__)

_STOP_POLL_S = 1.0

_EXTRACTION_PROMPT_TEMPLATE = (
    "You are extracting durable, long-term facts worth remembering about "
    "the user from a short conversation snippet. Only extract facts that "
    "would still matter weeks from now (preferences, names, ongoing "
    "projects, important dates) -- NOT small talk, greetings, or "
    "one-off questions.\n\n"
    "Conversation snippet:\n{conversation}\n\n"
    "Reply with 1 to {max_facts} short factual sentences, one per line, "
    "each under 20 words. If there is nothing durable worth remembering, "
    "reply with exactly: NONE"
)

_BULLET_PREFIX_RE = re.compile(r"^[\-\*\d\.\)\s]+")


def _is_ollama_reachable() -> bool:
    """
    Cheap reachability probe against Config.OLLAMA_HOST -- reuses the
    same host Sara's chat LLM already depends on, no new dependency.
    Failing this must never crash, only cause this tick to be skipped
    (see module docstring: "must never run if Ollama is unreachable").
    """
    try:
        req = urllib.request.Request(f"{Config.OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2.0):
            return True
    except Exception:
        return False


def _extract_facts(brain, conversation_text: str, max_facts: int) -> List[str]:
    """
    ONE bounded call to the local LLM via brain.generate_response() (the
    existing calling pattern in sara/core/llm/engine.py -- never a raw
    Ollama HTTP call built here, and never any cloud API). Returns a
    plain list of short fact strings (possibly empty). Never raises --
    any failure here degrades to "no facts extracted this tick".
    """
    prompt = _EXTRACTION_PROMPT_TEMPLATE.format(
        conversation=conversation_text, max_facts=max_facts
    )
    try:
        raw = brain.generate_response(prompt)
    except Exception as e:
        logger.warning(f"[MemoryConsolidation] LLM extraction call failed: {e}")
        return []
    if not raw or not raw.strip():
        return []
    if raw.strip().upper().startswith("NONE"):
        return []

    facts: List[str] = []
    for line in raw.strip().splitlines():
        cleaned = _BULLET_PREFIX_RE.sub("", line).strip()
        if cleaned and cleaned.upper() != "NONE":
            facts.append(cleaned)
    return facts[:max_facts]


def _consolidation_tick(db, brain, rag_memory) -> None:
    """One consolidation pass: read recent raw conversation, extract
    facts via the local LLM, store any into the RAG long-term store."""
    batch_size = getattr(Config, "MEMORY_CONSOLIDATION_BATCH_SIZE", 20)
    max_facts = getattr(Config, "MEMORY_CONSOLIDATION_MAX_FACTS", 3)

    rows = db.get_recent_messages(limit=batch_size)
    if not rows or len(rows) < 2:
        return  # not enough raw conversation yet to be worth summarizing

    conversation_text = "\n".join(
        f"{row.get('role', '?')}: {row.get('message', '')}" for row in rows
    )
    if not conversation_text.strip():
        return

    facts = _extract_facts(brain, conversation_text, max_facts)
    for fact in facts:
        try:
            rag_memory.add_memory(fact, source="consolidation")
        except Exception as e:
            logger.warning(f"[MemoryConsolidation] add_memory failed: {e}")


def _consolidation_loop(db, brain, rag_memory, stop_event: threading.Event) -> None:
    interval_s = getattr(Config, "MEMORY_CONSOLIDATION_INTERVAL_S", 1800)
    while not stop_event.is_set():
        try:
            if not getattr(Config, "MEMORY_CONSOLIDATION_ENABLED", True):
                # Config could theoretically be toggled at runtime by a
                # future settings page -- re-check every wake rather than
                # only once at thread start.
                stop_event.wait(_STOP_POLL_S)
                continue
            if rag_memory is None or not getattr(rag_memory, "enabled", False):
                stop_event.wait(_STOP_POLL_S)
                continue
            if not _is_ollama_reachable():
                logger.debug(
                    "[MemoryConsolidation] Ollama unreachable this cycle -- skipping "
                    "(per spec: never runs while Ollama is down)."
                )
                stop_event.wait(interval_s)
                continue
            _consolidation_tick(db, brain, rag_memory)
        except Exception as e:  # noqa: BLE001 -- one bad tick must never kill the thread
            logger.error(f"[MemoryConsolidation] tick failed: {e}")
            print(f"[MemoryConsolidation] tick failed (non-fatal): {e}")
        stop_event.wait(interval_s)


def start_memory_consolidation(
    db, brain, rag_memory, stop_event: Optional[threading.Event] = None
) -> Optional[threading.Thread]:
    """
    Starts the background memory-consolidation daemon thread. Returns the
    started Thread object, or None if consolidation is disabled via
    Config.MEMORY_CONSOLIDATION_ENABLED or no rag_memory instance was
    provided. Call this exactly ONCE at startup -- see module docstring
    for the exact call site and required arguments.
    """
    if not getattr(Config, "MEMORY_CONSOLIDATION_ENABLED", True):
        print("[MemoryConsolidation] Disabled via Config.MEMORY_CONSOLIDATION_ENABLED.")
        return None
    if rag_memory is None:
        print("[MemoryConsolidation] No rag_memory instance provided -- disabled.")
        return None

    event = stop_event or threading.Event()
    thread = threading.Thread(
        target=_consolidation_loop,
        args=(db, brain, rag_memory, event),
        name="MemoryConsolidation",
        daemon=True,
    )
    thread.start()
    print(
        f"[MemoryConsolidation] Started -- every "
        f"{getattr(Config, 'MEMORY_CONSOLIDATION_INTERVAL_S', 1800)}s, "
        f"batch={getattr(Config, 'MEMORY_CONSOLIDATION_BATCH_SIZE', 20)}, "
        f"max_facts={getattr(Config, 'MEMORY_CONSOLIDATION_MAX_FACTS', 3)}"
    )
    return thread