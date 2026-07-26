"""
sara.gui.app.engine
Api -- the pywebview JS-bridge object. Combines all the ApiXMixin classes
into the single object exposed to the frontend, exactly as before.
"""
from .core import ApiCoreMixin
from .reminders import ApiRemindersMixin
from .settings import ApiSettingsMixin
from .notes import ApiNotesMixin
from .media import ApiMediaMixin

import types

_MIXINS = (
    ApiCoreMixin,
    ApiRemindersMixin,
    ApiSettingsMixin,
    ApiNotesMixin,
    ApiMediaMixin,
)


class Api(*_MIXINS):
    """Combined JS-bridge API object -- identical public surface to the
    original monolithic Api class in sara/gui/app.py."""
    pass


# BUGFIX (root cause of "preview mode, no backend connected" persisting on
# machines where WebView2 isn't available): pywebview's EdgeChromium
# renderer exposes js_api methods via a full dir()/MRO walk, so plain
# multiple inheritance (Api(*_MIXINS): pass) worked fine there. But when
# WebView2 is missing, pywebview silently falls back to its WinForms/CEF
# renderer (confirmed via "[pywebview] Using WinForms / Chromium" in the
# console) -- and that renderer's js_api introspection only picks up
# callables that live directly in the exposed class's own __dict__, not
# ones only present via inheritance from a mixin. Since every real method
# here (send_text_command, get_system_stats, get_media_status, ...) was
# defined purely on the ApiXMixin classes and Api itself was just `pass`,
# NONE of them were ever visible to that renderer -- every single call
# silently fell through to app.js's mock fallback, which is exactly what
# produced the "(preview mode, no backend connected)" replies. Copying
# each mixin's public callables directly onto Api.__dict__ here makes
# them visible to both renderers, regardless of which one Windows picks.
for _mixin in _MIXINS:
    for _name, _member in vars(_mixin).items():
        if _name.startswith("_"):
            continue
        if callable(_member) and _name not in Api.__dict__:
            setattr(Api, _name, _member)
del _mixin, _name, _member
