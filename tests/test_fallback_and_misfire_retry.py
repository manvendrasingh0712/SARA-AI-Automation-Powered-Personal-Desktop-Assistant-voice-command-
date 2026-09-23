"""
tests/test_fallback_and_misfire_retry.py

Focused regression tests for two recent changes:

1. engine.py: SaraLLM tracks which stage (primary backend vs. the local
   Ollama fallback stage) actually served the most recently completed
   turn, exposed via used_fallback_last_turn().
2. intent_handlers.py: _handle_command()'s fast-path dispatch gives a
   handler's "likely misfire" reply (a matched-but-untrusted target,
   e.g. an unresolved pronoun -- see _LikelyMisfireReply) exactly one
   retry through the existing single-tool LLM resolution path before
   giving up, while a genuine/expected failure is left untouched.

Follows the same style as test_sara_smoke.py: plain unittest.TestCase
classes, lightweight hand-rolled Fake* stand-ins instead of a mocking
framework, and manual save/restore of any module-level state a test
temporarily overrides.
"""
import os
import sys
import unittest

# See tests/test_sara_smoke.py for why this is needed: running this file
# directly (`python tests/test_fallback_and_misfire_retry.py`) only puts
# tests/ on sys.path, not the project root where `config.py` and `sara/`
# live.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class _FakeTTS:
    """Minimal TTS stand-in -- same shape as test_sara_smoke.py's own
    _FakeTTS helpers, just accepts and discards every call."""

    def speak(self, text, fast=False, block=True):
        pass


class _FakeBrain:
    """
    Minimal SaraLLM stand-in for _handle_command() dispatch tests.
    Needs model_name (read by _retry_via_tool_router() to call
    resolve_tool_call()) and record_exchange() (called by
    _handle_command() on every successful fast-path result) -- nothing
    else in this module ever touches the brain object.
    """

    model_name = "test-model"

    def record_exchange(self, *args, **kwargs):
        pass


class _FakeMatch:
    """Minimal stand-in for a real regex Match object -- only .group()
    is ever touched by the handlers registered below."""

    def __init__(self, *groups):
        self._groups = groups

    def group(self, index):
        return self._groups[index - 1]


class FallbackStageTrackingTests(unittest.TestCase):
    """
    SaraLLM._stream_generic() is the single place that records which
    stage ("gemini"/"ollama"/the fallback label) actually served a
    turn's reply, via self._last_stage_used -- used_fallback_last_turn()
    just reads that back. These tests drive _stream_generic() directly
    with fake (label, opener) stage callables instead of the real
    Gemini/Ollama clients -- that's the exact "stages" contract
    generate_response_stream() itself builds, so this exercises the
    real tracking logic without needing to fake out
    _get_gemini_client()/_get_ollama_client() for no added coverage of
    what's actually being verified here.
    """

    def _make_brain(self):
        from sara.core.llm.engine import SaraLLM

        return SaraLLM()

    def test_flag_is_false_when_primary_stage_serves_the_reply(self):
        brain = self._make_brain()

        def _primary_opener(attempt):
            return iter(["Hello ", "there."])

        # max_retries=0 keeps this deterministic and instant regardless
        # of whatever LLM_MAX_RETRIES/LLM_RETRY_BASE_DELAY_S happen to
        # be configured -- irrelevant here since the primary succeeds on
        # its first attempt anyway.
        list(brain._stream_generic("hi", [("gemini", _primary_opener)], max_retries=0))

        self.assertFalse(brain.used_fallback_last_turn())

    def test_flag_is_true_when_fallback_stage_serves_the_reply(self):
        from sara.core.llm.engine import _FALLBACK_STAGE_LABEL

        brain = self._make_brain()

        def _dead_primary(attempt):
            # Simulates Gemini failing before yielding a single token
            # (no network, quota exhausted, auth error, ...).
            raise RuntimeError("primary backend unavailable")

        def _working_fallback(attempt):
            return iter(["Namaste, ", "main abhi local model pe hoon."])

        stages = [("gemini", _dead_primary), (_FALLBACK_STAGE_LABEL, _working_fallback)]
        list(brain._stream_generic("hi", stages, max_retries=0))

        self.assertTrue(brain.used_fallback_last_turn())

    def test_flag_reverts_to_false_once_the_primary_recovers(self):
        """
        A later turn served by the primary again must flip the flag back
        to False -- this is what lets run_sara_logic() in core_wiring.py
        announce "back to normal" instead of reporting fallback forever.
        """
        from sara.core.llm.engine import _FALLBACK_STAGE_LABEL

        brain = self._make_brain()

        def _dead_primary(attempt):
            raise RuntimeError("primary backend unavailable")

        def _working_fallback(attempt):
            return iter(["running on the fallback model"])

        def _recovered_primary(attempt):
            return iter(["back on the primary model"])

        list(brain._stream_generic(
            "turn one",
            [("gemini", _dead_primary), (_FALLBACK_STAGE_LABEL, _working_fallback)],
            max_retries=0,
        ))
        self.assertTrue(brain.used_fallback_last_turn())

        list(brain._stream_generic("turn two", [("gemini", _recovered_primary)], max_retries=0))
        self.assertFalse(brain.used_fallback_last_turn())


class LikelyMisfireRetryTests(unittest.TestCase):
    """
    _handle_command()'s fast-path dispatch block gives a handler's
    _LikelyMisfireReply (see intent_handlers.py) exactly one retry
    through resolve_tool_call() + TOOL_NAME_TO_INTENT +
    build_fake_match() before accepting it as the final answer -- a
    normal successful reply, or a genuine/expected failure that was
    never wrapped as _LikelyMisfireReply in the first place, must never
    trigger that retry at all.

    resolve_tool_call()/build_fake_match() are monkeypatched (saved and
    restored in a finally, same discipline test_sara_smoke.py's own
    Config.DB_PATH-swapping tests use) rather than called for real --
    the real resolve_tool_call() hits an actual local Ollama model
    (see test_tool_router_import()), which is slow, needs a running
    model, and isn't what's being tested here: only the DISPATCH
    plumbing around it is.
    """

    def test_likely_misfire_reply_triggers_one_retry_and_its_result_wins(self):
        from sara.core.intent import register_intent
        from sara.orchestrator import intent_handlers
        from sara.orchestrator.intent_handlers import (
            _handle_command,
            _quick_likely_misfire,
            register_handler,
        )

        call_count = {"resolve": 0}

        def _fake_resolve_tool_call(user_input, model_name):
            call_count["resolve"] += 1
            return {"name": "test_open_thing_tool", "arguments": {"target": "the correct thing"}}

        def _fake_build_fake_match(tool_name, tool_args):
            return _FakeMatch(tool_args["target"])

        def _primary_handler(match, ctx):
            if not match:
                return None
            # Simulates _h_open_app()/_h_close_app()-style unresolved-
            # target failure: matched the intent, but doesn't trust the
            # captured argument.
            return _quick_likely_misfire(ctx, "Which thing would you like me to open?")

        def _secondary_handler(match, ctx):
            if not match:
                return None
            # Simulates the SAME action, correctly resolved this time
            # via real LLM function-calling on the retry.
            return f"Opened {match.group(1)} via the tool router!"

        register_intent("test_misfire_primary", [r"open my thing"], gate=("thing",))
        register_handler("test_misfire_primary", _primary_handler)
        register_handler("test_misfire_secondary", _secondary_handler)
        intent_handlers.TOOL_NAME_TO_INTENT["test_open_thing_tool"] = "test_misfire_secondary"

        original_resolve = intent_handlers.resolve_tool_call
        original_build_match = intent_handlers.build_fake_match
        intent_handlers.resolve_tool_call = _fake_resolve_tool_call
        intent_handlers.build_fake_match = _fake_build_fake_match
        try:
            result = _handle_command(
                "open my thing",
                _FakeBrain(),
                _FakeTTS(),
                None,
                None,
                None,
                None,
                lambda *a: None,
                {},
                notes_memory=None,
            )
        finally:
            intent_handlers.resolve_tool_call = original_resolve
            intent_handlers.build_fake_match = original_build_match
            del intent_handlers.TOOL_NAME_TO_INTENT["test_open_thing_tool"]

        self.assertEqual(result, "Opened the correct thing via the tool router!")
        # Exactly one retry -- not zero (the misfire must be caught) and
        # not more than one (this is a single second opinion, not a
        # retry loop).
        self.assertEqual(call_count["resolve"], 1)

    def test_genuine_failure_does_not_trigger_a_retry(self):
        from sara.core.intent import register_intent
        from sara.orchestrator import intent_handlers
        from sara.orchestrator.intent_handlers import _handle_command, _quick, register_handler

        call_count = {"resolve": 0}

        def _fake_resolve_tool_call(user_input, model_name):
            call_count["resolve"] += 1
            return {"name": "irrelevant", "arguments": {}}

        def _permission_denied_handler(match, ctx):
            if not match:
                return None
            # A genuine, expected failure -- the app WAS found, it just
            # couldn't be closed. This must stay a plain str (via
            # _quick(), not _quick_likely_misfire()) and must never
            # trigger the retry.
            return _quick(
                ctx, "The app is running but I couldn't close it due to a permissions error."
            )

        register_intent("test_genuine_failure", [r"close my locked thing"], gate=("locked",))
        register_handler("test_genuine_failure", _permission_denied_handler)

        original_resolve = intent_handlers.resolve_tool_call
        intent_handlers.resolve_tool_call = _fake_resolve_tool_call
        try:
            result = _handle_command(
                "close my locked thing",
                _FakeBrain(),
                _FakeTTS(),
                None,
                None,
                None,
                None,
                lambda *a: None,
                {},
                notes_memory=None,
            )
        finally:
            intent_handlers.resolve_tool_call = original_resolve

        self.assertEqual(
            result, "The app is running but I couldn't close it due to a permissions error."
        )
        self.assertEqual(call_count["resolve"], 0)

    def test_quick_likely_misfire_marks_reply_without_changing_its_text(self):
        """
        Unit-level check of the signal itself, independent of dispatch:
        _quick_likely_misfire() must return something that IS a
        _LikelyMisfireReply (so _handle_command() can detect it) AND is
        still a plain str underneath (so every other existing caller --
        _build_plan_dispatch_fn(), the chat_route == "tool" branch,
        _quick() itself -- keeps working completely unchanged).
        """
        from sara.orchestrator.intent_handlers import _LikelyMisfireReply, _quick_likely_misfire

        ctx = {"ui_update": lambda *a: None, "tts": _FakeTTS()}
        reply = _quick_likely_misfire(ctx, "Which app would you like me to open?")

        self.assertIsInstance(reply, _LikelyMisfireReply)
        self.assertIsInstance(reply, str)
        self.assertEqual(reply, "Which app would you like me to open?")


if __name__ == "__main__":
    unittest.main()