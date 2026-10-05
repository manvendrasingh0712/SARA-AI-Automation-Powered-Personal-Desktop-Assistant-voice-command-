"""
A whole night with a sleep reminder: spoken, followed up while the user keeps
talking to Sara, acknowledged, and restarted. Real ReminderManager, real
ProactiveEngine and dispatcher-side acknowledgement; only the LLM and the
speaker are faked.
"""
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
from sara.tools.reminder_followup import acknowledge_if_applicable
from sara.tools.reminders import ReminderManager


class _TTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text, fast=False):
        self.spoken.append(text)


class NightStoryTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(Config, "PROACTIVE_LLM_PHRASING", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.prefs_path = os.path.join(self._tmp.name, "p.db")
        self.rem_path = os.path.join(self._tmp.name, "r.db")
        self.db = PreferencesDB(db_path=self.prefs_path)
        self.mgr = ReminderManager(db_path=self.rem_path)
        self.tts = _TTS()
        self.tracker = ActivityTracker()
        due = datetime.now() - timedelta(minutes=40)
        self.rid = self.mgr._conn.execute(
            "INSERT INTO reminders (message, due_at, created_at, triggered, done) VALUES (?,?,?,1,0)",
            ("sleep", due.isoformat(), (due - timedelta(hours=2)).isoformat()),
        ).lastrowid
        self.mgr._conn.commit()
        # The reminder was spoken 40 minutes ago (what _deliver_due records).
        self.mgr.update_intel(self.rid, delivered_at=due.isoformat())

    def tearDown(self):
        self.mgr.close()
        self.db.close()
        self._tmp.cleanup()

    def _engine(self, mgr=None):
        composer = ReminderComposer(
            SimpleNamespace(CONTEXTUAL_REMINDER_LLM=True, CONTEXTUAL_REMINDER_LLM_TIMEOUT_S=0.5),
            generate=lambda *a, **k: GenResult("You're still up. Please get some rest.", "gemini", "success", (), 5),
        )
        return ProactiveEngine(
            db=self.db, reminders=mgr or self.mgr, tts=self.tts, ui_update=lambda *a: None,
            activity_tracker=self.tracker, reminder_composer=composer,
        )

    def _cooldown_passed(self, mgr=None):
        (mgr or self.mgr).update_intel(
            self.rid, last_nudge_at=(datetime.now() - timedelta(minutes=31)).isoformat()
        )

    def test_silent_user_is_never_nudged(self):
        engine = self._engine()
        engine._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_active_user_then_acknowledgement_ends_the_nudges(self):
        engine = self._engine()
        self.tracker.touch()                                  # user talks to Sara
        engine._check_reminder_followups()
        self.assertEqual(self.tts.spoken, ["You're still up. Please get some rest."])

        reply = acknowledge_if_applicable("okay, going to sleep", self.mgr)
        self.assertEqual(reply, "Alright, good night.")

        self._cooldown_passed()
        self.tracker.touch()
        engine._check_reminder_followups()
        self.assertEqual(len(self.tts.spoken), 1)             # acknowledged: no second nudge

    def test_nudges_stop_at_the_limit_even_after_a_restart(self):
        self.tracker.touch()
        self._engine()._check_reminder_followups()            # nudge 1
        self._cooldown_passed()
        self.tracker.touch()
        self._engine()._check_reminder_followups()            # nudge 2
        self.assertEqual(len(self.tts.spoken), 2)

        self.mgr.close()
        self.mgr = ReminderManager(db_path=self.rem_path)     # app restarted
        self.assertEqual(self.mgr.get_intel(self.rid)["nudge_count"], 2)
        self._cooldown_passed()
        self.tracker.touch()
        self._engine()._check_reminder_followups()
        self.assertEqual(len(self.tts.spoken), 2)             # still 2, never 3

    def test_a_stray_okay_long_after_does_not_acknowledge(self):
        engine = self._engine()
        self.tracker.touch()
        # No nudge yet and the reminder was spoken 40 minutes ago: a bare "okay" means nothing here.
        self.assertIsNone(acknowledge_if_applicable("okay", self.mgr))
        engine._check_reminder_followups()
        self.assertEqual(len(self.tts.spoken), 1)

    def test_reminder_ticked_done_in_the_calendar_stops_nudges(self):
        engine = self._engine()
        self.tracker.touch()
        self.mgr._conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (self.rid,))
        self.mgr._conn.commit()
        engine._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])

    def test_follow_up_window_closes(self):
        old = (datetime.now() - timedelta(minutes=130)).isoformat()
        self.mgr.update_intel(self.rid, delivered_at=old)
        self.tracker.touch()
        self._engine()._check_reminder_followups()
        self.assertEqual(self.tts.spoken, [])


if __name__ == "__main__":
    unittest.main()