import contextlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config
from sara.core.llm.provider_runtime import GenResult
from sara.core.memory import PreferencesDB
from sara.orchestrator.proactive import ActivityTracker, ProactiveEngine
from sara.tools.reminder_composer import ReminderComposer
from sara.tools.reminders import ReminderManager


class _TTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text, fast=False):
        self.spoken.append(text)


class _Listening:
    def __init__(self, on):
        self._is_listening = SimpleNamespace(is_set=lambda: on)


class ActivityTrackerTests(unittest.TestCase):
    def test_start_is_not_activity(self):
        tracker = ActivityTracker()
        self.assertIsNone(tracker.last_touch_time())
        tracker.touch()
        self.assertIsNotNone(tracker.last_touch_time())
        self.assertLess(tracker.idle_seconds(), 5)


class ProactiveFollowupTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(Config, "PROACTIVE_LLM_PHRASING", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = PreferencesDB(db_path=os.path.join(self._tmp.name, "p.db"))
        self.mgr = ReminderManager(db_path=os.path.join(self._tmp.name, "r.db"))
        self.tts = _TTS()
        self.tracker = ActivityTracker()
        self.gen_calls = []
        # A sleep reminder that was due and spoken 40 minutes ago.
        due = datetime.now() - timedelta(minutes=40)
        self.rid = self.mgr._conn.execute(
            "INSERT INTO reminders (message, due_at, created_at, triggered, done) VALUES (?,?,?,1,0)",
            ("sleep", due.isoformat(), (due - timedelta(hours=1)).isoformat()),
        ).lastrowid
        self.mgr._conn.commit()
        self.mgr.update_intel(self.rid, delivered_at=due.isoformat())

    def tearDown(self):
        self.mgr.close()
        self.db.close()
        self._tmp.cleanup()

    def _gen(self, prompt, system, cfg, **kw):
        self.gen_calls.append(prompt)
        return GenResult("You're still up. Please get some rest.", "gemini", "success", (), 5)

    def _engine(self, composer="default", ears=None):
        if composer == "default":
            composer = ReminderComposer(
                SimpleNamespace(CONTEXTUAL_REMINDER_LLM=True, CONTEXTUAL_REMINDER_LLM_TIMEOUT_S=0.5),
                generate=self._gen,
            )
        return ProactiveEngine(
            db=self.db, reminders=self.mgr, tts=self.tts, ui_update=lambda *a: None,
            activity_tracker=self.tracker, ears=ears, reminder_composer=composer,
        )

    def test_followup_when_user_is_active(self):
        self.tracker.touch()
        self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, ["You're still up. Please get some rest."])
        intel = self.mgr.get_intel(self.rid)
        self.assertEqual(intel["nudge_count"], 1)
        self.assertTrue(intel["last_nudge_at"])
        self.assertEqual(intel["last_message"], "You're still up. Please get some rest.")
        self.assertIn("followup", self.gen_calls[0])

    def test_not_repeated_during_cooldown(self):
        self.tracker.touch()
        engine = self._engine()
        engine._check_reminder_followups()
        engine._check_reminder_followups()
        self.assertEqual(len(self.tts.spoken), 1)

    def test_second_nudge_after_cooldown_then_stops_at_max(self):
        self.tracker.touch()
        engine = self._engine()
        engine._check_reminder_followups()
        past = (datetime.now() - timedelta(minutes=31)).isoformat()
        self.mgr.update_intel(self.rid, last_nudge_at=past)
        engine._check_reminder_followups()
        self.assertEqual(len(self.tts.spoken), 2)
        self.assertEqual(self.mgr.get_intel(self.rid)["nudge_count"], 2)
        self.mgr.update_intel(self.rid, last_nudge_at=(datetime.now() - timedelta(minutes=31)).isoformat())
        engine._check_reminder_followups()
        self.assertEqual(len(self.tts.spoken), 2)  # max nudges reached

    def test_no_nudge_without_activity(self):
        self._engine()._check_reminder_followups()  # tracker never touched
        self.assertEqual(self.tts.spoken, [])
        self.assertEqual(self.gen_calls, [])

    def test_activity_before_delivery_does_not_count(self):
        due = datetime.now() - timedelta(minutes=40)
        self.mgr.update_intel(self.rid, delivered_at=(datetime.now() + timedelta(seconds=30)).isoformat())
        self.tracker.touch()
        self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_acknowledged_reminder_is_left_alone(self):
        self.tracker.touch()
        self.mgr.update_intel(self.rid, acknowledged=1)
        self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_done_reminder_is_left_alone(self):
        self.tracker.touch()
        self.mgr._conn.execute("UPDATE reminders SET done = 1")
        self.mgr._conn.commit()
        self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_settings_gate_followups(self):
        self.tracker.touch()
        for key in ("awake_awareness", "contextual_reminders"):
            self.db.set_preference(f"setting:{key}", "0")
            self._engine()._check_reminder_followups()
            self.assertEqual(self.tts.spoken, [], key)
            self.db.set_preference(f"setting:{key}", "1")

    def test_late_night_setting(self):
        self.tracker.touch()
        self.db.set_preference("setting:late_night_nudges", "0")
        with mock.patch("sara.tools.reminder_followup.time_bucket", return_value="late_night"):
            self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_proactive_reminders_toggle_and_no_composer(self):
        self.tracker.touch()
        self.db.set_preference("setting:proactive_reminders", "0")
        self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])
        self.db.set_preference("setting:proactive_reminders", "1")
        self._engine(composer=None)._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_user_speaking_blocks_followup_without_llm(self):
        self.tracker.touch()
        self._engine(ears=_Listening(True))._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])
        self.assertEqual(self.gen_calls, [])

    def test_failure_does_not_mark_nudge(self):
        self.tracker.touch()
        engine = self._engine()
        with mock.patch.object(engine, "_speak_and_notify", return_value=False):
            engine._check_reminder_followups()
        self.assertEqual(self.mgr.get_intel(self.rid)["nudge_count"], 0)

    def test_tick_calls_followups(self):
        engine = self._engine()
        others = [n for n in dir(engine) if n.startswith("_check_") and n != "_check_reminder_followups"]
        with contextlib.ExitStack() as stack:
            for name in others:
                stack.enter_context(mock.patch.object(engine, name))
            called = stack.enter_context(mock.patch.object(engine, "_check_reminder_followups"))
            engine._tick()
        called.assert_called_once()


if __name__ == "__main__":
    unittest.main()