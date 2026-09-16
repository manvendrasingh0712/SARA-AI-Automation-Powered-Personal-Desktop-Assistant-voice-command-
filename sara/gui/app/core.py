"""
sara.gui.app.core
ApiCoreMixin -- construction (__init__) plus system stats, weather, window
controls, wake/stop, and the main send_text_command() dispatch path.
"""
from . import events
from .events import _push
from .helpers import (
    _row_to_export_dict, _fetch_weather_from_api, _PrefWriter,
    WEATHER_CACHE_SECONDS, _weather_cache, _weather_lock,
)

import time
import queue
import random
import threading
import webview
from datetime import datetime

from config import Config

# ── CONCURRENCY FIX (v14) ──────────────────────────────────────────────
# One process-wide reentrant lock protecting the cross-modality state dicts
# (confirm_state / volume_state / playback_state / context_state) that
# build_core_objects() hands to BOTH this Api instance and run_sara_logic().
#
# Those dicts are plain dicts with no synchronisation of their own, and
# _handle_command() does read-modify-write sequences on them (e.g. reading
# confirm_state["pending"], deciding, then clearing it). Two commands running
# concurrently -- two fast typed commands, or one typed + one voice turn --
# could interleave mid-sequence and make a "yes" confirm the wrong pending
# action, or clear a confirm that had just been set.
#
# It lives at module scope (not on the instance) so the voice loop can take
# the SAME lock:
#     from sara.gui.app.core import STATE_LOCK
# See the note at the bottom of send_text_command() -- until run_sara_logic()
# also takes it, this only serialises the typed path against itself.
#
# RLock, not Lock: _handle_command() may re-enter Api methods on the same
# thread (e.g. the session_control "sleep" path calling stop_sara()).
STATE_LOCK = threading.RLock()

# How many typed commands may sit waiting while one is being processed.
# Small on purpose: this is a human typing, not a job queue. Beyond this the
# call is rejected with a "busy" reply instead of spawning more work.
_MAX_QUEUED_COMMANDS = 3

# Identical text submitted twice inside this window (double-click on Send,
# Enter key repeat, a bouncing JS handler) is treated as one command.
_DUPLICATE_WINDOW_SECONDS = 0.75

INDIAN_MUSIC_SEARCHES = [
    "latest Bollywood songs",
    "trending Hindi songs",
    "Top Hindi songs 2026",
    "Arijit Singh latest songs",
    "Pritam hits",
    "A. R. Rahman songs",
    "Shreya Ghoshal hits",
    "Jubin Nautiyal latest",
    "KK evergreen songs",
    "Atif Aslam Hindi songs",
    "Bollywood romantic songs",
    "Bollywood party songs",
    "Punjabi latest songs",
    "AP Dhillon songs",
    "Diljit Dosanjh songs",
    "Karan Aujla songs",
    "Honey Singh latest songs",
    "Indian indie music",
    "Hindi lofi songs",
    "Trending Indian music",
]


class ApiCoreMixin:
    def __init__(
        self,
        brain,
        tts,
        ears,
        db,
        vision,
        reminders,
        lang_state=None,
        assistant_state=None,
        confirm_state=None,
        volume_state=None,
        playback_state=None,
        context_state=None,
    ):
        self.brain = brain
        self.tts = tts
        self.ears = ears
        self.db = db
        self.vision = vision
        self.reminders = reminders
        # LANGUAGE-SYNC FEATURE: shared state object (see gui_main.py's
        # LanguageState) that lets the GUI's EN/HI toggle override the
        # existing per-turn auto-detection used by run_sara_logic, until
        # the user picks auto again.
        self.lang_state = lang_state
        # NEW UI WIRING: shared state object (see gui_main.py's
        # AssistantState) that lets the GUI's Pause/Resume Listening
        # control genuinely pause the wake-word detection loop, not just
        # the UI display.
        self.assistant_state = assistant_state

        # ── LATENCY OPTIMIZATION ──────────────────────────────────────────
        # Caching heavy imports inside __init__ ensures we avoid circular imports
        # at the module level, but we also avoid repeatedly locking sys.modules
        # during high-frequency API calls.
        import main as gui_main
        import psutil

        self.gui_main = gui_main
        self.psutil = psutil

        try:
            from sara.tools import system as system_tools

            self.system_tools = system_tools
        except ImportError:
            self.system_tools = None

        # Disk usage takes a system call (I/O operation) which creates a micro-stutter
        # on the JS bridge if polled every 3.5 seconds. We cache the disk usage and
        # only physically read it every 50 cycles (~3 minutes) to completely kill the lag.
        self._last_disk_usage = None
        self._disk_check_counter = 0
        self._last_disk_total_gb = 0
        self._last_disk_used_gb = 0
        self._last_net_sample = None  # (bytes_recv, bytes_sent, timestamp)

        # Baseline reading for delta-based network speed calculation in
        # get_system_stats() — see the LATENCY NOTE there. net_io_counters()
        # is cumulative since boot, so the very first poll after startup is
        # only used to establish this baseline; the *next* poll is what
        # produces the first real Mbps figure.
        try:
            self._last_net = self.psutil.net_io_counters()
        except Exception:
            self._last_net = None
        self._last_net_time = time.time()

        # Single serialized writer for all preference writes — see _PrefWriter
        # docstring above for why this replaced per-call threading.Thread(...).
        # NEW: also passes get_preference_fn + log_decision_fn so _PrefWriter
        # can log a decision_log entry ("what changed, from what, to what")
        # for every settings/config change made via voice OR the GUI --
        # see sara/gui/app/helpers.py's _PrefWriter docstring and
        # sara/core/memory.py's log_decision() for the full feature.
        # getattr() guards the case where an older/custom db object
        # doesn't have log_decision() yet -- decision logging then simply
        # never fires, same as before this feature existed.
        self._pref_writer = _PrefWriter(
            self.db.set_preference,
            get_preference_fn=self.db.get_preference,
            log_decision_fn=getattr(self.db, "log_decision", None),
        )

        self.music_queries = INDIAN_MUSIC_SEARCHES.copy()
        random.shuffle(self.music_queries)
        self.music_index = 0

        # CROSS-MODALITY FIX: ab ye dicts yahan locally nahi bante --
        # build_core_objects() mein ek baar bante hain aur bootstrap.main()
        # se yahan AUR run_sara_logic() dono ko SAME instance milta hai
        # (lang_state/assistant_state jaisa hi pattern). Isse voice se shuru
        # kiya confirm GUI mein "yes" type karke complete ho jata hai, mute
        # state dono jagah ek rehta hai, aur "what about jaipur?" type
        # follow-ups modality badalne par bhi kaam karte hain.
        #
        # None fallback deliberately hai -- koi purana caller Api ko sirf
        # 8 args ke saath banaye to crash na ho, bas purana isolated
        # behavior wapas mil jaye.
        self.volume_state: dict = volume_state if volume_state is not None else {}
        self.playback_state: dict = playback_state if playback_state is not None else {}
        self.confirm_state: dict = confirm_state if confirm_state is not None else {}
        self.context_state: dict = context_state if context_state is not None else {}

        # ── CONCURRENCY FIX (v14) ─────────────────────────────────────────
        # Guard for the four shared dicts above. Exposed as an attribute so
        # other modules that were handed the same dicts can take the same
        # lock without importing the module-level name.
        self.state_lock = STATE_LOCK

        # Per-session serialized command queue. send_text_command() used to
        # do threading.Thread(target=_worker).start() on EVERY call -- an
        # unbounded number of daemon threads all calling _handle_command()
        # (and thus the LLM, TTS and the shared dicts) at the same time.
        # Now every typed command is put on this bounded queue and drained
        # by exactly ONE long-lived worker thread, so only one command is
        # ever in flight.
        self._cmd_queue: "queue.Queue" = queue.Queue(maxsize=_MAX_QUEUED_COMMANDS)
        self._cmd_thread = None
        self._cmd_thread_lock = threading.Lock()

        # Double-submit suppression state, guarded by _cmd_thread_lock.
        self._last_cmd_text = ""
        self._last_cmd_ts = 0.0

        # See _bind_instance_methods() below for why this is needed on
        # top of engine.py's class-level setattr loop.
        self._bind_instance_methods()

    # BUGFIX: some pywebview renderers (WinForms/.NET reflection bridge,
    # confirmed in this app's own startup log: "[pywebview] Using
    # WinForms / Chromium") only reliably expose methods that live in the
    # *instance* __dict__ at the moment webview.create_window(js_api=...)
    # runs -- not ones that only exist via class-level inheritance or a
    # setattr() done on the class object after the fact. Defined directly
    # here (not monkey-patched from engine.py) so it's guaranteed to exist
    # on every Api instance via ordinary multiple inheritance the moment
    # the class is defined -- no import-order or module-reload dependency
    # that could silently fail to attach it.
    def _bind_instance_methods(self):
        import types
        for _klass in type(self).__mro__:
            if _klass is object:
                continue
            for _name, _member in vars(_klass).items():
                if _name.startswith("_"):
                    continue
                if callable(_member) and _name not in self.__dict__:
                    setattr(self, _name, types.MethodType(_member, self))
        # DIAGNOSTIC: prints exactly what pywebview will see exposed on
        # window.pywebview.api. Safe to leave in permanently -- runs once
        # at startup only.
        exposed = sorted(
            n for n in dir(self)
            if not n.startswith("_") and callable(getattr(self, n, None))
        )
        print(f"[Api] {len(exposed)} methods exposed to frontend: {exposed}")

    def get_system_stats(self):
        # Update disk usage sparingly to prevent UI thread blocking
        if self._last_disk_usage is None or self._disk_check_counter >= 50:
            disk = self.psutil.disk_usage("/")
            self._last_disk_usage = disk.percent
            self._last_disk_total_gb = disk.total / (1024**3)
            self._last_disk_used_gb = disk.used / (1024**3)
            self._disk_check_counter = 0
        self._disk_check_counter += 1

        # Real network speed: delta bytes since last call / delta time.
        now = time.time()
        net = self.psutil.net_io_counters()
        down_mbps, up_mbps = 0.0, 0.0
        if self._last_net_sample is not None:
            prev_bytes_recv, prev_bytes_sent, prev_time = self._last_net_sample
            dt = max(now - prev_time, 0.001)
            down_mbps = ((net.bytes_recv - prev_bytes_recv) * 8 / dt) / 1_000_000
            up_mbps = ((net.bytes_sent - prev_bytes_sent) * 8 / dt) / 1_000_000
        self._last_net_sample = (net.bytes_recv, net.bytes_sent, now)

        return {
            "cpu": self.psutil.cpu_percent(interval=None),
            "ram": self.psutil.virtual_memory().percent,
            "disk": self._last_disk_usage,
            "disk_total_gb": round(getattr(self, "_last_disk_total_gb", 0), 1),
            "disk_used_gb": round(getattr(self, "_last_disk_used_gb", 0), 1),
            "net_down_mbps": round(max(down_mbps, 0), 1),
            "net_up_mbps": round(max(up_mbps, 0), 1),
        }

    # ── AI Memory % (real, DB-backed) ──────────────────────────────────
    # Percentage of the conversation-memory buffer in use, computed from
    # the real SQLite conversation history relative to
    # Config.MAX_MEMORY_EXCHANGES. Also returns an approximate size in MB
    # so the Memory page's "X GB / Y GB" figure can eventually be swapped
    # to this too, if you want.
    def get_memory_stats(self):
        try:
            from config import Config

            max_exchanges = getattr(Config, "MAX_MEMORY_EXCHANGES", 20) or 20
            rows = self.db.get_recent_messages(limit=max(max_exchanges * 2, 500))
            total_rows = len(rows) if rows else 0
            exchange_count = total_rows // 2
            pct = min(100, round((exchange_count / max_exchanges) * 100))

            approx_bytes = 0
            for r in rows or []:
                d = _row_to_export_dict(r)
                approx_bytes += len((d.get("message") or "").encode("utf-8"))

            return {
                "ok": True,
                "pct": pct,
                "exchange_count": exchange_count,
                "max_exchanges": max_exchanges,
                "approx_mb": round(approx_bytes / (1024 * 1024), 2),
            }
        except Exception as e:
            print(f"[get_memory_stats error] {e}")
            return {"ok": False}

    # ── Proactive Insights card (Settings page) ─────────────────────────
    # Per-trigger counts + a recent-activity list for
    # sara/orchestrator/proactive.py's logged nudges (battery/reminder/
    # idle_break), backed by sara/core/memory.py's proactive_log table.
    def get_proactive_stats(self):
        try:
            if not hasattr(self.db, "get_proactive_stats"):
                return {"ok": False}
            stats = self.db.get_proactive_stats()
            return {
                "ok": True,
                "total": stats.get("total", 0),
                "by_trigger": stats.get("by_trigger", {}),
                "recent": stats.get("recent", []),
            }
        except Exception as e:
            print(f"[get_proactive_stats error] {e}")
            return {"ok": False}

    # ── Shareable Moments card (Memory page) ────────────────────────────
    def get_share_card_data(self):
        try:
            streak = self.db.get_streak_count() if hasattr(self.db, "get_streak_count") else 0
            convo_stats = (
                self.db.get_conversation_stats()
                if hasattr(self.db, "get_conversation_stats")
                else {"total_messages": 0, "first_message_date": None}
            )
            proactive_stats = (
                self.db.get_proactive_stats()
                if hasattr(self.db, "get_proactive_stats")
                else {"total": 0}
            )
            days_used = None
            first_date = convo_stats.get("first_message_date")
            if first_date:
                try:
                    first = datetime.fromisoformat(first_date).date()
                    days_used = max(1, (datetime.now().date() - first).days + 1)
                except ValueError:
                    days_used = None
            return {
                "ok": True,
                "streak": streak,
                "total_messages": convo_stats.get("total_messages", 0),
                "days_used": days_used,
                "proactive_nudges": proactive_stats.get("total", 0),
                "sara_name": getattr(Config, "SARA_NAME", "Sara"),
            }
        except Exception as e:
            print(f"[get_share_card_data error] {e}")
            return {"ok": False}

    # ── Weather card (Home page) ────────────────────────────────────────
    def get_weather(self):
        """
        Returns whatever is currently cached immediately (never blocks the
        bridge on a network call). If the cache is empty or older than
        WEATHER_CACHE_SECONDS, a background thread refreshes it and pushes
        a "weather_update" event to the frontend once the new data lands.
        """
        now = time.time()
        with _weather_lock:
            cached = _weather_cache["data"]
            age = now - _weather_cache["ts"]
            is_stale = cached is None or age > WEATHER_CACHE_SECONDS

        if is_stale:

            def _refresh():
                result = _fetch_weather_from_api()
                with _weather_lock:
                    _weather_cache["data"] = result
                    _weather_cache["ts"] = time.time()
                _push("weather_update", result)

            threading.Thread(target=_refresh, daemon=True, name="WeatherFetch").start()

        return {"ok": True, "data": cached}

    def wake_now(self):
        # v13: a fresh wake means a genuinely new turn is starting — clear
        # any Stop-latch left over from a previous reply so Sara can speak
        # again for this new interaction.
        try:
            if hasattr(self.tts, "clear_interrupt"):
                self.tts.clear_interrupt()
        except Exception as e:
            print(f"[wake_now clear_interrupt error] {e}")
        events._manual_wake_event.set()
        return {"ok": True}

    # ── Stop button (mic-wrap) — turant Sara ko rokna ──
    # tts.stop() already TextToSpeech class mein maujood hai, use call karte
    # hain taaki bol rahi ho to turant chup ho jaye. In-flight command ke liye
    # koi dedicated cancel-flag is codebase mein currently exist nahi karta,
    # isliye best-effort sirf tts.stop() + frontend ko "sleeping" status push
    # karna hi is round ke liye kaafi hai (jaisa docstring mein user ne allow
    # kiya). Har call apni try/except mein hai taaki ek fail ho to doosra na
    # ruke.
    def stop_sara(self):
        # sara/audio/tts.py's TextToSpeech.stop() (Kokoro-ONNX backend):
        #   1. sets self._stop -> interrupts the segment playing RIGHT NOW
        #      within ~8ms (the playback poll interval).
        #   2. sets self._interrupted (a persistent latch, v13) -> every
        #      subsequent speak()/speak_stream() call for the rest of the
        #      current multi-sentence reply is skipped entirely, instead
        #      of only the one sentence that happened to be mid-playback.
        #      Without this latch, the caller's per-sentence speak() loop
        #      (in gui_main.py) would just keep talking on the next
        #      sentence — which was the actual bug.
        #   3. clears whatever's already queued on the audio device.
        try:
            if hasattr(self.tts, "stop"):
                self.tts.stop()
        except Exception as e:
            print(f"[stop_sara tts.stop() error] {e}")
        try:
            _push("status", "sleeping")
        except Exception as e:
            print(f"[stop_sara push error] {e}")
        return {"ok": True}

    # ── Serialized typed-command pipeline (v14) ─────────────────────────
    def _ensure_command_worker(self):
        """
        Start the single command worker on first use. Lazy rather than in
        __init__ so a process that never types a command never pays for the
        thread, and so a worker that somehow died (it shouldn't -- the loop
        swallows everything) gets replaced on the next command.
        """
        with self._cmd_thread_lock:
            if self._cmd_thread is not None and self._cmd_thread.is_alive():
                return
            self._cmd_thread = threading.Thread(
                target=self._command_loop, daemon=True, name="SaraCmdWorker"
            )
            self._cmd_thread.start()

    def _command_loop(self):
        """
        The one and only consumer of _cmd_queue. Daemon thread, runs for the
        life of the process. Nothing in here is allowed to raise, or the
        queue would stop draining and every later command would silently
        hang at "busy".
        """
        while True:
            text = self._cmd_queue.get()
            try:
                if text is None:  # shutdown sentinel from close_window()
                    return
                self._run_text_command(text)
            except Exception as e:
                print(f"[command worker error] {e}")
            finally:
                self._cmd_queue.task_done()

    def _run_text_command(self, text):
        # v13: same as wake_now() -- a newly typed/sent command is a fresh
        # turn, so clear any Stop-latch from a previous reply first.
        # v14: this moved from send_text_command() into the worker. It must
        # happen when the command actually STARTS, not when it was queued:
        # clearing at queue time would un-latch the Stop the user just
        # pressed on the reply that is still being spoken.
        try:
            if hasattr(self.tts, "clear_interrupt"):
                self.tts.clear_interrupt()
        except Exception as e:
            print(f"[send_text_command clear_interrupt error] {e}")

        # SESSION-CONTROL FIX: exit/sleep/forget-memory/"my name is X"
        # used to only be checked in the voice loop (run_sara_logic),
        # never here -- typing "exit" or "my name is Priya" in the GUI
        # did nothing. _handle_command() now checks these itself and
        # reports back via this out-param dict instead of a return
        # value, so the normal `reply` string handling below stays
        # untouched either way.
        session_control: dict = {}
        try:
            # STATE_LOCK is held across the whole _handle_command() call, not
            # just around individual dict accesses, because the races that
            # matter are read-modify-write sequences INSIDE it (set a pending
            # confirm, then later consume it). Holding it for the duration is
            # affordable precisely because the queue already guarantees only
            # one typed command runs at a time -- the lock is what extends
            # that guarantee to the voice loop once it takes the lock too.
            with self.state_lock:
                reply = self.gui_main._handle_command(
                    text,
                    self.brain,
                    self.tts,
                    self.ears,
                    self.db,
                    self.reminders,
                    self.vision,
                    _push,
                    self.volume_state,
                    playback_state=self.playback_state,
                    confirm_state=self.confirm_state,
                    context_state=self.context_state,
                    session_control=session_control,
                )
        except Exception as e:
            print(f"[send_text_command _handle_command error] {e}")
            reply = "Sorry, something went wrong handling that. Please try again."
            try:
                _push("status", "sleeping")
            except Exception as e2:
                print(f"[send_text_command status push error] {e2}")
        if reply:
            _push("transcript", "sara", reply)
            try:
                self.db.log_message("user", text)
                self.db.log_message("assistant", reply)
            except Exception as e:
                print(f"[db log error] {e}")

        # SESSION-CONTROL FIX: act on what _handle_command() decided.
        # GUI equivalents of the voice loop's stop_event.set()/break --
        # "exit" closes the whole app window (same as the taskbar
        # close button, which already runs bootstrap.main()'s normal
        # shutdown/cleanup path once webview.start() returns); "sleep"
        # just stops the mic/TTS for this session like the Stop button,
        # window stays open.
        action = session_control.get("action")
        if action == "exit":
            self.close_window()
        elif action == "sleep":
            self.stop_sara()

    def send_text_command(self, text):
        """
        Queue a typed command. Returns immediately (the JS bridge call must
        never block on the LLM); the actual work happens on the single
        SaraCmdWorker thread, so commands are processed strictly one at a
        time and in submission order.

        Return contract is unchanged for the happy path ({"ok": True}); a
        rejected command now returns ok=False plus a "reason" the frontend
        can ignore safely if it doesn't care.
        """
        text = (text or "").strip()
        if not text:
            return {"ok": False, "reason": "empty"}

        now = time.time()
        with self._cmd_thread_lock:
            if (
                text == self._last_cmd_text
                and (now - self._last_cmd_ts) < _DUPLICATE_WINDOW_SECONDS
            ):
                # Double-click on Send / Enter repeat -- the user meant one
                # command. Swallow it silently; the first copy is already
                # queued and has already echoed to the transcript.
                return {"ok": False, "reason": "duplicate"}
            self._last_cmd_text = text
            self._last_cmd_ts = now

        self._ensure_command_worker()

        try:
            self._cmd_queue.put_nowait(text)
        except queue.Full:
            # Bounded on purpose: rejecting is better than letting a stuck
            # command pile up an unbounded backlog the user forgot about.
            _push(
                "transcript",
                "sara",
                "I'm still working on your previous request — give me a moment.",
            )
            return {"ok": False, "reason": "busy"}

        # Echo to the transcript only after the command is definitely
        # accepted, so a rejected one doesn't leave an orphan user bubble
        # that never gets a reply.
        # gui_main is cached in __init__, so we use self.gui_main
        # to avoid the micro-latency of repeating the import lock here.
        _push("transcript", "user", text)
        return {"ok": True, "queued": self._cmd_queue.qsize()}

    def minimize_window(self):
        # webview is globally imported, no need to re-import locally.
        for w in webview.windows:
            w.minimize()
        return {"ok": True}

    def toggle_maximize(self):
        for w in webview.windows:
            w.toggle_fullscreen()
        return {"ok": True}

    def close_window(self):
        # Drop anything still queued and ask the worker to exit. Best-effort
        # and non-blocking: close_window() can be called FROM the worker
        # itself (session_control action == "exit"), so it must never join
        # the worker thread. The thread is a daemon either way, so a command
        # mid-flight can't keep the process alive.
        try:
            while True:
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            self._cmd_queue.put_nowait(None)
        except Exception as e:
            print(f"[close_window queue drain error] {e}")

        for w in webview.windows:
            w.destroy()
        return {"ok": True}

    def run_action(self, action_key):

        if action_key == "play_music":
            # FIX: this used to build a search-style query from
            # INDIAN_MUSIC_SEARCHES (e.g. "latest Bollywood songs") and
            # send it through send_text_command(), which the orchestrator
            # resolves as a web/YouTube search -- not an actual "resume
            # my media" action. Route to the already-verified OS media
            # session instead (the same call the mini player's own
            # play/pause button makes) so Play Music actually resumes
            # whatever's loaded in Spotify/Chrome/VLC/etc. via SMTC.
            return self.toggle_music_playback(True)

        phrase_map = {
            "open_chrome": "open chrome",
            "send_message": "open whatsapp",
            "search_web": "search the web for ai news",
            "create_file": "create a new file",
            "system_info": "system info",
        }

        return self.send_text_command(phrase_map.get(action_key, action_key))