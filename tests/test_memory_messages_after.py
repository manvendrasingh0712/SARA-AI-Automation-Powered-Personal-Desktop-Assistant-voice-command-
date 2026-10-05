"""tests/test_memory_messages_after.py -- PreferencesDB.get_messages_after / get_max_message_id."""

import os
import tempfile

from sara.core.memory import PreferencesDB


def _db():
    tmp = tempfile.mkdtemp()
    return PreferencesDB(os.path.join(tmp, "t.db"))


def test_messages_after_filters_by_id_and_role():
    db = _db()
    try:
        db.log_message("user", "I live in Ajmer", wait=True)
        db.log_message("assistant", "Nice city", wait=True)
        db.log_message("user", "I study CSE", wait=True)
        rows = db.get_messages_after(0, 10)
        assert [r["message"] for r in rows] == ["I live in Ajmer", "I study CSE"]
        first = rows[0]["id"]
        later = db.get_messages_after(first, 10)
        assert [r["message"] for r in later] == ["I study CSE"]
        both = db.get_messages_after(0, 10, role=None)
        assert len(both) == 3
        assert db.get_max_message_id() == both[-1]["id"]
    finally:
        db.close()


def test_messages_after_limit_and_bad_input():
    db = _db()
    try:
        for i in range(5):
            db.log_message("user", f"msg {i}", wait=True)
        assert len(db.get_messages_after(0, 2)) == 2
        assert db.get_messages_after(0, 0) == []
        assert db.get_messages_after("abc", 3)[0]["message"] == "msg 0"
        assert db.get_messages_after(10**9, 5) == []
    finally:
        db.close()
