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

    # DESIGN NOTE (perceived-latency fix): window ab TURANT ban jaata
    # hai; heavy init (build_core_objects -> Whisper/Kokoro/DB, watcher,
    # emergency-stop hotkey, SaraLogic thread) ek background thread
    # (_boot_heavy) par chala gaya hai. create_window(js_api=...) ko
    # turant ek object chahiye -- _ApiProxy dete hain jiske method names
    # dir(Api) se copy kiye hain, taaki JS bridge shape shuru se hi
    # Api jaisi ho. webview.start() ab bhi MAIN thread par hi chalta
    # hai (Windows requirement) -- sirf heavy init hata hai.
    _boot = {"api": None, "db": None, "logic_thread": None, "error": None}
    _boot_ready = threading.Event()

    class _ApiProxy:
        """Boot ke dauraan real Api ki jagah. Method names Api class se
        hi liye hain (__dir__ override) taaki pywebview ka introspection
        sahi bridge banaye. Har call real Api ready hone tak bounded-wait
        karti hai; timeout ya boot-failure par crash/hang ki jagah ek
        safe {"ok": False, ...} deti hai."""
        _WAIT_TIMEOUT = 20.0

        def __dir__(self):
            return [n for n in dir(Api) if not n.startswith("_")]

        def __getattr__(self, name):
            if name.startswith("_") or not callable(getattr(Api, name, None)):
                raise AttributeError(name)

            def _proxied(*args, **kwargs):
                if not _boot_ready.wait(timeout=self._WAIT_TIMEOUT):
                    return {"ok": False, "reason": "booting"}
                if _boot["error"] is not None:
                    return {"ok": False, "reason": "boot_failed"}
                return getattr(_boot["api"], name)(*args, **kwargs)

            return _proxied

    def _boot_heavy():
        try:
            # build_core_objects() ab CoreObjects (NamedTuple) return karta
            # hai -- positional unpacking ki jagah attribute access, taaki
            # field order badalne/badhne par silent mismatch kabhi na ho.
            co = sara_main.build_core_objects(_push)
            brain = co.brain
            tts = co.tts
            ears = co.ears
            db = co.db
            vision = co.vision
            reminders = co.reminders
            db_writer = co.db_writer
            lang_state = co.lang_state
            assistant_state = co.assistant_state
            notes_memory = co.notes_memory

            # FIX 4 -- Startup sound: one-time Windows system "ding" on boot
            # if "setting:startup_sound" is on (default off). Runs on a
            # background daemon thread so it never delays boot, wrapped in
            # try/except so a failure here can never block/crash boot.
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
            notifications.init_watcher(tts, _push, db)

            # CROSS-MODALITY FIX: wahi 4 shared dicts jo neeche run_sara_logic
            # thread ko bhi jaate hain -- keyword se pass kiye hain taaki
            # arg-order par depend na karna pade.
            api = Api(
                brain, tts, ears, db, vision, reminders, lang_state, assistant_state,
                confirm_state=co.confirm_state,
                volume_state=co.volume_state,
                playback_state=co.playback_state,
                context_state=co.context_state,
                notes_memory=co.notes_memory,
            )

            # NEW: global emergency-stop hotkey (sara/orchestrator/emergency_stop.py).
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
                kwargs={
                    # Api(...) ko upar diye gaye EXACT same dict objects.
                    "confirm_state": co.confirm_state,
                    "volume_state": co.volume_state,
                    "playback_state": co.playback_state,
                    "context_state": co.context_state,
                },
                daemon=True,
                name="SaraLogic",
            )
            logic_thread.start()

            _boot["api"] = api
            _boot["db"] = db
            _boot["logic_thread"] = logic_thread

            # Ab hi Python-side API genuinely ready hai (pehle ye push
            # create_window ke turant baad, models load hone se pehle
            # hota tha -- misleading tha). Buffered rehta hai jab tak
            # page load nahi ho jaata.
            _push('backend_ready')
        except Exception as e:
            print(f"[boot heavy error] {e}")
            _boot["error"] = e
        finally:
            # Proxy calls ko hamesha unblock karo -- boot fail ho jaye
            # tab bhi, warna woh sab _WAIT_TIMEOUT tak latke rahenge.
            _boot_ready.set()

    events._window = webview.create_window(
        "SARA AI",
        HTML_PATH,
        js_api=_ApiProxy(),
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

    boot_thread = threading.Thread(
        target=_boot_heavy, daemon=True, name="SaraBootHeavy",
    )
    boot_thread.start()

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

    # Boot abhi complete nahi hua tha (ya fail ho gaya) to logic_thread
    # kabhi bana hi nahi -- None-check karke safely skip.
    logic_thread = _boot["logic_thread"]
    if logic_thread is not None:
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
    # before the window was closed) before the process exits. `api` tab
    # tak nahi bana agar boot complete nahi hua/fail hua -- None-check.
    api = _boot["api"]
    if api is not None:
        api._pref_writer.stop(timeout=3.0)

    # BUGFIX: db was never closed on shutdown, so the WAL file could stay
    # unmerged/unflushed across an abrupt exit — this is what caused
    # user_name (and other preferences) to silently fail to persist
    # across restarts. Close it explicitly now (None-checked, same reason).
    db = _boot["db"]
    if db is not None:
        try:
            db.close()
        except Exception as e:
            print(f"[shutdown] db.close() failed: {e}")