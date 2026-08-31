"""
sara.gui.app.reminders
ApiRemindersMixin -- Calendar/Reminders page CRUD, backed by ReminderManager.
"""

class ApiRemindersMixin:

    # ── Calendar / Reminders (Calendar page) ──────────────────────────
    # ReminderManager (sara/tools/reminders.py) now exposes add/delete/
    # toggle/get_all on the SAME table the voice "remind me..." intent
    # writes to, so calendar reminders persist across restarts and also
    # get spoken + beeped by the existing background poller when due.
    def add_reminder(self, date_str, time_str, text):
        try:
            if hasattr(self.reminders, "add"):
                new_id = self.reminders.add(date_str, time_str, text)
                return {"ok": new_id != -1, "id": new_id}
        except Exception as e:
            print(f"[add_reminder error] {e}")
        return {"ok": False, "id": None}

    def delete_reminder(self, reminder_id):
        try:
            if hasattr(self.reminders, "delete"):
                result = self.reminders.delete(reminder_id)
                # ReminderManager.delete()'s return contract isn't defined in
                # this codebase (sara/tools/reminders.py is external), so we
                # can't assume it returns a bool at all -- a plain None return
                # is the normal Python convention for "ran with no explicit
                # result" and must NOT be read as failure. The one thing we can
                # safely act on is an explicit False, which is the one signal
                # a caller-side contract would use to mean "did not happen"
                # (e.g. reminder_id not found) without guessing anything else.
                if result is False:
                    return {"ok": False}
                return {"ok": True}
        except Exception as e:
            print(f"[delete_reminder error] {e}")
        return {"ok": False}

    def toggle_reminder(self, reminder_id):
        try:
            if hasattr(self.reminders, "toggle"):
                result = self.reminders.toggle(reminder_id)
                # Same reasoning as delete_reminder above: only an explicit
                # False is treated as failure; a None return (the common
                # "no explicit result" convention) still reports ok.
                if result is False:
                    return {"ok": False}
                return {"ok": True}
        except Exception as e:
            print(f"[toggle_reminder error] {e}")
        return {"ok": False}

    def get_reminders(self):
        try:
            if hasattr(self.reminders, "get_all"):
                return {"ok": True, "data": self.reminders.get_all()}
        except Exception as e:
            print(f"[get_reminders error] {e}")
        return {"ok": False, "data": []}