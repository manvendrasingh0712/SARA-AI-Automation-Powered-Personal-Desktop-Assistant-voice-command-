"""
sara.gui.app.reminders
ApiRemindersMixin -- Calendar/Reminders page CRUD, backed by ReminderManager.
"""
import os
import json
import time
import queue
import random
import threading
import urllib.request
import urllib.parse
import webview

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
                self.reminders.delete(reminder_id)
                return {"ok": True}
        except Exception as e:
            print(f"[delete_reminder error] {e}")
        return {"ok": False}

    def toggle_reminder(self, reminder_id):
        try:
            if hasattr(self.reminders, "toggle"):
                self.reminders.toggle(reminder_id)
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
