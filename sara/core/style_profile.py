"""
sara.core.style_profile
A tiny, persistent profile of how this user likes to be answered (audit part 2, O6), learned from behaviour:

  * asks for detail ("detail me", "samjhao", "elaborate") .......... longer answers
  * asks for brevity ("short me", "chhota batao", "briefly") ....... shorter answers
  * interrupts SARA's reply (barge-in) ............................. shorter answers
  * writes in Roman-script Hindi / Hinglish ........................ answer in Hinglish

It produces ONE line for the cached system prompt. The line changes only when a threshold is crossed, so the Ollama prefix
cache is not disturbed turn after turn. Counters live in the preferences table (keys `style_*`).
"""
from __future__ import annotations

import re
from typing import Callable, Optional

_DETAIL = re.compile(r"\b(?:detail\s*(?:me|mein|se)|in\s+detail|elaborate|samjhao|samjhaiye|explain\s+more|thoda\s+lamba|vistar)\b", re.I)
_BRIEF = re.compile(r"\b(?:short\s*(?:me|mein)|briefly|in\s+short|chhota\s+(?:batao|jawab)|chota\s+(?:batao|jawab)|sankshep|jaldi\s+batao|one\s+line)\b", re.I)
_ROMAN_HINDI = re.compile(
    r"\b(?:kya|hai|hain|kaise|kaun|kab|kahan|kitna|mujhe|mera|meri|batao|karo|kar\s+do|nahi|aur|bhi|toh|yaar|accha|theek)\b", re.I
)
KEYS = ("style_short", "style_long", "style_hinglish", "style_turns")
_MIN_GAP = 3          # votes of difference before the length preference is stated
_MIN_TURNS = 10       # turns before the language preference is stated


class StyleProfile:
    def __init__(self, get_pref: Callable[[str], Optional[str]], set_pref: Callable[[str, str], object]):
        self._get, self._set = get_pref, set_pref

    def _n(self, key: str) -> int:
        try:
            return int(float(self._get(key) or 0))
        except (TypeError, ValueError):
            return 0

    def _bump(self, key: str, by: int = 1) -> None:
        self._set(key, str(self._n(key) + by))

    def observe(self, text: str, interrupted_last_reply: bool = False) -> None:
        """Call once per user turn (not for commands that were handled by a skill/tool)."""
        t = text or ""
        self._bump("style_turns")
        if _DETAIL.search(t):
            self._bump("style_long")
        if _BRIEF.search(t) or interrupted_last_reply:
            self._bump("style_short")
        if len(_ROMAN_HINDI.findall(t)) >= 2 and not re.search(r"[ऀ-ॿ]", t):
            self._bump("style_hinglish")

    def line(self) -> str:
        short, long_, hing, turns = (self._n(k) for k in KEYS)
        parts = []
        if short - long_ >= _MIN_GAP:
            parts.append("This user prefers very short answers: one sentence unless they ask for more.")
        elif long_ - short >= _MIN_GAP:
            parts.append("This user likes fuller explanations: use the upper end of the length rules.")
        if turns >= _MIN_TURNS and hing / max(1, turns) >= 0.6:
            parts.append("This user mostly writes Roman-script Hinglish; answer in Hinglish unless they switch to English.")
        return " ".join(parts)
