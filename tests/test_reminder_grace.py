import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.tools.reminders import ReminderManager, _grace_minutes_for


class ReminderGraceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "r.db")
        self.fired, self.late, self.missed = [], [], []
        self.mgr = ReminderManager(
            db_path=self.db_path,
            on_trigger=self.fired.append,
            on_late=self.late.extend,
            on_missed=self.missed.extend,
        )

    def tearDown(self):
        self.mgr.close()
        self._tmp.cleanup()

    def _insert(self, message, due_delta_min, created_delta_min=-1440, done=0):
        now = datetime.now()
        self.mgr._conn.execute(
            "INSERT INTO reminders (message, due_at, created_at, triggered, done) "
            "VALUES (?, ?, ?, 0, ?)",
            (
                message,
                (now + timedelta(minutes=due_delta_min)).isoformat(),
                (now + timedelta(minutes=created_delta_min)).isoformat(),
                done,
            ),
        )
        self.mgr._conn.commit()

    def test_grace_levels(self):
        self.assertEqual(_grace_minutes_for("team meeting"), 15)
        self.assertEqual(_grace_minutes_for("pay electricity bill"), 60)
        self.assertEqual(_grace_minutes_for("urgent call mom"), 60)
        self.assertEqual(_grace_minutes_for("sleep"), 45)
        self.assertEqual(_grace_minutes_for("something unknown"), 45)

    def test_on_time_fires_normally(self):
        self._insert("sleep", -0.5)
        self.mgr._check_due_reminders()
        self.assertEqual(self.fired, ["sleep"])
        self.assertEqual(self.late, [])
        self.assertEqual(self.missed, [])

    def test_late_inside_grace_goes_to_on_late(self):
        self._insert("study", -20)
        self.mgr._check_due_reminders()
        self.assertEqual(self.late, ["study"])
        self.assertEqual(self.fired, [])

    def test_past_grace_goes_to_on_missed(self):
        self._insert("meeting with team", -60)
        self.mgr._check_due_reminders()
        self.assertEqual(self.missed, ["meeting with team"])
        self.assertEqual(self.fired, [])

    def test_created_this_run_fires_even_if_old_due_time(self):
        self._insert("calendar item", -600, created_delta_min=5)
        self.mgr._check_due_reminders()
        self.assertEqual(self.fired, ["calendar item"])

    def test_done_reminder_does_not_fire(self):
        self._insert("already done", -0.5, done=1)
        self.mgr._check_due_reminders()
        self.assertEqual(self.fired, [])
        self.assertEqual(self.late, [])
        self.assertEqual(self.missed, [])

    def test_without_callbacks_old_behavior(self):
        fired = []
        mgr = ReminderManager(db_path=os.path.join(self._tmp.name, "b.db"), on_trigger=fired.append)
        try:
            now = datetime.now()
            mgr._conn.execute(
                "INSERT INTO reminders (message, due_at, created_at, triggered, done) VALUES (?,?,?,0,0)",
                ("old one", (now - timedelta(hours=5)).isoformat(), (now - timedelta(days=1)).isoformat()),
            )
            mgr._conn.commit()
            mgr._check_due_reminders()
            self.assertEqual(fired, ["old one"])
        finally:
            mgr.close()

    def test_triggered_flag_set_for_all_routes(self):
        self._insert("study", -20)
        self._insert("meeting x", -90)
        self.mgr._check_due_reminders()
        rows = self.mgr._conn.execute("SELECT triggered FROM reminders").fetchall()
        self.assertTrue(all(r[0] == 1 for r in rows))

    def test_get_upcoming_excludes_triggered(self):
        self._insert("soon", 5)
        self.assertEqual(len(self.mgr.get_upcoming(15)), 1)
        self.mgr._conn.execute("UPDATE reminders SET triggered = 1")
        self.mgr._conn.commit()
        self.assertEqual(self.mgr.get_upcoming(15), [])


if __name__ == "__main__":
    unittest.main()