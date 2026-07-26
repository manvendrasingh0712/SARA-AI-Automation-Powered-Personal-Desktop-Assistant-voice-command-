"""
sara.gui.app.notes
ApiNotesMixin -- Quick Notes panel + full conversation/preferences export.
"""
from .helpers import _row_to_export_dict
from .events import _push

import os
import json
import time
import queue
import random
import threading
import urllib.request
import urllib.parse
import webview

class ApiNotesMixin:

    # ── Quick Notes card ───────────────────────────────────────────────
    def save_note(self, text):
        try:
            if self.system_tools:
                result = self.system_tools.take_note(text, return_id=True)
                # take_note() may return a plain confirmation string (older
                # system_tools.py) or a dict with the new note's id (if it's
                # been updated to hand ids back) — support both so this
                # never breaks depending on which version is installed.
                if isinstance(result, dict):
                    return {
                        "ok": True,
                        "message": result.get("message", "Note saved."),
                        "id": result.get("id"),
                    }
                return {"ok": True, "message": result, "id": None}
            return {"ok": False, "message": "System tools missing", "id": None}
        except Exception as e:
            print(f"[save_note error] {e}")
            return {"ok": False, "id": None}

    # ── Quick Notes card: fetch notes back from the backend ────────────
    # Mirrors get_reminders() below. Expected contract: system_tools has a
    # get_notes() method returning a list of {"id","text","timestamp"}
    # dicts. If system_tools doesn't have get_notes() yet, this fails
    # soft (ok:False) instead of raising — the frontend's periodic sync
    # then simply finds nothing new instead of crashing. To wire this up
    # fully, add a get_notes() method to wherever notes are persisted
    # (e.g. sara/tools/system.py) that returns that shape.
    def get_notes(self):
        try:
            if self.system_tools and hasattr(self.system_tools, "get_notes"):
                return {"ok": True, "data": self.system_tools.get_notes()}
            return {"ok": False, "data": []}
        except Exception as e:
            print(f"[get_notes error] {e}")
            return {"ok": False, "data": []}

    # ── Memory page ────────────────────────────────────────────────────
    def export_memory(self):
        try:
            export_path = os.path.join(os.getcwd(), "memory_export.json")

            # Both the DB read (up to 500 rows) and the file write can be
            # slow enough to be felt on the bridge thread, so the whole
            # operation — read + write — happens in the background now.
            # The call still returns the (deterministic) path immediately;
            # failures are pushed to the frontend as an event instead of
            # being lost inside the thread.
            def _export():
                try:
                    rows = self.db.get_recent_messages(limit=500)
                    with open(export_path, "w", encoding="utf-8") as f:
                        json.dump(
                            [_row_to_export_dict(r) for r in rows],
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )
                    _push(
                        "notification",
                        "ti-database",
                        "#34d399",
                        "Memory export completed",
                    )
                except Exception as e:
                    print(f"[export_memory bg error] {e}")
                    _push(
                        "notification",
                        "ti-alert-triangle",
                        "#f87171",
                        "Memory export failed",
                    )

            threading.Thread(target=_export, daemon=True).start()
            return {"ok": True, "path": export_path}
        except Exception as e:
            print(f"[export_memory error] {e}")
            return {"ok": False}
