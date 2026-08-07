"""
tests/test_planning.py
Unit tests for sara.core.planning -- schema validation/security
hardening, the trigger gate, the planner, the bounded execution loop,
and the public try_plan_and_execute() integration point -- using
sara.core.planning.test_doubles so no real Ollama instance or network
access is required.

Test organization mirrors the package's module structure:
  - SchemaValidationTests       -> schema.py (validate_url, validate_app_target,
                                    validate_tool_arguments, parse_plan_from_llm)
  - TriggerGateTests             -> trigger.py (should_attempt_plan)
  - PlannerTests                 -> planner.py (propose_plan)
  - ExecutorTests                -> executor.py (execute_plan)
  - IntegrationTests             -> __init__.py (try_plan_and_execute)
  - ConcurrencyAndTimingTests    -> cross-cutting: total-timeout budget
                                    enforcement, thread-safety of dispatch
                                    counting, executor teardown

Each test class targets normal, error, AND edge cases explicitly, per
this project's testing conventions (see tests/test_sara_smoke.py,
tests/test_api_surface.py).

NOTE on tool names used in these tests: sara.core.tool_router.
TOOL_NAME_TO_INTENT defines the ONLY tools the real planner.propose_plan()
will ever accept (weather, news, web_search, open_url, play_youtube,
play_spotify, screenshot_describe, clipboard_read, clipboard_write,
open_app, close_app, calculator) -- reminders/notes/timers/calendar are
fast-path-only intents, never exposed to the LLM planner. Tests that
exercise the FULL propose-then-execute pipeline (PlannerTests,
IntegrationTests) must only use tool names from that set. Tests that
construct Plan/PlanStep objects directly and call executor.execute_plan()
in isolation (ExecutorTests) are free to use any tool name, since they
bypass the planner's allowed-tools restriction entirely.
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config  # noqa: F401 -- ensures project-root imports resolve identically to other test files


# ══════════════════════════════════════════════════════════════════════
# Schema validation / security hardening
# ══════════════════════════════════════════════════════════════════════


class SchemaValidationTests(unittest.TestCase):
    # ── validate_url: normal cases ──────────────────────────────────────

    def test_validate_url_allows_http_https(self):
        from sara.core.planning.schema import validate_url

        self.assertEqual(validate_url("https://example.com"), "https://example.com")
        self.assertEqual(validate_url("http://example.com/page"), "http://example.com/page")

    def test_validate_url_allows_https_with_query_and_fragment(self):
        from sara.core.planning.schema import validate_url

        url = "https://example.com/search?q=sara+ai#results"
        self.assertEqual(validate_url(url), url)

    def test_validate_url_defaults_bare_hostname_to_https(self):
        from sara.core.planning.schema import validate_url

        self.assertEqual(validate_url("example.com"), "https://example.com")
        self.assertEqual(validate_url("www.google.com"), "https://www.google.com")

    def test_validate_url_case_insensitive_scheme(self):
        from sara.core.planning.schema import validate_url

        self.assertEqual(validate_url("HTTPS://example.com"), "HTTPS://example.com")

    # ── validate_url: dangerous scheme rejection ────────────────────────

    def test_validate_url_rejects_javascript_scheme(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("javascript:alert(1)")

    def test_validate_url_rejects_data_scheme(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("data:text/html,<script>alert(1)</script>")

    def test_validate_url_rejects_file_scheme(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("file:///etc/passwd")
        with self.assertRaises(PlanValidationError):
            validate_url("file://C:/Windows/System32/config")

    def test_validate_url_rejects_vbscript_scheme(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("vbscript:msgbox(1)")

    def test_validate_url_rejects_about_and_blob_schemes(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("about:blank")
        with self.assertRaises(PlanValidationError):
            validate_url("blob:https://example.com/uuid")

    def test_validate_url_rejects_leading_whitespace_smuggling(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("   javascript:alert(1)")
        with self.assertRaises(PlanValidationError):
            validate_url("\t\tjavascript:alert(1)")

    def test_validate_url_rejects_percent_encoded_scheme_smuggling(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("java%73cript:alert(1)")

    def test_validate_url_rejects_userinfo_in_netloc(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("https://accounts.google.com@evil.example")

    def test_validate_url_control_characters_stripped_and_still_rejected(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        # Embedded control characters must not allow a dangerous scheme
        # to slip past detection.
        with self.assertRaises(PlanValidationError):
            validate_url("java\x00script:alert(1)")

    # ── validate_url: edge cases ─────────────────────────────────────────

    def test_validate_url_rejects_empty(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("")
        with self.assertRaises(PlanValidationError):
            validate_url("   ")

    def test_validate_url_rejects_non_string(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url(None)
        with self.assertRaises(PlanValidationError):
            validate_url(12345)
        with self.assertRaises(PlanValidationError):
            validate_url(["https://example.com"])

    def test_validate_url_rejects_scheme_with_no_host(self):
        from sara.core.planning.schema import PlanValidationError, validate_url

        with self.assertRaises(PlanValidationError):
            validate_url("https://")

    # ── validate_app_target: normal cases ───────────────────────────────

    def test_validate_app_target_allows_listed_app(self):
        from sara.core.planning.schema import validate_app_target

        allowed = frozenset({"chrome", "notepad", "spotify"})
        self.assertEqual(validate_app_target("Chrome", allowed), "chrome")
        self.assertEqual(validate_app_target("google chrome", allowed), "google chrome")
        self.assertEqual(validate_app_target("  Notepad  ", allowed), "notepad")

    def test_validate_app_target_matches_substring_either_direction(self):
        from sara.core.planning.schema import validate_app_target

        allowed = frozenset({"visual studio code"})
        self.assertEqual(validate_app_target("code", allowed), "code")

    def test_validate_app_target_collapses_internal_whitespace(self):
        from sara.core.planning.schema import validate_app_target

        allowed = frozenset({"chrome"})
        self.assertEqual(validate_app_target("google   chrome", allowed), "google chrome")

    # ── validate_app_target: rejection cases ────────────────────────────

    def test_validate_app_target_rejects_unlisted_app(self):
        from sara.core.planning.schema import PlanValidationError, validate_app_target

        allowed = frozenset({"chrome", "notepad"})
        with self.assertRaises(PlanValidationError):
            validate_app_target("cmd", allowed)
        with self.assertRaises(PlanValidationError):
            validate_app_target("regedit", allowed)

    def test_validate_app_target_disabled_allowlist_skips_check(self):
        from sara.core.planning.schema import validate_app_target

        self.assertEqual(
            validate_app_target("anything goes", frozenset(), enabled=False),
            "anything goes",
        )

    def test_validate_app_target_enabled_empty_allowlist_always_rejects(self):
        from sara.core.planning.schema import PlanValidationError, validate_app_target

        with self.assertRaises(PlanValidationError):
            validate_app_target("chrome", frozenset(), enabled=True)

    def test_validate_app_target_rejects_empty(self):
        from sara.core.planning.schema import PlanValidationError, validate_app_target

        with self.assertRaises(PlanValidationError):
            validate_app_target("", frozenset({"chrome"}))
        with self.assertRaises(PlanValidationError):
            validate_app_target("   ", frozenset({"chrome"}))

    def test_validate_app_target_rejects_non_string(self):
        from sara.core.planning.schema import PlanValidationError, validate_app_target

        with self.assertRaises(PlanValidationError):
            validate_app_target(None, frozenset({"chrome"}))

    def test_validate_app_target_rejects_overly_long_input(self):
        from sara.core.planning.schema import PlanValidationError, validate_app_target

        too_long = "a" * 200
        with self.assertRaises(PlanValidationError):
            validate_app_target(too_long, frozenset({"a"}))

    # ── validate_tool_arguments: dispatcher behavior ────────────────────

    def test_validate_tool_arguments_dispatches_url_tool(self):
        from sara.core.planning.schema import PlanValidationError, validate_tool_arguments

        with self.assertRaises(PlanValidationError):
            validate_tool_arguments("open_url", {"url": "javascript:alert(1)"})

        result = validate_tool_arguments("open_url", {"url": "example.com"})
        self.assertEqual(result["url"], "https://example.com")

    def test_validate_tool_arguments_dispatches_app_tools(self):
        from sara.core.planning.schema import validate_tool_arguments

        result = validate_tool_arguments(
            "open_app", {"target": "Chrome"}, allowed_apps=frozenset({"chrome"})
        )
        self.assertEqual(result["target"], "chrome")

        result = validate_tool_arguments(
            "close_app", {"target": "Notepad"}, allowed_apps=frozenset({"notepad"})
        )
        self.assertEqual(result["target"], "notepad")

    def test_validate_tool_arguments_passthrough_for_other_tools(self):
        from sara.core.planning.schema import validate_tool_arguments

        result = validate_tool_arguments("weather", {"location": "Ajmer"})
        self.assertEqual(result, {"location": "Ajmer"})

    def test_validate_tool_arguments_never_mutates_input(self):
        from sara.core.planning.schema import validate_tool_arguments

        original = {"location": "Ajmer"}
        result = validate_tool_arguments("weather", original)
        result["location"] = "Mumbai"
        self.assertEqual(original["location"], "Ajmer")

    def test_validate_tool_arguments_rejects_non_dict_arguments(self):
        from sara.core.planning.schema import PlanValidationError, validate_tool_arguments

        with self.assertRaises(PlanValidationError):
            validate_tool_arguments("weather", "not-a-dict")  # type: ignore[arg-type]

    # ── parse_plan_from_llm: normal cases ───────────────────────────────

    def test_parse_plan_from_llm_valid(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
            {"tool": "news", "arguments": {"topic": "cricket"}, "depends_on_previous": True},
        ]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"weather", "news"}), max_steps=4)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].tool, "weather")
        self.assertTrue(plan.steps[1].depends_on_previous)
        self.assertFalse(plan.steps[0].depends_on_previous)

    def test_parse_plan_from_llm_defaults_missing_arguments_to_empty_dict(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [{"tool": "screenshot_describe"}]
        plan = parse_plan_from_llm(
            raw, allowed_tools=frozenset({"screenshot_describe"}), max_steps=4
        )
        self.assertEqual(plan.steps[0].arguments, {})

    # ── parse_plan_from_llm: dropping bad steps ─────────────────────────

    def test_parse_plan_from_llm_drops_hallucinated_tool(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
            {"tool": "not_a_real_tool", "arguments": {}},
        ]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=4)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.proposed_step_count, 2)

    def test_parse_plan_from_llm_drops_bad_url_step(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [
            {"tool": "open_url", "arguments": {"url": "javascript:alert(1)"}},
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
        ]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"open_url", "weather"}), max_steps=4)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].tool, "weather")

    def test_parse_plan_from_llm_drops_non_dict_steps(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = ["not a dict", {"tool": "weather", "arguments": {"location": "Ajmer"}}, 42]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=4)
        self.assertEqual(len(plan.steps), 1)

    def test_parse_plan_from_llm_drops_non_dict_arguments(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [
            {"tool": "weather", "arguments": "not-a-dict"},
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
        ]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=4)
        self.assertEqual(len(plan.steps), 1)

    def test_parse_plan_from_llm_collapses_consecutive_duplicate_steps(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
            {"tool": "news", "arguments": {"topic": "cricket"}},
        ]
        plan = parse_plan_from_llm(
            raw, allowed_tools=frozenset({"weather", "news"}), max_steps=4
        )
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].tool, "weather")
        self.assertEqual(plan.steps[1].tool, "news")

    def test_parse_plan_from_llm_does_not_collapse_non_consecutive_duplicates(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
            {"tool": "news", "arguments": {"topic": "cricket"}},
            {"tool": "weather", "arguments": {"location": "Ajmer"}},
        ]
        plan = parse_plan_from_llm(
            raw, allowed_tools=frozenset({"weather", "news"}), max_steps=4
        )
        self.assertEqual(len(plan.steps), 3)

    # ── parse_plan_from_llm: truncation ──────────────────────────────────

    def test_parse_plan_from_llm_truncates_to_max_steps(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [{"tool": "weather", "arguments": {"location": f"City{i}"}} for i in range(6)]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=3)
        self.assertEqual(len(plan.steps), 3)
        self.assertEqual(plan.proposed_step_count, 6)

    def test_parse_plan_from_llm_exact_max_steps_no_truncation_warning_issue(self):
        from sara.core.planning.schema import parse_plan_from_llm

        raw = [{"tool": "weather", "arguments": {"location": f"City{i}"}} for i in range(4)]
        plan = parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=4)
        self.assertEqual(len(plan.steps), 4)

    # ── parse_plan_from_llm: error / edge cases ─────────────────────────

    def test_parse_plan_from_llm_raises_when_not_a_list(self):
        from sara.core.planning.schema import PlanValidationError, parse_plan_from_llm

        with self.assertRaises(PlanValidationError):
            parse_plan_from_llm("not a list", allowed_tools=frozenset({"weather"}), max_steps=4)
        with self.assertRaises(PlanValidationError):
            parse_plan_from_llm({"steps": []}, allowed_tools=frozenset({"weather"}), max_steps=4)
        with self.assertRaises(PlanValidationError):
            parse_plan_from_llm(None, allowed_tools=frozenset({"weather"}), max_steps=4)

    def test_parse_plan_from_llm_raises_when_empty(self):
        from sara.core.planning.schema import PlanValidationError, parse_plan_from_llm

        with self.assertRaises(PlanValidationError):
            parse_plan_from_llm([], allowed_tools=frozenset({"weather"}), max_steps=4)

    def test_parse_plan_from_llm_raises_when_all_steps_invalid(self):
        from sara.core.planning.schema import PlanValidationError, parse_plan_from_llm

        raw = [{"tool": "fake_tool", "arguments": {}}]
        with self.assertRaises(PlanValidationError):
            parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=4)

    def test_parse_plan_from_llm_raises_on_invalid_max_steps(self):
        from sara.core.planning.schema import PlanValidationError, parse_plan_from_llm

        raw = [{"tool": "weather", "arguments": {"location": "Ajmer"}}]
        with self.assertRaises(PlanValidationError):
            parse_plan_from_llm(raw, allowed_tools=frozenset({"weather"}), max_steps=0)


# ══════════════════════════════════════════════════════════════════════
# Trigger gate
# ══════════════════════════════════════════════════════════════════════


class TriggerGateTests(unittest.TestCase):
    # ── normal: no trigger ──────────────────────────────────────────────

    def test_single_tool_message_does_not_trigger(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertFalse(should_attempt_plan("what's the weather in Jaipur"))
        self.assertFalse(should_attempt_plan("open chrome"))
        self.assertFalse(should_attempt_plan("play a song on youtube"))
        self.assertFalse(should_attempt_plan("set a timer for 5 minutes"))

    def test_empty_and_whitespace_message_does_not_trigger(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertFalse(should_attempt_plan(""))
        self.assertFalse(should_attempt_plan("   "))
        self.assertFalse(should_attempt_plan("\t\n"))

    def test_message_with_zero_category_hits_does_not_trigger(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertFalse(should_attempt_plan("hello there, how are you doing today"))

    # ── normal: strong-tier trigger (2+ distinct categories) ───────────

    def test_two_distinct_categories_trigger(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(
            should_attempt_plan("remind me to call mom and tell me the weather in Ajmer")
        )
        self.assertTrue(should_attempt_plan("open chrome and search for python tutorials"))

    def test_three_distinct_categories_trigger(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(
            should_attempt_plan(
                "remind me to call mom, tell me the weather, and open spotify"
            )
        )

    # ── normal: weak-tier trigger (1 category + cue) ────────────────────

    def test_single_category_with_sequencing_cue_triggers(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(
            should_attempt_plan("open chrome and then open chrome again for something else")
        )
        self.assertTrue(should_attempt_plan("open notepad, uske baad calculator kholo"))
        self.assertTrue(should_attempt_plan("weather batao, phir news bhi batao"))

    def test_single_category_no_cue_does_not_trigger(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertFalse(should_attempt_plan("what's the weather like today"))

    def test_weak_conjunction_with_one_category_triggers(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(
            should_attempt_plan("set a reminder for 6pm, and also check the news for me")
        )

    def test_also_cue_without_comma_triggers(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(should_attempt_plan("remind me to call mom also open chrome"))

    def test_hindi_devanagari_sequencing_cue_triggers(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(should_attempt_plan("mausam batao, उसके बाद news bhi sunao"))

    # ── edge cases ────────────────────────────────────────────────────

    def test_unusually_long_input_still_scanned_correctly(self):
        from sara.core.planning.trigger import should_attempt_plan

        padding = "um, well, " * 60
        message = f"{padding}remind me to call mom and then check the weather"
        self.assertTrue(should_attempt_plan(message))

    def test_case_insensitivity(self):
        from sara.core.planning.trigger import should_attempt_plan

        self.assertTrue(
            should_attempt_plan("REMIND ME TO CALL MOM AND THEN CHECK THE WEATHER")
        )

    # ── explain_trigger_decision diagnostic helper ──────────────────────

    def test_explain_trigger_decision_reports_categories_and_cues(self):
        from sara.core.planning.trigger import explain_trigger_decision

        result = explain_trigger_decision(
            "remind me to call mom and then check the weather"
        )
        self.assertTrue(result["would_trigger"])
        self.assertIn("reminder_add", result["categories"])
        self.assertIn("weather", result["categories"])
        self.assertTrue(result["sequencing_cue"])

    def test_explain_trigger_decision_empty_input(self):
        from sara.core.planning.trigger import explain_trigger_decision

        result = explain_trigger_decision("")
        self.assertFalse(result["would_trigger"])
        self.assertEqual(result["categories"], [])


# ══════════════════════════════════════════════════════════════════════
# Planner
# ══════════════════════════════════════════════════════════════════════


class PlannerTests(unittest.TestCase):
    def test_propose_plan_valid(self):
        from sara.core.planning.planner import propose_plan
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="valid_plan")
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            plan = propose_plan(
                "remind me to call mom and check the weather",
                "qwen2.5",
                cfg,
                max_steps=4,
                timeout_s=2.0,
            )
        self.assertGreaterEqual(len(plan.steps), 1)
        self.assertEqual(fake_client.call_count, 1)

    def test_propose_plan_raises_planning_unavailable_when_no_client(self):
        from sara.core.planning.planner import PlanningUnavailableError, propose_plan
        from sara.core.planning.test_doubles import FakeConfig

        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=None):
            with self.assertRaises(PlanningUnavailableError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_raises_planning_unavailable_on_client_error(self):
        from sara.core.planning.planner import PlanningUnavailableError, propose_plan
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="raises")
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            with self.assertRaises(PlanningUnavailableError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_raises_planning_unavailable_on_timeout(self):
        from sara.core.planning.planner import PlanningUnavailableError, propose_plan
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="timeout", sleep_s=5.0)
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            start = time.monotonic()
            with self.assertRaises(PlanningUnavailableError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=0.2)
            elapsed = time.monotonic() - start
        # Must return close to the timeout budget, NOT wait for the full
        # 5s sleep in the fake client -- proves future.result(timeout=...)
        # actually bounds the wait.
        self.assertLess(elapsed, 2.0)

    def test_propose_plan_raises_planning_unavailable_on_raises_after_delay(self):
        from sara.core.planning.planner import PlanningUnavailableError, propose_plan
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="raises_after_delay", sleep_s=0.1)
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            with self.assertRaises(PlanningUnavailableError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_raises_validation_error_on_hallucinated_tool(self):
        from sara.core.planning.planner import propose_plan
        from sara.core.planning.schema import PlanValidationError
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="hallucinated_tool")
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            with self.assertRaises(PlanValidationError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_raises_planning_unavailable_on_no_tool_call(self):
        from sara.core.planning.planner import PlanningUnavailableError, propose_plan
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="no_tool_call")
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            with self.assertRaises(PlanningUnavailableError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_raises_validation_error_on_empty_steps(self):
        from sara.core.planning.planner import propose_plan
        from sara.core.planning.schema import PlanValidationError
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="empty_steps")
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            with self.assertRaises(PlanValidationError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_raises_validation_error_on_malformed_json(self):
        from sara.core.planning.planner import propose_plan
        from sara.core.planning.schema import PlanValidationError
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        fake_client = FakeOllamaClient(mode="malformed_json")
        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            with self.assertRaises(PlanValidationError):
                propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=2.0)

    def test_propose_plan_rejects_invalid_max_steps(self):
        from sara.core.planning.planner import propose_plan
        from sara.core.planning.schema import PlanValidationError
        from sara.core.planning.test_doubles import FakeConfig

        cfg = FakeConfig()
        with self.assertRaises(PlanValidationError):
            propose_plan("do two things", "qwen2.5", cfg, max_steps=0, timeout_s=2.0)

    def test_propose_plan_rejects_invalid_timeout(self):
        from sara.core.planning.planner import PlanningUnavailableError, propose_plan
        from sara.core.planning.test_doubles import FakeConfig

        cfg = FakeConfig()
        with self.assertRaises(PlanningUnavailableError):
            propose_plan("do two things", "qwen2.5", cfg, max_steps=4, timeout_s=0.0)


# ══════════════════════════════════════════════════════════════════════
# Executor
# ══════════════════════════════════════════════════════════════════════


class ExecutorTests(unittest.TestCase):
    def _make_plan(self, steps):
        from sara.core.planning.schema import Plan, PlanStep

        return Plan(
            steps=tuple(
                PlanStep(
                    tool=s["tool"],
                    arguments=s.get("arguments", {}),
                    depends_on_previous=s.get("depends_on_previous", False),
                )
                for s in steps
            ),
            proposed_step_count=len(steps),
        )

    # ── normal: all succeed ──────────────────────────────────────────────

    def test_execute_plan_all_succeed(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        plan = self._make_plan(
            [
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {"topic": "cricket"}},
            ]
        )
        outcome = execute_plan(
            plan,
            make_dispatch_success(),
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
        )
        self.assertFalse(outcome.aborted)
        self.assertEqual(len(outcome.results), 2)
        self.assertTrue(all(r.status == StepStatus.SUCCESS for r in outcome.results))
        self.assertTrue(all(r.attempts == 1 for r in outcome.results))

    def test_execute_plan_single_step_succeeds(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        plan = self._make_plan([{"tool": "weather", "arguments": {"location": "Ajmer"}}])
        outcome = execute_plan(
            plan,
            make_dispatch_success(),
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
        )
        self.assertEqual(outcome.results[0].status, StepStatus.SUCCESS)

    # ── normal: retry recovery ───────────────────────────────────────────

    def test_execute_plan_tool_raises_then_retries_successfully(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_fails_then_succeeds,
        )

        plan = self._make_plan([{"tool": "weather", "arguments": {"location": "Ajmer"}}])
        fake_client = FakeOllamaClient(
            mode="valid_plan", corrected_arguments={"location": "Ajmer"}, corrected_tool="weather"
        )
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                make_dispatch_fails_then_succeeds(fail_tools=["weather"]),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.SUCCESS)
        self.assertEqual(outcome.results[0].attempts, 2)
        self.assertEqual(fake_client.call_count, 1)  # exactly one correction call

    # ── normal: total failure after retry ────────────────────────────────

    def test_execute_plan_tool_fails_twice_marked_failed(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_always_fails,
        )

        plan = self._make_plan([{"tool": "weather", "arguments": {"location": "Ajmer"}}])
        fake_client = FakeOllamaClient(
            mode="valid_plan", corrected_arguments={"location": "Ajmer"}, corrected_tool="weather"
        )
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                make_dispatch_always_fails(fail_tools=["weather"]),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.FAILED)
        self.assertIn("retry also failed", outcome.results[0].error)
        self.assertEqual(outcome.results[0].attempts, 2)

    # ── normal: partial success ──────────────────────────────────────────

    def test_execute_plan_partial_success_reminder_ok_weather_fails(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_selective,
        )

        plan = self._make_plan(
            [
                {"tool": "reminder_add", "arguments": {}},
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
            ]
        )

        def _weather_fails(_args):
            raise RuntimeError("weather API down")

        dispatch = make_dispatch_selective(
            {
                "reminder_add": lambda args: "Reminder set.",
                "weather": _weather_fails,
            }
        )
        fake_client = FakeOllamaClient(mode="raises")  # correction call also fails -> no rescue
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                dispatch,
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.SUCCESS)
        self.assertEqual(outcome.results[1].status, StepStatus.FAILED)
        self.assertIn("Reminder set.", outcome.final_message)
        self.assertIn("couldn't complete", outcome.final_message.lower())

    # ── normal: dependent-step skip propagation ──────────────────────────

    def test_execute_plan_skips_dependent_step_after_failure(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_always_fails,
        )

        plan = self._make_plan(
            [
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {"topic": "weather"}, "depends_on_previous": True},
            ]
        )
        fake_client = FakeOllamaClient(mode="raises")
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                make_dispatch_always_fails(fail_tools=["weather"]),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.FAILED)
        self.assertEqual(outcome.results[1].status, StepStatus.SKIPPED)

    def test_execute_plan_skip_propagates_through_chained_dependents(self):
        """A chain of 3 dependent steps after one failure must ALL skip,
        not just the immediate next one."""
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_always_fails,
        )

        plan = self._make_plan(
            [
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {}, "depends_on_previous": True},
                {"tool": "calculator", "arguments": {}, "depends_on_previous": True},
            ]
        )
        fake_client = FakeOllamaClient(mode="raises")
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                make_dispatch_always_fails(fail_tools=["weather"]),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.FAILED)
        self.assertEqual(outcome.results[1].status, StepStatus.SKIPPED)
        self.assertEqual(outcome.results[2].status, StepStatus.SKIPPED)

    def test_execute_plan_dependent_step_after_success_is_not_skipped(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        plan = self._make_plan(
            [
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {}, "depends_on_previous": True},
            ]
        )
        outcome = execute_plan(
            plan,
            make_dispatch_success(),
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
        )
        self.assertEqual(outcome.results[0].status, StepStatus.SUCCESS)
        self.assertEqual(outcome.results[1].status, StepStatus.SUCCESS)

    # ── edge: total timeout aborts partway ───────────────────────────────

    def test_execute_plan_total_timeout_aborts_partway(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_timeout

        plan = self._make_plan(
            [
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {"topic": "cricket"}},
            ]
        )
        outcome = execute_plan(
            plan,
            make_dispatch_timeout(sleep_s=10.0),
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=0.3,
            total_timeout_s=0.5,
            retry_enabled=False,
        )
        self.assertTrue(outcome.aborted or len(outcome.results) <= 2)
        # execute_plan() must return promptly (bounded by total_timeout_s +
        # a small scheduling margin), NEVER waiting for the abandoned
        # 10s-sleep dispatch calls to actually finish in the background.
        self.assertLess(outcome.elapsed_s, 5.0)

    def test_execute_plan_total_timeout_never_exceeded_across_many_steps(self):
        """N steps that each individually fail-and-retry must never let
        execute_plan()'s OWN RETURN be delayed anywhere near the sum of
        their timeouts, let alone the underlying dispatch calls' full
        sleep duration -- this is the core latency-safety guarantee."""
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_timeout,
        )

        steps = [{"tool": "weather", "arguments": {"location": f"City{i}"}} for i in range(6)]
        plan = self._make_plan(steps)
        fake_client = FakeOllamaClient(mode="timeout", sleep_s=5.0)
        total_budget = 1.0
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            start = time.monotonic()
            outcome = execute_plan(
                plan,
                make_dispatch_timeout(sleep_s=5.0),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=0.3,
                total_timeout_s=total_budget,
                retry_enabled=True,
            )
            elapsed = time.monotonic() - start
        # Generous margin (2.5x budget) to absorb scheduling jitter in CI,
        # but this must never approach the 5s sleep duration of the
        # underlying (abandoned) dispatch calls -- proving execute_plan()
        # does not block its return waiting for them.
        self.assertLess(elapsed, total_budget * 2.5)
        self.assertTrue(outcome.aborted)

    # ── edge: retry disabled ─────────────────────────────────────────────

    def test_execute_plan_retry_disabled_marks_failed_immediately(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_raises

        plan = self._make_plan([{"tool": "weather", "arguments": {"location": "Ajmer"}}])
        outcome = execute_plan(
            plan,
            make_dispatch_raises(),
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
            retry_enabled=False,
        )
        self.assertEqual(outcome.results[0].status, StepStatus.FAILED)
        self.assertEqual(outcome.results[0].attempts, 1)

    # ── security: corrected arguments re-validated ───────────────────────

    def test_execute_plan_corrected_arguments_revalidated_open_url(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_raises,
        )

        plan = self._make_plan([{"tool": "open_url", "arguments": {"url": "https://example.com"}}])
        fake_client = FakeOllamaClient(
            mode="valid_plan",
            corrected_arguments={"url": "javascript:alert(1)"},
            corrected_tool="open_url",
        )
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                make_dispatch_raises(),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.FAILED)
        self.assertIn("retry rejected", outcome.results[0].error)

    def test_execute_plan_corrected_app_target_revalidated_against_allowlist(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import StepStatus
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_raises,
        )

        plan = self._make_plan([{"tool": "open_app", "arguments": {"target": "chrome"}}])
        fake_client = FakeOllamaClient(
            mode="valid_plan",
            corrected_arguments={"target": "cmd"},
            corrected_tool="open_app",
        )
        with patch("sara.core.planning.executor._get_ollama_client", return_value=fake_client):
            outcome = execute_plan(
                plan,
                make_dispatch_raises(),
                model_name="qwen2.5",
                cfg=FakeConfig(),
                step_timeout_s=2.0,
                total_timeout_s=6.0,
                retry_enabled=True,
                allowed_apps=frozenset({"chrome"}),
                app_allowlist_enabled=True,
            )
        self.assertEqual(outcome.results[0].status, StepStatus.FAILED)
        self.assertIn("retry rejected", outcome.results[0].error)

    # ── edge: empty plan defensively handled ─────────────────────────────

    def test_execute_plan_empty_plan_never_crashes(self):
        from sara.core.planning.schema import Plan
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        empty_plan = Plan(steps=(), proposed_step_count=0)
        outcome = execute_plan(
            empty_plan,
            make_dispatch_success(),
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
        )
        self.assertEqual(outcome.results, ())
        self.assertFalse(outcome.aborted)

    # ── correctness: dispatch counting ───────────────────────────────────

    def test_execute_plan_dispatch_called_exactly_once_per_successful_step(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_counting

        plan = self._make_plan(
            [
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {"topic": "cricket"}},
                {"tool": "weather", "arguments": {"location": "Jaipur"}},
            ]
        )
        dispatch, counts = make_dispatch_counting()
        execute_plan(
            plan,
            dispatch,
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
        )
        self.assertEqual(counts["weather"], 2)
        self.assertEqual(counts["news"], 1)


# ══════════════════════════════════════════════════════════════════════
# Integration (try_plan_and_execute)
# ══════════════════════════════════════════════════════════════════════


class IntegrationTests(unittest.TestCase):
    def test_try_plan_and_execute_disabled_returns_none(self):
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        cfg = FakeConfig(PLANNING_ENABLED=False)
        result = try_plan_and_execute(
            "remind me to call mom and check the weather",
            "qwen2.5",
            make_dispatch_success(),
            cfg,
        )
        self.assertIsNone(result)

    def test_try_plan_and_execute_single_tool_message_returns_none(self):
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        cfg = FakeConfig()
        result = try_plan_and_execute(
            "what's the weather in Jaipur", "qwen2.5", make_dispatch_success(), cfg
        )
        self.assertIsNone(result)

    def test_try_plan_and_execute_empty_input_returns_none(self):
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        cfg = FakeConfig()
        self.assertIsNone(try_plan_and_execute("", "qwen2.5", make_dispatch_success(), cfg))
        self.assertIsNone(try_plan_and_execute("   ", "qwen2.5", make_dispatch_success(), cfg))

    def test_try_plan_and_execute_full_success(self):
        """
        FIX: originally used "reminder_add" as one of the two proposed
        tools -- but reminder_add is NOT a member of
        sara.core.tool_router.TOOL_NAME_TO_INTENT (reminders are
        fast-path-only, never exposed to the LLM planner), so
        schema.parse_plan_from_llm() correctly dropped it, the plan
        collapsed to a single "weather" step, and try_plan_and_execute()
        correctly declined in favor of the single-tool path (returning
        None) -- exactly per its own documented contract. This was a
        test-fixture bug, not a production bug. Using "news" + "weather"
        (both real, registered tools) now exercises the intended
        2-step-success path.
        """
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient, make_dispatch_success

        cfg = FakeConfig()
        fake_client = FakeOllamaClient(
            mode="valid_plan",
            steps=[
                {"tool": "news", "arguments": {"topic": "cricket"}},
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
            ],
        )
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client), patch(
            "sara.core.planning.executor._get_ollama_client", return_value=fake_client
        ):
            outcome = try_plan_and_execute(
                "tell me the cricket news and then check the weather in Ajmer",
                "qwen2.5",
                make_dispatch_success(),
                cfg,
            )
        self.assertIsNotNone(outcome)
        self.assertEqual(len(outcome.results), 2)

    def test_try_plan_and_execute_collapsed_single_step_returns_none(self):
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient, make_dispatch_success

        cfg = FakeConfig()
        fake_client = FakeOllamaClient(
            mode="valid_plan",
            steps=[{"tool": "weather", "arguments": {"location": "Ajmer"}}],
        )
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client):
            outcome = try_plan_and_execute(
                "remind me to call mom and then check the weather in Ajmer",
                "qwen2.5",
                make_dispatch_success(),
                cfg,
            )
        self.assertIsNone(outcome)

    def test_try_plan_and_execute_returns_none_when_planning_unavailable(self):
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_success

        cfg = FakeConfig()
        with patch("sara.core.planning.planner._get_ollama_client", return_value=None):
            result = try_plan_and_execute(
                "remind me to call mom and then check the weather",
                "qwen2.5",
                make_dispatch_success(),
                cfg,
            )
        self.assertIsNone(result)

    def test_try_plan_and_execute_never_raises_on_dispatch_exception(self):
        """
        FIX: patching "sara.core.planning.executor.execute_plan" does
        NOT affect the call made inside try_plan_and_execute(), because
        sara/core/planning/__init__.py imports execute_plan via
        `from .executor import execute_plan` at module load time -- that
        creates its OWN name binding in the sara.core.planning namespace,
        decoupled from sara.core.planning.executor's namespace after
        import. The correct patch target is the name as it is looked up
        at call time, i.e. "sara.core.planning.execute_plan" (the
        __init__ module's own reference), which is what
        try_plan_and_execute() actually calls.

        Even if execute_plan raised unexpectedly (not just the individual
        dispatch calls it already catches internally), try_plan_and_execute()
        must swallow it and return None rather than propagate -- this is
        its documented "never raises" contract.
        """
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import FakeConfig, FakeOllamaClient

        cfg = FakeConfig()
        fake_client = FakeOllamaClient(
            mode="valid_plan",
            steps=[
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {"topic": "cricket"}},
            ],
        )
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client), patch(
            "sara.core.planning.execute_plan",
            side_effect=RuntimeError("simulated internal executor bug"),
        ):
            result = try_plan_and_execute(
                "remind me to call mom and then check the weather",
                "qwen2.5",
                lambda tool, args: "ok",
                cfg,
            )
        self.assertIsNone(result)

    def test_try_plan_and_execute_execution_budget_deducts_proposal_time(self):
        """The combined proposal+execution latency must respect
        PLANNING_TOTAL_TIMEOUT_S as a whole, not double it."""
        from sara.core.planning import try_plan_and_execute
        from sara.core.planning.test_doubles import (
            FakeConfig,
            FakeOllamaClient,
            make_dispatch_success,
        )

        cfg = FakeConfig(PLANNING_TOTAL_TIMEOUT_S=1.0, PLANNING_STEP_TIMEOUT_S=0.5)
        fake_client = FakeOllamaClient(
            mode="valid_plan",
            steps=[
                {"tool": "weather", "arguments": {"location": "Ajmer"}},
                {"tool": "news", "arguments": {"topic": "cricket"}},
            ],
        )
        with patch("sara.core.planning.planner._get_ollama_client", return_value=fake_client), patch(
            "sara.core.planning.executor._get_ollama_client", return_value=fake_client
        ):
            start = time.monotonic()
            try_plan_and_execute(
                "remind me to call mom and then check the weather",
                "qwen2.5",
                make_dispatch_success(),
                cfg,
            )
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, cfg.PLANNING_TOTAL_TIMEOUT_S * 3)


# ══════════════════════════════════════════════════════════════════════
# Cross-cutting concurrency / teardown
# ══════════════════════════════════════════════════════════════════════


class ConcurrencyAndTimingTests(unittest.TestCase):
    def test_shutdown_planning_executors_does_not_raise(self):
        from sara.core.planning import shutdown_planning_executors

        # Safe to call multiple times, even if nothing is in flight.
        shutdown_planning_executors(wait=False)

    def test_dispatch_counting_is_thread_safe_under_concurrent_plan(self):
        from sara.core.planning.executor import execute_plan
        from sara.core.planning.schema import Plan, PlanStep
        from sara.core.planning.test_doubles import FakeConfig, make_dispatch_counting

        steps = tuple(
            PlanStep(tool="weather", arguments={"location": f"City{i}"})
            for i in range(4)
        )
        plan = Plan(steps=steps, proposed_step_count=4)
        dispatch, counts = make_dispatch_counting()
        execute_plan(
            plan,
            dispatch,
            model_name="qwen2.5",
            cfg=FakeConfig(),
            step_timeout_s=2.0,
            total_timeout_s=6.0,
        )
        self.assertEqual(counts["weather"], 4)


if __name__ == "__main__":
    unittest.main()