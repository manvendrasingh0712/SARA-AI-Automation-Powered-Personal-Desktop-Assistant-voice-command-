"""Deterministic, data-independent prompt features for the learned router."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

import numpy as np

from .policy import is_simple_query

FEATURE_SCHEMA_VERSION = 1
FEATURE_NAMES: tuple[str, ...] = (
    "bias",
    "n_tokens_log",
    "n_sentences",
    "has_code_or_symbols",
    "has_math_or_numbers",
    "is_question",
    "lang_indic",
    "needs_tools",
    "convo_depth",
    "memory_injected",
    "followup_pronoun",
    "complexity",
)
FEATURE_DIM: int = len(FEATURE_NAMES)

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_SENTENCE_END = re.compile(r"[.!?\u0964]+(?:\s|$)")
_CODE = re.compile(
    r"[{}\[\]<>=;`\\|^~]|https?://|\b(?:def|import|class|function|return|print)\b|\w\(\)"
)
_MATH = re.compile(
    r"\d|\b(?:plus|minus|times|divided|percent|sqrt|calculate|equals|sum|average)\b",
    re.IGNORECASE,
)
_QUESTION_START = frozenset(
    "what why how when where who whom whose which is are do does did can could will would "
    "should shall kya kyun kyu kaise kab kahan kaun kitna kitni kisne kaisa kaisi".split()
)
_QUESTION_DEVA = re.compile("क्या|क्यों|कैसे|कब|कहाँ|कहां|कौन|कितना|कितनी")
_FOLLOWUP = re.compile(
    r"\b(?:it|that|this|they|them|ye|yeh|wo|woh|usse|uska|uski|isse|iska|iski|unka)\b",
    re.IGNORECASE,
)
_FOLLOWUP_DEVA = re.compile("यह|वह|उसे|उसका|उसकी|इसे|इसका|इसकी")
_THINK = re.compile(
    r"\b(?:think hard|deeply|deep|in detail|step by step|samjhao|samjha do)\b", re.IGNORECASE
)
_THINK_DEVA = re.compile("समझाओ|विस्तार से|गहराई")
_CLAUSE_MARKERS = (
    "and then", "compare", "explain why", "step by step", "why does", "difference between",
    "pros and cons", "kyun", "kyu ", "samjhao", "tulna", "क्यों", "समझाओ", "तुलना", "विस्तार",
)
_HINGLISH_FALLBACK = frozenset(
    "kya hai nahi nahin kaise kyun kyu yaar bhai karo kar mujhe tum aap haan mera meri batao "
    "bata samjhao accha achha abhi aaj kal ka ki ke ko se mein hain ho tha thi".split()
)

_detect_fn: Optional[Callable[[str], str]] = None
_tool_fn: Optional[Callable[[str], bool]] = None
_resolved = False


def warm() -> None:
    """Resolve the optional helpers once (guarded imports)."""
    global _detect_fn, _tool_fn, _resolved
    if _resolved:
        return
    try:
        from sara.skills._framework import detect_lang

        _detect_fn = detect_lang
    except Exception:
        _detect_fn = None
    try:
        from sara.core.tool_router import has_probable_tool_intent

        _tool_fn = has_probable_tool_intent
    except Exception:
        _tool_fn = None
    _resolved = True


@dataclass(frozen=True)
class Features:
    """Feature vector plus the flags the policy needs."""

    vector: "np.ndarray"
    needs_tools: bool
    lang: str
    complexity: float
    think_cue: bool
    simple: bool
    followup: bool


_NEUTRAL = Features(
    np.array([1.0] + [0.0] * (FEATURE_DIM - 1)), False, "en", 0.0, False, False, False
)


def _lang_of(text: str) -> str:
    if _DEVANAGARI.search(text):
        return "hi"
    if not _resolved:
        warm()
    if _detect_fn is not None:
        try:
            return "hinglish" if _detect_fn(text) == "hinglish" else "en"
        except Exception:
            pass
    tokens = re.findall(r"[a-z']+", text.lower())
    return "hinglish" if any(t in _HINGLISH_FALLBACK for t in tokens) else "en"


def _needs_tools(text: str) -> bool:
    if not _resolved:
        warm()
    if _tool_fn is None:
        return False
    try:
        return bool(_tool_fn(text))
    except Exception:
        return False


def _complexity(lowered: str, words: list) -> float:
    n = len(words)
    length = min(1.0, n / 40.0)
    rare = (sum(1 for w in words if len(w) >= 10) / n) if n else 0.0
    markers = sum(1 for m in _CLAUSE_MARKERS if m in lowered)
    return min(1.0, 0.4 * length + 0.2 * min(1.0, rare * 4.0) + 0.4 * min(1.0, markers / 2.0))


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract(text: str, ctx: Mapping[str, Any]) -> Features:
    stripped = text.strip()
    words = stripped.split()
    n = len(words)
    lowered = stripped.lower()
    lang = _lang_of(stripped)
    tools = _needs_tools(stripped)
    sentences = max(1, len(_SENTENCE_END.findall(stripped))) if n else 0
    first = words[0].lower().strip(",.") if words else ""
    question = bool(
        "?" in stripped or first in _QUESTION_START or _QUESTION_DEVA.search(stripped)
    )
    followup = bool(_FOLLOWUP.search(stripped) or _FOLLOWUP_DEVA.search(stripped))
    complexity = _complexity(lowered, words)
    lang_val = {"en": 0.0, "hinglish": 0.5, "hi": 1.0}[lang]
    vector = np.array(
        [
            1.0,
            min(1.0, np.log1p(n) / np.log1p(60.0)),
            min(1.0, max(0, sentences - 1) / 3.0),
            1.0 if _CODE.search(stripped) else 0.0,
            1.0 if _MATH.search(stripped) else 0.0,
            1.0 if question else 0.0,
            lang_val,
            1.0 if tools else 0.0,
            min(1.0, max(0.0, _num(ctx.get("depth", 0))) / 10.0),
            1.0 if ctx.get("memory_injected") else 0.0,
            1.0 if followup else 0.0,
            complexity,
        ],
        dtype=float,
    )
    return Features(
        vector=vector,
        needs_tools=tools,
        lang=lang,
        complexity=complexity,
        think_cue=bool(_THINK.search(stripped) or _THINK_DEVA.search(stripped)),
        simple=is_simple_query(text),
        followup=followup,
    )


def extract_features(text: str, ctx: Optional[Mapping[str, Any]] = None) -> Features:
    """Features for one prompt; deterministic, never raises."""
    try:
        return _extract(text if isinstance(text, str) else str(text or ""), ctx or {})
    except Exception:
        return _NEUTRAL