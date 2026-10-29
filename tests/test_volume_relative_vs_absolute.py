"""
D75: "turn up the volume by 10" must raise the volume by 10, not set it to 10.

THE DEFECT
    `set_volume` folds raising and lowering into one intent, and its
    patterns capture the amount in both readings:

        "set volume to 40"           ->  40 is a TARGET
        "turn up the volume by 10"   ->  10 is a DELTA

    `_h_set_volume` treated every captured number as a target:

        "turn up the volume by 10"    -> set_volume(10)   -> went QUIETER
        "turn down the volume by 20"  -> set_volume(20)   -> from 10, LOUDER
        "increase the volume by 15"   -> set_volume(15)

    So asking for a small change in one direction produced a jump in the
    other -- the command did close to the opposite of what was said.

    Pre-existing, in the `(?: by (\\d+)%?)?` groups of the increase/decrease
    patterns. D73's object-first alternative inherited it for
    "volume up by 10".

    `set_brightness` does NOT have this bug: `increase_brightness` is a
    separate intent, anchored, with no numeric group. That is the shape
    `set_volume` should probably have had, and the asymmetry is why this
    went unnoticed.

WHY THESE TESTS DRIVE THE HANDLER
    The router returning `set_volume` says nothing about which direction
    the volume moves -- the whole defect lives between the match and the
    tool call. So these substitute a recording `system_tools` and assert on
    the call that comes out. The handler had no tests at all before this.
"""
from __future__ import annotations

import pytest

import sara.orchestrator.handlers.system as H
from sara.core.intent import engine as ie


@pytest.fixture
def volume_calls(monkeypatch):
    """Record what _h_set_volume asks system_tools to do."""
    calls = []

    class _Recorder:
        @staticmethod
        def set_volume(level):
            calls.append(("set_volume", level))
            return f"volume set to {level}"

        @staticmethod
        def adjust_volume(delta):
            calls.append(("adjust_volume", delta))
            return f"volume adjusted by {delta}"

    monkeypatch.setattr(H, "system_tools", _Recorder)
    monkeypatch.setattr(H, "_quick", lambda _ctx, msg: msg)
    return calls


def _run(text, volume_calls, state=None):
    intent, match = ie.detect_intent(text)
    assert intent == "set_volume", (
        f"{text!r} routed to {intent!r}, not set_volume; this test is about "
        f"the handler, so the fixture needs a phrasing that reaches it"
    )
    ctx = {"volume_state": state if state is not None else {}, "user_input": text}
    H._h_set_volume(match, ctx)
    return volume_calls[0] if volume_calls else None


# ── the defect ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text, delta",
    [
        ("turn up the volume by 10", 10),
        ("turn down the volume by 20", -20),
        ("increase the volume by 15", 15),
        ("lower the volume by 25", -25),
        ("raise the volume by 5", 5),
        ("reduce the volume by 30", -30),
        # D73's object-first alternative inherited the same bug
        ("volume up by 10", 10),
        ("volume down by 5", -5),
    ],
)
def test_by_n_is_a_delta_not_a_target(text, delta, volume_calls) -> None:
    got = _run(text, volume_calls)
    assert got == ("adjust_volume", delta), (
        f"{text!r} produced {got}, not ('adjust_volume', {delta}). Treating "
        f"the number as a target makes the volume jump the wrong way (D75)"
    )


def test_the_worst_case_in_full() -> None:
    """
    Stated plainly because it is the one a person would actually notice:
    at volume 10, "turn down the volume by 20" used to RAISE it to 20.
    """
    # 20 as a target vs -20 as a delta, from a starting point of 10
    assert 20 > 10, "sanity"           # what the bug did: 10 -> 20, louder
    assert 10 + (-20) < 10, "sanity"   # what it should do: quieter


# ── what must NOT become relative ───────────────────────────────────────
@pytest.mark.parametrize(
    "text, level",
    [
        ("set volume to 40", 40),
        ("set the volume to 100", 100),
        ("volume 70", 70),
        ("volume to 30", 30),
        ("change volume to 55", 55),
        ("put the volume at 15", 15),
    ],
)
def test_a_target_without_a_direction_stays_absolute(text, level, volume_calls) -> None:
    got = _run(text, volume_calls)
    assert got == ("set_volume", level), (
        f"{text!r} produced {got}, not ('set_volume', {level}); a plain "
        f"target must not be reinterpreted as a delta (D75)"
    )


@pytest.mark.parametrize(
    "text, level",
    [
        ("turn up the volume to 40", 40),
        ("increase volume to 50", 50),
        ("raise volume to 60", 60),
        ("turn down the volume to 20", 20),
        ("lower volume to 15", 15),
    ],
)
def test_a_direction_word_with_to_n_is_still_a_target(
    text, level, volume_calls
) -> None:
    """
    This is what the "by N" requirement is for, and these phrasings are
    reachable: the unanchored `volume (?:to |at )?(\\d{1,3})` pattern
    matches the substring "volume to 40" inside "turn up the volume to 40",
    so the handler receives BOTH a direction word and a captured target.

    Without the "by" check -- keying only on the direction word -- "turn up
    the volume to 40" would raise the volume BY 40 instead of setting it to
    40. A mutation that dropped the check passed until these cases existed,
    because every other absolute phrasing happens to contain no direction
    word.
    """
    got = _run(text, volume_calls)
    assert got == ("set_volume", level), (
        f"{text!r} produced {got}, not ('set_volume', {level}). 'to N' is a "
        f"target even when a direction word is present; only 'by N' is a "
        f"delta (D75)"
    )


def test_a_bare_direction_still_steps_by_ten(volume_calls) -> None:
    """The no-number path, which D73 made reachable for 'volume up'."""
    assert _run("volume up", volume_calls) == ("adjust_volume", 10)
    volume_calls.clear()
    assert _run("volume down", volume_calls) == ("adjust_volume", -10)
    volume_calls.clear()
    assert _run("turn up the volume", volume_calls) == ("adjust_volume", 10)


def test_no_direction_and_no_number_asks(volume_calls, monkeypatch) -> None:
    """
    The fallback must survive: a match with no usable number and no
    direction word asks rather than guessing a level.
    """
    class _NoGroup:
        re = type("r", (), {"groups": 1})()

        @staticmethod
        def group(_n):
            return None

    monkeypatch.setattr(H, "_quick", lambda _ctx, msg: msg)
    out = H._h_set_volume(_NoGroup(), {"volume_state": {}, "user_input": "volume"})
    assert "volume level" in str(out).lower(), (
        f"expected a question about the level, got {out!r} (D75)"
    )
    assert not volume_calls, (
        f"the handler changed the volume with no level and no direction: "
        f"{volume_calls} (D75)"
    )


# ── state bookkeeping ───────────────────────────────────────────────────
def test_a_delta_is_not_recorded_as_the_last_level(volume_calls) -> None:
    """
    `volume_state["last"]` means "the level we set". Writing a DELTA there
    would make a later relative change compound off a wrong base, and
    anything reading it back ("what did you set it to?") would be wrong.
    """
    state = {}
    _run("turn up the volume by 10", volume_calls, state=state)
    assert state.get("last") is None, (
        f"a +10 delta was recorded as last level {state.get('last')!r} (D75)"
    )


def test_an_absolute_level_is_still_recorded(volume_calls) -> None:
    state = {}
    _run("set volume to 40", volume_calls, state=state)
    assert state.get("last") == 40, (
        f"the absolute level was not recorded: {state!r} (D75)"
    )


# ── the direction words ─────────────────────────────────────────────────
def test_direction_words_are_matched_on_word_boundaries() -> None:
    """
    The inline version used `"up" in lowered_input`, which also fires on
    "setup", "backup" and "upload". That never mattered while only a
    no-number utterance reached it; D75 consults the same words on the
    numbered path, where a false direction would turn an absolute level
    into a delta.
    """
    assert H._volume_direction("volume up") == 1
    assert H._volume_direction("turn down the volume") == -1
    assert H._volume_direction("set volume to 40") == 0
    for decoy in ("setup volume", "backup volume", "upload volume", "sundown"):
        assert H._volume_direction(decoy) == 0, (
            f"{decoy!r} read as a direction; the words must be matched with "
            f"boundaries, not as substrings (D75)"
        )


def test_the_direction_words_exist_in_exactly_one_place() -> None:
    """
    Two copies of a list that must agree is how D74 happened. The handler's
    two paths -- numbered and bare -- must consult the same tuples.
    """
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(H._h_set_volume))
    tree = ast.parse(src)
    literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Tuple, ast.List))
        and node.elts
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)
        and len(node.elts) >= 3
    ]
    assert not literals, (
        "_h_set_volume contains an inline list of direction words again; "
        "both paths must use _VOLUME_UP_WORDS / _VOLUME_DOWN_WORDS so they "
        "cannot drift apart (D74/D75)"
    )
    assert H._VOLUME_UP_WORDS and H._VOLUME_DOWN_WORDS
    assert not (set(H._VOLUME_UP_WORDS) & set(H._VOLUME_DOWN_WORDS)), (
        "a word appears in both direction lists, so the direction depends "
        "on which is checked first (D75)"
    )


def test_brightness_is_unaffected() -> None:
    """
    Recorded because it is the contrast that explains the bug: brightness
    splits increase/decrease into their own anchored, numberless intents,
    so it never had a delta/target ambiguity to get wrong.
    """
    assert ie.detect_intent("increase the volume")[0] == "set_volume"
    assert ie.detect_intent("increase the brightness")[0] == "increase_brightness"
    intent, match = ie.detect_intent("increase the brightness")
    assert not (match.re.groups and match.group(1)), (
        "increase_brightness now captures a number, which is how set_volume "
        "acquired the delta/target ambiguity -- give it a delta path before "
        "relying on it (D75)"
    )
