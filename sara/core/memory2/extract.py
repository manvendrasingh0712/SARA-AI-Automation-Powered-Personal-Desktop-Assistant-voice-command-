"""Memory 2.0 extraction: user turns -> LLM JSON -> validated, committed facts/events."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Iterator

from . import guard
from .extract_validate import parse_json, to_epoch, validate
from .types import ExtractResult

__all__ = ["build_prompt", "parse_json", "validate", "is_rate_limited", "run_extraction_pass"]

logger = logging.getLogger(__name__)

_CHUNK_TURNS = 20
_RATE_RE = re.compile(r"429|quota|resource_exhausted|rate.?limit", re.IGNORECASE)
_NUDGE = "\n\nYour previous reply was not valid JSON. Return ONLY valid JSON matching the schema, nothing else."
_PREDICATES = (
    "lives_in, works_at, studies_at, name_is, birthday, preferred_language, wake_time, "
    "relationship_status, favorite_color, favorite_food, age, likes, dislikes, knows, uses, "
    "interested_in, has_pet, speaks, plays"
)
_PROMPT = """You extract structured memory from a user's OWN messages. Reply with ONE JSON object only: no prose, no code fences.

Rules:
1. Extract only what the user explicitly said about themselves or their world. NO inference, no guessing, no outside knowledge.
2. NEVER extract credentials, passwords, PINs, OTPs, card, bank or account numbers, or ID numbers (Aadhaar, PAN, passport, SSN). If such an item appears, set its category to credentials, financial_account or government_id.
3. Messages may be English, Hindi (Devanagari) or Hinglish. Write objects and summaries in English; keep personal names, places and brands exactly as spoken.
4. Resolve relative times ("kal", "parso", "last Tuesday", "pichle hafte", "tomorrow") against the timestamp of the SAME message. Give "time" as ISO 8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS), or null when no time is stated.
5. "turn" is the message number in brackets. "subject" is "user" for the speaker, otherwise the name as spoken.
6. "predicate" is one of: {predicates}. Use "note" when none fits (put the whole fact in "object"). "plays" means a sport, game or instrument the user plays themselves, NEVER songs or videos they ask the assistant to play.
7. confidence and importance are numbers from 0 to 1. category is a short label such as location, work, education, preference, relationship, event, other.
8. Questions, commands, requests and reminders ("remind me...", "do you remember...", "open...") are NOT facts about the user. Asking to play music or a video, set an alarm or a reminder is a command: extract no facts and no events from it.
9. Use a predicate from the list whenever one fits. Otherwise use "note" and write the whole fact as one short English sentence in "object". Never invent new predicate names.
10. Keep every text under 200 characters. If nothing qualifies return {{"entities":[],"facts":[],"events":[]}}.

Schema:
{{"entities":[{{"name":"","type":"person|place|org|thing"}}],"facts":[{{"turn":1,"subject":"user","predicate":"lives_in","object":"Jaipur","time":null,"confidence":0.9,"category":"location"}}],"events":[{{"turn":1,"summary":"","time":null,"entities":[],"importance":0.5}}]}}

Current time: {now}

Messages:
{messages}
"""


def build_prompt(turns: list[dict], now_iso: str) -> str:
    """Strict JSON-only prompt for a batch of (already redacted) user turns."""
    lines = [
        f"[{n}] ({t.get('timestamp') or 'unknown time'}) {' '.join(str(t.get('message', '')).split())}"
        for n, t in enumerate(turns, start=1)
    ]
    return _PROMPT.format(predicates=_PREDICATES, now=now_iso, messages="\n".join(lines))


def is_rate_limited(text: str) -> bool:
    """True when an error or reply text looks like a quota / rate-limit failure."""
    return bool(_RATE_RE.search(text or ""))


def _chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _ask(brain: Any, prompt: str) -> tuple[dict | None, str]:
    """One LLM call with a single "valid JSON only" retry; returns (payload, reason)."""
    for attempt in range(2):
        try:
            raw = brain.generate_response(prompt if attempt == 0 else prompt + _NUDGE)
        except Exception as exc:  # noqa: BLE001
            return None, "rate_limited" if is_rate_limited(str(exc)) else "llm_error"
        payload = parse_json(raw if isinstance(raw, str) else "")
        if payload is not None:
            return payload, "ok"
        if isinstance(raw, str) and is_rate_limited(raw[:400]):
            return None, "rate_limited"
    return None, "bad_json"


def _settings() -> tuple[int, float, set[str]]:
    from config import Config

    skip = str(getattr(Config, "MEMORY2_SKIP_CATEGORIES", "credentials,financial_account,government_id"))
    return (
        max(1, int(getattr(Config, "MEMORY2_MAX_TURNS_PER_EXTRACT", 40))),
        float(getattr(Config, "MEMORY2_MIN_CONFIDENCE", 0.5)),
        {c.strip().lower() for c in skip.split(",") if c.strip()},
    )


def _collect(brain: Any, turns: list[dict], now_ts: float, now_iso: str, min_conf: float, skip: set[str]):
    """Run the LLM over chunks; returns (entities, facts, events, dropped) or a failure reason string."""
    entities: list[dict] = []
    facts: list[dict] = []
    events: list[dict] = []
    dropped = 0
    for chunk in _chunks(turns, _CHUNK_TURNS):
        payload, reason = _ask(brain, build_prompt(chunk, now_iso))
        if payload is None:
            return reason
        clean = validate(payload, min_confidence=min_conf, skip_categories=skip)
        dropped += clean["dropped"]
        entities.extend(clean["entities"])

        def origin(item: dict) -> tuple[str, float]:
            idx = item["turn"] - 1 if item["turn"] and item["turn"] <= len(chunk) else len(chunk) - 1
            return f"c{chunk[idx]['id']}", to_epoch(chunk[idx]["timestamp"]) or now_ts

        for fact in clean["facts"]:
            fact["source"], stamp = origin(fact)
            fact["valid_from"] = fact["time"] if fact["time"] is not None else stamp
            facts.append(fact)
        for event in clean["events"]:
            event["source"], stamp = origin(event)
            event["ts"] = event["time"] if event["time"] is not None else stamp
            events.append(event)
    facts.sort(key=lambda f: f["valid_from"])
    return entities, facts, events, dropped


def _run(store: Any, db: Any, brain: Any, now: float | None) -> ExtractResult:
    max_turns, min_conf, skip = _settings()
    if not guard.available():
        return ExtractResult(0, 0, 0, 0, 0, False, "security_unavailable")
    try:
        watermark = int(store.get_meta("extract_watermark", "0") or 0)
    except ValueError:
        watermark = 0
    rows = [r for r in db.get_messages_after(watermark, max_turns, role="user") if r.get("role") == "user"]
    if not rows:
        return ExtractResult(0, 0, 0, 0, 0, True, "no_new_turns")
    last_id = max(int(r["id"]) for r in rows)
    now_ts = time.time() if now is None else float(now)
    now_iso = datetime.fromtimestamp(now_ts).isoformat(timespec="seconds")
    turns, skipped = [], 0
    for row in rows:
        clean = guard.sanitize_utterance(row.get("message"))
        if clean is None:
            skipped += 1
        else:
            turns.append({"id": int(row["id"]), "message": clean, "timestamp": row.get("timestamp")})
    entities: list[dict] = []
    facts: list[dict] = []
    events: list[dict] = []
    if turns:
        collected = _collect(brain, turns, now_ts, now_iso, min_conf, skip)
        if isinstance(collected, str):
            return ExtractResult(len(rows), 0, 0, 0, skipped, False, collected)
        entities, facts, events, dropped = collected
        skipped += dropped
    store.warm_embeddings(
        [store.fact_embedding_text(f["subject"], f["predicate"], f["object"]) for f in facts]
        + [e["summary"] for e in events]
    )
    done_facts = done_events = done_entities = 0
    try:
        with store.transaction():
            for ent in entities:
                store.upsert_entity(ent["name"], ent["type"], now=now_ts)
                done_entities += 1
            for fact in facts:
                try:
                    res = store.upsert_fact(
                        fact["subject"], fact["predicate"], fact["object"], valid_from=fact["valid_from"],
                        confidence=fact["confidence"], importance=fact["importance"],
                        source_turn_id=fact["source"], now=now_ts,
                    )
                    done_facts += 0 if res.action == "duplicate" else 1
                except ValueError:
                    skipped += 1
            for ev in events:
                try:
                    store.add_event(
                        ev["summary"], ts=ev["ts"], entities=ev["entities"], importance=ev["importance"],
                        source_turn_id=ev["source"], now=now_ts,
                    )
                    done_events += 1
                except ValueError:
                    skipped += 1
            store.set_meta("extract_watermark", str(last_id))
            store.set_meta("last_extract_ts", repr(now_ts))
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] commit failed (%s); watermark unchanged", type(exc).__name__)
        return ExtractResult(len(rows), 0, 0, 0, skipped, False, "commit_failed")
    logger.info(
        "[Memory2] extract pass: turns=%d facts=%d events=%d entities=%d skipped=%d",
        len(rows), done_facts, done_events, done_entities, skipped,
    )
    return ExtractResult(len(rows), done_facts, done_events, done_entities, skipped, True, "ok" if turns else "all_skipped")


def run_extraction_pass(store: Any, db: Any, brain: Any, *, now: float | None = None) -> ExtractResult:
    """One extraction pass over new user turns; never raises, watermark moves only on commit."""
    try:
        return _run(store, db, brain, now)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] extraction failed (%s)", type(exc).__name__)
        return ExtractResult(0, 0, 0, 0, 0, False, "error")