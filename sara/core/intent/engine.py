"""
sara.core.intent.engine
Pattern compilation (with the groupless-merge optimization) and the
public detect_intent() entry point, including its LRU cache wrapper.
"""

import difflib
import re
from functools import lru_cache
from typing import Optional, Tuple

# ── Pattern table ──────────────────────────────────────────────────────
# Each entry: (intent_name, [pattern_strings])
# Order matters: more specific patterns must come before broad fallbacks.
from .patterns import _INTENT_PATTERNS, _INTENT_GATES



def _merge_groupless(patterns):
    """
    Collapse a multi-pattern intent group into a single compiled
    alternation regex when it's provably safe to do so — i.e. when
    NONE of its patterns contain a capturing group. Cuts N separate
    .search() engine invocations down to 1 for pure keyword/phrase
    toggle intents. Intents with any capturing pattern are compiled
    individually, unchanged, so match.group(1) semantics never shift.
    """
    compiled_each = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
    if len(patterns) > 1 and all(c.groups == 0 for c in compiled_each):
        merged = "|".join(f"(?:{p})" for p in patterns)
        return [re.compile(merged, re.IGNORECASE | re.UNICODE)]
    return compiled_each


# Pre-compile all patterns.
# Groupless multi-pattern intents are auto-merged into a single
# alternation regex via _merge_groupless() to cut the number of regex
# engine invocations per detect_intent() call (see docstring above).
# Intents needing capture groups are compiled individually, exactly as
# before — order, patterns, and flags all unchanged from the source
# _INTENT_PATTERNS table.
_COMPILED_PATTERNS = []
_ROUTES = ()

# ── Typo-tolerance vocabulary (rescue pass only — see _correct_typos) ──
# Built automatically from _INTENT_GATES rather than a hand-maintained
# list, so it can never drift out of sync with the real trigger words:
# every single-word gate keyword of length >= _TYPO_MIN_WORD_LEN is a
# candidate correction target. Multi-word gate entries (e.g. "log off")
# are skipped — word-by-word fuzzy correction can't safely reassemble a
# phrase, and gate entries under the length floor (e.g. "tab", "f5")
# are skipped because short strings produce unreliable fuzzy matches
# (too many unrelated words sit within one edit of them).
_TYPO_MIN_WORD_LEN = 5
_TYPO_CUTOFF = 0.8
_TRIGGER_VOCAB = set()


def _rebuild_routes() -> None:
    """
    (Re)builds _COMPILED_PATTERNS and _ROUTES from the current contents
    of _INTENT_PATTERNS/_INTENT_GATES. Called once at import time below,
    and again by register_intent() whenever a new intent is added at
    runtime (e.g. by a sara/skills/ plugin during startup) — this is the
    only thing that needs re-running for a freshly registered intent to
    actually start matching; nothing else in this module needs to change.
    """
    global _COMPILED_PATTERNS, _ROUTES, _TRIGGER_VOCAB
    _COMPILED_PATTERNS = [
        (name, _merge_groupless(patterns))
        for name, patterns in _INTENT_PATTERNS
    ]
    _TRIGGER_VOCAB = {
        kw
        for gate in _INTENT_GATES.values()
        for kw in gate
        if " " not in kw and len(kw) >= _TYPO_MIN_WORD_LEN
    }
    # ── Hot-path route table (Phase 3) ──────────────────────────────────
    # Joins (intent_name, compiled_patterns, gate) into one pre-built tuple
    # so the per-call hot loop never has to do a dict lookup
    # (_INTENT_GATES.get(...)) while iterating — the gate for each intent
    # already sits right next to its compiled patterns. Rebuilt from the
    # exact same _INTENT_GATES / _COMPILED_PATTERNS data every time, so it
    # cannot drift out of sync with them.
    _ROUTES = tuple(
        (name, compiled_list, _INTENT_GATES.get(name))
        for name, compiled_list in _COMPILED_PATTERNS
    )


_rebuild_routes()


def register_intent(name: str, patterns, gate=None) -> None:
    """
    Registers a new fast-path intent at runtime — the mechanism
    sara/skills/__init__.py's plugin auto-discovery uses so a brand-new
    skill file can add itself to detect_intent()'s matcher without ever
    editing this module or sara/core/intent/patterns.py by hand.

    `patterns` is a list of regex strings (same shape as an
    _INTENT_PATTERNS entry). `gate`, if given, is a tuple of lowercase
    substrings used as the cheap pre-filter (same shape as an
    _INTENT_GATES entry) — omit it only if no safe substring gate exists
    for this intent (matches the existing calculator/open_app/close_app
    convention in patterns.py).

    Safe to call more than once for the same name (replaces its patterns/
    gate rather than duplicating the entry) and safe to call from
    anywhere, including inside a try/except during optional-plugin
    loading — it never raises for a well-formed call, and a bad `patterns`
    list will surface immediately as a re.error when this rebuilds, not
    silently later.
    """
    _INTENT_PATTERNS[:] = [(n, p) for n, p in _INTENT_PATTERNS if n != name]
    # Inserted at the FRONT, not appended — patterns.py's own comments
    # note that open_app/close_app's catch-all patterns (last in the
    # static table) are deliberately greedy ("close|quit|exit|kill|end ...")
    # and WILL match an unrelated phrase that merely contains one of those
    # words as a substring if checked first (confirmed: a naive append-at-
    # end put a test intent after close_app, and "hello skill world" was
    # wrongly matched by close_app via its embedded "kill world"). Runtime-
    # registered skill intents must be checked before those catch-alls.
    _INTENT_PATTERNS.insert(0, (name, list(patterns)))
    if gate is not None:
        _INTENT_GATES[name] = tuple(gate)
    _rebuild_routes()
    _detect_intent_cached.cache_clear()


def _validate_intent_tables():
    """
    One-time startup sanity checks (debug builds only — stripped when
    Python is run with -O / -OO, so this costs nothing in such a build).
    Verifies every _INTENT_GATES key corresponds to a real intent (catches
    a typo that would silently disable a gate forever) and flags any exact
    duplicate pattern string compiled within the same intent's own
    alternation (a duplicate there is dead/unreachable). Never fires in
    normal operation against the current tables — it exists purely to
    catch a future editing mistake before it ships, and does not alter
    matching behavior in any way.
    """
    intent_names = {name for name, _ in _INTENT_PATTERNS}
    for gated_name in _INTENT_GATES:
        assert gated_name in intent_names, (
            f"_INTENT_GATES has an entry for unknown intent {gated_name!r}"
        )
    for name, patterns in _INTENT_PATTERNS:
        assert len(patterns) == len(set(patterns)), (
            f"duplicate pattern string(s) detected in intent {name!r}"
        )


if __debug__:
    _validate_intent_tables()

# Bounded size for the repeated-command memoization cache below. Voice
# commands repeat often ("what time is it", "open chrome", "play music"),
# but arbitrary "chat" fallback text (ordinary conversation) also gets
# cached and mostly never repeats — bounding the cache keeps memory flat
# over a long-running session instead of growing without limit.
_INTENT_CACHE_SIZE = 256


@lru_cache(maxsize=_INTENT_CACHE_SIZE)
def _detect_intent_cached(text: str) -> Tuple[str, Optional[re.Match]]:
    """
    Core matching routine behind detect_intent(), memoized with an LRU
    cache keyed on the exact (already-stripped) input string.

    Regex matching over an immutable string is a pure function of that
    string's contents, so an identical repeated command can only ever
    produce the same (intent_name, match) result — caching it is safe
    and lets repeats skip regex evaluation over all ~100 pattern groups
    entirely instead of re-running them.
    """
    text_lower = text.lower()

    for intent_name, compiled_list, gate in _ROUTES:
        if gate is not None:
            hit = False
            for kw in gate:
                if kw in text_lower:
                    hit = True
                    break
            if not hit:
                continue
        for pattern in compiled_list:
            match = pattern.search(text)
            if match:
                return intent_name, match

    return "chat", None


_TYPO_WORD_RE = re.compile(r"[.,!?;:]+$")


def _correct_typos(text: str) -> str:
    """
    Best-effort single-word typo/homophone correction against
    _TRIGGER_VOCAB (e.g. "whether" -> "weather", "restrt" -> "restart").

    Deliberately conservative and deliberately NOT used on the normal
    matching path: it only runs as a second-attempt rescue (see
    detect_intent() below) after the exact-spelling pass has already
    failed to find any intent, so it can never change the outcome for
    text that already matches something -- existing behavior for every
    currently-working command is 100% unaffected. Each word is corrected
    independently and only if it's close enough (_TYPO_CUTOFF) to a
    single vocabulary word; ordinary short/common words are left alone
    (see _TYPO_MIN_WORD_LEN), which is what keeps ordinary conversation
    ("I don't know whether to go", "I want some butter") from being
    mangled into a false command -- ambiguous or unrelated text simply
    comes back unchanged, or changed in a way that still fails to match
    any regex's location/structure requirements.
    """
    words = text.split()
    if not words:
        return text
    changed = False
    out = []
    for w in words:
        bare = _TYPO_WORD_RE.sub("", w.lower())
        if len(bare) < _TYPO_MIN_WORD_LEN or bare in _TRIGGER_VOCAB:
            out.append(w)
            continue
        match = difflib.get_close_matches(
            bare, _TRIGGER_VOCAB, n=1, cutoff=_TYPO_CUTOFF
        )
        if match:
            out.append(match[0])
            changed = True
        else:
            out.append(w)
    return " ".join(out) if changed else text


def detect_intent(text: str) -> Tuple[str, Optional[re.Match]]:
    """
    Detect the intent of a user command via fast local keyword matching.

    Tries the text exactly as given first. Only if that finds nothing
    (would return "chat") does it retry once against a typo-corrected
    version of the text (see _correct_typos) -- a rescue pass for common
    single-word typos/homophones of real trigger words (e.g. "whether"
    for "weather", "restrt" for "restart"). This ordering means the
    rescue pass can only ever turn a would-be "chat" fallback into a
    real intent; it can never override or change a match that already
    succeeded on the original text.

    Returns:
        (intent_name, match_object)
        Falls through to ("chat", None) when no intent matches, even
        after the typo-correction retry.
    """
    stripped = text.strip()
    intent_name, match = _detect_intent_cached(stripped)
    if intent_name != "chat":
        return intent_name, match

    corrected = _correct_typos(stripped)
    if corrected == stripped:
        return intent_name, match
    return _detect_intent_cached(corrected)


# Convenience passthroughs for tests/tools — do not affect matching
# behavior. cache_clear() resets memoized state; cache_info() reports
# hits/misses/maxsize/currsize for observability.
detect_intent.cache_clear = _detect_intent_cached.cache_clear
detect_intent.cache_info = _detect_intent_cached.cache_info