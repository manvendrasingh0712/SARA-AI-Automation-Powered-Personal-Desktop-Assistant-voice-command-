import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.core.llm.provider_runtime import GenResult
from sara.tools.reminder_composer import ReminderComposer
from sara.tools.reminder_context import (
    build_prompt,
    classify,
    effective_policy,
    minutes_until,
    time_bucket,
    upcoming_message,
    validate_text,
)

_NOW = datetime(2026, 10, 3, 9, 0)


class UpcomingMessageTests(unittest.TestCase):
    def test_generic(self):
        self.assertEqual(
            upcoming_message("check the oven", 12),
            "Heads up, you have a reminder in about 12 minutes: check the oven.",
        )

    def test_singular_minute(self):
        self.assertEqual(
            upcoming_message("check the oven", 1),
            "Heads up, you have a reminder in about 1 minute: check the oven.",
        )

    def test_unknown_minutes(self):
        self.assertEqual(
            upcoming_message("check the oven", None),
            "Heads up, you have a reminder coming up soon: check the oven.",
        )

    def test_sleep(self):
        self.assertEqual(
            upcoming_message("sleep", 10),
            "Heads up, your sleep reminder is in about 10 minutes. Start winding down.",
        )

    def test_empty_is_none(self):
        self.assertIsNone(upcoming_message("", 5))
        self.assertIsNone(upcoming_message(None, 5))

    def test_braces_are_safe(self):
        self.assertIn("{rent}", upcoming_message("pay {rent}", 5))


class MinutesUntilTests(unittest.TestCase):
    def test_rounding_and_floor(self):
        self.assertEqual(minutes_until("2026-10-03T09:12:00", _NOW), 12)
        self.assertEqual(minutes_until("2026-10-03T09:00:20", _NOW), 1)
        self.assertEqual(minutes_until("2026-10-03T08:59:00", _NOW), 1)

    def test_bad_input(self):
        self.assertIsNone(minutes_until("not a date", _NOW))
        self.assertIsNone(minutes_until(None, _NOW))


class PromptAndValidationTests(unittest.TestCase):
    def test_upcoming_prompt(self):
        intent = classify("sleep")
        bucket = time_bucket(_NOW)
        _, user = build_prompt(
            intent, bucket, _NOW, effective_policy(intent, bucket), stage="upcoming", minutes_until=12
        )
        self.assertIn("<stage>upcoming</stage>", user)
        self.assertIn("<minutes_until_due>12</minutes_until_due>", user)
        self.assertIn("The reminder is not due yet.", user)

    def test_due_prompt_unchanged(self):
        intent = classify("sleep")
        bucket = time_bucket(_NOW)
        _, user = build_prompt(intent, bucket, _NOW, effective_policy(intent, bucket))
        self.assertNotIn("<stage>", user)
        self.assertTrue(user.endswith("Write the reminder now."))

    def test_allowed_numbers(self):
        self.assertEqual(validate_text("Sleep in 12 minutes.", 25, allowed_numbers=[12]), "Sleep in 12 minutes.")
        self.assertIsNone(validate_text("Sleep in 10 minutes.", 25, allowed_numbers=[12]))
        self.assertEqual(validate_text("Sleep soon.", 25, allowed_numbers=[]), "Sleep soon.")
        self.assertEqual(validate_text("Sleep in 10 minutes.", 25), "Sleep in 10 minutes.")


def _cfg():
    return SimpleNamespace(CONTEXTUAL_REMINDER_LLM=True, CONTEXTUAL_REMINDER_LLM_TIMEOUT_S=0.5)


def _ok(text):
    return GenResult(text, "gemini", "success", (("gemini", "success"),), 100)


class ComposerUpcomingTests(unittest.TestCase):
    def _composer(self, gen, cfg=None):
        return ReminderComposer(cfg or _cfg(), generate=gen, clock=lambda: _NOW)

    def test_llm_heads_up_with_correct_minutes(self):
        c = self._composer(lambda *a, **k: _ok("Your sleep reminder is in 12 minutes."))
        r = c.start("sleep", stage="upcoming", minutes_until=12)()
        self.assertEqual((r.source, r.text), ("llm", "Your sleep reminder is in 12 minutes."))

    def test_wrong_minutes_falls_back_to_template(self):
        c = self._composer(lambda *a, **k: _ok("Your sleep reminder is in 5 minutes."))
        r = c.start("sleep", stage="upcoming", minutes_until=12)()
        self.assertEqual((r.source, r.failure), ("template", "invalid_output"))
        self.assertEqual(
            r.text, "Heads up, your sleep reminder is in about 12 minutes. Start winding down."
        )

    def test_number_in_reminder_text_is_allowed(self):
        c = self._composer(lambda *a, **k: _ok("Heads up, chapter 5 revision is in 12 minutes."))
        r = c.start("study chapter 5", stage="upcoming", minutes_until=12)()
        self.assertEqual(r.source, "llm")

    def test_failure_uses_upcoming_template(self):
        c = self._composer(lambda *a, **k: GenResult(None, "none", "quota", (), 5))
        r = c.start("check the oven", stage="upcoming", minutes_until=7)()
        self.assertEqual(r.text, "Heads up, you have a reminder in about 7 minutes: check the oven.")

    def test_disabled_and_empty_defaults(self):
        c = self._composer(lambda *a, **k: None, cfg=SimpleNamespace(CONTEXTUAL_REMINDER_LLM=False))
        self.assertEqual(
            c.start("x", stage="upcoming", minutes_until=3)().text,
            "Heads up, you have a reminder in about 3 minutes: x.",
        )
        r = self._composer(lambda *a, **k: None).start("", stage="upcoming")()
        self.assertEqual(r.text, 'Just a heads-up, "" is coming up soon.')

    def test_due_stage_unchanged(self):
        c = self._composer(lambda *a, **k: _ok("It's 9 AM, time to get some rest."))
        r = c.start("sleep")()
        self.assertEqual(r.source, "llm")


if __name__ == "__main__":
    unittest.main()