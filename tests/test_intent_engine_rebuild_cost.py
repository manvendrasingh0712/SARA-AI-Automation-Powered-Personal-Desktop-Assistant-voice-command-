"""
Regression tests for the intent-engine rebuild cost (audit batch 6b).

THE DEFECT
----------
engine._rebuild_routes() recompiles EVERY intent's regex patterns, and
register_intent() calls it once per registration. Importing sara/skills/ fires
14 registrations, so the whole table was rebuilt 14 times over. Measured on the
pre-fix tree:

    intents in table        : 140
    regex patterns in table : 733
    re.compile during sara.skills import : 12,703   (14 full rebuilds)
    import sara.orchestrator             : ~4.6 s

_merge_groupless() is pure -- the same pattern strings always yield the same
compiled objects -- so its compile step is now memoised
(_merge_groupless_cached). After the fix:

    re.compile during sara.skills import : 586
    import sara.orchestrator             : ~1.5 s

These tests assert COMPILE COUNTS rather than wall-clock time, so they are
deterministic and cannot go flaky on a loaded CI machine.
"""
from __future__ import annotations

import re

import pytest

from sara.core.intent import engine


def _uncached_merge(patterns):
    """The original, pre-fix algorithm, kept here as the reference."""
    compiled_each = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
    if len(patterns) > 1 and all(c.groups == 0 for c in compiled_each):
        merged = "|".join(f"(?:{p})" for p in patterns)
        return [re.compile(merged, re.IGNORECASE | re.UNICODE)]
    return compiled_each


def _signature(compiled_list):
    return tuple((c.pattern, c.flags, c.groups) for c in compiled_list)


def test_route_table_matches_the_uncached_algorithm() -> None:
    """
    The cache must not change WHAT is built, only how often. Compares the live
    route table against the original algorithm, pattern by pattern.
    """
    live = [(name, _signature(compiled)) for name, compiled in engine._COMPILED_PATTERNS]
    reference = [
        (name, _signature(_uncached_merge(patterns)))
        for name, patterns in engine._INTENT_PATTERNS
    ]
    assert live == reference, "cached route table diverged from the original algorithm"


def test_routes_stay_in_step_with_compiled_patterns() -> None:
    assert len(engine._ROUTES) == len(engine._COMPILED_PATTERNS)
    assert [r[0] for r in engine._ROUTES] == [n for n, _ in engine._COMPILED_PATTERNS]


def test_repeated_rebuilds_do_not_recompile(monkeypatch) -> None:
    """
    The actual regression guard: rebuilding the route table N times must not
    cost N times the compilations. Before the fix this was linear in
    (rebuilds x patterns) and dominated startup.
    """
    engine._rebuild_routes()  # warm the cache for the current table

    calls = {"n": 0}
    real_compile = re.compile

    def counting_compile(*args, **kwargs):
        calls["n"] += 1
        return real_compile(*args, **kwargs)

    monkeypatch.setattr(engine.re, "compile", counting_compile)
    for _ in range(10):
        engine._rebuild_routes()

    total_patterns = sum(len(p) for _, p in engine._INTENT_PATTERNS)
    assert total_patterns > 100, "sanity: the pattern table should be large"
    assert calls["n"] == 0, (
        f"{calls['n']} regex compilations across 10 warm rebuilds of "
        f"{total_patterns} patterns -- the memoisation is not working"
    )


def test_merge_groupless_returns_a_fresh_list_each_call() -> None:
    """
    Callers may treat the result as their own, so the cache must hand back a
    new list over shared (immutable) compiled objects.
    """
    first = engine._merge_groupless(["alpha", "beta"])
    second = engine._merge_groupless(["alpha", "beta"])
    assert first is not second, "callers must not share one mutable list"
    assert first[0] is second[0], "compiled objects should be reused"

    first.append("mutated")
    third = engine._merge_groupless(["alpha", "beta"])
    assert len(third) == 1, "mutating a returned list must not corrupt the cache"


@pytest.mark.parametrize(
    "patterns",
    [
        ["solo"],
        ["one", "two", "three"],
        [r"capture (\w+)", "plain"],
        [r"(\d+) first", r"(\d+) second"],
    ],
)
def test_cached_and_uncached_agree(patterns) -> None:
    assert _signature(engine._merge_groupless(patterns)) == _signature(
        _uncached_merge(patterns)
    )


def test_registering_an_intent_still_makes_it_match() -> None:
    """
    The cache must not break the thing _rebuild_routes() exists for: a newly
    registered intent has to start matching immediately.
    """
    from sara.core.intent import detect_intent, register_intent

    name = "_audit_probe_intent"
    register_intent(name, [r"\bzzqq audit probe\b"], gate=("zzqq",))
    try:
        assert detect_intent("zzqq audit probe")[0] == name
    finally:
        engine._INTENT_PATTERNS[:] = [e for e in engine._INTENT_PATTERNS if e[0] != name]
        engine._INTENT_GATES.pop(name, None)
        engine._rebuild_routes()
        try:
            engine._detect_intent_cached.cache_clear()
        except AttributeError:
            pass
