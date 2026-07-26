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
import random
import threading
import webview
from datetime import datetime

from config import Config

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
        self._pref_writer = _PrefWriter(self.db.set_preference)

        self.music_queries = INDIAN_MUSIC_SEARCHES.copy()
        random.shuffle(self.music_queries)
        self.music_index = 0

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

    def send_text_command(self, text):
        # v13: same as wake_now() — a newly typed/sent command is a fresh
        # turn, so clear any Stop-latch from a previous reply first.
        try:
            if hasattr(self.tts, "clear_interrupt"):
                self.tts.clear_interrupt()
        except Exception as e:
            print(f"[send_text_command clear_interrupt error] {e}")
        # gui_main is cached in __init__, so we use self.gui_main
        # to avoid the micro-latency of repeating the import lock here.
        _push("transcript", "user", text)

        def _worker():
            try:
                reply = self.gui_main._handle_command(
                    text,
                    self.brain,
                    self.tts,
                    self.ears,
                    self.db,
                    self.reminders,
                    self.vision,
                    _push,
                    {},
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

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True}

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
        for w in webview.windows:
            w.destroy()
        return {"ok": True}

    def run_action(self, action_key):

        if action_key == "play_music":

            if self.music_index >= len(self.music_queries):
                random.shuffle(self.music_queries)
                self.music_index = 0

            query = self.music_queries[self.music_index]
            self.music_index += 1

            return self.send_text_command(f"play {query}")

        phrase_map = {
            "open_chrome": "open chrome",
            "send_message": "open whatsapp",
            "search_web": "search the web for ai news",
            "create_file": "create a new file",
            "system_info": "system info",
        }

        return self.send_text_command(phrase_map.get(action_key, action_key))