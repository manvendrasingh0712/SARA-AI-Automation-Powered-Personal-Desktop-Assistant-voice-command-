"""Part 3, P6: shared SQLite settings and the indexes that replaced full scans."""
import sqlite3

from sara.core.db_common import apply_pragmas, optimize_and_checkpoint


def test_apply_pragmas_sets_wal_normal_and_a_busy_timeout(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    apply_pragmas(c)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA synchronous").fetchone()[0] == 1          # NORMAL
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.execute("PRAGMA temp_store").fetchone()[0] == 2           # MEMORY
    optimize_and_checkpoint(c, truncate=True)                            # must not raise


def test_a_fresh_preferences_db_has_the_new_indexes(tmp_path):
    from sara.core.memory import PreferencesDB

    _db = PreferencesDB(str(tmp_path / "sara.db"))      # creating it builds the schema; the test then inspects the file
    con = sqlite3.connect(tmp_path / "sara.db")
    names = {r[0] for r in con.execute("select name from sqlite_master where type='index'")}
    assert {"idx_conversation_log_timestamp", "idx_action_log_timestamp", "idx_action_log_name_outcome"} <= names
    plan = " ".join(r[3] for r in con.execute(
        "explain query plan select * from action_log where timestamp>='2026-01-01' and timestamp<'2026-01-02'"))
    assert "USING INDEX" in plan.upper() or "USING COVERING INDEX" in plan.upper()
