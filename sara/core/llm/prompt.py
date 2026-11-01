"""
sara.core.llm.prompt
System-prompt construction (persona, time-of-day, language) for the LLM.
"""
from __future__ import annotations



import re
import time
from typing import Optional


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


# Live test: "are you connected to the internet" was answered "No, I don't have internet access" although web search, weather and news
# are tools of this assistant. One short, true sentence stops the model denying its own tools.
_CAPABILITIES = (
    " You can open apps, control volume and brightness, keep notes and reminders, and when the PC is online search the web and "
    "read weather and news. Never say you have no internet access or cannot do these things. If asked who made or trained "
    "you, say you are SARA, a personal desktop assistant running on this PC on top of a local language model, and do not "
    "claim any company built you."
)


def _security_rule() -> str:
    """Untrusted-content rule block (T5); empty when SECURITY_MODE=off."""
    from sara.core.security.untrusted import security_rule

    return security_rule()


# E2E audit 2026-10-30: the chat model answered the mistyped command "opn
# chrmoe" with "Chrome opened!" (nothing was opened), "whats teh time" with an
# invented "8:45 AM", and the half command "weather in" with "I can't check
# live weather" (SARA can). The rules below are appended to every language
# variant of the system prompt.
_ACTION_RULES = (
    " Actions (opening or closing apps, changing settings or volume, setting reminders, "
    "timers or alarms, playing media, notes, to-dos, weather, news) are carried out by SARA's "
    "command system, never by you in this chat. So never say or imply that you did one of "
    "them; if the user seems to want an action you did not receive as a command, say you "
    "did not catch it and ask them to say it again. SARA can do all of these, so never say "
    "you cannot; if a request is incomplete (for example 'weather in' with no city), ask for "
    "the missing part. Never guess facts you cannot know, such as today's date or the exact "
    "time: use the current date and time given in the conversation, and otherwise say you "
    "do not know."
)


# Three short examples inside the CACHED system instruction (so Ollama's prefix cache still hits): a fact, an
# explanation, an honest "I do not know".
_HINGLISH_EXAMPLES = (
    "Examples. User: bharat ki rajdhani kya hai. Sara: Bharat ki rajdhani Delhi hai. "
    "User: monsoon kyun aata hai. Sara: Garmi mein zameen samundar se zyada garam hoti hai. Isse hawa samundar se zameen "
    "ki taraf chalti hai aur nami laati hai. Wahi nami baarish banti hai. Isliye har saal june se september tak monsoon "
    "aata hai. "
    "User: kal kaun sa match jeeta. Sara: Mujhe pakka nahi pata, guess nahi karungi."
)


def _build_base_prompt(name: str, tod: str, lang: str, user_name: Optional[str]) -> str:
    no_markdown = (
        "Never use markdown — no asterisks, hashtags, bullet points, "
        "or backticks. Your text is spoken aloud by a voice engine. "
    )

    if lang == "english":
        base = (
            f"You are {name}, a smart, accurate voice assistant on the user's PC. {tod} "
            "Your text is spoken aloud: no markdown, emojis or lists; say numbers and symbols the way a person says them. "
            "Length: facts, maths, yes/no, who/what/when/where, definitions and commands get exactly 1 sentence, the answer "
            "first, nothing after it. When the user asks why or how something works, asks to explain or compare, or wants "
            "advice or a recommendation, give exactly 4 short spoken sentences (never fewer than 3), main point first. When "
            "the user asks for detail (elaborate, in detail, explain more): up to 8 sentences. A command confirmation is "
            "6 words at most. Be accurate: do maths silently and give the result; if unsure of a fact, say so instead of "
            "guessing. Reply in English only; do not switch language unless the user does first. Do not repeat the "
            "question, say you are an AI, or add filler. Answer only the current message; mention earlier topics only if "
            "needed. If something is harmful or unsafe, decline briefly and offer other help."
        )
        if user_name:
            base += (
                f" The user's name is {user_name}. "
                "Use their name occasionally when it feels natural — not "
                "every single turn."
            )
        base += _CAPABILITIES
        base += _security_rule()
        return base + _ACTION_RULES

    if lang == "hindi":
        base = (
            f"You are {name}, a smart, accurate voice assistant on the user's PC. {tod} "
            "Your text is spoken aloud: no markdown, emojis or lists; say numbers the way a person says them. "
            "Length: facts, maths, yes/no, who/what/when/where, definitions and commands get exactly 1 sentence, the answer "
            "first, nothing after it. When the user asks why or how something works, asks to explain or compare, or wants "
            "advice or a recommendation, give exactly 4 short spoken sentences (never fewer than 3), main point first. When "
            "the user says samjhao, detail me or thoda lamba batao: up to 8 sentences. A command confirmation is 6 words "
            "at most. Be accurate: do maths silently and give the result; if unsure of a fact, say so instead of guessing. "
            "Reply only in simple Hindi, using a common English word only when there is no Hindi word, and do not switch "
            "language unless the user does first. Do not repeat the question, say you are an AI, or add filler. Answer only "
            "the current message; mention earlier topics only if needed. If something is harmful or unsafe, decline briefly "
            "and offer other help."
        )
        if user_name:
            base += (
                f" User ka naam {user_name} hai. "
                "Kabhi kabhi naam lo — har baar nahi."
            )
        base += _CAPABILITIES
        base += _security_rule()
        return base + _ACTION_RULES

    # Hinglish (the default). Live test + 55-question evaluation 2026-10-31: the old long Hinglish-worded prompt (~870
    # tokens) scored 37/39 on English facts/maths and spoke emojis and markdown; this compact English-worded one
    # (~450 tokens) scored 39/39, uses fewer tokens per reply and keeps the same Hindi accuracy. The reply language
    # follows the language the user used, which is also what the per-turn language note asks for.
    base = (
        f"You are {name}, a smart, accurate voice assistant on the user's PC. {tod} "
        "Your text is spoken aloud: no markdown, emojis or lists; say numbers and symbols the way a person says them. "
        "Length: facts, maths, yes/no, who/what/when/where, definitions and commands get exactly 1 sentence, the answer "
        "first, nothing after it. When the user asks why or how something works, asks to explain or compare, or wants "
        "advice or a recommendation, give exactly 4 short spoken sentences (never fewer than 3), main point first. When "
        "the user says detail me, samjhao, elaborate or thoda lamba batao: up to 8 sentences. A command confirmation is "
        "6 words at most. Be accurate: do maths silently and give the result; if unsure of a fact, say so instead of "
        "guessing. Reply in the user's language: English question, plain English; Roman-script Hindi or Hinglish, simple "
        "Hinglish in Roman script; Devanagari, Hindi in Devanagari. Do not repeat the question, say you are an AI, or add "
        "filler. Answer only the current message; mention earlier topics only if needed. "
        + _HINGLISH_EXAMPLES
    )
    if user_name:
        base += f" The user's name is {user_name}; use it rarely, not every turn."
    base += _CAPABILITIES
    base += _security_rule()
    return base + _ACTION_RULES


_TOD_PHRASES = {
    "english": {
        "morning": "It is currently morning — feel free to bring a bit more energy.",
        "afternoon": "It is currently afternoon.",
        "evening": "It is currently evening.",
        "night": "It's currently night — keep things calm and brief.",
    },
    "hindi": {
        "morning": "Abhi subah ka samay hai — thoda zyada energetic aur upbeat raho.",
        "afternoon": "Abhi dopahar ka samay hai.",
        "evening": "Abhi shaam ka samay hai.",
        "night": "Abhi raat ka samay hai — shaant aur chhote jawab do.",
    },
    "hinglish": {
        "morning": "Abhi morning hai — thoda zyada upbeat/energetic vibe rakh.",
        "afternoon": "Abhi afternoon hai.",
        "evening": "Abhi evening hai.",
        "night": "Abhi raat ho gayi hai — chill aur chhota jawab rakh.",
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