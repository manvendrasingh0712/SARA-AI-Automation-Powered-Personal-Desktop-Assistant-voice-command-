"""
sara.orchestrator.context_tracking

"What about jaipur?" / "aur dilli ka?" style follow-up context, and
unresolved pronoun/reference detection ("close it" / "usko band kar
do"). Split out of the former monolithic intent_handlers.py -- see
that module's docstring for the full feature rationale.
"""
import re
import time

from sara.tools import web as web_tools

from .network_utils import _call_with_timeout
from .command_helpers import _quick, _ack, _run_activity

# ── Context Follow-ups ("what about jaipur?" / "aur dilli ka?") ────────
# ctx["context_state"] is a plain dict, created once per run in
# sara/orchestrator/core_wiring.py -- same pattern as playback_state and
# confirm_state -- so it genuinely persists turn-to-turn.
#
# GENERALIZED (NEW): this used to be a single hardcoded slot
# ("last_slot_intent"), so only ONE recently-mentioned thing could ever
# be remembered at a time, and only weather/news ever wrote to it. It's
# now ctx["context_state"]["recent_entities"], a dict of independently
# labeled slots (e.g. "last_location", "last_topic", "last_app",
# "last_file", and -- for a future contacts/calling feature -- 
# "last_person") each carrying its own value/timestamp, so an app opened
# a minute ago and a city asked about just before it can BOTH still be
# remembered at once, instead of the second one silently evicting the
# first. Any handler can opt a new slot in by calling _remember_entity()
# with its own slot name -- see _h_open_app()/_h_close_app() (last_app)
# and _h_find_file() (last_file) below for examples. Weather/news keep
# using the pre-existing _remember_context() wrapper unchanged (it now
# delegates to _remember_entity() under the hood) so this section's
# "what about X" behavior is not just preserved but untouched from the
# caller's point of view.
_CONTEXT_TTL_S = 120  # short window: a natural follow-up, not a resumed
                       # conversation from minutes ago

# Only a slot recorded against one of these intent names is eligible to
# answer a "what about X" / "aur X ka" followup_query -- see
# _latest_followup_entity() below. _remember_context() (weather/news)
# tags its entry with the intent name automatically; a slot written
# directly via _remember_entity() with followup_intent left at its
# default of None (last_app, last_file, ...) is still tracked for any
# future pronoun-style handler ("usko band karo") but is simply never a
# candidate here, exactly like every non-weather/news intent today.
_FOLLOWUP_SLOT_BY_INTENT = {
    "weather": "last_location",
    "news": "last_topic",
}


def _remember_entity(ctx, slot_name: str, value: str, followup_intent: str = None) -> None:
    """
    Record `value` under `slot_name` in
    ctx["context_state"]["recent_entities"], stamped with the current
    time. `followup_intent`, when given, is the intent name
    _h_followup_query() should treat this slot as continuing (see
    _FOLLOWUP_TOOL_BY_INTENT below) -- leave it at its default of None
    for entities (apps, files, ...) that don't yet back a "what about X"
    style re-query.

    Silently no-ops on a falsy value, same as the old _remember_context()
    did -- callers can pass a possibly-empty regex capture straight
    through without their own guard.
    """
    if not value:
        return
    ctx["context_state"].setdefault("recent_entities", {})[slot_name] = {
        "value": value,
        "ts": time.time(),
        "intent": followup_intent,
    }


def _resolve_app_target(ctx, captured_text: str):
    """
    Resolve a fast-path regex capture meant to be an application name,
    accounting for the fact that fast-path intents (open_app, close_app,
    restart_application, switch_to_application) never go through
    _route_chat_message()'s pronoun/reference handling -- a message like
    "close it" / "usko band kar do" matches close_app directly on its
    own regex and would otherwise hand the literal word "it"/"usko"
    straight to allowlist validation as if it were a real app name.

    Returns:
      - `captured_text` unchanged if it's NOT a pronoun/reference (see
        _is_pronoun_reference() above) -- the overwhelming majority of
        calls, and the ONLY thing that happens for them: one cheap
        regex check, no dict lookup, no added latency for the normal
        case.
      - the still-fresh value of
        ctx["context_state"]["recent_entities"]["last_app"] (same
        _CONTEXT_TTL_S expiry window _remember_entity()/
        _describe_recent_entities() already use elsewhere in this file)
        if `captured_text` IS a pronoun/reference and that slot exists
        and hasn't expired.
      - None if `captured_text` is a pronoun/reference but there's
        nothing fresh in "last_app" to resolve it to -- callers must
        check for this explicitly and ask the user to clarify rather
        than letting it fall through to allowlist validation, which
        would reject it with a misleading "not an allowed application"
        message instead of the real problem (missing context).

    Pure read of ctx["context_state"]; never writes to it -- writing the
    resolved name back into "last_app" remains each handler's own job
    via its existing _remember_entity() call, same as before.
    """
    if not _is_pronoun_reference(captured_text):
        return captured_text
    entry = (ctx.get("context_state") or {}).get("recent_entities", {}).get("last_app")
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > _CONTEXT_TTL_S:
        return None
    return entry.get("value")


# Hinglish phrasing per verb for _build_app_clarification()'s question
# text -- kept as its own small table (rather than string-formatting the
# verb directly) so each phrasing reads naturally instead of just
# gluing the English verb onto "karu".
_APP_CLARIFY_VERB_PHRASES = {
    "open": "kholu",
    "close": "band karu",
    "restart": "restart karu",
    "switch to": "switch karu",
}


def _build_app_clarification(ctx, verb: str):
    """
    Build a smart clarifying question ("Chrome ya Spotify, kaunsa band
    karu?") plus the candidate list backing it, for use when
    _resolve_app_target() returns None (an unresolved pronoun/reference
    with nothing fresh to resolve to) -- instead of the old generic
    "Which app would you like me to <verb>?".

    Candidate ranking (best first):
      1. Any still-fresh entries in
         ctx["context_state"]["recent_entities"] (e.g. last_app,
         last_closed_app), most recent first, deduplicated.
      2. Then filled from system_tools.list_open_app_names() (currently
         running applications), skipping anything already included.
    Capped at 3 candidates, preferring 2 unless a third genuinely
    distinct strong candidate (from step 1) is already available.

    If fewer than 2 usable candidates turn up, this deliberately does
    NOT force a fake choice -- it falls back to the old generic
    message, `(question, [])`, so nothing regresses for the
    no-real-candidates case.

    Pure function w.r.t. ctx -- only reads recent_entities, never
    writes to it, same convention _resolve_app_target() above follows.
    """
    from sara.tools import system as system_tools

    entities = (ctx.get("context_state") or {}).get("recent_entities") or {}
    now = time.time()
    fresh = [
        entry for entry in entities.values()
        if entry.get("value") and now - entry.get("ts", 0) <= _CONTEXT_TTL_S
    ]
    fresh.sort(key=lambda entry: entry["ts"], reverse=True)

    candidates = []
    for entry in fresh:
        value = entry["value"]
        if value not in candidates:
            candidates.append(value)
    strong_count = len(candidates)

    if len(candidates) < 3:
        try:
            for name in system_tools.list_open_app_names():
                if name not in candidates:
                    candidates.append(name)
                if len(candidates) >= 3:
                    break
        except Exception:
            pass

    if len(candidates) < 2:
        return (f"Which app would you like me to {verb}?", [])

    cap = 3 if strong_count >= 3 else 2
    candidates = candidates[:cap]

    verb_phrase = _APP_CLARIFY_VERB_PHRASES.get(verb, f"{verb} karu")
    if len(candidates) == 2:
        question = f"{candidates[0]} ya {candidates[1]}, kaunsa {verb_phrase}?"
    else:
        question = f"{candidates[0]}, {candidates[1]} ya {candidates[2]}, kaunsa {verb_phrase}?"

    return (question, candidates)


def _remember_context(ctx, intent_name: str, slot_value: str) -> None:
    """
    Back-compat wrapper kept so existing call sites (_h_weather(),
    _h_news(), and _h_followup_query() itself) don't need to know about
    slot names at all -- it maps the intent name to its slot via
    _FOLLOWUP_SLOT_BY_INTENT (falling back to the intent name itself for
    any future caller that doesn't bother registering one) and tags the
    entry with that same intent so _h_followup_query() can still find it.
    """
    slot_name = _FOLLOWUP_SLOT_BY_INTENT.get(intent_name, intent_name)
    _remember_entity(ctx, slot_name, slot_value, followup_intent=intent_name)


# Maps a remembered intent name to the tool function that intent's own
# handler calls, so a follow-up can redo "the same kind of question"
# with a new slot value without duplicating each handler's own
# ack/status/error-handling logic.
_FOLLOWUP_TOOL_BY_INTENT = {
    "weather": web_tools.get_weather,
    "news": web_tools.get_news,
}


def _latest_followup_entity(ctx):
    """
    Among ctx["context_state"]["recent_entities"], return the most
    recently recorded, non-expired entry whose "intent" is one
    _h_followup_query() actually knows how to redo (see
    _FOLLOWUP_TOOL_BY_INTENT above) -- or None if nothing qualifies.

    BUGFIX vs. the old single-slot design: with several independent
    slots now live at once, "the most recent thing said" is no longer
    just "the one slot" -- it has to be resolved by comparing
    timestamps across slots. Without this, a followup_query right after
    e.g. opening an app (which records into last_app with no "intent")
    could otherwise have nothing to fall back to, or worse, could pick
    an arbitrary/stale weather-or-news slot instead of the genuinely
    most recent one.
    """
    entities = ctx["context_state"].get("recent_entities") or {}
    now = time.time()
    best = None
    for entry in entities.values():
        if entry.get("intent") not in _FOLLOWUP_TOOL_BY_INTENT:
            continue
        if now - entry.get("ts", 0) > _CONTEXT_TTL_S:
            continue
        if best is None or entry["ts"] > best["ts"]:
            best = entry
    return best


def _h_followup_query(match, ctx):
    if not match:
        return None
    state = _latest_followup_entity(ctx)
    if not state:
        return _quick(
            ctx, "I'm not sure what you're asking about — could you rephrase that?"
        )
    tool_fn = _FOLLOWUP_TOOL_BY_INTENT.get(state["intent"])
    if tool_fn is None:
        return _quick(
            ctx, "I'm not sure what you're asking about — could you rephrase that?"
        )
    new_slot = match.group(1).strip()
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    _remember_context(ctx, state["intent"], new_slot)
    is_news = state["intent"] == "news"
    return _quick(
        ctx,
        _run_activity(
            ctx,
            "news" if is_news else "weather",
            "Fetching news" if is_news else "Checking weather",
            "News ready" if is_news else "Weather ready",
            "Couldn't get news" if is_news else "Couldn't get weather",
            lambda: _call_with_timeout(tool_fn, new_slot),
        ),
    )



# ── Unresolved pronoun/reference detection (NEW) ────────────────────────
# Common Hindi/English pronoun & reference words that, on their own, tell
# us nothing -- but combined with a still-fresh entry in
# ctx["context_state"]["recent_entities"] (see _remember_entity() /
# _CONTEXT_TTL_S higher up in this file), strongly suggest the user means
# something they or Sara just talked about ("close it", "uska naam kya
# hai", "wo wala phir se kholo"). Matched at a word boundary, same
# discipline as _TOOL_SIGNAL_RE/_CHAT_SIGNAL_RE above, so this never
# fires on a substring inside an unrelated word.
_REFERENCE_HINT_RE = re.compile(
    r"\b("
    r"it|that one|this one|the other one|the same one|that|this|them|those|these"
    r"|uska|uski|uske|usko|usse|iska|iski|iske|isko|isse"
    r"|unka|unki|unke|unko|inka|inki|inke|inko"
    r"|wo|voh|woh|vo|ye|yeh"
    r"|waha|wahan|yaha|yahan"
    r")\b",
    re.IGNORECASE,
)


def _is_pronoun_reference(text: str) -> bool:
    """
    True if `text` contains an unresolved pronoun/reference word (see
    _REFERENCE_HINT_RE above) -- e.g. "that", "usko", "wo wala" -- with
    no attempt to resolve WHAT it refers to; that's the caller's job
    (see _resolve_app_target() and _route_chat_message() below, the two
    current call sites).

    Pulled out as its own tiny helper (rather than each call site
    matching _REFERENCE_HINT_RE directly) so there's exactly ONE source
    of truth for "what counts as an unresolved pronoun/reference" across
    the whole file -- _route_chat_message()'s chat-path detection and
    _resolve_app_target()'s fast-path-regex-handler detection now share
    it instead of drifting independently.

    Cheap, local, string-only regex match -- no LLM call, no I/O, safe
    to call on every fast-path handler invocation with no added latency.
    """
    return bool(_REFERENCE_HINT_RE.search(text or ""))


# Slot name (as written by _remember_entity()) -> human-readable label
# used when describing a recent entity back to the LLM below. An
# explicit whitelist, rather than reformatting the raw slot name, so a
# slot with an awkward internal key never leaks straight into the prompt
# unreadably.
_ENTITY_SLOT_LABELS = {
    "last_location": "location",
    "last_topic": "topic",
    "last_app": "application",
    "last_file": "file",
    "last_person": "person",
}


def _describe_recent_entities(context_state: dict) -> str:
    """
    Build a short, plain-text description of every still-fresh entry in
    context_state["recent_entities"] (see _remember_entity() /
    _CONTEXT_TTL_S higher up in this file) -- e.g.
    "application: chrome; location: mumbai" -- for injection into the
    chat prompt when the user's message contains an unresolved
    pronoun/reference. Returns "" (never None, so callers can use it
    directly in a plain `if` check, same as the existing memory_context
    truthy-check pattern elsewhere in this codebase) when there's
    nothing fresh to describe.

    Deliberately includes EVERY still-fresh slot, not just the single
    most recent one (unlike _latest_followup_entity() above, which must
    pick exactly one because it feeds a specific tool call) -- the LLM
    itself is far better placed than this pure-string function to work
    out which of several recent things ("the app you opened" vs. "the
    city you asked about") a given pronoun actually refers to.
    """
    entities = (context_state or {}).get("recent_entities") or {}
    now = time.time()
    parts = []
    for slot_name, entry in entities.items():
        if now - entry.get("ts", 0) > _CONTEXT_TTL_S:
            continue
        value = entry.get("value")
        if not value:
            continue
        label = _ENTITY_SLOT_LABELS.get(slot_name, slot_name.replace("_", " "))
        parts.append(f"{label}: {value}")
    return "; ".join(parts)

