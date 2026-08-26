"""
sara.gui.app.bootstrap
Window creation and application entry point (main()).
"""
from . import events
from .events import _push
from .engine import Api

import os
import threading
import webview

from sara.orchestrator import notifications, emergency_stop

# NOTE: this file lives at sara/gui/app/bootstrap.py — one directory deeper
# than the original sara/gui/app.py. index.html itself did NOT move (it's
# still at sara/gui/index.html), so BASE_DIR must go up one extra level to
# find it, unlike the original single-file version.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(BASE_DIR, "index.html")


def main():
    # Lazy import — avoids a circular dependency (main.py imports this
    # module's main() as its GUI entry point, so this module cannot import
    # main.py at module load time; it's only safe once main() actually runs).
    import main as sara_main

    if not os.path.exists(HTML_PATH):
        raise FileNotFoundError(
            f"index.html not found at: {HTML_PATH}\n"
            f"Place index.html inside sara/gui/ folder."
        )

    brain, tts, ears, db, vision, reminders, db_writer, lang_state, assistant_state, notes_memory = (
        sara_main.build_core_objects(_push)
    )

    # FIX 4 -- Startup sound: one-time Windows system "ding" on boot if
    # "setting:startup_sound" is on (default off, so existing silence
    # is preserved unless the user opts in). Runs on a background
    # daemon thread so it never delays app startup, and is wrapped in
    # try/except so any failure here can never block or crash boot.
    try:
        if db.get_preference("setting:startup_sound", "0") == "1":
            import winsound
            threading.Thread(
                target=lambda: winsound.MessageBeep(),
                daemon=True,
            ).start()
    except Exception as e:
        print(f"[startup sound error] {e}")

    # NEW: file-notification watcher (sara/orchestrator/notifications.py).
    # Constructed and started right here, right after `tts` exists, same
    # "as soon as its dependencies are ready" spirit as everything else
    # in build_core_objects() above. Config-gated internally
    # (NOTIFICATIONS_ENABLED, checked fresh every tick) -- starting the
    # thread unconditionally here is safe even if the feature is
    # disabled, since the thread's own tick() immediately no-ops when
    # the gate is off.
    notifications.init_watcher(tts, _push, db)

    api = Api(brain, tts, ears, db, vision, reminders, lang_state, assistant_state)

    # NEW: global emergency-stop hotkey (sara/orchestrator/emergency_stop.py).
    # Registered right here, right after `api` exists -- api.stop_sara()
    # is what the hotkey callback needs, and this is the first point in
    # main() where it's available. Config-gated internally
    # (EMERGENCY_STOP_ENABLED) and idempotent (safe even if main() were
    # ever re-entered in this same process).
    emergency_stop.register_emergency_stop(api)

    logic_thread = threading.Thread(
        target=sara_main.run_sara_logic,
        args=(
            _push,
            events._stop_event,
            brain,
            tts,
            ears,
            db,
            vision,
            reminders,
            db_writer,
            notes_memory,
            events._manual_wake_event,
            lang_state,
            assistant_state,
        ),
        daemon=True,
        name="SaraLogic",
    )
    logic_thread.start()

    events._window = webview.create_window(
        "SARA AI",
        HTML_PATH,
        js_api=api,
        width=1280,
        height=800,
        min_size=(1000, 640),
        background_color="#070912",
    )
    # Any _push() call made before this fires (boot greeting, ollama
    # warm-up footer text, early wake-word status) is buffered and
    # flushed here instead of being silently dropped — see the
    # STARTUP-RACE FIX note in events.py.
    events._window.events.loaded += events._on_window_loaded  # type: ignore
    # Notify the frontend that the Python-side API will be available so the
    # status bar can refresh itself (some renderers inspect window.pywebview
    # later than the page load). This is buffered until the page loads.
    _push('backend_ready')

    # DEBUG_MODE also turns on pywebview's own debug flag: this enables
    # right-click "Inspect Element" / F12 DevTools on the window (off by
    # default on the EdgeChromium/WebView2 backend) and prints any
    # WebView2/js_api bridge errors to this terminal instead of
    # swallowing them silently — essential for diagnosing cases where
    # window.pywebview.api never binds (see helpers.py hallucination-
    # filter comment / troubleshooting notes for the "preview mode, no
    # backend connected" symptom this surfaces).
    webview.start(
        debug=bool(getattr(sara_main.Config, "DEBUG_MODE", False)),
        gui="edgechromium",
        private_mode=False,
    )
    events._stop_event.set()
    logic_thread.join(timeout=5.0)

    # NEW: clean teardown for the emergency-stop hotkey and the
    # notification watcher -- mirrors the existing pref-writer/db
    # teardown immediately below (each wrapped so one failing cleanup
    # step can never block the rest of shutdown).
    try:
        emergency_stop.unregister_emergency_stop()
    except Exception as e:
        print(f"[shutdown] unregister_emergency_stop failed: {e}")
    try:
        notifications.shutdown_watcher()
    except Exception as e:
        print(f"[shutdown] notifications watcher shutdown failed: {e}")

    # Flush any pending preference writes (e.g. a slider dragged right
    # before the window was closed) before the process exits.
    api._pref_writer.stop(timeout=3.0)
    # BUGFIX: db was never closed on shutdown, so the WAL file could stay
    # unmerged/unflushed across an abrupt exit — this is what caused
    # user_name (and other preferences) to silently fail to persist
    # across restarts. Close it explicitly now.
    try:
        db.close()
    except Exception as e:
        print(f"[shutdown] db.close() failed: {e}")