import os
import sys
import unittest
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.tools.reminder_context import (
    classify,
    due_message,
    effective_policy,
    time_bucket,
    urgency_level,
)


def _at(h, m=0):
    return datetime(2026, 10, 3, h, m)


class TimeBucketTests(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            ((23, 59), "late_night"), ((0, 0), "late_night"), ((0, 59), "late_night"),
            ((1, 0), "deep_night"), ((4, 59), "deep_night"), ((5, 0), "morning"),
            ((11, 59), "morning"), ((12, 0), "afternoon"), ((16, 59), "afternoon"),
            ((17, 0), "evening"), ((21, 59), "evening"), ((22, 0), "late_night"),
        ]
        for (h, m), expected in cases:
            self.assertEqual(time_bucket(_at(h, m)), expected, f"{h:02d}:{m:02d}")


class ClassifyTests(unittest.TestCase):
    def test_categories(self):
        cases = {
            "sleep": "sleep", "go to bed": "sleep", "wake up": "wake",
            "study DBMS": "study", "exam tomorrow": "exam", "submit assignment": "assignment",
            "project demo": "project", "team meeting": "meeting", "call mom": "call",
            "leave for station": "travel", "take medicine": "health_routine",
            "pay electricity bill": "payment", "drink water": "water", "go to the gym": "exercise",
            "take a break": "break", "buy groceries": "shopping", "mom's birthday": "birthday",
            "check the oven": "generic", "": "generic",
        }
        for text, expected in cases.items():
            self.assertEqual(classify(text).category, expected, text)

    def test_priority_order(self):
        self.assertEqual(classify("study for exam").category, "exam")
        self.assertEqual(classify("pay for train ticket").category, "travel")
        self.assertEqual(classify("workout").category, "exercise")

    def test_word_boundaries(self):
        self.assertEqual(classify("recall the plan").category, "generic")
        self.assertEqual(classify("workout").category, "exercise")

    def test_none_is_safe(self):
        intent = classify(None)
        self.assertEqual(intent.category, "generic")
        self.assertEqual(intent.subject, "")

    def test_subject_is_tidied_and_capped(self):
        self.assertEqual(classify("  study   DBMS. ").subject, "study DBMS")
        self.assertLessEqual(len(classify("x " * 100).subject), 60)

    def test_late_night_relevance(self):
        self.assertTrue(classify("sleep").late_night_relevance)
        self.assertFalse(classify("study").late_night_relevance)


class LevelTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(urgency_level("team meeting"), "critical")
        self.assertEqual(urgency_level("pay electricity bill"), "important")
        self.assertEqual(urgency_level("urgent call mom"), "important")
        self.assertEqual(urgency_level("sleep"), "routine")
        self.assertEqual(urgency_level("something unknown"), "normal")


class PolicyTests(unittest.TestCase):
    def test_sleep_policy_allows_followup(self):
        p = effective_policy(classify("sleep"), "morning")
        self.assertTrue(p.post_due_followup)
        self.assertEqual(p.max_nudges, 2)

    def test_night_is_shorter_and_quieter(self):
        p = effective_policy(classify("check the oven"), "deep_night")
        self.assertEqual(p.tone, "night")
        self.assertLessEqual(p.max_words, 25)

    def test_generic_default_policy(self):
        p = effective_policy(classify("check the oven"), "morning")
        self.assertFalse(p.post_due_followup)
        self.assertEqual(p.max_nudges, 0)


class DueMessageTests(unittest.TestCase):
    def test_sleep_message_mentions_time(self):
        self.assertEqual(
            due_message("sleep", _at(1, 0)),
            "It's 1:00 AM. Time to wind down and get some rest.",
        )

    def test_generic_matches_old_wording(self):
        self.assertEqual(due_message("check the oven", _at(9)), "Reminder: check the oven")

    def test_subject_templates(self):
        self.assertEqual(due_message("team meeting", _at(9)), "Reminder: team meeting. It's time.")

    def test_empty_returns_none(self):
        self.assertIsNone(due_message("", _at(9)))
        self.assertIsNone(due_message(None, _at(9)))

    def test_braces_in_text_are_safe(self):
        self.assertEqual(due_message("pay {rent}", _at(9)), "Reminder: pay {rent}. Please take care of it now.")


if __name__ == "__main__":
    unittest.main()