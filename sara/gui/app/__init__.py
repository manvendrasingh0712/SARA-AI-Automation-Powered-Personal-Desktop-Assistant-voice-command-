"""
sara.gui.app
Public package API for the pywebview GUI layer. External code keeps using
this exactly as before:

    from sara.gui.app import main as webview_main

Internal layout (the original 1354-line Api class is now composed from
focused mixins instead of being one file):
    events.py     - shared window-lifecycle state + Python->JS push bridge
    helpers.py    - standalone helpers (export shaping, weather fallback, pref writer)
    core.py       - ApiCoreMixin: __init__, system stats, weather, window, wake/stop
    reminders.py  - ApiRemindersMixin: Calendar/Reminders CRUD
    settings.py   - ApiSettingsMixin: mute/focus/settings/mic/speed/wifi/language
    notes.py      - ApiNotesMixin: Quick Notes + memory export
    media.py      - ApiMediaMixin: media player status/controls
    engine.py     - Api, composed from all the mixins above
    bootstrap.py  - main(): window creation + application entry point
"""
from .bootstrap import main
from .engine import Api

__all__ = ["main", "Api"]
