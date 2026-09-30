"""
sara.gui.app.ui_prefs
ApiUiPrefsMixin -- backend-owned UI preferences (colour theme, accent hue, clock-card theme).

The DB is the single source of truth. The frontend (js/prefs.js) keeps a localStorage copy only so the
very first paint is right before the backend answers.

Stored as preferences "ui:theme", "ui:accent_hue", "ui:clock_theme" through the shared pref writer
(so writes stay ordered and off the bridge thread). "ui:" keys are skipped by the decision log
(helpers._PrefWriter) -- a colour change is not a "why did you change that" event.
No relative imports on purpose: this file can be imported and tested on its own.
"""

_THEMES = ("sara", "ember", "violet", "slate", "rose", "paper")
_CLOCK_THEMES = ("sara", "aurora", "midnight", "glass", "pulse")
_KEYS = ("theme", "accent_hue", "clock_theme")


def normalize_ui_pref(key, value):
    """Return the cleaned string to store, or None if (key, value) is invalid.
    '' is always allowed and means "back to default"."""
    if key not in _KEYS:
        return None
    text = "" if value is None else str(value).strip()
    if text == "":
        return ""
    if key == "theme":
        return text if text in _THEMES else None
    if key == "clock_theme":
        return text if text in _CLOCK_THEMES else None
    # accent_hue: whole degrees 0..360
    try:
        deg = int(round(float(text)))
    except (TypeError, ValueError):
        return None
    if deg < 0 or deg > 360:
        return None
    return str(deg)


class ApiUiPrefsMixin:

    def get_ui_prefs(self):
        try:
            data = {}
            for key in _KEYS:
                value = self.db.get_preference(f"ui:{key}")
                data[key] = "" if value is None else str(value)
            return {"ok": True, "data": data}
        except Exception as e:
            print(f"[get_ui_prefs error] {e}")
            return {"ok": False, "data": {}}

    def set_ui_pref(self, key, value):
        try:
            cleaned = normalize_ui_pref(key, value)
            if cleaned is None:
                return {"ok": False, "error": "Invalid UI preference."}
            self._pref_writer.enqueue(f"ui:{key}", cleaned)
            return {"ok": True}
        except Exception as e:
            print(f"[set_ui_pref error] {e}")
            return {"ok": False, "error": "Could not save."}
