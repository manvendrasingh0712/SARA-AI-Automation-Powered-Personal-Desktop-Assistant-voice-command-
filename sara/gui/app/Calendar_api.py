"""
sara.gui.app.calendar_api
ApiCalendarMixin -- Settings page Calendar card. Thin wrappers around
sara/tools/calendar.py, same shape as ApiRemindersMixin
(sara/gui/app/reminders.py).
"""
from sara.tools import calendar as calendar_tools


class ApiCalendarMixin:

    def get_calendar_status(self):
        try:
            status = calendar_tools.get_calendar_status()
            return {"ok": True, "data": status}
        except Exception as e:
            print(f"[get_calendar_status error] {e}")
            return {"ok": False, "data": {"connected": False, "email": None}}

    def get_today_calendar_events(self):
        try:
            events = calendar_tools.get_today_events()
            return {"ok": True, "data": events}
        except Exception as e:
            print(f"[get_today_calendar_events error] {e}")
            return {"ok": False, "data": []}