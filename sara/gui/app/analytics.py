"""
sara.gui.app.analytics
ApiAnalyticsMixin -- Usage Analytics Dashboard page.

Owns a small, self-contained JSON counter file (analytics_usage.json,
written next to the app -- same convention as memory_export.json) for
"most-used commands" and a day-by-day usage trend. This is intentionally
separate from sara/core/memory.py: memory.py is not touched for this
feature -- this mixin tracks its own counts independently and only
*reads* memory.py's existing get_conversation_stats() and
get_proactive_stats() (via self.db) to round out the dashboard.

Commands are recorded by the frontend: every call to send_text_command()
in app.js is mirrored by a call to record_command_usage() here, so this
file needs no changes to sara/core/dispatch.py or any other backend
dispatch code. Trade-off: this only sees commands that flow through
send_text_command (typed chat, quick actions, web search, quick tools).
Commands triggered purely by voice, which never touch that JS path,
are not counted here.
"""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from threading import Lock

# Written next to the app, same convention as memory_export.json
# (see Memory page: "Saves your recent conversation history to
# memory_export.json next to the app"). analytics.py lives at
# sara/gui/app/analytics.py, so parents[3] is the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ANALYTICS_PATH = _PROJECT_ROOT / "analytics_usage.json"

_LOCK = Lock()

_MAX_TRACKED_COMMANDS = 200   # cap distinct command keys so the file can't grow unbounded
_TREND_DAYS = 14              # how many days of history the dashboard shows


def _empty_state():
    return {"total_commands": 0, "commands": {}, "daily": {}}


def _load_state():
    try:
        if _ANALYTICS_PATH.exists():
            with open(_ANALYTICS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "commands" in data and "daily" in data:
                data.setdefault("total_commands", 0)
                return data
    except Exception as e:
        print(f"[analytics] load error, starting fresh: {e}")
    return _empty_state()


def _save_state(state):
    try:
        tmp_path = str(_ANALYTICS_PATH) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(_ANALYTICS_PATH))
        return True
    except Exception as e:
        print(f"[analytics] save error: {e}")
        return False


def _normalize_command(text):
    text = (text or "").strip().lower()
    if not text:
        return "unknown"
    # Collapse obvious free-text variance a little so long free-text
    # commands (e.g. "search the web for X") don't each become their own
    # bucket -- keep only the first few words as the bucket key.
    words = text.split()
    return " ".join(words[:6])[:80]


class ApiAnalyticsMixin:

    # ── recording (called by the frontend on every send_text_command) ──
    def record_command_usage(self, command_text):
        try:
            key = _normalize_command(command_text)
            today = datetime.now().strftime("%Y-%m-%d")
            with _LOCK:
                state = _load_state()
                state["total_commands"] = state.get("total_commands", 0) + 1
                commands = state.setdefault("commands", {})
                commands[key] = commands.get(key, 0) + 1
                if len(commands) > _MAX_TRACKED_COMMANDS:
                    trimmed = dict(
                        sorted(commands.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_TRACKED_COMMANDS]
                    )
                    state["commands"] = trimmed
                daily = state.setdefault("daily", {})
                daily[today] = daily.get(today, 0) + 1
                cutoff = (datetime.now() - timedelta(days=_TREND_DAYS * 3)).strftime("%Y-%m-%d")
                state["daily"] = {d: c for d, c in daily.items() if d >= cutoff}
                _save_state(state)
            return {"ok": True}
        except Exception as e:
            print(f"[record_command_usage error] {e}")
            return {"ok": False}

    # ── dashboard payload (Analytics page) ─────────────────────────────
    def get_analytics_dashboard(self):
        try:
            with _LOCK:
                state = _load_state()

            top_commands = sorted(state.get("commands", {}).items(), key=lambda kv: kv[1], reverse=True)[:8]
            top_commands = [{"name": name, "count": count} for name, count in top_commands]

            daily = state.get("daily", {})
            trend = []
            for i in range(_TREND_DAYS - 1, -1, -1):
                d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                trend.append({"date": d, "count": daily.get(d, 0)})

            conversation_stats = {}
            proactive_stats = {}
            try:
                if hasattr(self, "db") and hasattr(self.db, "get_conversation_stats"):
                    conversation_stats = self.db.get_conversation_stats() or {}
            except Exception as e:
                print(f"[get_analytics_dashboard conversation_stats error] {e}")
            try:
                if hasattr(self, "db") and hasattr(self.db, "get_proactive_stats"):
                    proactive_stats = self.db.get_proactive_stats() or {}
            except Exception as e:
                print(f"[get_analytics_dashboard proactive_stats error] {e}")

            return {
                "ok": True,
                "data": {
                    "total_commands": state.get("total_commands", 0),
                    "top_commands": top_commands,
                    "daily_trend": trend,
                    "conversation_stats": conversation_stats,
                    "proactive_stats": proactive_stats,
                }
            }
        except Exception as e:
            print(f"[get_analytics_dashboard error] {e}")
            return {"ok": False, "data": {
                "total_commands": 0, "top_commands": [], "daily_trend": [],
                "conversation_stats": {}, "proactive_stats": {}
            }}