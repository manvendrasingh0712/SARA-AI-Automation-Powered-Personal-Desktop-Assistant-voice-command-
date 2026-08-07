"""
sara.core.planning.trigger
The single latency-critical gate that decides whether a "chat"-intent
user message is even worth the cost of a planning LLM call.

DESIGN CONTRACT (do not weaken without re-reading this):
---------------------------------------------------------
This module is pure string processing -- no I/O, no LLM call, no lock
on the hot path, no dict/set allocation per call beyond what a handful
of `in` checks and pre-compiled regex .search() calls need. It MUST
stay cheap enough to run on every single unmatched ("chat" intent) voice
command with no perceptible cost, because it runs unconditionally, on
the calling (voice-loop) thread, before any decision about whether to
spend a real network round-trip is made.

should_attempt_plan() returns True only when the message shows a
genuine, cheap-to-detect signal of needing more than one action:

  1. Two or more DISTINCT tool categories are plausibly referenced
     (e.g. both "remind" and "weather" keywords present) -- a
     single-tool message, however phrased, can never satisfy this on
     its own.

     -- OR --

  2. Exactly one tool category is referenced, but an explicit
     multi-action / sequencing cue is also present (" and then ",
     "after that", ", phir ", Hinglish "uske baad", etc.) -- this
     catches "remind me to call mom and then what's the weather" where
     the second clause's tool keyword might overlap with the first
     category's gate, or where a single category's keyword legitimately
     appears twice across two clauses.

     -- OR --

  3. Exactly one tool category is referenced, but a weaker list-style
     conjunction (", and " / ", aur ") is present -- catches phrasing
     like "set a reminder for 6pm, and also let me know the weather"
     where the second action's own keyword gate might not fire cleanly.

A plain single-tool message ("what's the weather in Jaipur") satisfies
none of the three and returns False immediately, at which point the
EXISTING resolve_tool_call() path runs completely unchanged -- this
module adds zero branches to that path's execution, only a preceding
cheap check that rejects it before planning is ever considered.

This module intentionally does NOT import sara.core.tool_router's
TOOLS_SCHEMA or TOOL_NAME_TO_INTENT to build its keyword table --
duplicating a small, hand-tuned keyword set here (deliberately
overlapping in spirit with tool_router.py's own _TOOL_KEYWORD_GATE, but
independently maintained) keeps this module import-light and avoids
coupling its behavior to any future change in the tool schema's
descriptions. If a new tool is added to tool_router.py, its keywords
should be added to _TOOL_CATEGORY_KEYWORDS below as a deliberate,
reviewed edit -- not inherited silently.

PERFORMANCE NOTE
------------------
_matched_categories() short-circuits as soon as it finds evidence of a
SECOND distinct category (the strong-tier trigger condition), so a
message that clearly needs planning does not pay the cost of scanning
every remaining category's keyword set once its answer is already
determined. A message with zero or one category match still scans the
full table (unavoidable, since "no more categories exist" can only be
known by checking all of them), but this remains a bounded, small
number of `in` checks -- cheaper than a single regex compile, let alone
an LLM round trip.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, FrozenSet, Set, Tuple

logger = logging.getLogger("sara.core.planning.trigger")


# ══════════════════════════════════════════════════════════════════════
# Per-tool-category keyword gates
# ══════════════════════════════════════════════════════════════════════
# Each tool name maps to a frozenset of lowercase substrings that must
# have at least one hit for that category to count as "plausibly
# referenced." Deliberately conservative/over-inclusive per category
# (same tradeoff tool_router.py's has_probable_tool_intent() already
# accepts) -- a false positive here only costs one bounded planning call
# that a multi-tool request would have needed anyway; a false negative
# means a genuinely multi-step request gets treated as single-tool
# (falls back to today's behavior, i.e. no regression, just no
# improvement for that one phrasing).
_TOOL_CATEGORY_KEYWORDS: Dict[str, FrozenSet[str]] = {
    "weather": frozenset({"weather", "temperature", "rain", "forecast", "mausam", "मौसम"}),
    "news": frozenset({"news", "headline", "khabar", "khabren", "खबर"}),
    "web_search": frozenset(
        {"search for", "look up", "google", "find out", "search karo", "dhundo", "dhoondo"}
    ),
    "open_url": frozenset({"open url", "visit", "http://", "https://", "website", "site kholo"}),
    "play_youtube": frozenset({"youtube"}),
    "play_spotify": frozenset({"spotify"}),
    "screenshot_describe": frozenset({"screenshot", "screen dikhao", "screen batao"}),
    "clipboard_read": frozenset({"clipboard"}),
    "clipboard_write": frozenset({"clipboard"}),
    "open_app": frozenset({"open ", "launch ", "start ", "kholo", "chalao", "chalu karo"}),
    "close_app": frozenset({"close ", "quit ", "exit ", "terminate ", "band karo", "band kro"}),
    "calculator": frozenset({"calculate", "what is", "how much", "jod", "ghata", "guna", "bhaag"}),
    "reminder_add": frozenset({"remind", "reminder", "yaad dila", "yaad dilana"}),
    "set_timer": frozenset({"timer", "countdown"}),
    "take_note": frozenset({"note down", "jot down", "note karo", "note kar lo"}),
    "calendar_create": frozenset({"schedule a meeting", "schedule an event", "meeting set karo"}),
    "run_routine": frozenset({"routine chalao", " routine"}),
}

# ══════════════════════════════════════════════════════════════════════
# Sequencing / multi-action cue detection
# ══════════════════════════════════════════════════════════════════════
# Pre-compiled once at import time -- these are the connective phrases
# that signal "there is a second, distinct action coming," independent
# of whether the two clauses happen to share a tool category. English
# and Hinglish/Hindi variants both included, matching this codebase's
# consistent bilingual support elsewhere (see patterns.py, prompt.py).
_SEQUENCING_PATTERNS: Tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\band then\b",
        r"\bafter that\b",
        r"\bafterwards\b",
        r"\bthen also\b",
        r",\s*and then\b",
        r",\s*then\b",
        r"\bonce (?:that'?s|that is|you'?re) done\b",
        r"\bfollowed by\b",
        r"\bnext,?\s+(?:can you|please|also)\b",
        r"\buske baad\b",
        r"\bus ke baad\b",
        r"\bphir\b",
        r"\baur (?:phir|uske baad)\b",
        r"\btab\b.{0,3}\bphir\b",
        r"\bउसके बाद\b",
        r"\bफिर\b",
    )
)

# A bare comma-plus-conjunction is a weaker signal than the explicit
# phrases above (a single clause can legitimately contain "X, Y, and Z"
# as a plain list with no second ACTION implied) -- kept separate so it
# only counts alongside at least one genuine keyword-category hit,
# never as a standalone trigger on its own.
_WEAK_CONJUNCTION_RE = re.compile(r",\s*(?:and|aur)\s+", re.IGNORECASE)

# A second, independent weak signal: "also"/"bhi" appearing after the
# first clause, without a comma -- e.g. "remind me to call mom also
# check the weather." Kept separate from the comma-conjunction check
# since it has a slightly different (no-comma) shape.
_ALSO_CUE_RE = re.compile(r"\b(?:also|bhi)\b", re.IGNORECASE)

_MIN_DISTINCT_CATEGORIES_FOR_AUTO_TRIGGER = 2

# Guards against a pathologically long message (e.g. a misheard STT
# transcript that ran on) being scanned against every keyword/regex
# unnecessarily -- real voice commands are short; anything past this
# length is still scanned (never silently dropped/truncated for
# correctness), but logged at debug level since it's an unusual case
# worth being able to spot in logs.
_UNUSUALLY_LONG_INPUT_CHARS = 500


def _matched_categories(lowered_text: str) -> FrozenSet[str]:
    """
    Returns the set of tool-category names whose keyword gate has at
    least one substring hit in `lowered_text`. Pure substring scanning,
    no regex -- mirrors the cost profile of tool_router.py's own
    has_probable_tool_intent() pre-gate.

    Short-circuits as soon as 2 distinct categories are found (the
    strong-tier trigger threshold), since no caller of this function
    needs to know about a 3rd, 4th, etc. category once the strong-tier
    condition is already satisfied.
    """
    hits: Set[str] = set()
    for category, keywords in _TOOL_CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lowered_text:
                hits.add(category)
                break
        if len(hits) >= _MIN_DISTINCT_CATEGORIES_FOR_AUTO_TRIGGER:
            return frozenset(hits)
    return frozenset(hits)


def _has_sequencing_cue(lowered_text: str) -> bool:
    """Returns True if an explicit multi-action sequencing phrase is present."""
    return any(pattern.search(lowered_text) for pattern in _SEQUENCING_PATTERNS)


def _has_weak_conjunction(lowered_text: str) -> bool:
    """Returns True if a comma-plus-'and'/'aur' list-style conjunction is present."""
    return bool(_WEAK_CONJUNCTION_RE.search(lowered_text))


def _has_also_cue(lowered_text: str) -> bool:
    """Returns True if a standalone 'also'/'bhi' cue is present."""
    return bool(_ALSO_CUE_RE.search(lowered_text))


def should_attempt_plan(user_input: str) -> bool:
    """
    Decides whether `user_input` is worth a bounded planning LLM call,
    as opposed to going straight to the existing single-tool
    resolve_tool_call() path.

    Returns True only when the message shows a genuine, cheap-to-detect
    signal of needing more than one action:

      - Two or more DISTINCT tool categories are plausibly referenced, OR
      - Exactly one category is referenced AND an explicit sequencing
        cue ("and then", "uske baad", "phir", ...) is also present, OR
      - Exactly one category is referenced AND a weak list-conjunction
        (", and " / ", aur ") or a standalone "also"/"bhi" cue is
        present alongside it.

    Returns False for empty/whitespace-only input, input with zero
    category hits, or input with exactly one category hit and none of
    the above cues -- this is the overwhelming majority of real voice
    commands, and for all of them this function costs a handful of `in`
    checks and (at most) a handful of compiled-regex .search() calls,
    with no LLM call, no lock, and no I/O of any kind.

    This function deliberately does NOT consult
    tool_router.has_probable_tool_intent() -- callers in
    sara/orchestrator/intent_handlers.py are expected to call this ONLY
    from within the branch that already established `intent == "chat"`
    (i.e. the fast-path regex matcher found nothing), which is the same
    precondition has_probable_tool_intent() itself is used under today.
    """
    if not user_input or not user_input.strip():
        return False

    stripped = user_input.strip()
    if len(stripped) > _UNUSUALLY_LONG_INPUT_CHARS:
        logger.debug(
            "Planning trigger: unusually long input (%d chars) -- scanning anyway.",
            len(stripped),
        )

    lowered = stripped.lower()
    categories = _matched_categories(lowered)

    if not categories:
        return False

    if len(categories) >= _MIN_DISTINCT_CATEGORIES_FOR_AUTO_TRIGGER:
        logger.debug(
            "Planning trigger: %d distinct categories matched (%s) for input=%r",
            len(categories),
            sorted(categories),
            user_input,
        )
        return True

    if _has_sequencing_cue(lowered):
        logger.debug(
            "Planning trigger: 1 category (%s) + sequencing cue for input=%r",
            sorted(categories),
            user_input,
        )
        return True

    if _has_weak_conjunction(lowered) or _has_also_cue(lowered):
        logger.debug(
            "Planning trigger: 1 category (%s) + weak conjunction/also-cue "
            "for input=%r",
            sorted(categories),
            user_input,
        )
        return True

    return False


def explain_trigger_decision(user_input: str) -> Dict[str, object]:
    """
    Diagnostic helper (not used on the hot path) that returns a
    structured breakdown of why should_attempt_plan() would return True
    or False for a given input -- categories matched, which cues fired.
    Intended for debugging/tuning the keyword tables and for tests that
    want to assert on the REASON a trigger fired, not just the boolean.

    Deliberately re-implements the full (non-short-circuited) category
    scan rather than reusing _matched_categories()'s early-exit version,
    since a diagnostic tool should show the complete picture regardless
    of the performance shortcut the hot path takes.
    """
    if not user_input or not user_input.strip():
        return {
            "would_trigger": False,
            "categories": [],
            "sequencing_cue": False,
            "weak_conjunction": False,
            "also_cue": False,
        }

    lowered = user_input.strip().lower()
    all_categories: Set[str] = set()
    for category, keywords in _TOOL_CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            all_categories.add(category)

    sequencing = _has_sequencing_cue(lowered)
    weak_conjunction = _has_weak_conjunction(lowered)
    also_cue = _has_also_cue(lowered)

    would_trigger = len(all_categories) >= _MIN_DISTINCT_CATEGORIES_FOR_AUTO_TRIGGER or (
        len(all_categories) == 1 and (sequencing or weak_conjunction or also_cue)
    )

    return {
        "would_trigger": would_trigger,
        "categories": sorted(all_categories),
        "sequencing_cue": sequencing,
        "weak_conjunction": weak_conjunction,
        "also_cue": also_cue,
    }