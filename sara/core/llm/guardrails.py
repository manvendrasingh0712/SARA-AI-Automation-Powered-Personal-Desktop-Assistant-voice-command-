"""
sara.core.llm.guardrails
A backstop around the chat model's streamed reply (audit part 2, O10). The system prompt already forbids claiming an
action and the small model still sometimes does ("opn chrmoe" -> "Chrome opened!" with nothing opened). Chat turns run
no tool, so a first-person claim that an action was done is false by construction.

  * false action claim, when the user's message looked like a command  -> one honest sentence instead, generation stops
  * a sentence repeated three times, or a stuck loop                    -> generation stops after the second copy
Pure Python, no I/O; applied per sentence so the first words still reach the speaker as early as before.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

from sara.core.diag import swallowed

_CLAIM_EN = re.compile(
    r"^\W*(?:[\w' ]{1,28}\s)?(?:opened|closed|launched|started|muted|unmuted|locked|deleted|saved|sent|created)\W*$|"
    r"^\W*(?:ok(?:ay)?[,!. ]*|sure[,!. ]*|done[,!. ]*)?(?:i(?:'ve| have|'m| am)?\s+)?"
    r"(?:opened|opening|closed|closing|launched|started|turned\s+(?:on|off)|switched\s+(?:on|off)|muted|unmuted|"
    r"set\s+(?:the\s+|a\s+|your\s+)?(?:timer|alarm|reminder|volume|brightness)|increased|decreased|played|playing|sent|saved|"
    r"deleted|created|added|locked|shut\s+down|restarted)\b|^\W*(?:done|sorted)\W*$",
    re.IGNORECASE,
)
_CLAIM_HI = re.compile(
    r"\b(?:kar\s+diya|kar\s+di|khol\s+diya|khol\s+di|laga\s+diya|laga\s+di|chala\s+diya|chala\s+di|band\s+kar\s+diya|"
    r"badha\s+diya|kam\s+kar\s+diya|bhej\s+diya|save\s+kar\s+diya|set\s+kar\s+diya|ho\s+gaya|ho\s+gayi)\b|"
    r"(?:खोल\s+दिया|कर\s+दिया|लगा\s+दिया|चला\s+दिया|बंद\s+कर\s+दिया|हो\s+गया)",
    re.IGNORECASE,
)
_COMMANDISH = re.compile(
    r"\b(?:open|close|set|turn|switch|start|stop|play|pause|mute|unmute|send|delete|remove|create|add|launch|lock|restart|"
    r"increase|decrease|kholo|khol|band|laga|lagao|chalao|chala|karo|kar\s+do|bhejo|hatao|badhao|ghatao|opn|opne|clos)\b",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"(?<=[.!?\u0964])\s+")

FALLBACK = {
    "en": "I didn't catch that as a command, so I haven't done anything. Could you say it again?",
    "hi": "Maine ise command ki tarah nahi pakda, isliye kuch kiya nahi. Dobara bolo?",
}


def claims_action(sentence: str) -> bool:
    s = (sentence or "").strip()
    return bool(s) and bool(_CLAIM_EN.search(s) or _CLAIM_HI.search(s))


_QUESTION_OPENER = re.compile(
    r"^\W*(?:what|whats|who|whom|whose|why|how|when|where|which|is|are|was|were|do|does|did|can|could|would|should|tell|explain|"
    r"describe|define|kya|kaun|kyun|kyu|kaise|kab|kahan|kitna|kitni|kitne|batao|bataiye)\b",
    re.IGNORECASE,
)


def looks_like_command(user_text: str) -> bool:
    t = user_text or ""
    if _COMMANDISH.search(t) and not _QUESTION_OPENER.match(t):
        return True
    # a short sentence that is not a question, e.g. the garbled "opn chrmoe"
    return len(t.split()) <= 5 and not _QUESTION_OPENER.match(t) and not t.strip().endswith("?")


def _close(closer) -> None:
    if callable(closer):
        try:
            closer()
        except Exception as exc:  # noqa: BLE001
            swallowed(None, "guardrails.close", exc)


def _is_hinglish(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097f]", text or "")) or bool(
        re.search(r"\b(?:karo|kholo|chalao|laga|band|bhejo|kar\s+do|batao)\b", text or "", re.IGNORECASE)
    )


def _devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return (sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters)) if letters else 0.0


def guard_stream(stream: Iterable[str], user_text: str, *, expect_latin: bool = False, regenerate=None) -> Iterator[str]:
    """Yield the model's chunks, replacing a false action claim and cutting a repetition loop.

    expect_latin + regenerate: the user wrote Roman script and the language follows the user, but the reply starts in
    Devanagari (the small model sometimes drifts). The wrong reply is dropped unspoken and `regenerate()` (a new chunk
    iterator asking for Roman script) is used instead, once."""
    command = looks_like_command(user_text)
    seen: dict = {}
    closer = getattr(stream, "close", None)
    first = True
    for chunk in stream:
        if first and expect_latin and regenerate is not None and _devanagari_share(chunk) > 0.6:
            _close(closer)
            yield from guard_stream(regenerate(), user_text)
            return
        first = False
        sentences = [chunk] if not chunk or len(chunk) < 3 else _SPLIT.split(chunk)
        for sentence in sentences:
            key = re.sub(r"\W+", " ", sentence.lower()).strip()
            if command and claims_action(sentence):
                yield FALLBACK["hi" if _is_hinglish(user_text) else "en"]
                _close(closer)
                return
            if key:
                seen[key] = seen.get(key, 0) + 1
                if seen[key] >= 3:
                    _close(closer)
                    return
        yield chunk
