"""
tests/test_new_features.py

Covers three additions layered onto the existing fast-path handlers:
  1. Clock-time alarms (_h_set_alarm) and the module-level stopwatch
     (start/lap/stop_stopwatch in sara.tools.system.system_info).
  2. find_and_open_file()'s single-vs-multi-match contract, as seen
     through the _h_open_file() handler.
  3. The structured to-do list (add_todo/list_todos/complete_todo/
     delete_todo) and its independence from the pre-existing plain-notes
     feature (take_note/read_notes/clear_notes).

Follows the same style as tests/test_sara_smoke.py: unittest.TestCase,
real imports of the actual sara modules, tempfile-backed sandboxing for
anything persistent, and small hand-rolled fakes (FakeTTS, a fake regex
match) rather than a mocking framework beyond unittest.mock.patch for
swapping out datetime/dateparser/system_tools calls.

SCOPE NOTE: the module that actually implements take_note/read_notes/
clear_notes/add_todo/list_todos/complete_todo/delete_todo/
find_and_open_file was not available to ground a white-box test against
(see the accompanying summary). Every test below is written against
ONLY the public call contract intent_handlers.py itself already relies
on (exact function names and argument shapes), and asserts on
OBSERVABLE behavior (what list_todos()/read_notes() report afterward)
rather than on any internal constant or threshold name we don't have
visibility into. Two sandboxing assumptions are called out inline where
they're used.
"""

import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from unittest.mock import patch

# Same sys.path bootstrap as test_sara_smoke.py, for the same reason:
# running this file directly only puts tests/ on sys.path, not the
# project root where config.py and sara/ live.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class _FakeMatch:
    """Minimal stand-in for a regex Match: truthy, with a single-arg
    group(1) that returns whatever text the test wants the handler to
    have "captured". Mirrors the _FakeMatch already used internally by
    sara.core.tool_router.build_fake_match()."""

    def __init__(self, captured_text):
        self._captured_text = captured_text

    def group(self, index):
        return self._captured_text


class _FakeTTS:
    """No-op TTS -- handlers call .speak(text, fast=..., block=...) in
    various combinations; accept and ignore all of them."""

    def speak(self, text, fast=False, block=True):
        pass


def _fake_ctx(**overrides):
    """Smallest ctx dict a fast-path handler needs to run without
    touching real audio/UI/DB objects. Individual tests add whatever
    extra keys their handler under test reads."""
    ctx = {
        "tts": _FakeTTS(),
        "ui_update": lambda *args: None,
        "context_state": {},
    }
    ctx.update(overrides)
    return ctx


class NewFeatureTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # The stopwatch is module-level mutable state (_stopwatch_start /
    # _stopwatch_laps in system_info.py) with no public reset API, so
    # tests in this class (or a future test added alongside them) could
    # otherwise leak a "still running" stopwatch across test methods.
    # Force it back to a clean slate before and after every test here,
    # the same way existing tests clean up their own tempfiles/DBs.
    # ------------------------------------------------------------------
    def setUp(self):
        from sara.tools.system import system_info

        system_info._stopwatch_start = None
        system_info._stopwatch_laps = []

    def tearDown(self):
        from sara.tools.system import system_info

        system_info._stopwatch_start = None
        system_info._stopwatch_laps = []

    # ------------------------------------------------------------------
    # Intent registration -- cheap regression net that the three new
    # features are actually wired into dispatch, without needing their
    # implementations.
    # ------------------------------------------------------------------
    def test_new_intents_are_registered(self):
        from sara.orchestrator.intent_handlers import _INTENT_HANDLERS

        for intent_name in (
            "set_alarm",
            "start_stopwatch",
            "stop_stopwatch",
            "lap_stopwatch",
            "open_file",
            "add_todo",
            "list_todos",
            "complete_todo",
            "delete_todo",
        ):
            self.assertIn(intent_name, _INTENT_HANDLERS)

    def test_preexisting_intents_still_route_correctly(self):
        """Existing regression check from test_sara_smoke.py, re-affirmed
        here: none of the three new features touched intent detection
        for pre-existing commands."""
        from sara.core.intent import detect_intent

        intent, _ = detect_intent("what time is it")
        self.assertEqual(intent, "time_query")
        intent, _ = detect_intent("open chrome")
        self.assertEqual(intent, "open_app")
        intent, _ = detect_intent("close chrome")
        self.assertEqual(intent, "close_app")

    # ------------------------------------------------------------------
    # Alarm scheduling (_h_set_alarm) -- fully grounded, since
    # intent_handlers.py's source was available. dateparser.parse() and
    # datetime.now() are both mocked so the delay math is checked
    # deterministically, with no real waiting and no dependence on the
    # actual clock the test happens to run at.
    # ------------------------------------------------------------------
    def test_alarm_schedules_correct_delay_for_a_future_time(self):
        import sara.orchestrator.intent_handlers as ih

        fixed_now = datetime(2026, 9, 22, 8, 0, 0)  # 8:00 AM
        target = datetime(2026, 9, 22, 21, 0, 0)  # 9:00 PM, same day

        ctx = _fake_ctx()
        with patch("sara.orchestrator.intent_handlers.datetime") as mock_dt, \
             patch("sara.orchestrator.intent_handlers.dateparser") as mock_dateparser, \
             patch.object(ih.system_tools, "set_timer", return_value="Alarm set.") as mock_set_timer:
            mock_dt.now.return_value = fixed_now
            mock_dateparser.parse.return_value = target

            ih._h_set_alarm(_FakeMatch("9pm"), ctx)

        self.assertTrue(mock_set_timer.called)
        seconds_arg, label_arg = mock_set_timer.call_args[0][0], mock_set_timer.call_args[0][1]
        self.assertAlmostEqual(seconds_arg, 13 * 3600)  # 8:00 AM -> 9:00 PM
        self.assertEqual(label_arg, "9:00 PM")

    def test_alarm_rolls_over_to_tomorrow_when_time_already_passed_today(self):
        """dateparser resolving a bare clock time ("7am") can still land
        on today's 7am even after 8am has already happened -- the
        handler must roll it onto tomorrow rather than reject it."""
        import sara.orchestrator.intent_handlers as ih

        fixed_now = datetime(2026, 9, 22, 8, 0, 0)  # 8:00 AM
        target_before_rollover = datetime(2026, 9, 22, 7, 0, 0)  # already passed

        ctx = _fake_ctx()
        with patch("sara.orchestrator.intent_handlers.datetime") as mock_dt, \
             patch("sara.orchestrator.intent_handlers.dateparser") as mock_dateparser, \
             patch.object(ih.system_tools, "set_timer", return_value="Alarm set.") as mock_set_timer:
            mock_dt.now.return_value = fixed_now
            mock_dateparser.parse.return_value = target_before_rollover

            ih._h_set_alarm(_FakeMatch("7am"), ctx)

        seconds_arg, label_arg = mock_set_timer.call_args[0][0], mock_set_timer.call_args[0][1]
        self.assertAlmostEqual(seconds_arg, 23 * 3600)  # rolled to tomorrow 7am
        self.assertEqual(label_arg, "7:00 AM")

    # ------------------------------------------------------------------
    # Stopwatch -- fully grounded (system_info.py source was available).
    # datetime.now().timestamp() is mocked with a fixed sequence so
    # elapsed/split times are exact, not dependent on real sleeps.
    # ------------------------------------------------------------------
    def test_stopwatch_lifecycle_and_guard_conditions(self):
        from sara.tools.system import system_info

        # Guard: stopping with nothing running must not touch datetime
        # at all, so this is safe to call before any patching.
        self.assertIn("No stopwatch", system_info.stop_stopwatch())

        # start=1000.0, double-start check=1000.5, lap=1005.0, stop=1012.0
        fixed_timestamps = iter([1000.0, 1000.5, 1005.0, 1012.0])
        with patch("sara.tools.system.system_info.datetime") as mock_dt:
            mock_dt.now.return_value.timestamp.side_effect = fixed_timestamps

            self.assertEqual(system_info.start_stopwatch(), "Stopwatch started.")

            # Guard: starting again while already running must not reset
            # progress -- it should just report how long it's been going.
            second_start = system_info.start_stopwatch()
            self.assertIn("already running", second_start)

            lap_result = system_info.lap_stopwatch()
            self.assertEqual(lap_result, "Lap 1: 5 seconds, total 5 seconds.")

            stop_result = system_info.stop_stopwatch()
            self.assertEqual(stop_result, "Stopwatch stopped at 12 seconds.")

        # Guard: stopping again with nothing running, after a real stop.
        self.assertIn("No stopwatch", system_info.stop_stopwatch())

    def test_lap_stopwatch_without_starting_is_rejected(self):
        from sara.tools.system import system_info

        result = system_info.lap_stopwatch()
        self.assertIn("isn't running", result)

    # ------------------------------------------------------------------
    # find_and_open_file(), via _h_open_file() -- see the scope note at
    # the top of this file: the function's own matching logic isn't
    # available to test directly, so this locks in the CALLER contract
    # instead -- the handler must relay find_and_open_file()'s result
    # unchanged whether it's a single-match "opened" message or a
    # multi-match disambiguation listing (i.e. it must never "guess" and
    # open something on an ambiguous result), and must record the query
    # either way so a later "open that file" follow-up has something to
    # resolve against.
    # ------------------------------------------------------------------
    def test_open_file_handler_relays_single_and_multi_match_results(self):
        import sara.orchestrator.intent_handlers as ih

        single_match_reply = "Opened resume.pdf."
        multi_match_reply = (
            "I found 2 files matching 'resume': resume.pdf, resume_old.pdf. "
            "Which one did you mean?"
        )

        for canned_reply in (single_match_reply, multi_match_reply):
            with self.subTest(canned_reply=canned_reply):
                ctx = _fake_ctx()
                with patch.object(
                    ih.system_tools, "find_and_open_file", return_value=canned_reply
                ):
                    result = ih._h_open_file(_FakeMatch("resume"), ctx)

                # Pass-through: the handler must not alter or reinterpret
                # what find_and_open_file() decided, in either case.
                self.assertEqual(result, canned_reply)
                # Context tracking: the query is remembered regardless of
                # whether it resolved to one file or several.
                self.assertEqual(
                    ctx["context_state"]["recent_entities"]["last_file"]["value"],
                    "resume",
                )

    # ------------------------------------------------------------------
    # To-do round trip + low-confidence refusal.
    #
    # ASSUMPTION (flagged): add_todo/list_todos/complete_todo/
    # delete_todo are called with no ctx["db"] argument in
    # intent_handlers.py (unlike e.g. memory recall, which is handed
    # ctx["db"] explicitly) -- so they must resolve their own storage
    # internally, the same way sara.skills._is_skill_user_disabled()
    # resolves its own storage by reading Config.DB_PATH directly at
    # call time rather than through an injected db object. Sandboxing
    # via a temporary Config.DB_PATH override (exactly the pattern
    # test_skill_user_disabled_reads_preference already uses in
    # test_sara_smoke.py) is the safest bet available without the real
    # source, and is called out here rather than asserted as fact.
    # ------------------------------------------------------------------
    def test_todo_round_trip_and_low_confidence_refusal(self):
        from sara.tools import system as system_tools
        import config as config_module

        db_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        db_tmp.close()
        original_db_path = config_module.Config.DB_PATH
        config_module.Config.DB_PATH = db_tmp.name
        try:
            # Unique text per run so this test is safe even if it ever
            # runs against a non-empty/shared store.
            item_a = f"call about invoice {uuid.uuid4().hex[:8]}"
            item_b = f"pack bag for trip {uuid.uuid4().hex[:8]}"

            system_tools.add_todo(item_a)
            system_tools.add_todo(item_b)

            pending = system_tools.list_todos(pending_only=True)
            self.assertIn(item_a, pending)
            self.assertIn(item_b, pending)

            # Exact-text completion should succeed.
            system_tools.complete_todo(item_a)
            pending_after = system_tools.list_todos(pending_only=True)
            self.assertNotIn(item_a, pending_after)
            self.assertIn(item_b, pending_after)

            all_todos = system_tools.list_todos(pending_only=False)
            self.assertIn(item_a, all_todos)  # still visible, just not pending

            # A confidently-unrelated identifier must be refused rather
            # than guessed at -- item_b must be completely untouched.
            system_tools.complete_todo(
                "zzz_completely_unrelated_gibberish_should_not_match_anything"
            )
            self.assertIn(item_b, system_tools.list_todos(pending_only=True))

            system_tools.delete_todo(item_b)
            self.assertNotIn(item_b, system_tools.list_todos(pending_only=False))
        finally:
            config_module.Config.DB_PATH = original_db_path
            if os.path.exists(db_tmp.name):
                os.unlink(db_tmp.name)

    # ------------------------------------------------------------------
    # Plain notes vs. to-dos: two independent stores, one change must
    # never leak into or clear the other.
    #
    # take_note/read_notes/clear_notes are reached through the public
    # package (`sara.tools.system`, aliased system_tools), exactly like
    # intent_handlers.py does -- they are defined in files_notes.py, not
    # system_info.py, so system_info.take_note is not a valid call path.
    #
    # SANDBOXING (flagged): the notes file path is a module-level
    # `_NOTES_FILE` constant read at import time, so overriding
    # Config.NOTES_FILE_PATH after import would not take effect; the
    # module attribute itself is patched directly instead, the same way
    # existing tests already reach into private internals. Because the
    # constant could live in files_notes.py or system_info.py, this
    # redirects it in whichever of the two defines it, and refuses to
    # run at all if it can't find one -- otherwise take_note() and
    # clear_notes() would operate on the REAL notes file.
    # ------------------------------------------------------------------
    def test_notes_and_todos_do_not_interfere_with_each_other(self):
        from sara.tools import system as system_tools
        from sara.tools.system import files_notes, system_info
        import config as config_module

        notes_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        notes_tmp.close()
        db_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        db_tmp.close()

        original_db_path = config_module.Config.DB_PATH
        config_module.Config.DB_PATH = db_tmp.name

        patched_notes_modules = []
        for module in (files_notes, system_info):
            if hasattr(module, "_NOTES_FILE"):
                patched_notes_modules.append((module, module._NOTES_FILE))
                module._NOTES_FILE = notes_tmp.name
        try:
            # Safety guard: never call take_note()/clear_notes() unless
            # the notes file was actually redirected to the temp file.
            self.assertTrue(
                patched_notes_modules,
                "Could not find a module-level _NOTES_FILE in files_notes or "
                "system_info to redirect; refusing to run take_note()/"
                "clear_notes() against the real notes file.",
            )

            note_text = f"note {uuid.uuid4().hex[:8]}: water the plants"
            todo_text = f"todo {uuid.uuid4().hex[:8]}: water the plants"

            system_tools.take_note(note_text)
            system_tools.add_todo(todo_text)

            notes_after = system_tools.read_notes()
            todos_after = system_tools.list_todos(pending_only=True)

            self.assertIn(note_text, notes_after)
            self.assertNotIn(note_text, todos_after)
            self.assertIn(todo_text, todos_after)
            self.assertNotIn(todo_text, notes_after)

            # Clearing notes must not touch the to-do list.
            system_tools.clear_notes()
            self.assertNotIn(note_text, system_tools.read_notes())
            self.assertIn(todo_text, system_tools.list_todos(pending_only=True))
        finally:
            for module, original_value in patched_notes_modules:
                module._NOTES_FILE = original_value
            config_module.Config.DB_PATH = original_db_path
            for path in (notes_tmp.name, db_tmp.name):
                if os.path.exists(path):
                    os.unlink(path)


if __name__ == "__main__":
    unittest.main()