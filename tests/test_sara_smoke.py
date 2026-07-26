import importlib
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta

from config import Config


class SaraSmokeTests(unittest.TestCase):
    def test_config_validate(self):
        Config.validate(force=True)

    def test_intent_engine(self):
        from sara.core.intent.engine import detect_intent

        intent, match = detect_intent("open chrome")
        self.assertEqual(intent, "open_app")
        self.assertTrue(match)

    def test_calc_utils(self):
        from sara.orchestrator.calc_utils import _safe_calc, _parse_duration_to_seconds

        self.assertEqual(_safe_calc("12 * (3 + 4)"), "The answer is 84.")
        self.assertEqual(_parse_duration_to_seconds("5 minutes"), 300)

    def test_preferences_db(self):
        from sara.core.memory import PreferencesDB

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            db = PreferencesDB(db_path=tmp.name)
            self.assertTrue(db.set_preference("test_key", "test_value"))
            self.assertEqual(db.get_preference("test_key"), "test_value")
            self.assertTrue(db.delete_preference("test_key"))
            db.close()
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_reminder_manager_shutdown(self):
        from sara.tools.reminders import ReminderManager

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            manager = ReminderManager(db_path=tmp.name)
            manager.start()
            manager.shutdown()
            self.assertFalse(manager._thread and manager._thread.is_alive())
            self.assertIsNone(manager._conn)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_audio_module_imports(self):
        importlib.import_module("sara.audio.aec")
        importlib.import_module("sara.audio.stt.engine")

    def test_tool_router_import(self):
        from sara.core.tool_router import TOOL_NAME_TO_INTENT, resolve_tool_call, build_fake_match

        self.assertIn("weather", TOOL_NAME_TO_INTENT)
        resolved = resolve_tool_call("what's the weather in Mumbai", "qwen2.5")
        self.assertEqual(resolved["name"], "weather")
        self.assertTrue(resolved["arguments"]["location"].lower().startswith("mumbai"))

        fake_match = build_fake_match(resolved["name"], resolved["arguments"])
        self.assertTrue(fake_match)
        self.assertEqual(fake_match.group(1), resolved["arguments"]["location"])

    def test_tts_engine_initialization(self):
        from sara.audio.tts import TextToSpeech

        tts = TextToSpeech()
        try:
            tts.speak("Hello from Sara!")
        finally:
            tts.shutdown()

    def test_vision_module_import(self):
        from sara.tools.vision import VisionAssistant

        assistant = VisionAssistant()
        self.assertTrue(hasattr(assistant, "capture_screenshot"))

    def test_proactive_engine_gating(self):
        from sara.core.memory import PreferencesDB
        from sara.orchestrator.proactive import ActivityTracker, ProactiveEngine
        from sara.orchestrator.state import AssistantState

        tracker = ActivityTracker()
        self.assertLess(tracker.idle_seconds(), 1.0)
        tracker.touch()
        self.assertLess(tracker.idle_seconds(), 1.0)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            db = PreferencesDB(db_path=tmp.name)
            state = AssistantState(initial_active=True)
            engine = ProactiveEngine(
                db=db,
                reminders=None,
                tts=None,
                ui_update=lambda *a: None,
                activity_tracker=tracker,
                assistant_state=state,
                lang_state=None,
            )
            # Default ON: nothing set yet in the DB -> enabled.
            self.assertTrue(engine._enabled_now())
            # Explicit opt-out from the Settings page toggle.
            db.set_preference("setting:proactive_mode", "0")
            self.assertFalse(engine._enabled_now())
            db.set_preference("setting:proactive_mode", "1")
            self.assertTrue(engine._enabled_now())
            # Focus mode silences it regardless of the toggle above.
            db.set_preference("focus_mode", "1")
            self.assertFalse(engine._enabled_now())
            db.set_preference("focus_mode", "0")
            self.assertTrue(engine._enabled_now())
            # A paused assistant (Home page Pause Listening) silences it too.
            state.set_active(False)
            self.assertFalse(engine._enabled_now())
            db.close()
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_battery_raw_and_reminder_upcoming(self):
        from sara.tools.system.system_info import get_battery_raw

        raw = get_battery_raw()
        self.assertTrue(raw is None or (isinstance(raw, tuple) and len(raw) == 2))

        from sara.tools.reminders import ReminderManager

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            manager = ReminderManager(db_path=tmp.name)
            self.assertEqual(manager.get_upcoming(15), [])
            manager.shutdown()
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_proactive_log_and_transparency(self):
        from sara.core.intent import detect_intent
        from sara.core.memory import PreferencesDB
        from sara.orchestrator.intent_handlers import _h_why_proactive

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            db = PreferencesDB(db_path=tmp.name)

            # Nothing logged yet.
            self.assertIsNone(db.get_last_proactive_event())
            self.assertEqual(db.get_proactive_stats()["total"], 0)

            db.log_proactive_event(
                "battery", "Battery is low.", "Battery was at 12%.", wait=True
            )
            db.log_proactive_event(
                "idle_break", "Take a break.", "Idle for 95 minutes.", wait=True
            )

            last = db.get_last_proactive_event()
            self.assertEqual(last["trigger"], "idle_break")
            self.assertIn("95", last["reason"])

            stats = db.get_proactive_stats()
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["by_trigger"].get("battery"), 1)

            # "Why did you say that?" (English) and "kyu bola" (Hinglish)
            # must both route to the why_proactive intent.
            intent_en, match_en = detect_intent("why did you say that")
            self.assertEqual(intent_en, "why_proactive")
            intent_hi, _ = detect_intent("kyu bola tumne")
            self.assertEqual(intent_hi, "why_proactive")

            class _FakeTTS:
                def speak(self, text, fast=False):
                    pass

            ctx = {"db": db, "tts": _FakeTTS(), "ui_update": lambda *a: None}
            result = _h_why_proactive(match_en, ctx)
            self.assertIn("95", result)  # explains the LAST (idle_break) event

            db.close()
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_skills_auto_registration(self):
        from sara.core.intent import detect_intent
        from sara.orchestrator.intent_handlers import _INTENT_HANDLERS

        self.assertIn("daily_briefing", _INTENT_HANDLERS)
        self.assertIn("notes_qa", _INTENT_HANDLERS)

        intent, _ = detect_intent("give me my daily briefing")
        self.assertEqual(intent, "daily_briefing")
        intent, _ = detect_intent("what do my notes say about photosynthesis")
        self.assertEqual(intent, "notes_qa")
        # Existing intents must be completely unaffected by the new skills.
        intent, _ = detect_intent("what time is it")
        self.assertEqual(intent, "time_query")
        intent, _ = detect_intent("close chrome")
        self.assertEqual(intent, "close_app")

    def test_notes_qa_sync_and_skip_unchanged(self):
        from sara.core.memory import PreferencesDB
        from sara.skills.notes_qa import sync_notes_folder

        class _FakeRagMemory:
            enabled = True

            def __init__(self):
                self.added = []

            def add_memory(self, text, source="conversation"):
                self.added.append((text, source))

        notes_dir = tempfile.mkdtemp()
        db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        db_file.close()
        try:
            note_path = os.path.join(notes_dir, "geography.txt")
            with open(note_path, "w", encoding="utf-8") as f:
                f.write("Federalism divides power between central and state governments. " * 20)

            db = PreferencesDB(db_path=db_file.name)
            rag = _FakeRagMemory()

            import config as config_module

            original_folder = config_module.Config.NOTES_FOLDER
            config_module.Config.NOTES_FOLDER = notes_dir
            try:
                ingested = sync_notes_folder(rag, db)
                self.assertEqual(ingested, 1)
                self.assertGreater(len(rag.added), 0)
                self.assertTrue(all(src == "notes:geography.txt" for _, src in rag.added))

                # Second call, file unchanged -> nothing new ingested.
                rag.added.clear()
                ingested_again = sync_notes_folder(rag, db)
                self.assertEqual(ingested_again, 0)
                self.assertEqual(len(rag.added), 0)
            finally:
                config_module.Config.NOTES_FOLDER = original_folder
            db.close()
        finally:
            shutil.rmtree(notes_dir, ignore_errors=True)
            if os.path.exists(db_file.name):
                os.unlink(db_file.name)

    def test_handler_exception_does_not_crash_dispatch(self):
        """
        Production-hardening regression test: a handler (built-in or a
        sara/skills/ plugin) that raises must never propagate out of
        _handle_command() — that would climb into run_sara_logic()'s
        outer try/except, which treats any escaped exception as fatal and
        exits the ENTIRE main voice loop thread, not just that one turn.
        """
        from sara.core.intent import register_intent
        from sara.orchestrator.intent_handlers import _handle_command, register_handler

        def _broken_handler(match, ctx):
            raise RuntimeError("boom - deliberately broken for this test")

        register_intent(
            "test_broken_skill", [r"trigger the broken test skill"], gate=("broken",)
        )
        register_handler("test_broken_skill", _broken_handler)

        class _FakeTTS:
            def speak(self, text, fast=False):
                pass

        class _FakeBrain:
            model_name = "test-model"

        result = _handle_command(
            "trigger the broken test skill",
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
        self.assertIsInstance(result, str)
        self.assertIn("problem", result.lower())

    def test_streak_tracking_and_conversation_stats(self):
        from sara.core.memory import PreferencesDB

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        try:
            db = PreferencesDB(db_path=tmp.name)
            self.assertEqual(db.get_streak_count(), 0)

            # First call today -> streak becomes 1.
            streak = db.record_interaction_day()
            self.assertEqual(streak, 1)
            self.assertEqual(db.get_streak_count(), 1)

            # Calling again the SAME day must be a no-op (idempotent).
            streak_again = db.record_interaction_day()
            self.assertEqual(streak_again, 1)

            # Simulate a 3-day streak already in progress, ending yesterday.
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            db.set_preference("streak_last_date", yesterday)
            db.set_preference("streak_count", "2")
            streak = db.record_interaction_day()
            self.assertEqual(streak, 3)
            self.assertEqual(db.get_preference("streak_pending_milestone"), "3")

            # A gap (missed a day) must reset the streak to 1, not just +1.
            db.set_preference("streak_last_date", "2020-01-01")
            db.set_preference("streak_count", "10")
            streak = db.record_interaction_day()
            self.assertEqual(streak, 1)

            stats = db.get_conversation_stats()
            self.assertEqual(stats["total_messages"], 0)
            db.log_message("user", "hello", wait=True)
            db.log_message("assistant", "hi there", wait=True)
            stats = db.get_conversation_stats()
            self.assertEqual(stats["total_messages"], 2)

            db.close()
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def test_joke_and_streak_skills_registered(self):
        from sara.core.intent import detect_intent
        from sara.orchestrator.intent_handlers import _INTENT_HANDLERS

        self.assertIn("tell_joke", _INTENT_HANDLERS)
        self.assertIn("check_streak", _INTENT_HANDLERS)

        intent, _ = detect_intent("tell me a joke")
        self.assertEqual(intent, "tell_joke")
        intent, _ = detect_intent("what's my streak")
        self.assertEqual(intent, "check_streak")
        # Existing intents must remain unaffected.
        intent, _ = detect_intent("what time is it")
        self.assertEqual(intent, "time_query")


if __name__ == "__main__":
    unittest.main()