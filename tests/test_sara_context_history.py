"""
Focused regression tests for three related changes across
sara/orchestrator/intent_handlers.py and sara/core/llm/engine.py:

  1. HISTORY FIX -- brain.record_exchange() is now called after every
     successful fast-path intent-handler turn (and SIMPLE_ACTIONS/tool/
     plan turns), not just the plain-chat path, so SaraLLM's own
     conversation history actually reflects that the turn happened.
  2. GENERALIZED RECENT ENTITIES -- ctx["context_state"]["recent_entities"]
     replaces the old single "last_slot_intent" slot with independently
     labeled, independently expiring slots (_remember_entity() /
     _CONTEXT_TTL_S), so multiple recently-mentioned things (an app, a
     file, a location, ...) can all be remembered at once instead of the
     newest one silently evicting the last.
  3. PRONOUN/REFERENCE INJECTION -- _route_chat_message() now detects an
     unresolved pronoun/reference ("it", "that", "uska", "wo", ...)
     alongside a still-fresh recent entity and, when found, short-circuits
     straight to chat with a context_hint that reaches
     brain.generate_response_stream() as its new optional
     reference_context= argument.

See test_sara_smoke.py for the mocking/fixture conventions this file
follows: real objects where construction is cheap/local (SaraLLM(),
matching that file's real TextToSpeech()/VisionAssistant() instances),
small locally-defined _FakeTTS/_FakeBrain stubs where a dependency would
otherwise be heavy or unrelated to what's under test, and a
patch-then-restore-in-finally pattern for module-level state (that file
already does this for Config.NOTES_FOLDER/Config.DB_PATH; here it's
extended to two module-level FUNCTION references -- detect_intent and
log_unmatched -- for exactly the same reason: make a "chat"-intent turn
reachable deterministically without depending on the live intent-regex
table or the unmatched-log sink, neither of which is part of this
change and neither of which is available in these three files).

── Do the 21 existing tests in test_sara_smoke.py still hold? ──────────
Yes, for two structural reasons that hold across all of them:

* brain.record_exchange() (change 1) is ONLY called from the "handler
  ran and returned a non-None result" success branches inside
  _handle_command() -- never from the exception branch, never from the
  "handler declined (returned None)" branch. The one existing test that
  drives the full _handle_command() dispatcher,
  test_handler_exception_does_not_crash_dispatch, deliberately exercises
  a handler that RAISES, so it never reaches a record_exchange() call --
  its _FakeBrain (which only defines .model_name) needed no change and
  still doesn't need a .record_exchange method.
* The context_state / recent_entities / reference_context plumbing
  (changes 2 and 3) is only ever touched when intent == "chat".
  test_handler_exception_does_not_crash_dispatch drives a custom
  "test_broken_skill" intent, and every other existing test that reaches
  a handler (test_proactive_log_and_transparency's direct
  _h_why_proactive() call, etc.) uses intents that aren't "chat" either
  -- so none of them exercise _route_chat_message() and none of them
  needed a "context_state" key added to their ctx dicts. Both
  context_state and the new generate_response_stream(reference_context=)
  parameter are optional with safe defaults (None), so any caller that
  predates this change keeps behaving exactly as before.

No existing test's assumptions need updating.
"""
import os
import sys
import time
import unittest

# See tests/test_api_surface.py for why this is needed: running this file
# directly (`python tests/test_sara_context_history.py`) only puts tests/
# on sys.path, not the project root where `config.py` and `sara/` live.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class SaraContextAndHistoryTests(unittest.TestCase):
    # ── CHANGE 1: conversation-history recording ─────────────────────────

    def test_record_exchange_writes_and_dedupes_real_sara_llm_history(self):
        """
        engine.py side: brain.record_exchange() must land in SaraLLM's
        REAL conversation history through its existing _append_history()
        dedup logic, not a reimplementation of it.

        A real SaraLLM() is constructed directly -- same style as
        test_sara_smoke.py's real TextToSpeech()/VisionAssistant()
        instances above -- rather than stubbed. Its own __init__ and
        _check_ollama() docstrings guarantee it degrades to "no backend
        reachable" without raising, so this stays fast and deterministic
        whether or not a local Ollama/Gemini backend happens to be
        running on the test machine.
        """
        from sara.core.llm.engine import SaraLLM

        brain = SaraLLM()

        brain.record_exchange("what's the weather", "It's sunny in Jaipur.")
        self.assertEqual(brain.get_history_length(), 1)
        self.assertEqual(
            brain.get_history_snapshot(),
            [("what's the weather", "It's sunny in Jaipur.")],
        )

        # Guard against blank input (per record_exchange()'s own
        # docstring): a blank prompt/reply is silently skipped rather
        # than polluting history with a no-op turn.
        brain.record_exchange("", "")
        self.assertEqual(brain.get_history_length(), 1)

        # Dedup: re-recording against the SAME prompt replaces the
        # previous entry instead of appending a second one -- the "exact
        # same dedup-aware append logic the plain-chat path already
        # relies on" that record_exchange()'s docstring promises callers
        # outside sara.core.llm.engine get for free.
        brain.record_exchange("what's the weather", "Correction -- it's cloudy now.")
        self.assertEqual(brain.get_history_length(), 1)
        self.assertEqual(
            brain.get_history_snapshot(),
            [("what's the weather", "Correction -- it's cloudy now.")],
        )

    def test_fast_path_handler_success_recorded_via_brain_record_exchange(self):
        """
        intent_handlers.py side: _handle_command()'s fast-path
        _INTENT_HANDLERS dispatch block must call
        brain.record_exchange(user_input, result) after a handler
        returns a real (non-None) result -- this is the actual new
        production call site, complementary to (not redundant with) the
        SaraLLM-side test above.

        "what time is it" -> time_query is already confirmed by
        test_joke_and_streak_skills_registered in test_sara_smoke.py, and
        _h_time_query() only touches ctx["ui_update"]/ctx["tts"] -- no
        db/reminders/vision required -- so this stays a true unit test of
        just the dispatch wiring, with the same kind of minimal _FakeTTS/
        _FakeBrain stubs test_handler_exception_does_not_crash_dispatch
        already uses for this same dispatcher.
        """
        from sara.orchestrator.intent_handlers import _handle_command

        class _FakeTTS:
            def speak(self, text, fast=False):
                pass

        class _FakeBrain:
            model_name = "test-model"

            def __init__(self):
                self.record_calls = []

            def record_exchange(self, user_input, reply):
                self.record_calls.append((user_input, reply))

        brain = _FakeBrain()
        result = _handle_command(
            "what time is it",
            brain,
            _FakeTTS(),
            None,
            None,
            None,
            None,
            lambda *a: None,
            {},
        )
        self.assertIsInstance(result, str)
        self.assertEqual(brain.record_calls, [("what time is it", result)])

    # ── CHANGE 2: generalized multi-slot recent entities ──────────────────

    def test_remember_entity_is_retrievable_via_generic_describer(self):
        """
        Any handler can opt a new slot into
        ctx["context_state"]["recent_entities"] via _remember_entity() --
        this must work for a slot that has NOTHING to do with weather/
        news (here, "last_app", the slot _h_open_app()/_h_close_app()
        use), proving the mechanism is genuinely generalized and not
        still secretly weather/news-specific under the hood.
        """
        from sara.orchestrator.intent_handlers import (
            _remember_entity,
            _describe_recent_entities,
        )

        ctx = {"context_state": {}}
        _remember_entity(ctx, "last_app", "notepad")

        entry = ctx["context_state"]["recent_entities"]["last_app"]
        self.assertEqual(entry["value"], "notepad")

        # _ENTITY_SLOT_LABELS maps "last_app" -> "application" for the
        # human-readable description fed into the chat prompt.
        self.assertEqual(
            _describe_recent_entities(ctx["context_state"]), "application: notepad"
        )

    def test_multiple_recent_entities_coexist_independently(self):
        """
        The actual bug being fixed by change 2: with the old single-slot
        design, a second _remember_context() call would silently EVICT
        the first. Now each slot is independent, so a city asked about
        via weather and an app opened just after it must BOTH still be
        remembered at once.
        """
        from sara.orchestrator.intent_handlers import (
            _remember_context,
            _remember_entity,
            _describe_recent_entities,
        )

        ctx = {"context_state": {}}
        _remember_context(ctx, "weather", "jaipur")  # pre-existing weather/news path
        _remember_entity(ctx, "last_app", "notepad")  # new generalized path

        description = _describe_recent_entities(ctx["context_state"])
        self.assertIn("location: jaipur", description)
        self.assertIn("application: notepad", description)

    def test_recent_entity_expires_after_context_ttl(self):
        """
        A slot older than _CONTEXT_TTL_S must be treated as gone by
        _describe_recent_entities() -- tested via "last_app" specifically
        (not weather/news) because the generalized expiry has to hold for
        any slot, not just the two that predate this change.
        """
        from sara.orchestrator.intent_handlers import (
            _remember_entity,
            _describe_recent_entities,
            _CONTEXT_TTL_S,
        )

        ctx = {"context_state": {}}
        _remember_entity(ctx, "last_app", "notepad")

        # Still fresh right after being set.
        self.assertEqual(
            _describe_recent_entities(ctx["context_state"]), "application: notepad"
        )

        # Backdate the timestamp past the TTL and confirm it's dropped.
        ctx["context_state"]["recent_entities"]["last_app"]["ts"] -= (_CONTEXT_TTL_S + 1)
        self.assertEqual(_describe_recent_entities(ctx["context_state"]), "")

    # ── CHANGE 3: pronoun/reference detection -> context_hint injection ───

    def test_route_chat_message_returns_context_hint_for_pronoun_with_fresh_entity(self):
        """
        _route_chat_message() is documented as a pure, LLM-free function
        that's "trivially unit-testable" -- tested directly here rather
        than through the full dispatcher, since it's the single source of
        truth for whether a context_hint gets produced at all.
        """
        from sara.orchestrator.intent_handlers import _route_chat_message

        context_state = {
            "recent_entities": {
                "last_app": {"value": "notepad", "ts": time.time(), "intent": None},
            }
        }
        route, hint = _route_chat_message("can you close it please", context_state)
        self.assertEqual(route, "chat")
        self.assertEqual(hint, "application: notepad")

    def test_route_chat_message_no_hint_when_no_pronoun_present(self):
        """
        Negative case #1: a fresh recent entity alone is not enough -- the
        message itself must contain an unresolved pronoun/reference, or
        context_hint stays None. (route isn't asserted here: which of
        plan/tool/chat a plain message resolves to depends on heuristics
        outside these three files -- only that no hint is produced.)
        """
        from sara.orchestrator.intent_handlers import _route_chat_message

        context_state = {
            "recent_entities": {
                "last_app": {"value": "notepad", "ts": time.time(), "intent": None},
            }
        }
        _route, hint = _route_chat_message("what's your favorite color", context_state)
        self.assertIsNone(hint)

    def test_route_chat_message_no_hint_when_only_entity_present_is_expired(self):
        """
        Negative case #2: a pronoun alone is not enough either -- an
        expired entity must be treated the same as no entity at all.
        """
        from sara.orchestrator.intent_handlers import _route_chat_message, _CONTEXT_TTL_S

        context_state = {
            "recent_entities": {
                "last_app": {
                    "value": "notepad",
                    "ts": time.time() - _CONTEXT_TTL_S - 1,
                    "intent": None,
                },
            }
        }
        _route, hint = _route_chat_message("can you close it please", context_state)
        self.assertIsNone(hint)

    def test_generate_response_stream_receives_populated_reference_context_end_to_end(self):
        """
        Full wiring, through _handle_command(): for a "chat"-intent turn
        whose message contains an unresolved pronoun/reference AND a
        still-fresh recent entity, the context_hint _route_chat_message()
        computes must actually reach brain.generate_response_stream() as
        its reference_context= kwarg -- not just be computed and dropped.

        detect_intent() and log_unmatched() are monkeypatched
        (patch-then-restore-in-finally, the same technique
        test_notes_qa_sync_and_skip_unchanged and
        test_skill_user_disabled_reads_preference already use for
        Config.NOTES_FOLDER/Config.DB_PATH) so this test is deterministic
        regardless of the live intent-regex table, and doesn't depend on
        sara.core.unmatched_log's own (unrelated) file/DB writes.
        """
        import sara.orchestrator.intent_handlers as ih

        class _FakeTTS:
            def speak(self, text, fast=False):
                pass

            def speak_stream(self, stream, on_first_chunk=None, on_chunk=None):
                if on_first_chunk:
                    on_first_chunk()
                sentences = list(stream)
                for s in sentences:
                    if on_chunk:
                        on_chunk(s)
                return sentences

        class _FakeBrain:
            model_name = "test-model"

            def __init__(self):
                self.stream_calls = []

            def generate_response_stream(self, prompt, reference_context=None):
                self.stream_calls.append((prompt, reference_context))
                return iter(["Okay, closing it."])

        context_state = {
            "recent_entities": {
                "last_app": {"value": "notepad", "ts": time.time(), "intent": None},
            }
        }
        brain = _FakeBrain()

        original_detect_intent = ih.detect_intent
        original_log_unmatched = ih.log_unmatched
        ih.detect_intent = lambda text: ("chat", None)
        ih.log_unmatched = lambda *a, **k: None
        try:
            result = ih._handle_command(
                "can you close it please",
                brain,
                _FakeTTS(),
                None,
                None,
                None,
                None,
                lambda *a: None,
                {},
                context_state=context_state,
            )
        finally:
            ih.detect_intent = original_detect_intent
            ih.log_unmatched = original_log_unmatched

        self.assertEqual(result, "Okay, closing it.")
        self.assertEqual(len(brain.stream_calls), 1)
        prompt, reference_context = brain.stream_calls[0]
        self.assertEqual(prompt, "can you close it please")
        self.assertEqual(reference_context, "application: notepad")


if __name__ == "__main__":
    unittest.main()