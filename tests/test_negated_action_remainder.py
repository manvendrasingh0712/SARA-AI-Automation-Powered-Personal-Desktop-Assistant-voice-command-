"""
D74: a negated command must not execute. The guard now decides "is the rest
of this an action?" by MATCHING the remainder, not by checking its verb
against a hand-kept allowlist.

THE DEFECT
    `_NEGATED_ACTION_PATTERNS` gates every alternative on `_ACTION_VERBS`
    -- about 30 verbs: open, launch, start, run, restart, close, delete,
    mute, lock, turn on, turn off, play, stop, install, send, buy ...

    The pattern set has ~130 intents, and the verbs they actually use were
    never added: set, increase, decrease, dim, max, min, maximize,
    minimize, snap, move, resize, scroll, zoom, switch, press, paste, copy,
    type, undo, redo, add, mark, remind, show, read, list, find, search,
    calculate, summarize, empty, hibernate. Each new intent shipped its
    pattern and not its verb, so the guard fell steadily behind the router
    it guards.

MEASURED SCOPE, on the project's own golden commands
    840 negated phrasings were built by prefixing every positive command in
    intents_en.jsonl and intents_en_noisy.jsonl with "don't ", "do not "
    and "never ". Before D74, **558 of them slipped the guard and matched
    an executable intent**, including:

        tier 3  do not empty recycle bin
                do not hibernate my pc
                do not put the computer to sleep
        tier 2  do not lock my laptop
                do not press the enter key
                do not paste it
                do not cancel all my reminders
                do not type this: ...
        tier 1  65 further intents, 333 phrases

    A negated destructive command executing is the worst failure this
    router can have, and it was 66% of them. After D74 the figure is 0.

WHY MATCHING THE REMAINDER IS THE RIGHT TEST
    The verb list was only ever a proxy for "the rest of this is an action
    request". The matcher answers that exactly, for every intent -- and for
    intents a skill registers at runtime, which a static list can never
    cover.

    It also cannot over-trigger the way a bare "starts with don't" rule
    would: it fires only when the remainder genuinely resolves to a
    non-chat intent. "don't worry about it" leaves "worry about it", which
    is chat, so nothing happens.

SCOPE -- what this does NOT change
    Only ENGLISH OPENING negation. The Hinglish/Hindi closing form
    ("... mat karo", "... नहीं करना") has the same allowlist limitation and
    is deliberately left alone: the leak measured above is entirely
    English, and a first attempt at the closing form regressed a golden row
    immediately (it matched the "ना" inside "भरना"). Bare trailing na/ना is
    also usually EMPHASIS in Hinglish -- "volume up kar do na" means
    *please* turn it up -- so treating it as negation would refuse commands
    the user is asking for more insistently. Measured separately or not at
    all; not guessed at.
"""
from __future__ import annotations

import collections
import json
import pathlib

import pytest

from sara.core.intent import engine as ie
from sara.core.security import tiers


NEGATIONS = ("don't ", "do not ", "never ")


def _golden_english_commands():
    rows = []
    for name in ("intents_en.jsonl", "intents_en_noisy.jsonl"):
        path = pathlib.Path(__file__).resolve().parents[1] / "bench" / "golden" / name
        if not path.exists():  # pragma: no cover - dataset is part of the repo
            pytest.skip(f"{name} is missing")
        defaults = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "_defaults" in row:
                defaults = row["_defaults"]
                continue
            merged = {**defaults, **row}
            if merged.get("kind") == "negative":
                continue
            rows.append(merged["t"])
    assert rows, "no golden English commands were loaded"
    return rows


# ── the headline property ───────────────────────────────────────────────
def test_no_negated_golden_command_reaches_an_executable_intent() -> None:
    """
    The whole point, measured the same way the defect was. 840 negated
    phrasings, 558 of which used to execute.

    Deliberately asserts on detect_intent()'s OUTCOME, not on
    is_negated_action_request(): what matters is that nothing runs. A
    phrase the guard does not flag but the router does not match either is
    safe, and this test says so rather than over-constraining the guard.
    """
    leaked = collections.defaultdict(list)
    checked = 0
    for text in _golden_english_commands():
        for neg in NEGATIONS:
            phrase = neg + text
            checked += 1
            intent, _match = ie.detect_intent(phrase)
            if intent and intent != "chat":
                leaked[intent].append(phrase)

    assert checked >= 800, f"only {checked} phrases were checked; dataset shrank?"
    if leaked:
        worst = sorted(
            leaked.items(),
            key=lambda kv: -(tiers.tier_of(kv[0]) or 0),
        )
        detail = "; ".join(
            f"tier {tiers.tier_of(i)} {i} e.g. {p[0]!r}" for i, p in worst[:6]
        )
        pytest.fail(
            f"{sum(len(v) for v in leaked.values())} of {checked} negated "
            f"commands still route to an executable intent: {detail} (D74)"
        )


def test_the_destructive_cases_specifically() -> None:
    """
    Named, because these are the ones that cause real damage and because a
    future change to the dataset must not quietly drop them from the sweep
    above.
    """
    cases = [
        "do not empty recycle bin",
        "do not hibernate my pc",
        "do not put the computer to sleep",
        "don't lock my laptop",
        "do not press the enter key",
        "don't paste it",
        "do not cancel all my reminders",
        "never restart my pc",
        "don't delete that file",
        "do not shut down the computer",
    ]
    for phrase in cases:
        intent, _m = ie.detect_intent(phrase)
        assert intent == "chat", (
            f"{phrase!r} routed to {intent!r} -- a negated destructive "
            f"command would execute (D74)"
        )


# ── over-triggering: the risk a broadening change carries ───────────────
@pytest.mark.parametrize(
    "text",
    [
        "don't worry about it",
        "never mind",
        "not now",
        "not bad",
        "i don't know what to do",
        "don't you think that's funny",
        "cannot complain",
        # If a do-not-disturb intent is ever added, this must keep working:
        # the remainder is "disturb", which is chat, so the guard stays out
        # of the way.
        "do not disturb",
    ],
)
def test_a_negation_that_is_not_a_negated_command_is_left_alone(text) -> None:
    assert not ie.is_negated_action_request(text), (
        f"{text!r} was treated as a negated action request. The remainder is "
        f"not an action, so the guard must not fire -- otherwise it is just "
        f"a 'starts with don't' rule (D74)"
    )


@pytest.mark.parametrize(
    "text, want",
    [
        ("open chrome", "open_app"),
        ("volume up", "set_volume"),
        ("play never gonna give you up", "play_youtube"),
        ("search for don't stop believin", "web_search"),
        ("take note: do not forget the milk", "take_note"),
        ("set volume to 40", "set_volume"),
        ("lock my laptop", "lock_pc"),
        ("empty recycle bin", "empty_recycle_bin"),
    ],
)
def test_real_commands_still_route_including_ones_containing_negation_words(
    text, want
) -> None:
    """
    A command that merely CONTAINS "don't"/"never" in its content, or whose
    object happens to, must be unaffected -- the guard is anchored at the
    start of the utterance.
    """
    intent, _m = ie.detect_intent(text)
    assert intent == want, f"{text!r} moved from {want!r} to {intent!r} (D74)"


def test_command_verbs_are_not_treated_as_negation_markers() -> None:
    """
    "stop", "pause" and "cancel" all read like refusals in English but are
    real commands here (stop_media, pause_media, cancel_shutdown). If any
    of them were added to _OPENING_NEGATION, those commands would stop
    working.
    """
    for text, forbidden_chat in (
        ("stop the music", "stop_media"),
        ("cancel shutdown", "cancel_shutdown"),
    ):
        intent, _m = ie.detect_intent(text)
        assert intent != "chat", (
            f"{text!r} now routes to chat; a command verb was probably added "
            f"to the opening-negation marker set, which breaks "
            f"{forbidden_chat} (D74)"
        )
    src_markers = ie._OPENING_NEGATION.pattern
    for verb in ("stop", "pause", "cancel", "skip", "mute"):
        assert f"|{verb}\\s" not in src_markers and f"|{verb})" not in src_markers, (
            f"{verb!r} was added as an opening-negation marker; it is a "
            f"command verb in this router (D74)"
        )


# ── the guard must track the matcher, or the difference leaks ───────────
def test_the_guard_mirrors_the_matchers_typo_escalation() -> None:
    """
    detect_intent() recovers a misspelled command through _correct_typos().
    A guard that only tries the raw remainder is therefore weaker than the
    matcher it guards, and the gap is exactly what leaks.

    Measured: checking only _detect_intent_cached left 15 of the 840 still
    routing, every one from intents_en_noisy with an STT-style misspelling.
    """
    for phrase in (
        "don't set brightness two 70",
        "don't paste it hear",
        "don't snap the window to the write",
        "don't switch two study mode",
    ):
        intent, _m = ie.detect_intent(phrase)
        assert intent == "chat", (
            f"{phrase!r} routed to {intent!r}. The typo-corrected remainder "
            f"matches an intent, so the guard must try the same correction "
            f"detect_intent() does (D74)"
        )


def test_the_guard_tries_every_route_detect_intent_tries() -> None:
    """
    The generalised form of the D74 defect: the guard fell behind the
    router. This fails if they diverge again.

    `detect_intent()` resolves an utterance through more than one route --
    `_resolve_known_app_open()` before the pattern matcher, then
    `_detect_intent_cached()`, then the same with `_correct_typos()`
    applied. Every route the router uses to say "this is an action" is a
    route the guard must use to say "this would have been an action",
    or the difference is a leak.

    WHY THIS IS STRUCTURAL RATHER THAN BEHAVIOURAL
        An earlier version of this test asserted on phrases --
        "don't open spotify" and friends. A mutation run that DELETED the
        guard's `_resolve_known_app_open` branch left it passing: "open" is
        in _ACTION_VERBS, so the old allowlist patterns catch those phrases
        before the new check is reached. Sweeping all 590 golden texts
        against five negation forms found **zero** cases where that branch
        is the decider, so no phrase can exercise it today.

        The branch is kept anyway -- it makes the guard complete with
        respect to the router's routes rather than complete with respect to
        today's dataset -- and this test pins the property that justifies
        keeping it.
    """
    import ast
    import inspect
    import textwrap

    def _calls(fn):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    router_routes = _calls(ie._detect_intent_unguarded) & {
        "_detect_intent_cached",
        "_resolve_known_app_open",
        "_correct_typos",
    }
    guard_routes = _calls(ie._negated_remainder_is_an_action)

    assert router_routes, (
        "detect_intent() no longer calls any of the known route resolvers; "
        "re-derive this test against its new shape (D74)"
    )
    missing = router_routes - guard_routes
    assert not missing, (
        f"detect_intent() resolves an action through {sorted(missing)} but "
        f"the negation guard does not try {'it' if len(missing) == 1 else 'them'}. "
        f"That difference is a leak: a negated command resolved by that "
        f"route alone would execute. This is the D74 defect recurring -- "
        f"the guard falling behind the router it guards."
    )


def test_the_guard_cannot_recurse() -> None:
    """
    detect_intent() calls the guard BEFORE matching, so the guard must use
    _detect_intent_cached (which does not consult the guard). Calling
    detect_intent() from inside it would recurse until the stack blew.

    Asserted structurally, because the behavioural symptom is a crash.

    Parsed with `ast` rather than grepped. A line scan over the source
    tripped on this function's own docstring, which names detect_intent()
    while explaining why it must not be called -- the same
    comment-breaks-the-scan trap this audit hit three times before. The AST
    sees calls only.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(
        ie._negated_remainder_is_an_action
    )))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_detect_intent_cached" in called, (
        f"the remainder check no longer calls _detect_intent_cached "
        f"(it calls {sorted(called)}) (D74)"
    )
    assert "detect_intent" not in called, (
        "the remainder check calls detect_intent(), which re-enters the "
        "guard through is_negated_action_request() and recurses (D74)"
    )


def test_a_matcher_failure_cannot_break_routing(monkeypatch) -> None:
    """
    The guard runs on the path of EVERY utterance. If the remainder check
    raises, routing must continue rather than the turn dying.

    Behavioural, not a count of `except` clauses: an earlier version of
    this test asserted `src.count("except Exception") >= 2`, and a mutation
    that turned the handlers into bare `raise` left it passing -- the
    clauses were still there, they just re-raised. This injects a failing
    matcher instead.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("matcher exploded")

    monkeypatch.setattr(ie, "_detect_intent_cached", _boom)
    monkeypatch.setattr(ie, "_resolve_known_app_open", _boom)
    monkeypatch.setattr(ie, "_correct_typos", _boom)

    # The guard must answer rather than propagate...
    for phrase in ("don't open chrome", "do not empty recycle bin", "never mind"):
        result = ie.is_negated_action_request(phrase)
        assert isinstance(result, bool), (
            f"the guard returned {result!r} for {phrase!r} with a failing "
            f"matcher; it must degrade to a bool, not propagate (D74)"
        )

    # ...and routing as a whole must still return a usable answer.
    intent, _match = ie.detect_intent("don't open chrome")
    assert isinstance(intent, str) and intent, (
        "detect_intent() did not survive a failing matcher inside the "
        "negation guard; one bad input would kill every turn (D74)"
    )


def test_a_failing_app_resolver_cannot_break_routing(monkeypatch) -> None:
    """
    The guard's second handler, reached only when the matcher SUCCEEDS
    (returning chat) and the app resolver then raises. Covered separately
    because the first handler's `continue` skips the resolver entirely, so
    the previous test cannot exercise this one -- a mutation that made this
    handler re-raise passed until this test existed.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(ie, "_detect_intent_cached", lambda _t: ("chat", None))
    monkeypatch.setattr(ie, "_resolve_known_app_open", _boom)

    result = ie.is_negated_action_request("do not frobnicate the widget")
    assert isinstance(result, bool), (
        f"the guard returned {result!r} with a failing app resolver; it must "
        f"degrade to a bool, not propagate (D74)"
    )


def test_the_opening_negation_is_anchored_by_match_not_search() -> None:
    """
    The real anchor is `.match()`, not the `^` in the pattern -- `.match()`
    already binds to position 0, which is why removing the `^` is a no-op
    and why a mutation run could not distinguish it.

    What WOULD change behaviour is switching to `.search()`: the marker
    would then be found anywhere, and the remainder taken from mid-sentence
    ("the sign says don't open this door" -> remainder "this door"). Pinned
    structurally because that is where the anchoring actually lives.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(
        ie._negated_remainder_is_an_action
    )))
    methods = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "match" in methods, (
        "the opening-negation regex is no longer applied with .match(); "
        "that call is what anchors it to the start of the utterance (D74)"
    )
    assert "search" not in methods, (
        "the opening-negation regex is applied with .search(), so the "
        "marker is found mid-sentence and the remainder is taken from the "
        "wrong place (D74)"
    )
    # behavioural companion: a marker in the middle must not trigger it
    assert not ie.is_negated_action_request(
        "the sign says don't open this door"
    ), (
        "a negation marker in the middle of a sentence triggered the guard; "
        "it must be anchored at the start (D74)"
    )


def test_the_guard_tolerates_hostile_input() -> None:
    for junk in ("", "   ", "don't ", "don't " + "x" * 5000, "नहीं", "don't \u0000"):
        assert isinstance(ie.is_negated_action_request(junk), bool)


# ── scope: the Hinglish half is deliberately untouched ─────────────────
def test_the_hinglish_closing_form_is_out_of_scope_and_stays_working() -> None:
    """
    Pins the scope decision, and the specific golden row that a first
    attempt at the closing form broke: the Devanagari particle "ना" also
    ends the ordinary verb "भरना", so a pattern without an explicit
    preceding space turns a valid add_todo into chat.
    """
    text = "टू-डू में जोड़ो बिजली का बिल भरना"
    intent, _m = ie.detect_intent(text)
    assert intent == "add_todo", (
        f"{text!r} routed to {intent!r}. A closing-negation pattern is "
        f"matching the 'ना' inside 'भरना' -- it needs an explicit preceding "
        f"space, and bare na/ना should be excluded entirely because it is "
        f"usually emphasis, not negation (D74)"
    )
    # the emphasis reading, which must never be refused
    assert not ie.is_negated_action_request("volume up kar do na"), (
        "'volume up kar do na' is an INSISTENT request, not a negated one; "
        "treating trailing 'na' as negation refuses commands the user is "
        "asking for more firmly (D74)"
    )


def test_the_old_allowlist_patterns_are_still_in_place() -> None:
    """
    D74 ADDS a check; it does not replace _NEGATED_ACTION_PATTERNS. Those
    still carry the Hinglish and Devanagari forms, which the new check does
    not cover.
    """
    assert ie._NEGATED_ACTION_PATTERNS, "the allowlist patterns were removed"
    assert ie.is_negated_action_request("chrome mat kholo"), (
        "the Hinglish closing-negation path stopped working; D74 was meant "
        "to be additive (D74)"
    )
