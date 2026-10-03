"""
sara/tools/reminder_context.py
Local, instant understanding of a reminder: no database, no network, no LLM.

    time_bucket(dt)          -> "morning" | "afternoon" | "evening" | "late_night" | "deep_night"
    classify(message)        -> ReminderIntent (category, subject, level)
    urgency_level(message)   -> "critical" | "important" | "routine" | "normal"
    effective_policy(...)    -> ReminderPolicy (tone / word limit / follow-up rules)
    due_message(message)     -> short English text to speak when the reminder is due

English only for now. Everything here is deterministic, so it is also the
final fallback when no LLM is available.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Tuple

# ----------------------------------------------------------------------
# Time of day
# ----------------------------------------------------------------------


def time_bucket(now: Optional[datetime] = None) -> str:
    """05-11 morning, 12-16 afternoon, 17-21 evening, 22-00 late_night, 01-04 deep_night."""
    hour = (now or datetime.now()).hour
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 21:
        return "evening"
    if hour >= 22 or hour == 0:
        return "late_night"
    return "deep_night"


def _is_night(bucket: str) -> bool:
    return bucket in ("late_night", "deep_night")


# ----------------------------------------------------------------------
# Category + urgency
# ----------------------------------------------------------------------

# Order matters: first match wins. Urgent-sounding groups come first so that
# "pay for train ticket" is a travel reminder and "study for exam" an exam one.
_CATEGORY_RULES = (
    ("meeting", r"meeting|meet|interview|appointment|conference|standup|class|lecture"),
    ("call", r"call|phone|dial"),
    ("travel", r"flight|train|bus|station|airport|leave for|leaving|depart|travel|ticket|cab|taxi"),
    ("health_routine", r"medicine|medication|tablet|tablets|pill|pills|vitamin|vitamins|dose"),
    ("wake", r"wake|wake up|get up|alarm"),
    ("exam", r"exam|exams|test|quiz|viva"),
    ("assignment", r"assignment|homework|submit|submission"),
    ("project", r"project|deadline|presentation|demo"),
    ("payment", r"pay|payment|bill|bills|rent|emi|recharge|invoice"),
    ("sleep", r"sleep|bedtime|go to bed|nap"),
    ("study", r"study|studying|revise|revision"),
    ("coding", r"code|coding|programming|debug|leetcode"),
    ("exercise", r"exercise|workout|work ?out|gym|running|jog|jogging|yoga|walk"),
    ("work", r"work|office|email|emails"),
    ("water", r"water|hydrate"),
    ("food", r"eat|food|breakfast|lunch|dinner|snack|meal|cook"),
    ("break", r"break|stretch|rest"),
    ("shopping", r"buy|shopping|grocery|groceries|order"),
    ("birthday", r"birthday|bday|wish"),
    ("anniversary", r"anniversary"),
)
_COMPILED_RULES = tuple(
    (name, re.compile(r"\b(?:" + words + r")\b")) for name, words in _CATEGORY_RULES
)

_URGENT_RE = re.compile(r"\b(?:urgent|emergency|asap|important|deadline)\b")

_LEVEL_BY_CATEGORY = {
    "meeting": "critical",
    "call": "critical",
    "travel": "critical",
    "health_routine": "critical",
    "wake": "critical",
    "exam": "important",
    "assignment": "important",
    "project": "important",
    "payment": "important",
    "sleep": "routine",
    "study": "routine",
    "exercise": "routine",
    "water": "routine",
    "food": "routine",
    "break": "routine",
}

_MAX_SUBJECT_CHARS = 60


@dataclass(frozen=True)
class ReminderIntent:
    category: str            # one of the names above, or "generic"
    subject: str             # the reminder text, tidied
    level: str               # "critical" | "important" | "routine" | "normal"
    late_night_relevance: bool


def _tidy(message: Optional[str]) -> str:
    text = re.sub(r"\s+", " ", message or "").strip(" .,!?:;-")
    return text[:_MAX_SUBJECT_CHARS].rstrip()


def _category_of(text: str) -> str:
    for name, pattern in _COMPILED_RULES:
        if pattern.search(text):
            return name
    return "generic"


def urgency_level(message: Optional[str]) -> str:
    """
    "critical"  - time-bound things (meeting, call, travel, medicine, wake up)
    "important" - exam, assignment, project, payment, or words like "urgent"
    "routine"   - sleep, study, water, food, exercise, break
    "normal"    - anything Sara cannot place
    """
    text = (message or "").lower()
    if _URGENT_RE.search(text):
        return "important"
    return _LEVEL_BY_CATEGORY.get(_category_of(text), "normal")


def classify(message: Optional[str]) -> ReminderIntent:
    text = (message or "").lower()
    category = _category_of(text)
    return ReminderIntent(
        category=category,
        subject=_tidy(message),
        level=urgency_level(message),
        late_night_relevance=(category == "sleep"),
    )


# ----------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ReminderPolicy:
    tone: str = "neutral"
    max_words: int = 40
    post_due_followup: bool = False
    max_nudges: int = 0


_POLICY_BY_CATEGORY = {
    "sleep": ReminderPolicy(tone="caring", max_words=30, post_due_followup=True, max_nudges=2),
    "study": ReminderPolicy(tone="friendly", post_due_followup=True, max_nudges=1),
    "break": ReminderPolicy(tone="friendly", post_due_followup=True, max_nudges=1),
    "meeting": ReminderPolicy(tone="professional", max_words=25),
    "call": ReminderPolicy(tone="professional", max_words=25),
    "birthday": ReminderPolicy(tone="friendly"),
    "anniversary": ReminderPolicy(tone="friendly"),
}
_DEFAULT_POLICY = ReminderPolicy()
_NIGHT_MAX_WORDS = 25


def effective_policy(intent: ReminderIntent, bucket: str) -> ReminderPolicy:
    """Category policy, adjusted for the time of day (night = quieter and shorter)."""
    policy = _POLICY_BY_CATEGORY.get(intent.category, _DEFAULT_POLICY)
    if _is_night(bucket):
        return ReminderPolicy(
            tone="night",
            max_words=min(policy.max_words, _NIGHT_MAX_WORDS),
            post_due_followup=policy.post_due_followup,
            max_nudges=policy.max_nudges,
        )
    return policy


# ----------------------------------------------------------------------
# Deterministic wording for a due reminder
# ----------------------------------------------------------------------

_DUE_TEMPLATES = {
    "sleep": "It's {time}. Time to wind down and get some rest.",
    "wake": "It's {time}. Time to wake up.",
    "study": "Your study reminder is here. Let's get started.",
    "water": "Time to drink some water.",
    "exercise": "Time to get moving.",
    "break": "Time for a short break.",
    "meeting": "Reminder: {subject}. It's time.",
    "call": "Reminder: {subject}. It's time.",
    "travel": "Reminder: {subject}. Time to head out.",
    "exam": "Reminder: {subject}. Time to focus.",
    "assignment": "Reminder: {subject}. Let's get it done.",
    "project": "Reminder: {subject}. Time to make some progress.",
    "payment": "Reminder: {subject}. Please take care of it now.",
}
_GENERIC_TEMPLATE = "Reminder: {subject}"


def _clock(now: datetime) -> str:
    return now.strftime("%I:%M %p").lstrip("0")


def due_message(message: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """
    Short English text to speak when a reminder comes due, or None if the
    reminder text is empty (the caller then keeps its own default).
    Never raises.
    """
    try:
        intent = classify(message)
        if not intent.subject:
            return None
        template = _DUE_TEMPLATES.get(intent.category, _GENERIC_TEMPLATE)
        return template.format(subject=intent.subject, time=_clock(now or datetime.now()))
    except Exception:
        return None


# ----------------------------------------------------------------------
# LLM prompt and output validation (provider-neutral)
# ----------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are Sara, a calm and friendly voice assistant. Write ONE short spoken "
    "reminder in English. Rules: use only the facts given inside the tags; never "
    "invent plans, deadlines or details; at most {max_words} words; plain spoken "
    "sentences; no markdown, no emojis, no quotes, no lists; do not mention AI, "
    "models or providers. Tone: {tone}."
)


def _clean_tag_text(text: str) -> str:
    """User or note text goes inside XML-style tags, so strip angle brackets."""
    return re.sub(r"\s+", " ", (text or "").replace("<", " ").replace(">", " ")).strip()


def build_prompt(
    intent: ReminderIntent,
    bucket: str,
    now: datetime,
    policy: ReminderPolicy,
    notes: Sequence[str] = (),
    activity: Sequence[str] = (),
) -> Tuple[str, str]:
    """Returns (system_prompt, user_prompt) for generate_short()."""
    system = _SYSTEM_PROMPT.format(max_words=policy.max_words, tone=policy.tone)
    lines = [
        f"<verified_reminder>{_clean_tag_text(intent.subject)}</verified_reminder>",
        f"<category>{intent.category}</category>",
        f"<current_time>{_clock(now)} ({bucket.replace('_', ' ')})</current_time>",
    ]
    for note in notes:
        cleaned = _clean_tag_text(note)
        if cleaned:
            lines.append(f"<relevant_note>{cleaned}</relevant_note>")
    for item in activity:
        cleaned = _clean_tag_text(item)
        if cleaned:
            lines.append(f"<recent_activity>{cleaned}</recent_activity>")
    lines.append("Write the reminder now.")
    return system, "\n".join(lines)


_BANNED_RE = re.compile(
    r"\b(?:gemini|google|ollama|openai|chatgpt|anthropic|claude|llama|qwen|"
    r"language model|as an ai)\b",
    re.IGNORECASE,
)
_PREAMBLE_RE = re.compile(r"^(?:sure|here(?:'s| is)|okay|ok|certainly|of course)\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def validate_text(
    text: Optional[str],
    max_words: int,
    recent: Sequence[str] = (),
) -> Optional[str]:
    """
    Returns the cleaned single-line text if it is safe to speak, else None
    (the caller then uses the template). Rejects: empty, markdown, too long,
    chatty preambles, provider names, and a repeat of any `recent` message.
    """
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip().strip("\"'\u201c\u201d")
    if len(cleaned) < 3:
        return None
    if any(ch in cleaned for ch in ("*", "#", "`")):
        return None
    if _PREAMBLE_RE.match(cleaned) or _BANNED_RE.search(cleaned):
        return None
    if len(cleaned.split()) > max_words:
        return None
    if _norm(cleaned) in {_norm(r) for r in recent}:
        return None
    return cleaned