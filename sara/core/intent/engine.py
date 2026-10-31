"""
sara.core.intent.engine
Pattern compilation (with the groupless-merge optimization) and the
public detect_intent() entry point, including its LRU cache wrapper.
"""

import difflib
import re
import threading
import unicodedata
from functools import lru_cache
from typing import Optional, Tuple

# ── Pattern table ──────────────────────────────────────────────────────
# Each entry: (intent_name, [pattern_strings])
# Order matters: more specific patterns must come before broad fallbacks.
from .patterns import _INTENT_PATTERNS, _INTENT_GATES, _NON_APP_TOKENS


_ACTION_VERBS = (
    r"open|launch|start|run|restart|reboot|shutdown|shut\s+down|close|quit|kill|"
    r"delete|remove|clear|format|mute|unmute|lock|unlock|enable|disable|turn\s+on|"
    r"turn\s+off|play|pause|stop|install|uninstall|send|submit|purchase|buy|exit|"
    r"leave|log\s+off|sign\s+out|forget|erase|khol(?:o|na|de|do|dijiye)?|chalu|"
    r"shuru|bandh?|mita|hata|baja|rok"
)

_NEGATED_ACTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.UNICODE)
    for pattern in (
        rf"^\s*(?:(?:please|sara|hey\s+sara)\s+)*(?:do\s+not|don't|dont|never(?:\s+ever)?|must\s+not|should(?:\s+not|'t)|can't|cannot|won't|will\s+not|not)\s+(?:(?:you|please|ever|again|just)\s+)*(?:{_ACTION_VERBS})\b",
        rf"\b(?:i|we|you)\s+(?:do\s+not|don't|dont|never|must\s+not|should(?:\s+not|'t)|can't|cannot|won't|will\s+not)\s+(?:(?:really|ever)\s+)?(?:(?:want|need|allow|expect)\s+(?:(?:you|me|it)\s+)?to\s+)?(?:{_ACTION_VERBS})\b",
        rf"\b[\w.+-]+(?:\s+[\w.+-]+){{0,3}}\s+(?:ko\s+)?(?:mat|nahi|nahin|na)\s+(?:please\s+)?(?:{_ACTION_VERBS})\b",
        rf"\b(?:mat|nahi|nahin|na)\s+(?:please\s+)?(?:{_ACTION_VERBS})\b",
        r"\b[\w.+-]+(?:\s+[\w.+-]+){0,3}\s+(?:ko\s+)?(?:खोल|चालू|शुरू|बंद|रीस्टार्ट|रिस्टार्ट|डिलीट|मिटा|हटा|म्यूट|लॉक|प्ले|रोक)\s*(?:मत|नहीं|नही|ना)\b",
        r"\b[\w.+-]+(?:\s+[\w.+-]+){0,3}\s+(?:को\s*)?(?:मत|नहीं|नही|ना)\s*(?:खोल|चालू|शुरू|बंद|रीस्टार्ट|रिस्टार्ट|डिलीट|मिटा|हटा|म्यूट|लॉक|प्ले|रोक)",
        r"\b(?:मत|नहीं|नही|ना)\s*(?:खोल|चालू|शुरू|बंद|रीस्टार्ट|रिस्टार्ट|डिलीट|मिटा|हटा|म्यूट|लॉक|प्ले|रोक)",
        rf"\b[\w.+-]+(?:\s+[\w.+-]+){{0,3}}\s+(?:ko\s+)?(?:{_ACTION_VERBS})(?:\s+(?:kar|karo|karna|do|de|dijiye))?\s+(?:mat|nahi|nahin|na)(?:\s+(?:kar|karo|karna|do|de|dijiye))?\b",
    )
)

_NORMALIZE_TRANSLATION = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
})


def normalize_intent_text(text: str) -> str:
    """Normalize harmless text variation without deleting negation or meaning words."""
    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFKC", text).translate(_NORMALIZE_TRANSLATION)
    normalized = re.sub(r"[\u200b\ufeff]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\bkr\s*(do|de|dena|dijiye)\b", r"kar \1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bkro\b", "karo", normalized, flags=re.IGNORECASE)
    # Roman spellings the Hinglish recogniser writes for words the patterns know ("Aavaaz kam karo.",
    # "Gaana pahunch karo." for "gaana pause karo").
    normalized = re.sub(r"\b(?:aa?va+a?[zj]|aawa+[zj]|awaj)\b", "awaaz", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(gaana|gana|gaane|music|song)\s+pahunch\b", r"\1 pause", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bkrna\b", "karna", normalized, flags=re.IGNORECASE)
    # Greeting/politeness at the edges carries no intent: "hey can you open
    # chrome for me" fell to chat because "hey " and " for me" broke the
    # open_app patterns (E2E held-out test 2026-10-29).
    normalized = re.sub(r"^(?:hey|hi|ok|okay)[,\s]+(?!sara\b)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+(?:for me|please)\W*$", "", normalized, flags=re.IGNORECASE)
    # Whisper ends every transcript with "." / "?" / "!" / "।". Patterns anchored with $ then never matched:
    # "Volume down." and "Increase the brightness." went to the chat model, "Offen Chrome." captured the app
    # name "Chrome." Dictation (type: ... / write ...) keeps its own punctuation.
    if not re.match(r"^\s*(?:type|write|likho|dictate)\b", normalized, flags=re.IGNORECASE):
        normalized = re.sub(r"^[\s.,;:!?\u2026\u0964-]+", "", normalized)
        normalized = re.sub(r"[\s.,;:!?\u2026\u0964]+$", "", normalized)
    return normalized


# D74: an utterance that OPENS with a negation, with the wake-word and
# politeness prefixes absorbed the same way _NEGATED_ACTION_PATTERNS[0]
# absorbs them.
#
# "stop" is deliberately absent: "stop the music" is stop_media, a real
# command, so treating it as a negation marker would break it. "pause" and
# "cancel" are absent for the same reason (pause_media, cancel_shutdown).
_OPENING_NEGATION = re.compile(
    r"^\s*(?:(?:please|sara|hey\s+sara)[,\s]+)*"
    r"(?:do\s+not|don'?t|dont|never(?:\s+ever)?|must\s+not|"
    r"should\s+not|shouldn'?t|can'?t|cannot|won'?t|will\s+not|"
    r"no\s+need\s+to|not)\s+"
    r"(?:(?:you|please|ever|again|just|really)\s+)*",
    re.IGNORECASE | re.UNICODE,
)

# Hinglish/Hindi put the negation AFTER the verb ("... mat karo", "...
# नहीं करना"), and _NEGATED_ACTION_PATTERNS covers that shape with the same
# _ACTION_VERBS limitation. That half is NOT fixed here, on purpose:
#
#   * The 558-case leak measured below is entirely English-prefix. The
#     Hinglish gap has not been measured, so fixing it in the same change
#     would be shipping an unmeasured edit alongside a measured one.
#
#   * A first attempt at it regressed a golden row immediately. The closing
#     form `\s*(?:मत|नहीं|नही|ना)...$` matched the "ना" INSIDE "भरना" in
#     'टू-डू में जोड़ो बिजली का बिल भरना' ("add paying the electricity bill
#     to the to-do list"), turning a valid add_todo into chat. Devanagari
#     has no usable \b, so the particle needs an explicit preceding space.
#
#   * Worse, bare trailing "na"/"ना" is usually EMPHASIS in Hinglish, not
#     negation -- "volume up kar do na" means *please* turn it up. Treating
#     it as negation would refuse commands the user is asking for more
#     insistently, which is a far worse failure than the one being fixed.
#
# So the Hinglish side needs its own measurement and its own particle set
# (mat / nahi / nahin / मत / नहीं / नही, with bare na/ना excluded). Recorded
# as open rather than guessed at.


def _negated_remainder_is_an_action(normalized: str) -> bool:
    """
    D74: decide "is the rest of this an action?" by MATCHING it, instead of
    checking the verb against a hand-kept allowlist.

    THE DEFECT THIS REPLACES
        _NEGATED_ACTION_PATTERNS gates every alternative on
        _ACTION_VERBS -- about 30 verbs. The pattern set has ~130 intents,
        and the verbs they use (set, increase, decrease, dim, max, min,
        maximize, minimize, snap, move, resize, scroll, zoom, switch,
        press, paste, copy, type, undo, redo, add, mark, remind, show,
        read, list, find, search, calculate, summarize, empty,
        hibernate...) were never added to it. Each new intent shipped its
        pattern and not its verb.

        Measured on the project's own golden commands: of 840 negated
        phrasings ("don't X" / "do not X" / "never X" built from
        intents_en*), **558 slipped the guard and matched an intent** --
        including 18 tier-3 (`do not empty recycle bin`,
        `do not hibernate my pc`, `do not put the computer to sleep`) and
        60 tier-2 (`do not lock my laptop`, `do not press the enter key`,
        `do not paste it`, `do not cancel all my reminders`).

        A negated destructive command executing is the worst failure this
        router can have, and it was 66% of them.

    WHY MATCHING THE REMAINDER IS THE RIGHT TEST
        The verb list was only ever a proxy for "the rest of this is an
        action request". The matcher answers that question exactly, for
        every intent, including ones registered later by a skill -- so this
        cannot fall behind the pattern set the way the list did.

        It also cannot over-trigger the way a bare "starts with don't"
        rule would: it fires only when the remainder genuinely resolves to
        a non-chat intent. "don't worry about it" leaves "worry about it",
        which is chat, so nothing happens. If a "do not disturb" intent is
        ever added, "do not disturb" leaves "disturb", also chat, so it
        keeps working.

    RECURSION
        detect_intent() calls this BEFORE matching, so this must use
        _detect_intent_cached(), which does not consult the negation guard.
        Going through detect_intent() here would recurse.
    """
    if not normalized:
        return False

    opening = _OPENING_NEGATION.match(normalized)
    if opening is None:
        return False

    remainder = normalized[opening.end():].strip(" ,.!?।")
    if not remainder:
        return False

    # The remainder has to be tried the SAME way detect_intent() tries a
    # whole utterance, or the guard is weaker than the matcher it guards
    # and the difference is exactly what leaks.
    #
    # Measured: checking only _detect_intent_cached() left 15 of the 840
    # negated phrasings still routing to an intent, every one of them from
    # intents_en_noisy with an STT-style misspelling --
    #   "don't set brightness two 70", "don't paste it hear",
    #   "don't snap the window to the write", "don't switch two study mode"
    # -- because detect_intent() recovers those through _correct_typos()
    # and this did not. Mirroring its escalation closes them.
    #
    # _resolve_known_app_open() is likewise a separate route in
    # detect_intent(), so "don't open spotify" has to be asked about there
    # too or it leaks the same way.
    candidates = [remainder]
    try:
        corrected = _correct_typos(remainder)
        if corrected and corrected != remainder:
            candidates.append(corrected)
    except Exception:  # noqa: BLE001 - never let the guard break routing
        pass

    for candidate in candidates:
        try:
            intent, _match = _detect_intent_cached(candidate)
        except Exception:  # noqa: BLE001
            continue
        if intent and intent != "chat":
            return True
        try:
            if _resolve_known_app_open(candidate) is not None:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def is_negated_action_request(text: str) -> bool:
    """Detect clear English/Hinglish/Hindi negated action requests before routing."""
    normalized = normalize_intent_text(text)
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in _NEGATED_ACTION_PATTERNS):
        return True
    # D74: the verb-allowlist patterns above miss 66% of negated commands;
    # this asks the matcher instead. See _negated_remainder_is_an_action.
    return _negated_remainder_is_an_action(normalized)

# O2: guards every mutation of the module-level _INTENT_PATTERNS /
# _INTENT_GATES lists/dicts done by register_intent() below. Without
# this, two skills registering concurrently during startup (e.g. from
# separate plugin-loading threads) could race on the same list/dict
# mutation. Matching itself (_detect_intent_cached) never touches this
# lock -- it only reads the already-rebuilt, immutable-per-rebuild
# _ROUTES tuple, so normal hot-path matching pays no locking cost.
_registry_lock = threading.Lock()



# One entry per distinct intent pattern-tuple. The table holds ~140 intents;
# the headroom covers plugin skills registering and re-registering at runtime.
_MERGE_CACHE_SIZE = 1024


@lru_cache(maxsize=_MERGE_CACHE_SIZE)
def _merge_groupless_cached(patterns: tuple):
    """
    The compile half of _merge_groupless(), memoised on the pattern tuple.

    STARTUP-COST FIX. _rebuild_routes() recompiles EVERY intent's patterns,
    and register_intent() calls it once per registration. Loading
    sara/skills/ fires 14 registrations, so the whole table was rebuilt 14
    times: 140 intents / 733 patterns -> 12,703 redundant re.compile() calls
    on every launch, measured at ~3.1 s of the ~4.9 s `import
    sara.orchestrator` cost.

    The function is pure -- the same pattern strings always produce the same
    compiled objects -- so memoising it makes rebuilds 2..N nearly free while
    leaving the result identical. Compiled pattern objects are immutable and
    the only caller copies the result into a fresh list, so sharing them
    across rebuilds is safe.
    """
    compiled_each = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
    if len(patterns) > 1 and all(c.groups == 0 for c in compiled_each):
        merged = "|".join(f"(?:{p})" for p in patterns)
        return (re.compile(merged, re.IGNORECASE | re.UNICODE),)
    return tuple(compiled_each)


def _merge_groupless(patterns):
    """
    Collapse a multi-pattern intent group into a single compiled
    alternation regex when it's provably safe to do so — i.e. when
    NONE of its patterns contain a capturing group. Cuts N separate
    .search() engine invocations down to 1 for pure keyword/phrase
    toggle intents. Intents with any capturing pattern are compiled
    individually, unchanged, so match.group(1) semantics never shift.

    Returns a NEW list each call (over cached compiled objects), so a
    caller is still free to treat the result as its own.
    """
    return list(_merge_groupless_cached(tuple(patterns)))


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

# ── D29: explicit speech-to-text homophone map (rescue pass only) ──────
# _correct_typos()'s docstring already names homophone correction as its
# job, but the fuzzy mechanism cannot do it:
#   * _TYPO_MIN_WORD_LEN = 5 skips "two" (3), "hear" (4), "paws" (4),
#     "no" (2) outright -- and those are exactly the words ASR confuses;
#   * difflib compares spelling, and homophones are words that sound alike
#     while being spelled differently, so "two"/"to" and "write"/"right"
#     never come within _TYPO_CUTOFF of each other.
#
# So this is an explicit map rather than a heuristic. Every entry below is
# taken from a real row in bench/golden -- nothing speculative:
#   "switch two study mode"               two   -> to
#   "set brightness two 70"               two   -> to
#   "snap window two the left"            two   -> to
#   "snap the window to the write"        write -> right
#   "paste it hear"                       hear  -> here
#   "paws the music"                      paws  -> pause
#   "forget everything you no about me"   no    -> know
#
# SAFETY comes from WHERE this is applied, not from the map being cautious:
# _correct_typos() runs only as a second attempt after the exact-spelling
# pass has already returned no intent. A command that works today never
# reaches it, so no existing behaviour can change -- and an ordinary
# sentence that gets rewritten ("I have two options" -> "I have to
# options") still has to satisfy some pattern's full structure to become a
# command, which it does not. _TYPO_PROTECTED is still honoured.
_HOMOPHONES = {
    "two": "to",
    "write": "right",
    "hear": "here",
    "paws": "pause",
    "no": "know",
    # E2E audit 2026-10-30: very short command typos the fuzzy pass cannot
    # reach (it ignores words under 5 letters). "opn chrmoe" was answered by
    # the chat model with an invented "Chrome opened!", "whats teh time"
    # with an invented time. Only unambiguous misspellings go here.
    "teh": "the",
    # Whisper spellings seen in sara_unmatched_queries.jsonl ("Offen Chrome.", "nest track")
    "offen": "open",
    "ofen": "open",
    "opan": "open",
    "nest": "next",
    "opn": "open",
    "opne": "open",
    "oepn": "open",
    "pley": "play",
    "plya": "play",
    "timr": "timer",
    "tmer": "timer",
}

# ASR also splits closed compounds into two words, which no per-word map
# can repair. Applied to the whole string in the same rescue pass.
# Evidence: "turn off wi fi" (golden note: "speech-to-text splits wifi
# into two words").
_SPLIT_COMPOUNDS = (
    (re.compile(r"\bwi\s+fi\b", re.IGNORECASE), "wifi"),
    (re.compile(r"\bblue\s+tooth\b", re.IGNORECASE), "bluetooth"),
)

try:  # words the typo-rescue pass must never "correct" into a command word
    from .patterns_multilingual import TYPO_PROTECTED as _TYPO_PROTECTED
except Exception:  # optional layer
    _TYPO_PROTECTED = frozenset()
_TYPO_PROTECTED = frozenset(_TYPO_PROTECTED) | {"water", "waters"}


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
    # App names the user may mistype ("chrmoe", "spotfy"): single-word
    # entries of the launch allowlist. Without them "open chrmoe" had no
    # vocabulary word to be corrected to.
    try:
        from config import Config as _Cfg

        _TRIGGER_VOCAB |= {
            str(a).strip().lower()
            for a in (getattr(_Cfg, "APP_LAUNCH_ALLOWLIST", None) or [])
            if " " not in str(a).strip() and len(str(a).strip()) >= _TYPO_MIN_WORD_LEN
        }
    except Exception:
        pass
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
    for this intent (matches the existing `calculator` convention in
    patterns.py — the one remaining intent with no gate).

    Safe to call more than once for the same name (replaces its patterns/
    gate rather than duplicating the entry) and safe to call from
    anywhere, including inside a try/except during optional-plugin
    loading — it never raises for a well-formed call. A bad `patterns`
    list is now caught by an O3 dry-run compile BEFORE anything is
    mutated: re.error propagates immediately to the caller and neither
    _INTENT_PATTERNS nor _INTENT_GATES is touched, so a failed
    registration can never leave the tables in a half-updated state.
    O2: the whole body runs under _registry_lock so two concurrent
    registrations (e.g. two plugins loading on separate threads at
    startup) can't interleave their reads/mutations of these shared
    module-level structures.
    """
    with _registry_lock:
        patterns = list(patterns)
        # O3: validate every pattern compiles BEFORE removing the old
        # entry or inserting the new one. If any pattern is malformed,
        # this raises re.error here and _INTENT_PATTERNS/_INTENT_GATES
        # are left completely untouched (the previous registration for
        # `name`, if any, still stands) — no silent swallow, and no
        # partially-applied registration for _rebuild_routes() to trip
        # over later.
        for p in patterns:
            re.compile(p)

        _INTENT_PATTERNS[:] = [(n, p) for n, p in _INTENT_PATTERNS if n != name]
        # Inserted at the FRONT, not appended — patterns.py's own comments
        # note that open_app/close_app's catch-all patterns (last in the
        # static table) are deliberately greedy ("close|quit|exit|kill ...")
        # and WILL match an unrelated phrase that merely contains one of
        # those words as a substring if checked first (confirmed: a naive
        # append-at-end put a test intent after close_app, and "hello
        # skill world" was wrongly matched by close_app via its embedded
        # "kill world"). Runtime-registered skill intents must be checked
        # before those catch-alls.
        _INTENT_PATTERNS.insert(0, (name, patterns))
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

# LAYER 4: intents whose sole capturing group is a free-text "app name"
# that shape-bounding (patterns.py's _APP_NAME) can't fully disambiguate
# on its own -- e.g. "gym start karna hai" is still shape-valid ("gym"
# and "karna" and "hai" all individually look like app-name tokens).
# For exactly these four intents, a successful regex match is not
# trusted outright: it's checked against _NON_APP_TOKENS first.
_APP_NAME_REJECT_INTENTS = frozenset({
    "open_app", "close_app", "restart_application", "switch_to_application",
})


# English copulas/modals: "open source software is great" is a sentence about
# something, not an app name.
_ENGLISH_SENTENCE_TOKENS = frozenset({
    "is", "are", "was", "were", "be", "been", "should", "would", "could",
    "because", "great", "good", "bad", "awesome",
    # "awaaz band karo" / "volume band karo" mean mute, not "close the app named awaaz".
    "awaaz", "awaz", "volume", "sound",
})


def _captured_app_name_is_rejected(match: "re.Match") -> bool:
    """
    True if match's app-name capture (always group 1 for these four
    intents -- each of their patterns has exactly one capturing group)
    contains a Hinglish verb-ending/function-word token from
    _NON_APP_TOKENS, meaning it's almost certainly someone talking
    ABOUT starting/closing/restarting/switching something rather than
    naming an actual app. Comparison is case-insensitive and per
    whitespace-separated token, same tokenization _NON_APP_TOKENS was
    built against.
    """
    captured = match.group(1)
    if not captured:
        return False
    return any(
        tok in _NON_APP_TOKENS or tok in _ENGLISH_SENTENCE_TOKENS
        for tok in captured.lower().split()
    )


@lru_cache(maxsize=_INTENT_CACHE_SIZE)
def _detect_intent_cached(text: str) -> Tuple[str, Optional[re.Match]]:
    """
    Core matching routine behind detect_intent(), memoized with an LRU
    cache keyed on the exact (already-stripped) input string.

    Regex matching over an immutable string is a pure function of that
    string's contents, so an identical repeated command can only ever
    produce the same (intent_name, match) result — caching it is safe
    and lets repeats skip regex evaluation over all ~130 pattern groups
    entirely instead of re-running them.

    LAYER 4 note: for open_app/close_app/restart_application/
    switch_to_application specifically, a regex match is provisional —
    see _captured_app_name_is_rejected(). A rejected match is discarded
    and matching CONTINUES (next pattern in this intent's compiled_list,
    then the next intent in _ROUTES) rather than returning "chat"
    immediately, since some other, unrelated intent further down may
    still be the correct match for the same text. This rejection is
    scoped to exactly those four intents; every other intent's matching
    behavior, and the _ROUTES structure itself, are unchanged.
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
                if (
                    intent_name in _APP_NAME_REJECT_INTENTS
                    and _captured_app_name_is_rejected(match)
                ):
                    continue
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
    # D29: repair split compounds first -- these span two words, so a
    # per-word loop cannot see them.
    for pattern, replacement in _SPLIT_COMPOUNDS:
        new_text = pattern.sub(replacement, text)
        if new_text != text:
            text = new_text

    words = text.split()
    if not words:
        return text
    changed = False
    out = []
    for w in words:
        bare = _TYPO_WORD_RE.sub("", w.lower())
        # D29: explicit homophones come before the length/vocab checks --
        # every one of them is shorter than _TYPO_MIN_WORD_LEN or is itself
        # a real word, so the fuzzy path below can never reach them.
        # _TYPO_PROTECTED still wins, so a word listed there is never
        # rewritten by any mechanism.
        if bare not in _TYPO_PROTECTED and bare in _HOMOPHONES:
            out.append(_HOMOPHONES[bare])
            changed = True
            continue
        if len(bare) < _TYPO_MIN_WORD_LEN or bare in _TRIGGER_VOCAB or bare in _TYPO_PROTECTED:
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


def _canonical_known_app_alias(candidate: str) -> Optional[str]:
    """Resolve a known app alias conservatively to its canonical name."""
    try:
        from sara.tools.system.apps import _APP_ALIASES
    except Exception:
        return None

    key = re.sub(r"\s+", " ", normalize_intent_text(candidate).lower()).strip(" .,!?")
    if not key:
        return None

    aliases = _APP_ALIASES
    matched = key if key in aliases else None

    if matched is None:
        if len(key) < 4:
            return None

        ranked = sorted(
            (
                (difflib.SequenceMatcher(None, key, alias).ratio(), alias)
                for alias in aliases
            ),
            reverse=True,
        )

        if not ranked or ranked[0][0] < 0.88:
            return None

        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.06:
            return None

        matched = ranked[0][1]

    executable = aliases[matched]
    equivalent_aliases = [
        alias for alias, target in aliases.items()
        if target == executable
    ]

    if not equivalent_aliases:
        return matched

    return min(
        equivalent_aliases,
        key=lambda alias: (len(alias.split()), len(alias), alias),
    )


def _resolve_known_app_open(text: str) -> Optional[Tuple[str, re.Match]]:
    """Resolve strict app-opening commands before broad intent routes."""
    native_intent, native_match = _detect_intent_cached(text)
    if (
        native_intent.startswith("open_")
        and native_intent != "open_app"
        and native_match is not None
    ):
        return native_intent, native_match

    for intent_name, compiled_list, _gate in _ROUTES:
        if intent_name != "open_app":
            continue

        for pattern in compiled_list:
            candidate_match = pattern.search(text)
            if not candidate_match or not candidate_match.lastindex:
                continue

            canonical = _canonical_known_app_alias(candidate_match.group(1))
            if canonical is None:
                continue

            native_intent, native_match = _detect_intent_cached(
                f"{canonical} kholo"
            )

            if (
                native_intent.startswith("open_")
                and native_intent != "open_app"
                and native_match
            ):
                return native_intent, native_match

            resolved_intent, resolved_match = _detect_intent_cached(
                f"open {canonical}"
            )

            if resolved_intent == "open_app" and resolved_match:
                return resolved_intent, resolved_match

    return None


# A question ABOUT an action is not a request to do it: "what does mute
# mean in hindi" muted the PC and "how to take a screenshot on mac" took a
# screenshot (E2E held-out test 2026-10-29). Only openers that are never a
# command themselves -- not "why ..." or "explain ...": "why did you say
# that" and "explain that message" ARE commands (why_proactive).
_INFO_QUESTION_RE = re.compile(
    r"^\W*(?:(?:sara|hey\s+sara)[,\s]+)?"
    r"(?:how\s+(?:to|do\s+i|do\s+you|can\s+i|should\s+i|does)\b"
    r"|what\s+does\s+\S.*\bmean\b"
    r"|what\s+is\s+the\s+meaning\s+of\b"
    r"|(?:the\s+)?meaning\s+of\b)",
    re.IGNORECASE,
)


def detect_intent(text: str) -> Tuple[str, Optional[re.Match]]:
    """Resolve a command locally while preserving negation and legacy match groups."""
    stripped = text.strip() if isinstance(text, str) else ""
    if not stripped or is_negated_action_request(stripped):
        return "chat", None
    if _INFO_QUESTION_RE.match(stripped):
        return "chat", None

    normalized = normalize_intent_text(stripped)
    app_route = _resolve_known_app_open(normalized)
    if app_route is not None:
        return app_route

    intent_name, match = _detect_intent_cached(stripped)
    if intent_name != "chat":
        return intent_name, match

    if normalized != stripped:
        intent_name, match = _detect_intent_cached(normalized)
        if intent_name != "chat":
            return intent_name, match

    corrected = _correct_typos(normalized)
    if corrected == normalized:
        return intent_name, match

    return _detect_intent_cached(corrected)


# Convenience passthroughs for tests/tools — do not affect matching
# behavior. cache_clear() resets memoized state; cache_info() reports
# hits/misses/maxsize/currsize for observability.
detect_intent.cache_clear = _detect_intent_cached.cache_clear
detect_intent.cache_info = _detect_intent_cached.cache_info