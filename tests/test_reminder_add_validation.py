"""Live-test finding: the Calendar-page reminder add stored any string as its date."""
import os
import sqlite3
import tempfile

import pytest

from sara.tools.reminders import ReminderManager


@pytest.fixture
def mgr():
    d = tempfile.TemporaryDirectory()
    m = ReminderManager(db_path=os.path.join(d.name, "r.db"))
    yield m
    try:
        m.close()
    except Exception:
        pass
    d.cleanup()


def _rows(m):
    return m._conn.execute("select message, due_at from reminders").fetchall()


@pytest.mark.parametrize("date,time,expect", [
    ("2030-01-01", "09:00", "2030-01-01T09:00:00"),
    ("2030-01-01", "09:30:15", "2030-01-01T09:30:15"),
    ("2030-12-31", "", "2030-12-31T00:00:00"),
    (" 2030-06-05 ", " 7:05 ", "2030-06-05T07:05:00"),
])
def test_valid_dates_are_stored_normalised(mgr, date, time, expect):
    rid = mgr.add(date, time, "x")
    assert rid > 0 and _rows(mgr) == [("x", expect)]


@pytest.mark.parametrize("date,time", [
    ("SWEEP TEMP REMINDERT2030-01-01", "09:00"), ("2030-13-01", "09:00"), ("2030-02-30", "09:00"), ("tomorrow", "09:00"),
    ("2030-01-01", "25:00"), ("2030-01-01", "nine"), ("", "09:00"), (None, "09:00"),
])
def test_malformed_dates_and_times_are_refused(mgr, date, time):
    assert mgr.add(date, time, "x") == -1
    assert _rows(mgr) == []


def test_empty_text_still_refused(mgr):
    assert mgr.add("2030-01-01", "09:00", "  ") == -1
