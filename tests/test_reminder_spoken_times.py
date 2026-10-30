"""
Reminder times as people say them.

Live E2E 2026-10-30: dateparser read "tomorrow at 9" as tomorrow 01:30
(the 9 was dropped -- the reminder would ring at the wrong time) and could
not read "tomorrow morning", "tonight", "7 baje", "5 baje shaam", "kal
subah" or "2 ghante"; "remind me tomorrow morning about the meeting" did
not even route to reminder_add.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from sara.core.intent import detect_intent
from sara.tools.reminder_time import parse_when, split_trailing_day
from sara.tools.reminders import ReminderManager

NOW = datetime(2026, 10, 30, 1, 30)


@pytest.mark.parametrize("text, expected", [
    ("20 minutes", NOW + timedelta(minutes=20)),
    ("in 20 minutes", NOW + timedelta(minutes=20)),
    ("10 minute baad", NOW + timedelta(minutes=10)),
    ("2 ghante", NOW + timedelta(hours=2)),
    ("tomorrow morning", datetime(2026, 10, 31, 9, 0)),
    ("tomorrow at 9", datetime(2026, 10, 31, 9, 0)),
    ("tomorrow at 9 pm", datetime(2026, 10, 31, 21, 0)),
    ("tonight", datetime(2026, 10, 30, 21, 0)),
    ("this evening", datetime(2026, 10, 30, 18, 0)),
    ("kal subah", datetime(2026, 10, 31, 9, 0)),
    ("kal 8 baje", datetime(2026, 10, 31, 8, 0)),
    ("7 baje", datetime(2026, 10, 30, 7, 0)),
    ("5 baje shaam", datetime(2026, 10, 30, 17, 0)),
    ("aaj raat 10 baje", datetime(2026, 10, 30, 22, 0)),
    ("9:30 pm", datetime(2026, 10, 30, 21, 30)),
    ("parso shaam", datetime(2026, 11, 1, 18, 0)),
])
def test_parse_when(text, expected):
    assert parse_when(text, NOW) == expected


def test_bare_baje_already_passed_today_means_this_evening():
    assert parse_when("7 baje", datetime(2026, 10, 30, 8, 0)) == datetime(2026, 10, 30, 19, 0)


@pytest.mark.parametrize("text", ["meeting", "", "the oven"])
def test_unknown_forms_fall_through(text):
    assert parse_when(text, NOW) is None


def test_day_left_in_the_task_moves_to_the_time():
    assert split_trailing_day("call mom tomorrow") == ("call mom", "tomorrow")
    assert split_trailing_day("meeting kal subah") == ("meeting", "kal subah")
    assert split_trailing_day("check the oven") == ("check the oven", "")


@pytest.mark.parametrize("text, task, when", [
    ("remind me tomorrow morning about the meeting", "the meeting", "tomorrow morning"),
    ("remind me about the meeting tonight", "the meeting", "tonight"),
    ("remind me this evening to water the plants", "water the plants", "this evening"),
])
def test_day_only_reminders_route(text, task, when):
    detect_intent.cache_clear()
    intent, match = detect_intent(text)
    assert intent == "reminder_add" and (match.group(1), match.group(2)) == (task, when)


def test_manager_stores_tomorrow_at_9_at_nine(tmp_path):
    mgr = ReminderManager(db_path=str(tmp_path / "r.db"))
    try:
        reply = mgr.add_reminder("call mom tomorrow", "9")
        assert "call mom" in reply
        due = sqlite3.connect(str(tmp_path / "r.db")).execute(
            "SELECT message, due_at FROM reminders").fetchone()
        assert due[0] == "call mom"
        stamp = datetime.fromisoformat(due[1])
        assert (stamp.hour, stamp.minute) == (9, 0)
        assert stamp.date() == (datetime.now() + timedelta(days=1)).date()
    finally:
        mgr.close()
