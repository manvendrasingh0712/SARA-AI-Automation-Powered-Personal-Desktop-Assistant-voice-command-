"""
sara.orchestrator.notifications
File Notification Watcher -- watches a single folder for a file
"finishing" (a new file appearing and its size settling, or a
browser-style temp file like .crdownload/.part disappearing) and
announces it once via the existing TTS worker.

Lifecycle shape mirrors sara/orchestrator/proactive.py's ProactiveEngine:
construct once (via init_watcher()), start() once, shutdown() during app
teardown. A background daemon thread polls at a config-gated interval
(NOTIFICATIONS_CHECK_INTERVAL_S) -- same `while not
self._stop_event.wait(timeout=interval):` shape as proactive.py's
_poll_loop() -- checking the NOTIFICATIONS_ENABLED gate FRESH every tick
(same defaulting convention as proactive.py: an explicit gate-off value
disables checking; the gate does not need to be read at thread-start
time only).

SINGLETON WIRING (why this file, not core_wiring.py, owns construction)
-------------------------------------------------------------------------
This project's build_core_objects()/run_sara_logic() call chain
(sara/orchestrator/core_wiring.py) was explicitly marked out-of-scope
for this change. To wire this watcher in without touching that file (or
_handle_command()'s parameter list), this module exposes a small
process-wide singleton instead: sara/gui/app/bootstrap.py calls
init_watcher(tts, ui_update) once, right after tts is built, and the
notify_on_file intent handler (sara/orchestrator/intent_handlers.py)
retrieves the same instance via get_watcher(). This is a deliberate
deviation from ProactiveEngine's explicit-construction style, made ONLY
because of the "don't touch other files" constraint -- flagged here, not
hidden.

DETECTION APPROACH -- READ BEFORE CHANGING
---------------------------------------------
Only ONE watch is active at a time (v1 limitation, as specced). Calling
watch_for_next_file() again while a watch is already running SILENTLY
REPLACES the previous target -- it never crashes and never silently
no-ops.

Completion is detected via a plain os.listdir() + os.path.getsize()
poll every tick -- NOT via the `watchdog` library's own event-driven
Observer thread, even though `watchdog` was added to requirements.txt
as the proposed filesystem-watching dependency. This was a deliberate
trade-off: matching sara/orchestrator/proactive.py's single
poll-loop-with-stop-event thread shape exactly ("do not invent a
different threading approach") would be broken by also running
watchdog's Observer, which manages its own separate internal thread.
Flagging this explicitly rather than silently picking one: if you'd
rather this use watchdog's Observer directly (accepting a second,
differently-shaped background thread), say so and this file gets
rewritten around it instead.

Two completion signals are checked every tick, in this order:
  1. Temp-file rename/disappearance -- a newly-seen file ending in one
     of _TEMP_FILE_SUFFIXES (.crdownload, .part, .tmp, .download) is
     tracked as "in progress" under its suffix-stripped base name. The
     first tick where that temp file no longer exists is treated as
     completion (using the real final filename if one now exists under
     the tracked base name, otherwise reporting the temp name itself
     with its suffix stripped -- some browsers rename to a completely
     different final name that can't be predicted from here).
  2. Stable-size fallback -- for a newly-seen file that never went
     through a temp-suffix stage (a direct save, a plain file copy/drop
     into the folder), its size is compared tick-to-tick; once it has
     been seen unchanged (and > 0 bytes) across two consecutive ticks,
     it's treated as complete.

EDGE CASES (flagged, not silently ignored)
---------------------------------------------
  - Antivirus real-time scanning can briefly hold a lock on a
    just-completed file; the two-tick stable-size confirmation reduces
    false positives from this but cannot eliminate the race entirely on
    a slow-scanning AV.
  - A OneDrive-synced Downloads folder can report a file at its final
    local size immediately while OneDrive itself continues
    uploading/placeholder-hydrating in the background -- this module has
    no visibility into that and announces based on local size stability
    only.
  - A very large file written in bursts could, in theory, have two poll
    ticks land during a real mid-write pause. NOTIFICATIONS_CHECK_INTERVAL_S
    defaults to 2 seconds specifically to make this unlikely, not
    impossible.
"""

import os
import threading
from typing import Callable, Optional

from config import Config

_DEBUG = getattr(Config, "DEBUG_MODE", False)

_TEMP_FILE_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")

# Number of consecutive ticks a newly-seen file's size must stay
# unchanged (and > 0) before the stable-size fallback treats it as
# complete. 2 means "unchanged across one full poll interval after
# first being seen", trading a small amount of extra latency for fewer
# false-positive "done" announcements on a still-growing file.
_STABLE_TICKS_REQUIRED = 2


def get_downloads_folder() -> str:
    """
    Resolves the CURRENT Windows user's real Downloads folder via the
    Known Folder API (FOLDERID_Downloads) -- NOT a hardcoded
    "C:\\Users\\<name>\\Downloads" guess. This is correct even when
    Downloads has been moved/redirected (a common OneDrive setup) or the
    OS language/username would make a guessed path wrong.

    stdlib-only (ctypes) -- no new pip dependency for this piece. Falls
    back to os.path.expanduser("~/Downloads") if the Known Folder call
    fails for any reason (e.g. running under a non-Windows OS during
    development), matching this codebase's "never crash, degrade
    gracefully" convention.
    """
    try:
        import ctypes
        from ctypes import windll

        FOLDERID_Downloads = "{374DE290-123F-4565-9164-39C4925E467B}"

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = GUID()
        windll.ole32.CLSIDFromString(FOLDERID_Downloads, ctypes.byref(guid))

        path_ptr = ctypes.c_wchar_p()
        result = windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, 0, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            path = path_ptr.value
            windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception as e:  # noqa: BLE001 -- must never crash startup
        print(f"[Notifications] SHGetKnownFolderPath failed, falling back: {e}")
    return os.path.expanduser(os.path.join("~", "Downloads"))


class NotificationWatcher:
    """
    Background file-completion watcher. Same lifecycle shape as
    sara/orchestrator/proactive.py's ProactiveEngine: construct once,
    start() once, shutdown() during app teardown.
    """

    def __init__(self, tts, ui_update: Callable[..., None]) -> None:
        self._tts = tts
        self._ui_update = ui_update

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Guards every field below -- watch_for_next_file()/cancel() can
        # be called from the main voice-loop thread (via the
        # notify_on_file intent handler, or the emergency-stop hotkey's
        # own callback thread) while the background poll thread reads
        # the same state.
        self._lock = threading.Lock()
        self._folder: Optional[str] = None
        self._temp_candidates: dict = {}   # base name (no suffix) -> temp filename
        self._size_candidates: dict = {}   # filename -> (last_size, stable_ticks)
        self._known_files: set = set()     # files already seen when the watch armed
        self._active = False

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="sara-notifications"
        )
        self._thread.start()
        if _DEBUG:
            print("[Notifications] Background watcher thread started.")

    def stop(self) -> None:
        self._stop_event.set()

    def shutdown(self) -> None:
        self.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------
    # Public control -- called by the notify_on_file intent handler and
    # by the emergency-stop hotkey
    # ------------------------------------------------------------

    def watch_for_next_file(self, folder: Optional[str] = None) -> str:
        """
        Arms (or re-arms) a single watch on `folder` (defaults to the
        real Downloads folder via get_downloads_folder()). Only one
        watch is active at a time: calling this while a watch is already
        in progress SILENTLY REPLACES the previous target -- it never
        crashes and never silently no-ops. Returns the spoken
        confirmation string.
        """
        target_folder = folder or get_downloads_folder()
        try:
            existing = set(os.listdir(target_folder))
        except Exception as e:
            print(f"[Notifications] Could not list '{target_folder}': {e}")
            return f"Sorry, I can't access the folder '{target_folder}'."

        with self._lock:
            was_active = self._active
            self._folder = target_folder
            self._known_files = existing
            self._temp_candidates = {}
            self._size_candidates = {}
            self._active = True

        if _DEBUG:
            print(f"[Notifications] Watching '{target_folder}' for the next completed file.")

        if was_active:
            return (
                f"Okay, switching to watch {target_folder} instead -- "
                f"I'll let you know when the next file finishes there."
            )
        return f"Okay, I'll let you know when a file finishes in {target_folder}."

    def cancel(self) -> None:
        """
        Cancels the active watch, if any -- a no-op if nothing is being
        watched. Used by the emergency-stop hotkey to clear a pending
        watch as part of "cancel any pending/queued actions".
        """
        with self._lock:
            self._active = False
            self._folder = None
            self._temp_candidates = {}
            self._size_candidates = {}
            self._known_files = set()

    # ------------------------------------------------------------
    # Main loop -- same `while not self._stop_event.wait(timeout=interval)`
    # shape as sara/orchestrator/proactive.py's ProactiveEngine._poll_loop()
    # ------------------------------------------------------------

    def _poll_loop(self) -> None:
        interval = max(1, int(getattr(Config, "NOTIFICATIONS_CHECK_INTERVAL_S", 2)))
        while not self._stop_event.wait(timeout=interval):
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 -- a bad tick must never kill the thread
                print(f"[Notifications] tick failed: {e}")

    def _tick(self) -> None:
        # Gate checked FRESH every tick (same convention as
        # proactive.py's _enabled_now()) -- toggling NOTIFICATIONS_ENABLED
        # off mid-watch simply pauses checking without losing the armed
        # watch state; turning it back on picks up right where it left off.
        if not getattr(Config, "NOTIFICATIONS_ENABLED", True):
            return

        with self._lock:
            if not self._active or not self._folder:
                return
            folder = self._folder

        try:
            current_files = set(os.listdir(folder))
        except Exception as e:
            if _DEBUG:
                print(f"[Notifications] listdir failed for '{folder}': {e}")
            return

        completed_name = None

        with self._lock:
            # A concurrent cancel()/watch_for_next_file() call could have
            # changed things while listdir() above was running -- re-check
            # under the lock before touching any shared state.
            if not self._active or self._folder != folder:
                return

            new_files = current_files - self._known_files

            for name in new_files:
                full_path = os.path.join(folder, name)
                lowered = name.lower()
                if lowered.endswith(_TEMP_FILE_SUFFIXES):
                    base = name
                    for suffix in _TEMP_FILE_SUFFIXES:
                        if lowered.endswith(suffix):
                            base = name[: -len(suffix)]
                            break
                    self._temp_candidates[base] = name
                elif name not in self._size_candidates:
                    try:
                        size = os.path.getsize(full_path)
                    except OSError:
                        size = -1
                    self._size_candidates[name] = (size, 0)

            self._known_files |= new_files

            # Signal 1: a tracked temp file has disappeared.
            for base, temp_name in list(self._temp_candidates.items()):
                temp_path = os.path.join(folder, temp_name)
                if not os.path.exists(temp_path):
                    real_candidate = os.path.join(folder, base)
                    completed_name = base if os.path.exists(real_candidate) else temp_name
                    del self._temp_candidates[base]
                    break

            # Signal 2: stable-size fallback for files with no temp stage.
            if completed_name is None:
                for name, (last_size, stable_ticks) in list(self._size_candidates.items()):
                    full_path = os.path.join(folder, name)
                    try:
                        current_size = os.path.getsize(full_path)
                    except OSError:
                        # Vanished between ticks (e.g. renamed elsewhere) --
                        # nothing more to track for it.
                        del self._size_candidates[name]
                        continue
                    if current_size == last_size and current_size > 0:
                        stable_ticks += 1
                        if stable_ticks >= _STABLE_TICKS_REQUIRED:
                            completed_name = name
                            del self._size_candidates[name]
                            break
                        self._size_candidates[name] = (current_size, stable_ticks)
                    else:
                        self._size_candidates[name] = (current_size, 0)

            if completed_name is not None:
                self._active = False
                self._folder = None
                self._temp_candidates = {}
                self._size_candidates = {}
                self._known_files = set()

        if completed_name is not None:
            self._announce(completed_name, folder)

    # ------------------------------------------------------------
    # Announce -- via the existing TTS worker, same call pattern
    # sara/orchestrator/proactive.py's _speak_and_notify() uses
    # (self._tts.speak(text, fast=True)) -- never calls the underlying
    # TextToSpeech engine directly.
    # ------------------------------------------------------------

    def _announce(self, filename: str, folder: str) -> None:
        text = f"Your download finished -- {filename} is ready in {folder}."
        try:
            self._tts.speak(text, fast=True)
        except Exception as e:
            print(f"[Notifications] tts.speak failed: {e}")
        try:
            self._ui_update("transcript", "sara", text)
            self._ui_update(
                "proactive_notification", "ti-download", "#34d399", text, "file_notification"
            )
        except Exception as e:
            print(f"[Notifications] ui_update failed: {e}")


# ----------------------------------------------------------------------------
# Process-wide singleton -- see module docstring's "SINGLETON WIRING" note
# ----------------------------------------------------------------------------

_watcher_instance: Optional[NotificationWatcher] = None
_watcher_lock = threading.Lock()


def init_watcher(tts, ui_update: Callable[..., None]) -> NotificationWatcher:
    """
    Creates (if not already created) and starts the process-wide
    NotificationWatcher singleton. Called once from
    sara/gui/app/bootstrap.py, right after `tts` is built. Idempotent --
    calling this more than once just returns the existing instance
    rather than starting a second thread.
    """
    global _watcher_instance
    with _watcher_lock:
        if _watcher_instance is None:
            _watcher_instance = NotificationWatcher(tts, ui_update)
            _watcher_instance.start()
        return _watcher_instance


def get_watcher(
    tts=None, ui_update: Optional[Callable[..., None]] = None
) -> Optional[NotificationWatcher]:
    """
    Returns the process-wide NotificationWatcher singleton. If it was
    never initialized (init_watcher() never ran -- shouldn't normally
    happen post-boot, but defensive) AND both tts and ui_update are
    given, it is lazily created here instead. Callers that only need to
    read/cancel an existing watch (e.g. the emergency-stop hotkey) can
    call this with no arguments and safely get back None if nothing was
    ever started.
    """
    global _watcher_instance
    with _watcher_lock:
        if _watcher_instance is None and tts is not None and ui_update is not None:
            _watcher_instance = NotificationWatcher(tts, ui_update)
            _watcher_instance.start()
        return _watcher_instance


def shutdown_watcher() -> None:
    """Shuts down the singleton, if one was ever created. Called from
    sara/gui/app/bootstrap.py during app teardown."""
    global _watcher_instance
    with _watcher_lock:
        if _watcher_instance is not None:
            _watcher_instance.shutdown()