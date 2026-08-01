"""
sara/tools/calendar.py
Google Calendar integration for Sara AI — lets the assistant read real
events (today's schedule, what's coming up) and create new events, so it
can be genuinely proactive about meetings, the way a real premium
assistant (Siri/Google Assistant) is.

Auth model (OAuth2 "installed app" flow — this is a personal desktop app,
so InstalledAppFlow is the correct flow, not a service account):
  - credentials.json (OAuth client id/secret from Google Cloud Console) is
    a PRE-REQUISITE the user generates themselves and drops into the
    project root — this module never creates or manages that file.
  - The first time any function here needs the API, it opens a browser
    for one-time consent, then saves the resulting refresh token to
    token.json (also project root — see Config.GOOGLE_CALENDAR_TOKEN_PATH,
    the same CWD-independent-path pattern DB_PATH already uses), so every
    later call is silent — no repeat consent prompts.
  - If credentials.json is missing (or the google-auth/api-client packages
    aren't installed), every public function here degrades to a safe
    "not connected" result instead of crashing. Calendar is an entirely
    optional, additive feature — same shape as sara/tools/web.py's
    DDGS/BeautifulSoup optional-import pattern.

Every public function below is wrapped so NOTHING ever raises up to the
caller — same never-crash contract as sara/tools/reminders.py and
sara/tools/web.py. On any failure, create_event() returns a safe
{"ok": False, ...} dict; every read function returns an empty list/dict.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import Config

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    _GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    Request = None  # type: ignore[assignment]
    Credentials = None  # type: ignore[assignment]
    InstalledAppFlow = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]
    _GOOGLE_LIBS_AVAILABLE = False

# Full calendar scope (read + write) so create_event() can also work,
# not just the read-only scope.
_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_CALENDAR_ID = "primary"


def _creds_path() -> str:
    return getattr(Config, "GOOGLE_CALENDAR_CREDENTIALS_PATH", "") or ""


def _token_path() -> str:
    return getattr(Config, "GOOGLE_CALENDAR_TOKEN_PATH", "") or ""


def get_calendar_service():
    """
    Returns an authorized Google Calendar API service object, or None if
    calendar isn't configured / auth fails for any reason. Never raises —
    calendar features simply go into a "not connected" state instead.
    """
    if not _GOOGLE_LIBS_AVAILABLE:
        if Config.DEBUG_MODE:
            print("[Calendar] google-auth/api-client packages not installed.")
        return None

    creds_path = _creds_path()
    if not creds_path or not os.path.exists(creds_path):
        # Gracefully "not configured" — credentials.json is a PRE-REQUISITE
        # the user generates themselves via Google Cloud Console.
        return None

    token_path = _token_path()

    try:
        creds = None
        if token_path and os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, _SCOPES)
            except Exception as e:
                if Config.DEBUG_MODE:
                    print(f"[Calendar] Couldn't load token.json, will re-auth: {e}")
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"[Calendar] Token refresh failed, re-running consent flow: {e}")
                    creds = None

            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, _SCOPES)
                # Opens the user's default browser for one-time consent.
                creds = flow.run_local_server(port=0)

            if token_path:
                try:
                    with open(token_path, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())
                except Exception as e:
                    print(f"[Calendar] Failed to save token.json: {e}")

        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"[Error] get_calendar_service failed: {e}")
        return None


def _event_to_dict(event: Dict[str, Any]) -> Dict[str, Any]:
    start = event.get("start", {}) or {}
    end = event.get("end", {}) or {}
    return {
        "summary": event.get("summary") or "(No title)",
        "start": start.get("dateTime") or start.get("date") or "",
        "end": end.get("dateTime") or end.get("date") or "",
        "location": event.get("location") or "",
    }


def _list_events(time_min: datetime, time_max: datetime) -> List[Dict[str, Any]]:
    """Shared list-events helper behind get_today_events()/get_upcoming_events()."""
    try:
        service = get_calendar_service()
        if service is None:
            return []
        events_result = (
            service.events()
            .list(
                calendarId=_CALENDAR_ID,
                timeMin=time_min.astimezone().isoformat(),
                timeMax=time_max.astimezone().isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return [_event_to_dict(e) for e in events_result.get("items", [])]
    except Exception as e:
        print(f"[Error] Calendar event listing failed: {e}")
        return []


def get_today_events() -> List[Dict[str, Any]]:
    """
    Returns today's events:
    [{"summary": str, "start": ISO-str, "end": ISO-str, "location": str}, ...]
    Empty list if calendar isn't connected or the call fails for any reason.
    """
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    return _list_events(start_of_day, end_of_day)


def get_upcoming_events(days: int = 7) -> List[Dict[str, Any]]:
    """
    Returns events over the next `days` days, same shape as
    get_today_events(). Used by the Proactive Engine's upcoming_meeting
    trigger to check what's coming up soon.
    """
    try:
        days = max(1, min(30, int(days)))
    except (TypeError, ValueError):
        days = 7
    now = datetime.now()
    return _list_events(now, now + timedelta(days=days))


def create_event(
    summary: str, start_dt: datetime, end_dt: datetime, description: str = ""
) -> Dict[str, Any]:
    """
    Creates a new Google Calendar event.

    Returns {"ok": bool, "message": str, "event_id": str|None} — ok=False
    with a clear, spoken-friendly message on ANY failure (no auth, API
    error, invalid time). Never raises.
    """
    if not summary or not summary.strip():
        return {"ok": False, "message": "Please tell me what to call the event.", "event_id": None}

    if not isinstance(start_dt, datetime) or not isinstance(end_dt, datetime):
        return {"ok": False, "message": "I didn't get a valid time for that event.", "event_id": None}

    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    try:
        service = get_calendar_service()
        if service is None:
            return {
                "ok": False,
                "message": "Calendar isn't connected yet. Add credentials.json to set it up.",
                "event_id": None,
            }

        body = {
            "summary": summary.strip(),
            "description": description or "",
            "start": {"dateTime": start_dt.astimezone().isoformat()},
            "end": {"dateTime": end_dt.astimezone().isoformat()},
        }
        created = service.events().insert(calendarId=_CALENDAR_ID, body=body).execute()
        friendly_time = start_dt.strftime("%I:%M %p on %B %d")
        return {
            "ok": True,
            "message": f"Done. I've scheduled '{summary.strip()}' at {friendly_time}.",
            "event_id": created.get("id"),
        }
    except Exception as e:
        print(f"[Error] create_event failed: {e}")
        return {"ok": False, "message": "Sorry, I couldn't create that event.", "event_id": None}


def get_calendar_status() -> Dict[str, Any]:
    """
    {"connected": bool, "email": str|None} — for the Settings page, to
    show whether Google Calendar is actually connected right now.
    """
    try:
        service = get_calendar_service()
        if service is None:
            return {"connected": False, "email": None}
        calendar_info = service.calendars().get(calendarId=_CALENDAR_ID).execute()
        return {"connected": True, "email": calendar_info.get("id")}
    except Exception as e:
        print(f"[Error] get_calendar_status failed: {e}")
        return {"connected": False, "email": None}