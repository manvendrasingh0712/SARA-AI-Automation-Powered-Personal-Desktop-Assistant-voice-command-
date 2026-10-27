import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.core.llm.provider_runtime import GenResult
from sara.tools.reminder_composer import ReminderComposer
from sara.tools.reminder_context import build_prompt, classify, effective_policy, followup_message, time_bucket
from sara.tools.reminder_followup import (
    AWAKE_HIGH,
    AWAKE_LOW,
    FollowupSettings,
    acknowledge_if_applicable,
    awake_level,
    classify_ack,
    pick_followup,
    settings_from_config,
)
from sara.tools.reminders import ReminderManager

_NOW = datetime(2026, 10, 3, 1, 40)
_SET = FollowupSettings(cooldown_min=30, max_nudges=2, awake_window_min=10, post_due_min=120)


def _cand(message="sleep", delivered_min_ago=40, nudges=0, last_nudge_min_ago=None, rid=1):
    return {
        "id": rid,
        "message": message,
        "due_at": (_NOW - timedelta(minutes=delivered_min_ago)).isoformat(),
        "delivered_at": (_NOW - timedelta(minutes=delivered_min_ago)).isoformat(),
        "last_nudge_at": None if last_nudge_min_ago is None else (_NOW - timedelta(minutes=last_nudge_min_ago)).isoformat(),
        "nudge_count": nudges,
    }


def _touch(minutes_ago):
    return _NOW - timedelta(minutes=minutes_ago)


class AwakeLevelTests(unittest.TestCase):
    def test_levels(self):
        delivered = _NOW - timedelta(minutes=40)
        self.assertEqual(awake_level(_touch(2), delivered, _NOW, 10), AWAKE_HIGH)
        self.assertEqual(awake_level(_touch(15), delivered, _NOW, 10), AWAKE_LOW)   # too old
        self.assertEqual(awake_level(_touch(45), delivered, _NOW, 10), AWAKE_LOW)   # before delivery
        self.assertEqual(awake_level(None, delivered, _NOW, 10), AWAKE_LOW)


class PickFollowupTests(unittest.TestCase):
    def test_eligible_sleep(self):
        plan = pick_followup([_cand()], _touch(2), _NOW, _SET)
        self.assertEqual((plan.reminder_id, plan.category, plan.nudge_number), (1, "sleep", 1))
        self.assertEqual(plan.minutes_since, 40)

    def test_no_activity_no_nudge(self):
        self.assertIsNone(pick_followup([_cand()], None, _NOW, _SET))
        self.assertIsNone(pick_followup([_cand()], _touch(30), _NOW, _SET))

    def test_first_nudge_waits_for_cooldown(self):
        self.assertIsNone(pick_followup([_cand(delivered_min_ago=5)], _touch(1), _NOW, _SET))
        self.assertIsNotNone(pick_followup([_cand(delivered_min_ago=31)], _touch(1), _NOW, _SET))

    def test_cooldown_after_a_nudge(self):
        c = _cand(nudges=1, last_nudge_min_ago=10)
        self.assertIsNone(pick_followup([c], _touch(1), _NOW, _SET))
        c = _cand(nudges=1, last_nudge_min_ago=31)
        self.assertEqual(pick_followup([c], _touch(1), _NOW, _SET).nudge_number, 2)

    def test_max_nudges(self):
        self.assertIsNone(pick_followup([_cand(nudges=2, last_nudge_min_ago=60)], _touch(1), _NOW, _SET))
        one = FollowupSettings(30, 1, 10, 120)
        self.assertIsNone(pick_followup([_cand(nudges=1, last_nudge_min_ago=60)], _touch(1), _NOW, one))

    def test_policy_limit_for_study_is_one(self):
        c = _cand("study DBMS", nudges=1, last_nudge_min_ago=60)
        self.assertIsNone(pick_followup([c], _touch(1), _NOW, _SET))

    def test_post_due_window(self):
        self.assertIsNone(pick_followup([_cand(delivered_min_ago=130)], _touch(1), _NOW, _SET))

    def test_categories_without_followup_policy(self):
        for text in ("team meeting", "check the oven", "pay electricity bill"):
            self.assertIsNone(pick_followup([_cand(text)], _touch(1), _NOW, _SET), text)

    def test_night_setting(self):
        self.assertIsNone(pick_followup([_cand()], _touch(1), _NOW, _SET, night_allowed=False))
        daytime = datetime(2026, 10, 3, 14, 40)
        c = {**_cand(), "delivered_at": (daytime - timedelta(minutes=40)).isoformat(),
             "due_at": (daytime - timedelta(minutes=40)).isoformat()}
        self.assertIsNotNone(pick_followup([c], daytime - timedelta(minutes=1), daytime, _SET, night_allowed=False))

    def test_bad_timestamps_are_skipped(self):
        c = {**_cand(), "delivered_at": "garbage"}
        self.assertIsNone(pick_followup([c], _touch(1), _NOW, _SET))

    def test_first_eligible_wins(self):
        plan = pick_followup([_cand("check the oven", rid=5), _cand("sleep", rid=6)], _touch(1), _NOW, _SET)
        self.assertEqual(plan.reminder_id, 6)

    def test_settings_from_config(self):
        s = settings_from_config(SimpleNamespace(CONTEXTUAL_REMINDER_MAX_NUDGES="3"))
        self.assertEqual((s.cooldown_min, s.max_nudges, s.awake_window_min, s.post_due_min), (30, 3, 10, 120))
        self.assertEqual(settings_from_config(SimpleNamespace(CONTEXTUAL_REMINDER_MAX_NUDGES="x")).max_nudges, 2)


class FakeReminders:
    def __init__(self, cands):
        self.cands = cands
        self.updates = []

    def followup_candidates(self):
        return self.cands

    def update_intel(self, rid, **fields):
        self.updates.append((rid, fields))
        return True


class AckTests(unittest.TestCase):
    def test_classification(self):
        for text in ("okay", "Ok Sara!", "got it", "Done.", "thank you", "all right"):
            self.assertEqual(classify_ack(text), "bare", text)
        for text in ("I'm going to sleep now", "ok going to bed", "good night Sara", "goodnight",
                     "reminder done", "mark it as done", "stop reminding me", "i'll sleep now"):
            self.assertEqual(classify_ack(text), "strong", text)
        for text in ("open chrome", "what's the weather", "okay open chrome", "", None,
                     "set a reminder for sleep"):
            self.assertIsNone(classify_ack(text), repr(text))

    def test_bare_ok_right_after_reminder(self):
        r = FakeReminders([_cand(delivered_min_ago=3)])
        self.assertEqual(acknowledge_if_applicable("okay", r, _NOW), "Alright, good night.")
        self.assertEqual(r.updates, [(1, {"acknowledged": 1})])

    def test_bare_ok_much_later_is_ignored(self):
        r = FakeReminders([_cand(delivered_min_ago=20)])
        self.assertIsNone(acknowledge_if_applicable("okay", r, _NOW))
        self.assertEqual(r.updates, [])

    def test_strong_phrase_counts_for_an_hour(self):
        r = FakeReminders([_cand(delivered_min_ago=45)])
        self.assertEqual(acknowledge_if_applicable("ok I'm going to sleep", r, _NOW), "Alright, good night.")

    def test_last_nudge_time_is_used(self):
        r = FakeReminders([_cand(delivered_min_ago=90, nudges=1, last_nudge_min_ago=2)])
        self.assertIsNotNone(acknowledge_if_applicable("okay", r, _NOW))

    def test_non_sleep_reply(self):
        r = FakeReminders([_cand("study DBMS", delivered_min_ago=3)])
        self.assertEqual(acknowledge_if_applicable("done", r, _NOW), "Okay, noted.")

    def test_most_recent_reminder_is_acknowledged(self):
        r = FakeReminders([_cand("study DBMS", delivered_min_ago=8, rid=1), _cand("sleep", delivered_min_ago=2, rid=2)])
        acknowledge_if_applicable("okay", r, _NOW)
        self.assertEqual(r.updates[0][0], 2)

    def test_no_candidates_or_unrelated_text(self):
        self.assertIsNone(acknowledge_if_applicable("okay", FakeReminders([]), _NOW))
        self.assertIsNone(acknowledge_if_applicable("open chrome", FakeReminders([_cand(delivered_min_ago=1)]), _NOW))

    def test_never_raises(self):
        class Bad:
            def followup_candidates(self):
                raise RuntimeError("db gone")

        self.assertIsNone(acknowledge_if_applicable("okay", Bad(), _NOW))
        self.assertIsNone(acknowledge_if_applicable("okay", None, _NOW))
        self.assertIsNone(acknowledge_if_applicable("okay", object(), _NOW))


class WordingTests(unittest.TestCase):
    def test_sleep_followup(self):
        self.assertEqual(
            followup_message("sleep", 40, _NOW),
            "It's 1:40 AM, and you're still talking to me. Your sleep reminder was 40 minutes ago. Please get some rest.",
        )

    def test_generic_and_hours(self):
        self.assertEqual(followup_message("check the oven", 125, _NOW), "Your reminder was about 2 hours ago: check the oven.")
        self.assertEqual(followup_message("study", 1, _NOW), "Your study reminder was 1 minute ago. Whenever you're ready, let's get started.")

    def test_empty_is_none(self):
        self.assertIsNone(followup_message("", 10, _NOW))

    def test_followup_prompt(self):
        intent = classify("sleep")
        bucket = time_bucket(_NOW)
        _, user = build_prompt(intent, bucket, _NOW, effective_policy(intent, bucket), stage="followup", minutes_since=40)
        self.assertIn("<stage>followup</stage>", user)
        self.assertIn("<minutes_since_reminder>40</minutes_since_reminder>", user)
        self.assertIn("still actively talking to Sara", user)
        self.assertIn("do not mention health", user)


class ComposerFollowupTests(unittest.TestCase):
    def _composer(self, text):
        cfg = SimpleNamespace(CONTEXTUAL_REMINDER_LLM=True, CONTEXTUAL_REMINDER_LLM_TIMEOUT_S=0.5)
        gen = lambda *a, **k: GenResult(text, "gemini", "success", (), 5)
        return ReminderComposer(cfg, generate=gen, clock=lambda: _NOW)

    def test_llm_followup_with_correct_numbers(self):
        r = self._composer("It's 1:40 and your sleep reminder was 40 minutes ago, please rest.").start(
            "sleep", stage="followup", minutes_since=40)()
        self.assertEqual(r.source, "llm")

    def test_invented_number_falls_back_to_template(self):
        r = self._composer("You have been up for 6 hours, please sleep.").start("sleep", stage="followup", minutes_since=40)()
        self.assertEqual((r.source, r.failure), ("template", "invalid_output"))
        self.assertIn("Your sleep reminder was 40 minutes ago", r.text)

    def test_failure_template(self):
        cfg = SimpleNamespace(CONTEXTUAL_REMINDER_LLM=False)
        r = ReminderComposer(cfg, clock=lambda: _NOW).start("study", stage="followup", minutes_since=35)()
        self.assertEqual(r.text, "Your study reminder was 35 minutes ago. Whenever you're ready, let's get started.")


class ManagerCandidatesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mgr = ReminderManager(db_path=os.path.join(self._tmp.name, "f.db"))
        now = datetime.now()
        self.rid = self.mgr._conn.execute(
            "INSERT INTO reminders (message, due_at, created_at, triggered, done) VALUES (?,?,?,1,0)",
            ("sleep", now.isoformat(), now.isoformat()),
        ).lastrowid
        self.mgr._conn.commit()

    def tearDown(self):
        self.mgr.close()
        self._tmp.cleanup()

    def test_only_delivered_unacknowledged_not_done(self):
        self.assertEqual(self.mgr.followup_candidates(), [])  # not delivered yet
        self.mgr.update_intel(self.rid, delivered_at=datetime.now().isoformat())
        cands = self.mgr.followup_candidates()
        self.assertEqual([(c["id"], c["message"], c["nudge_count"]) for c in cands], [(self.rid, "sleep", 0)])
        self.mgr.update_intel(self.rid, acknowledged=1)
        self.assertEqual(self.mgr.followup_candidates(), [])
        self.mgr.update_intel(self.rid, acknowledged=0)
        self.mgr._conn.execute("UPDATE reminders SET done = 1")
        self.mgr._conn.commit()
        self.assertEqual(self.mgr.followup_candidates(), [])

    def test_deleted_reminder_has_no_candidates(self):
        self.mgr.update_intel(self.rid, delivered_at=datetime.now().isoformat())
        self.mgr._conn.execute("DELETE FROM reminders WHERE id = ?", (self.rid,))
        self.mgr._conn.commit()
        self.assertEqual(self.mgr.followup_candidates(), [])

    def test_ack_through_manager(self):
        self.mgr.update_intel(self.rid, delivered_at=datetime.now().isoformat())
        self.assertEqual(acknowledge_if_applicable("okay", self.mgr), "Alright, good night.")
        self.assertEqual(self.mgr.get_intel(self.rid)["acknowledged"], 1)
        self.assertIsNone(acknowledge_if_applicable("okay", self.mgr))


if __name__ == "__main__":
    unittest.main()