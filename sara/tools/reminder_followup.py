"""
sara/tools/reminder_followup.py
Rules for gentle follow-ups after a reminder was spoken, and for noticing
when the user has answered it. Pure logic: no I/O, no LLM, easy to test.

    pick_followup(...)                 -> FollowupPlan or None
    acknowledge_if_applicable(...)     -> reply text or None

A follow-up is only ever offered when ALL of these hold:
  - the reminder's category allows follow-ups (policy.post_due_followup)
  - it was spoken (delivered) recently, inside the post-due window
  - the user is verifiably active: they spoke to Sara AFTER the reminder
    was delivered, and within the awake window (HIGH evidence)
  - the user has not acknowledged it, and it is not marked done
  - nudge count is below the limit and the cooldown since the last
    message about it has passed
Low evidence (no recent interaction) never produces a nudge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sara.tools.reminder_context import classify, effective_policy, time_bucket

AWAKE_HIGH = "high"
AWAKE_LOW = "low"

_NIGHT_BUCKETS = ("late_night", "deep_night")


@dataclass(frozen=True)
class FollowupSettings:
    cooldown_min: int = 30
    max_nudges: int = 2
    awake_window_min: int = 10
    post_due_min: int = 120


def settings_from_config(cfg: Any) -> FollowupSettings:
    def _val(name: str, default: int) -> int:
        try:
            return max(0, int(getattr(cfg, name, default)))
        except (TypeError, ValueError):
            return default

    return FollowupSettings(
        cooldown_min=_val("CONTEXTUAL_REMINDER_NUDGE_COOLDOWN_MINUTES", 30),
        max_nudges=_val("CONTEXTUAL_REMINDER_MAX_NUDGES", 2),
        awake_window_min=_val("CONTEXTUAL_REMINDER_AWAKE_WINDOW_MINUTES", 10),
        post_due_min=_val("CONTEXTUAL_REMINDER_POST_DUE_GRACE_MINUTES", 120),
    )


@dataclass(frozen=True)
class FollowupPlan:
    reminder_id: int
    message: str
    category: str
    minutes_since: int      # minutes since the reminder was due
    nudge_number: int       # 1 for the first follow-up, 2 for the second, ...


def _parse(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def awake_level(
    last_touch: Optional[datetime],
    delivered_at: datetime,
    now: datetime,
    window_min: int,
) -> str:
    """HIGH only if the user spoke to Sara after the reminder, and recently."""
    if last_touch is None:
        return AWAKE_LOW
    if last_touch <= delivered_at:
        return AWAKE_LOW
    if now - last_touch > timedelta(minutes=window_min):
        return AWAKE_LOW
    return AWAKE_HIGH


def pick_followup(
    candidates: Sequence[Dict[str, Any]],
    last_touch: Optional[datetime],
    now: datetime,
    settings: FollowupSettings,
    night_allowed: bool = True,
) -> Optional[FollowupPlan]:
    """Returns the first reminder that is eligible for a follow-up right now."""
    bucket = time_bucket(now)
    for cand in candidates:
        message = cand.get("message") or ""
        intent = classify(message)
        policy = effective_policy(intent, bucket)
        if not policy.post_due_followup:
            continue
        sent = int(cand.get("nudge_count") or 0)
        if sent >= min(policy.max_nudges, settings.max_nudges):
            continue
        delivered = _parse(cand.get("delivered_at"))
        if delivered is None or now - delivered > timedelta(minutes=settings.post_due_min):
            continue
        last_message_at = _parse(cand.get("last_nudge_at")) or delivered
        if now - last_message_at < timedelta(minutes=settings.cooldown_min):
            continue
        if awake_level(last_touch, delivered, now, settings.awake_window_min) != AWAKE_HIGH:
            continue
        if bucket in _NIGHT_BUCKETS and not night_allowed:
            continue
        due = _parse(cand.get("due_at")) or delivered
        minutes_since = max(1, int(round((now - due).total_seconds() / 60)))
        return FollowupPlan(
            reminder_id=cand["id"],
            message=message,
            category=intent.category,
            minutes_since=minutes_since,
            nudge_number=sent + 1,
        )
    return None


# ----------------------------------------------------------------------
# Acknowledgement ("okay, going to sleep")
# ----------------------------------------------------------------------

_STRONG_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\b(?:i am |i'm |im |i will |i'll )?(?:going|go|heading) to (?:sleep|bed)\b",
        r"\boff to (?:bed|sleep)\b",
        r"\bi(?: will|'ll) sleep(?: now)?\b",
        r"\b(?:i am|i'm|im) sleeping(?: now)?\b",
        r"\bgood ?night\b",
        r"\breminder (?:is )?(?:done|complete|completed)\b",
        r"\bmark (?:it|that|this|the reminder) (?:as )?(?:done|complete|completed)\b",
        r"\bstop (?:reminding|nudging) me\b",
    )
)
_BARE_ACKS = frozenset(
    {"ok", "okay", "alright", "all right", "got it", "done", "thanks", "thank you",
     "understood", "i know", "yes i know", "fine"}
)
_STRONG_CONTEXT_MIN = 60   # a clear "going to sleep" counts for an hour after the reminder
_BARE_CONTEXT_MIN = 10     # a bare "okay" only counts right after the message


def _normalize(text: Optional[str]) -> str:
    lowered = (text or "").lower().replace("\u2019", "'")
    lowered = re.sub(r"\bsara\b", " ", lowered)
    lowered = re.sub(r"[^a-z0-9' ]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def classify_ack(text: Optional[str]) -> Optional[str]:
    """Returns "strong", "bare" or None."""
    norm = _normalize(text)
    if not norm:
        return None
    if norm in _BARE_ACKS:
        return "bare"
    if any(p.search(norm) for p in _STRONG_PATTERNS):
        return "strong"
    return None


def acknowledge_if_applicable(
    text: Optional[str],
    reminders: Any,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """
    If `text` clearly answers the reminder Sara just spoke about, marks that
    reminder acknowledged and returns Sara's short reply. Otherwise None, and
    the utterance is handled normally. Never raises.
    """
    try:
        kind = classify_ack(text)
        if kind is None or reminders is None or not hasattr(reminders, "followup_candidates"):
            return None
        now = now or datetime.now()
        within = _STRONG_CONTEXT_MIN if kind == "strong" else _BARE_CONTEXT_MIN
        newest: Optional[Dict[str, Any]] = None
        newest_at: Optional[datetime] = None
        for cand in reminders.followup_candidates() or []:
            spoken_at = _parse(cand.get("last_nudge_at")) or _parse(cand.get("delivered_at"))
            if spoken_at is None or now - spoken_at > timedelta(minutes=within):
                continue
            if newest_at is None or spoken_at > newest_at:
                newest, newest_at = cand, spoken_at
        if newest is None:
            return None
        if not reminders.update_intel(newest["id"], acknowledged=1):
            return None
        if classify(newest.get("message")).category == "sleep":
            return "Alright, good night."
        return "Okay, noted."
    except Exception:  # noqa: BLE001
        return None