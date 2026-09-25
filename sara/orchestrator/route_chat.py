"""
sara.orchestrator.route_chat

Chat-route selection (LATENCY FIX): a pure, LLM-free heuristic that
picks exactly one of "plan" / "tool" / "chat" for a "chat"-intent
message, so a single turn costs at most 2 LLM round-trips instead of
3. Also the misfire-retry helper that gives the LLM tool router one
more shot at a fast-path handler's own "I don't trust the argument I
captured" reply. Split out of the former monolithic
intent_handlers.py -- see that module's docstring, in particular the
"LLM ROUTING IS MUTUALLY EXCLUSIVE" section.
"""
import re
from typing import Optional

from config import Config

from ._shared_state import (
    resolve_tool_call,
    build_fake_match,
    TOOL_NAME_TO_INTENT,
    _HAS_PLANNING,
    try_plan_and_execute,
    should_attempt_plan,
)
from .context_tracking import _is_pronoun_reference, _describe_recent_entities

# ── Chat-route selection (LATENCY FIX) ──────────────────────────────────
# Before this, a "chat"-intent message (i.e. the fast-path regex matcher
# found nothing) could cost up to THREE sequential LLM round-trips:
#
#   1. try_plan_and_execute()          -> planner LLM call
#   2. resolve_tool_call()             -> tool-router LLM call
#   3. brain.generate_response_stream() -> final chat LLM call
#
# because the planner block and the tool-router block were two INDEPENDENT
# `if` statements: whenever the planner engaged and then returned None (no
# usable plan, or a plan that produced nothing), execution fell straight
# through into the tool-router, and from there into plain chat.
#
# _route_chat_message() makes those two routing stages MUTUALLY EXCLUSIVE.
# It is a pure string heuristic -- zero LLM calls, zero I/O -- that picks
# exactly ONE of:
#
#   "plan" -> only the planner is attempted. If it yields nothing we go
#             straight to plain chat; the tool-router is NOT tried.
#   "tool" -> only the single-tool LLM resolver is attempted. Same deal:
#             no plan attempt, and a miss falls straight to plain chat.
#   "chat" -> neither routing stage runs at all. A conversational message
#             ("what do you think about X", "tell me a joke") now costs
#             exactly ONE LLM call instead of two.
#
# Worst case therefore drops from 3 sequential LLM calls to 2, and the
# common conversational case drops from 2 to 1.
#
# Trade-off, stated plainly: a message that the heuristic routes to "tool"
# but that was really a multi-step request no longer gets a second chance
# at the planner (and vice versa). That is the deliberate price of the
# latency win -- the fallback in both cases is a normal chat answer, not
# an error.

# Upper word count for anything to be considered a command at all. Real
# tool requests are short imperatives ("open chrome", "remind me at 6 to
# call mom"); a long sentence is nearly always conversation.
_TOOL_SIGNAL_MAX_WORDS = 16

# Action verbs that signal "do something", EN + Hinglish. Matched only at
# a word boundary so "opening hours" / "search engine kya hai" don't trip
# it purely by containing the substring.
_TOOL_SIGNAL_RE = re.compile(
    r"\b("
    r"open|launch|start|run|close|quit|kill|stop|restart|switch"
    r"|play|pause|resume|skip|next|mute|unmute"
    r"|search|google|youtube|look\s+up|find|download"
    r"|set|change|increase|decrease|raise|lower|turn"
    r"|remind|schedule|book|create|make|add|delete|remove|clear"
    r"|send|message|call|email|type|press|copy|paste"
    r"|screenshot|lock|shutdown|sleep|restart"
    r"|kholo|khol|chalao|chala|band|bajao|baja|dhundo|dhoondo"
    r"|bhejo|likho|banao|karo|kar\s+do|set\s+karo|yaad\s+dila"
    r")\b",
    re.IGNORECASE,
)

# Conversational openers that OVERRIDE the verb match above. "what is the
# best way to open a jar" contains "open" but is obviously a question, not
# a command -- these keep such messages out of the tool-router entirely.
_CHAT_SIGNAL_RE = re.compile(
    r"^\s*("
    r"who|what|why|how|when|which|whose|whom"
    r"|tell\s+me|explain|describe|define|summar|compare|suggest|recommend"
    r"|do\s+you|are\s+you|can\s+you\s+explain|should\s+i|is\s+it|was\s+it"
    r"|kya|kyun|kyu|kaise|kaun|kab|kahan|batao|bata|samjhao|samjha"
    r")\b",
    re.IGNORECASE,
)


def _tool_router_available() -> bool:
    """
    Cheap, LLM-free check: is the single-tool LLM router
    (resolve_tool_call() + build_fake_match() + TOOL_NAME_TO_INTENT)
    actually usable right now? Factored out of _route_chat_message()
    below (which used to inline this exact check) so
    _retry_via_tool_router() can share the identical condition instead
    of a second, easily-drifting copy of it.
    """
    return bool(
        getattr(Config, "TOOL_CALLING_ENABLED", True)
        and resolve_tool_call is not None
        and build_fake_match is not None
        and TOOL_NAME_TO_INTENT
    )


def _route_chat_message(user_input: str, context_state: dict = None) -> tuple:
    """
    Decide which SINGLE routing stage (if any) a "chat"-intent message
    gets, and whether this turn should carry an injected "what the user
    is likely referring to" hint into the final chat prompt.

    Returns a (route, context_hint) 2-tuple: route is exactly one of
    "plan", "tool", or "chat"; context_hint is None unless an unresolved
    pronoun/reference ("it", "uska", "wo", ...) was found alongside at
    least one still-fresh entry in context_state["recent_entities"], in
    which case it's the short plain-text description built by
    _describe_recent_entities() above.

    context_state is OPTIONAL and defaults to None -- a caller that
    doesn't pass it simply never gets a context_hint, and this
    function's plan/tool/chat decision behaves EXACTLY as it did before
    this parameter existed.

    Pure function: makes no LLM call, touches no network, no DB, and
    only READS (never writes) context_state -- so it remains safe to
    call on every chat turn and trivially unit-testable.

    Order matters. The plan check runs first because a genuine multi-action
    message ("open chrome and then play some music") would ALSO match the
    tool-verb heuristic below, and the planner is the correct handler for
    it; the tool-router could only ever execute the first of the two.
    """
    text = (user_input or "").strip()
    if not text:
        return "chat", None

    # ── 0. Unresolved pronoun/reference to something recent? ───────────
    # Checked FIRST, ahead of the plan/tool heuristics below, and
    # deliberately SHORT-CIRCUITS past them rather than touching their
    # internal logic -- a message like "close it" or "uska naam kya hai"
    # would otherwise be routed to the tool-router or planner purely on
    # the strength of a verb, with no way for either to resolve what
    # "it"/"uska" actually means. Going straight to chat -- with the
    # recent-entity hint injected into the prompt -- gives the LLM a
    # real chance instead of guessing blind or making the user repeat
    # themselves.
    #
    # Gated on context_state actually having something fresh to offer
    # (_describe_recent_entities() returns "" otherwise, which is
    # falsy), so a genuinely context-free "how's it going" costs nothing
    # extra and behaves exactly as before this feature existed -- still
    # just a plain string match, no LLM call, no added latency.
    if context_state and _is_pronoun_reference(text):
        context_hint = _describe_recent_entities(context_state)
        if context_hint:
            return "chat", context_hint

    # ── 1. Multi-step plan? ─────────────────────────────────────────────
    planning_available = (
        _HAS_PLANNING
        and try_plan_and_execute is not None
        and should_attempt_plan is not None
        and getattr(Config, "PLANNING_ENABLED", True)
    )
    if planning_available:
        try:
            wants_plan = should_attempt_plan(text)
        except TypeError:
            # Tolerate an older/newer signature that also takes Config,
            # rather than hard-failing the whole routing decision on it.
            try:
                wants_plan = should_attempt_plan(text, Config)
            except Exception:  # noqa: BLE001
                wants_plan = False
        except Exception:  # noqa: BLE001
            # A broken trigger must degrade to "no plan", never to a
            # crashed command -- same defensive posture as every other
            # optional-feature call site in this module.
            wants_plan = False
        if wants_plan:
            return "plan", None

    # ── 2. Single tool? ─────────────────────────────────────────────────
    if not _tool_router_available():
        return "chat", None

    if len(text.split()) > _TOOL_SIGNAL_MAX_WORDS:
        return "chat", None
    if _CHAT_SIGNAL_RE.match(text):
        return "chat", None
    if _TOOL_SIGNAL_RE.search(text):
        return "tool", None

    # ── 3. Neither. Straight to plain chat, one LLM call total. ─────────
    return "chat", None


def _retry_via_tool_router(user_input: str, ctx: dict, brain) -> Optional[str]:
    """
    Give the smarter LLM tool router (resolve_tool_call()) ONE genuine
    second attempt at the SAME original user_input, for the specific
    case where a fast-path regex handler matched but came back with its
    own "I don't trust the argument I captured" signal (see
    _LikelyMisfireReply above) -- e.g. "close that" where the regex
    grabbed a pronoun no fast-path handler could resolve on its own,
    but real function-calling parsing the full sentence might.

    Mirrors the chat_route == "tool" branch in _handle_command() below
    EXACTLY (same resolve_tool_call() -> TOOL_NAME_TO_INTENT ->
    build_fake_match() -> _INTENT_HANDLERS chain) -- kept as its own
    function so the two call sites can't silently drift apart, and so
    there's a single obvious place gating on _tool_router_available()
    and swallowing this path's own failures.

    Returns the tool handler's result string, or None if the router
    isn't available, couldn't resolve anything, or the resolved handler
    itself declined/raised -- callers MUST treat None as "no better
    answer available" and fall back to whatever they already had, never
    as a reason to error out or go silent.
    """
    if not _tool_router_available():
        return None
    # LOCAL IMPORT (NEW, structural only -- not a behavior change): this
    # module used to be part of the same monolithic file as
    # _INTENT_HANDLERS. Now that _INTENT_HANDLERS lives in
    # sara.orchestrator.dispatcher, and dispatcher.py itself needs
    # _route_chat_message and _retry_via_tool_router from THIS module,
    # a module-level `from .dispatcher import _INTENT_HANDLERS` here
    # would be a circular import. Deferring it to call time (dispatcher
    # is always fully loaded by the time any real command is handled)
    # avoids that without changing what this function does or returns.
    from .dispatcher import _INTENT_HANDLERS
    try:
        resolved = resolve_tool_call(user_input, brain.model_name)
        tool_name = resolved.get("name")
        tool_args = resolved.get("arguments", {})
        mapped_intent = TOOL_NAME_TO_INTENT.get(tool_name)
        if not mapped_intent:
            return None
        tool_handler = _INTENT_HANDLERS.get(mapped_intent)
        if tool_handler is None:
            return None
        fake_match = build_fake_match(tool_name, tool_args)
        return tool_handler(fake_match, ctx)
    except Exception as e:
        print(f"[ToolRouter] misfire-retry resolution failed: {e}")
        return None

