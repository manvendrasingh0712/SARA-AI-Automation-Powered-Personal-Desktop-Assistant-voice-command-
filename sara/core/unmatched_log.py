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
import re
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


# Words that make a whole utterance small talk, not a command we failed to learn.
_CHITCHAT_WORDS = frozenset({
    "thank", "thanks", "thankyou", "you", "very", "much", "so", "yes", "yeah", "yep", "no", "nope",
    "ok", "okay", "okey", "sure", "cool", "nice", "great", "good", "fine", "alright", "right",
    "hello", "hi", "hey", "bye", "goodbye", "please", "sorry", "wow", "hmm", "oh", "ah",
    "haan", "han", "nahi", "theek", "thik", "accha", "achha", "shukriya", "dhanyavad", "ji",
    "sara", "sira", "sarah", "the", "a", "and", "i", "me", "my", "it", "is", "that", "this",
})
_QUESTION_START = re.compile(
    r"^(who|what|whats|what's|why|how|when|where|which|whom|is|are|was|were|do|does|did|"
    r"can you hear|can you see|could you hear|kya|kaun|kyun|kyu|kaise|kahan|kab|kitna|kitne)\b",
    re.IGNORECASE,
)
_ACTION_HINT = re.compile(
    r"\b(open|close|launch|start|stop|play|pause|set|turn|switch|mute|unmute|increase|decrease|"
    r"volume|brightness|remind|timer|alarm|send|call|search|find|show|create|delete|remove|add|"
    r"kholo|band|chalao|lagao|badhao|ghatao)\b",
    re.IGNORECASE,
)


def is_not_a_command(text: str) -> bool:
    """
    True for small talk ("thank you very much", "yes") and plain knowledge
    questions ("who is the prime minister of india?"). Those are correctly
    answered by chat; listing them as "missed commands" only buries the
    phrases that really need a new pattern.
    """
    t = (text or "").strip().lower()
    if not t:
        return True
    words = re.findall(r"[a-zऀ-ॿ']+", t)
    if not words:
        return True
    if all(w in _CHITCHAT_WORDS for w in words):
        return True
    # Whisper silence hallucinations: "thank you for watching", "nd nd nd nd"
    if "watching" in words or (len(words) >= 3 and len(set(words)) == 1):
        return True
    if _QUESTION_START.match(t) and not _ACTION_HINT.search(t):
        return True
    return False


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
    if is_not_a_command(text):
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