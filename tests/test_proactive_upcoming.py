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


class ProactiveUpcomingTests(unittest.TestCase):
    def setUp(self):
        # The legacy wording path would otherwise call a real LLM to rephrase.
        patcher = mock.patch.object(Config, "PROACTIVE_LLM_PHRASING", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = PreferencesDB(db_path=os.path.join(self._tmp.name, "p.db"))
        self.mgr = ReminderManager(db_path=os.path.join(self._tmp.name, "r.db"))
        due = datetime.now() + timedelta(minutes=10)
        self.rid = self.mgr.add(due.strftime("%Y-%m-%d"), due.strftime("%H:%M"), "sleep")
        self.tts = _TTS()
        self.gen_calls = []

    def tearDown(self):
        self.mgr.close()
        self.db.close()
        self._tmp.cleanup()

    def _gen(self, prompt, system, cfg, **kw):
        self.gen_calls.append(prompt)
        return GenResult("Your sleep reminder is coming up.", "gemini", "success", (), 50)

    def _engine(self, composer="default", ears=None):
        if composer == "default":
            composer = ReminderComposer(
                SimpleNamespace(CONTEXTUAL_REMINDER_LLM=True, CONTEXTUAL_REMINDER_LLM_TIMEOUT_S=0.5),
                generate=self._gen,
            )
        return ProactiveEngine(
            db=self.db,
            reminders=self.mgr,
            tts=self.tts,
            ui_update=lambda *a: None,
            activity_tracker=ActivityTracker(),
            ears=ears,
            reminder_composer=composer,
        )

    def test_contextual_heads_up_spoken_and_recorded(self):
        engine = self._engine()
        engine._check_upcoming_reminders()
        self.assertEqual(self.tts.spoken, ["Your sleep reminder is coming up."])
        self.assertTrue(self.mgr.get_intel(self.rid)["upcoming_notified_at"])

    def test_not_repeated_in_same_run(self):
        engine = self._engine()
        engine._check_upcoming_reminders()
        engine._check_upcoming_reminders()
        self.assertEqual(len(self.tts.spoken), 1)

    def test_not_repeated_after_restart(self):
        self._engine()._check_upcoming_reminders()
        self.assertEqual(len(self.tts.spoken), 1)
        restarted = self._engine()  # new engine = empty in-memory set
        restarted._check_upcoming_reminders()
        self.assertEqual(len(self.tts.spoken), 1)

    def test_setting_off_uses_legacy_wording(self):
        self.db.set_preference("setting:contextual_reminders", "0")
        engine = self._engine()
        engine._check_upcoming_reminders()
        self.assertEqual(self.gen_calls, [])
        self.assertEqual(len(self.tts.spoken), 1)
        self.assertIn("sleep", self.tts.spoken[0])

    def test_no_composer_uses_legacy_wording(self):
        engine = self._engine(composer=None)
        engine._check_upcoming_reminders()
        self.assertEqual(len(self.tts.spoken), 1)
        self.assertIn("sleep", self.tts.spoken[0])

    def test_composer_error_uses_legacy_wording(self):
        class Boom:
            def start(self, *a, **k):
                raise RuntimeError("boom")

        engine = self._engine(composer=Boom())
        engine._check_upcoming_reminders()
        self.assertEqual(len(self.tts.spoken), 1)

    def test_listening_skips_llm_and_does_not_mark(self):
        engine = self._engine(ears=_Listening(True))
        engine._check_upcoming_reminders()
        self.assertEqual(self.gen_calls, [])
        self.assertEqual(self.tts.spoken, [])
        self.assertEqual(self.mgr.get_intel(self.rid), {})  # retry on a later tick
        engine._ears = _Listening(False)
        engine._check_upcoming_reminders()
        self.assertEqual(len(self.tts.spoken), 1)

    def test_proactive_reminders_toggle_off_is_silent(self):
        self.db.set_preference("setting:proactive_reminders", "0")
        engine = self._engine()
        engine._check_upcoming_reminders()
        self.assertEqual(self.tts.spoken, [])


if __name__ == "__main__":
    unittest.main()