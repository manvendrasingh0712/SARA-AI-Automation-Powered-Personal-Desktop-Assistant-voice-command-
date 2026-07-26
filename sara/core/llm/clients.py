"""
sara.core.llm.clients
Lazy client construction for the Ollama and Gemini backends.
"""
from __future__ import annotations



import re
import threading
import time
from collections import deque
from typing import Iterator, List, NamedTuple, Optional, Tuple

from config import Config

# ══════════════════════════════════════════════════════════════════════
# Module-level compiled regexes
# ══════════════════════════════════════════════════════════════════════

_SENT_END_RE = re.compile(r"([.!?।॥])\s+")
_MD_STRIP_RE = re.compile(r"(\*{1,3}|#{1,6}|`{1,3}|_{1,2}|~~|\|\|)")
_CLAUSE_RE = re.compile(r",\s+(?:and|but|so|yet|or|nor)\s+", re.IGNORECASE)
_SEMI_RE = re.compile(r";\s+")

_ABBREV_SET: frozenset[str] = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "vs",
        "rev",
        "gen",
        "sgt",
        "cpl",
        "pvt",
        "lt",
        "col",
        "maj",
        "capt",
        "cmdr",
        "etc",
        "approx",
        "dept",
        "est",
        "govt",
        "inc",
        "ltd",
        "corp",
        "fig",
        "vol",
        "pp",
        "no",
        "st",
        "ave",
        "blvd",
        "rd",
        "rs",
        "usd",
        "eur",
        "gbp",
        "kg",
        "km",
        "cm",
        "mm",
        "mg",
        "lb",
        "oz",
        "ft",
        "yd",
        "mph",
        "kmh",
        "kph",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    }
)


# ══════════════════════════════════════════════════════════════════════
# Localized fallback messages (v7) — used instead of raw exception text
# anywhere a reply could reach speak_stream()/speak() and be read aloud.
# ══════════════════════════════════════════════════════════════════════

_STREAM_FAIL_MESSAGES = {
    "english": "Sorry, I'm having trouble reaching my brain right now — could you try that again in a moment?",
    "hindi": "Maafi chahta hoon, abhi thodi dikkat aa rahi hai — thodi der baad phir try karo.",
    "hinglish": "Sorry yaar, abhi thoda glitch ho raha hai — thodi der baad dobara try karna.",
}

_STREAM_INTERRUPTED_MESSAGES = {
    "english": "Hmm, my connection glitched mid-thought — that's all I've got for now.",
    "hindi": "Hmm, beech mein connection mein dikkat aa gayi — abhi itna hi keh sakta hoon.",
    "hinglish": "Hmm, beech mein thoda glitch ho gaya — abhi bas itna hi.",
}


# ══════════════════════════════════════════════════════════════════════
# Language-aware system prompt templates
# ══════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════
# Lazy backend client accessors
# ══════════════════════════════════════════════════════════════════════

_ollama_client = None
_gemini_client = None
_client_lock = threading.Lock()


def _get_ollama_client(cfg):
    global _ollama_client
    if _ollama_client is not None:
        return _ollama_client
    with _client_lock:
        if _ollama_client is not None:
            return _ollama_client
        try:
            import ollama as _lib

            _ollama_client = _lib.Client(
                host=getattr(cfg, "OLLAMA_HOST", "http://localhost:11434"),
                # v7: fallback default now matches Config.OLLAMA_TIMEOUT's
                # own default (30s) instead of a stale, inconsistent 10.0.
                timeout=getattr(cfg, "OLLAMA_TIMEOUT", 30.0),
            )
        except ImportError:
            print("[LLM Error] 'ollama' missing. Run: pip install ollama")
        except Exception as e:
            print(f"[LLM Error] Ollama client init failed: {e}")
    return _ollama_client


def _get_gemini_client(cfg):
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    with _client_lock:
        if _gemini_client is not None:
            return _gemini_client
        try:
            from google import genai as _genai

            _gemini_client = _genai.Client(api_key=cfg.GEMINI_API_KEY)
        except Exception as e:
            print(f"[LLM Error] Gemini init failed: {e}")
    return _gemini_client
