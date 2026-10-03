"""tests/test_security_tiers.py -- every registered intent has exactly one reviewed tier."""

import sara.orchestrator.intent_handlers  # noqa: F401  (registers skill intents)
from sara.core.intent import patterns
from sara.core.security import tiers

REGISTERED = [name for name, _ in patterns._INTENT_PATTERNS]


def test_every_registered_intent_has_a_tier():
    missing = [n for n in REGISTERED if n not in tiers.TIERS]
    assert not missing, missing


def test_no_extra_names_in_tier_table():
    extra = sorted(set(tiers.TIERS) - set(REGISTERED))
    assert not extra, extra


def test_tier_values_are_valid():
    assert set(tiers.TIERS.values()) <= {0, 1, 2, 3}


def test_unknown_intent_is_fail_safe():
    assert tiers.tier_of("totally_unknown_tool") == tiers.UNKNOWN_TIER == 2


def test_confirm_state_alias_resolves():
    assert tiers.tier_of("forget_all_memories") == tiers.tier_of("memory_forget_all") == 3


def test_destructive_intents_are_t3():
    for name in ("shutdown_system", "restart_system", "log_off", "empty_recycle_bin", "clear_notes", "memory_forget_all"):
        assert tiers.tier_of(name) == 3, name


def test_read_only_intents_are_t0():
    for name in ("time_query", "weather", "calculator", "web_search", "system_info"):
        assert tiers.tier_of(name) == 0, name


def test_close_and_keystroke_intents_are_at_least_t2():
    for name in ("close_app", "typing_text", "press_key", "move_window", "calendar_create"):
        assert tiers.tier_of(name) >= 2, name
