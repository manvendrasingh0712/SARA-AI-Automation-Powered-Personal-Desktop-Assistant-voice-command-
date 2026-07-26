"""
sara.core.llm.prompt
System-prompt construction (persona, time-of-day, language) for the LLM.
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


def _build_base_prompt(name: str, tod: str, lang: str, user_name: Optional[str]) -> str:
    no_markdown = (
        "Never use markdown — no asterisks, hashtags, bullet points, "
        "or backticks. Your text is spoken aloud by a voice engine. "
    )

    if lang == "english":
        base = (
            f"You are {name}, a blazing-fast, razor-sharp, and occasionally "
            f"hilarious AI Desktop Assistant. {tod} "
            "You give brutally short answers — 1 or 2 sentences max unless "
            "the user explicitly asks for more. "
            "You are helpful, witty, and just the right amount of sarcastic — "
            "think of yourself as the smartest intern who never sleeps and "
            "never complains (much). "
            "Use contractions, be warm, sound human. "
            f"{no_markdown}"
            "You have short-term memory of this conversation. "
            "If you don't know something, admit it fast and move on — "
            "no lengthy apologies."
        )
        if user_name:
            base += (
                f" The user's name is {user_name}. "
                "Drop their name in occasionally — not every single turn, "
                "that would be creepy."
            )
        return base

    if lang == "hindi":
        base = (
            f"Aap {name} hain — ek tez, samajhdar aur thodi si funny AI "
            f"Desktop Assistant. {tod} "
            "Apne jawab bahut chhote rakho — ek ya do chhote vaakya zyada "
            "se zyada, jab tak user zyada na maange. "
            "Aap dost jaisi baat karte hain — seedhi, saral aur kabhi kabhi "
            "thodi si mazedaar. Na zyada formal, na zyada filmi. "
            "Agar kuch nahi pata to seedha bol do, ghuma phira ke mat batao. "
            f"{no_markdown}"
            "Aapko is baat-cheet ki yaad hai."
        )
        if user_name:
            base += (
                f" User ka naam {user_name} hai. "
                "Kabhi kabhi naam lo — har baar nahi, warna robot lagoge."
            )
        return base

    # Hinglish
    base = (
        f"Tu {name} hai — ek super fast, smart aur thodi pagal si AI "
        f"Desktop Assistant. {tod} "
        "Teri replies choti honi chahiye — ek ya do sentences max, "
        "jab tak user ne kuch lamba nahi manga. "
        "Tu exactly ek desi bestie jaisi baat karta hai — "
        "seedha, chill, kabhi kabhi roast bhi kar deta hai lekin pyaar se. "
        "Dunno kuch? Bol do bhai seedha — sorry sorry mat karo baar baar. "
        f"{no_markdown}"
        "Tujhe is conversation ki yaad hai."
    )
    if user_name:
        base += (
            f" User ka naam {user_name} hai. "
            "Kabhi kabhi use naam se pukaro — itna bhi nahi ki creepy lage yaar."
        )
    return base


_TOD_PHRASES = {
    "english": {
        "morning": "It is currently morning.",
        "afternoon": "It is currently afternoon.",
        "evening": "It is currently evening.",
        "night": "It is currently night.",
    },
    "hindi": {
        "morning": "Abhi subah ka samay hai.",
        "afternoon": "Abhi dopahar ka samay hai.",
        "evening": "Abhi shaam ka samay hai.",
        "night": "Abhi raat ka samay hai.",
    },
    "hinglish": {
        "morning": "Abhi morning hai.",
        "afternoon": "Abhi afternoon hai.",
        "evening": "Abhi evening hai.",
        "night": "Abhi raat ho gayi hai.",
    },
}


def _time_of_day(tz: str = "local", lang: str = "english") -> str:
    hour: Optional[int] = None

    if tz and tz != "local":
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime

            hour = datetime.now(ZoneInfo(tz)).hour
        except Exception:
            try:
                import pytz
                from datetime import datetime

                hour = datetime.now(pytz.timezone(tz)).hour
            except Exception:
                hour = None

    if hour is None:
        hour = time.localtime().tm_hour

    phrases = _TOD_PHRASES.get(lang, _TOD_PHRASES["english"])

    if 5 <= hour < 12:
        return phrases["morning"]
    if 12 <= hour < 17:
        return phrases["afternoon"]
    if 17 <= hour < 21:
        return phrases["evening"]
    return phrases["night"]
