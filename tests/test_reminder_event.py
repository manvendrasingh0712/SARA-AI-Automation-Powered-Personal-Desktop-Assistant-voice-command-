import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.tools.reminders import ReminderEvent, ReminderManager


class ReminderEventTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "e.db")
        self.events, self.strings = [], []
        self.mgr = ReminderManager(
            db_path=self.db_path,
            on_trigger=self.strings.append,
            on_trigger_event=self.events.append,
        )

    def tearDown(self):
        self.mgr.close()
        self._tmp.cleanup()

    def _insert(self, message, due_delta_min=-0.5, done=0):
        now = datetime.now()
        cur = self.mgr._conn.execute(
            "INSERT INTO reminders (message, due_at, created_at, triggered, done) "
            "VALUES (?, ?, ?, 0, ?)",
            (
                message,
                (now + timedelta(minutes=due_delta_min)).isoformat(),
                (now - timedelta(days=1)).isoformat(),
                done,
            ),
        )
        self.mgr._conn.commit()
        return cur.lastrowid

    def test_event_callback_gets_structured_event(self):
        rid = self._insert("sleep")
        self.mgr._check_due_reminders()
        self.assertEqual(len(self.events), 1)
        ev = self.events[0]
        self.assertIsInstance(ev, ReminderEvent)
        self.assertEqual(ev.reminder_id, rid)
        self.assertEqual(ev.message, "sleep")
        self.assertEqual(ev.stage, "due")
        self.assertTrue(ev.due_at)
        self.assertTrue(ev.triggered_at)
        self.assertEqual(self.strings, [])  # string callback not used when event callback is set

    def test_string_callback_used_when_no_event_callback(self):
        got = []
        mgr = ReminderManager(db_path=os.path.join(self._tmp.name, "s.db"), on_trigger=got.append)
        try:
            now = datetime.now()
            mgr._conn.execute(
                "INSERT INTO reminders (message, due_at, created_at, triggered, done) VALUES (?,?,?,0,0)",
                ("plain", (now - timedelta(seconds=10)).isoformat(), (now - timedelta(days=1)).isoformat()),
            )
            mgr._conn.commit()
            mgr._check_due_reminders()
            self.assertEqual(got, ["plain"])
        finally:
            mgr.close()

    def test_delivery_time_recorded(self):
        rid = self._insert("study")
        self.mgr._check_due_reminders()
        intel = self.mgr.get_intel(rid)
        self.assertTrue(intel["delivered_at"])
        self.assertEqual(intel["nudge_count"], 0)
        self.assertEqual(intel["acknowledged"], 0)

    def test_update_and_get_intel(self):
        rid = self._insert("sleep", due_delta_min=60)
        self.assertEqual(self.mgr.get_intel(rid), {})
        self.assertTrue(self.mgr.update_intel(rid, nudge_count=2, acknowledged=1, category="sleep"))
        intel = self.mgr.get_intel(rid)
        self.assertEqual(intel["nudge_count"], 2)
        self.assertEqual(intel["acknowledged"], 1)
        self.assertEqual(intel["category"], "sleep")

    def test_update_intel_ignores_unknown_columns(self):
        rid = self._insert("sleep", due_delta_min=60)
        self.assertFalse(self.mgr.update_intel(rid, evil="x; DROP TABLE reminders"))
        self.assertEqual(self.mgr.get_intel(rid), {})

    def test_event_callback_error_does_not_break_poller(self):
        def boom(_ev):
            raise RuntimeError("callback failed")

        mgr = ReminderManager(db_path=os.path.join(self._tmp.name, "x.db"), on_trigger_event=boom)
        try:
            now = datetime.now()
            mgr._conn.execute(
                "INSERT INTO reminders (message, due_at, created_at, triggered, done) VALUES (?,?,?,0,0)",
                ("a", (now - timedelta(seconds=5)).isoformat(), (now - timedelta(days=1)).isoformat()),
            )
            mgr._conn.commit()
            mgr._check_due_reminders()  # must not raise
            row = mgr._conn.execute("SELECT triggered FROM reminders").fetchone()
            self.assertEqual(row[0], 1)
        finally:
            mgr.close()

    def test_orphan_intel_rows_pruned_on_startup(self):
        rid = self._insert("sleep", due_delta_min=60)
        self.mgr.update_intel(rid, nudge_count=1)
        self.mgr._conn.execute("DELETE FROM reminders WHERE id = ?", (rid,))
        self.mgr._conn.commit()
        self.mgr.close()
        self.mgr = ReminderManager(db_path=self.db_path)
        count = self.mgr._conn.execute("SELECT COUNT(*) FROM reminder_intelligence").fetchone()[0]
        self.assertEqual(count, 0)

    def test_reminders_table_unchanged(self):
        cols = [r[1] for r in self.mgr._conn.execute("PRAGMA table_info(reminders)").fetchall()]
        self.assertEqual(cols, ["id", "message", "due_at", "created_at", "triggered", "done"])


if __name__ == "__main__":
    unittest.main()