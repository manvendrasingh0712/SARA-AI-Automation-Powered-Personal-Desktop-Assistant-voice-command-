"""
sara.gui.app.helpers
Small standalone helpers used by the Api class: memory-row export shaping,
the direct-weather-API fallback fetch, and the debounced preference writer.
"""
import os
import json
import time
import queue
import random
import threading
import urllib.request
import urllib.parse
import webview

# ── Weather integration (OpenWeatherMap free tier) ──────────────────────────
# The API key is loaded from the WEATHER_API_KEY environment variable so the
# real key never lives in source control. Set it before launching the app, e.g.
#   export WEATHER_API_KEY="your-openweathermap-key"   (macOS/Linux)
#   setx WEATHER_API_KEY "your-openweathermap-key"     (Windows)
# WEATHER_CITY uses OpenWeatherMap's "City,CountryCode" query format.
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
WEATHER_CITY = "Ajmer,IN"
WEATHER_CACHE_SECONDS = 900  # 15 minutes — keeps us well inside the free tier

if not WEATHER_API_KEY:
    print(
        "[weather] WARNING: WEATHER_API_KEY environment variable is not set. "
        "The Weather card will fail to fetch data until it is configured."
    )

_weather_cache = {"data": None, "ts": 0.0}
_weather_lock = threading.Lock()

# OpenWeatherMap's Air Pollution endpoint reports AQI on its own 1-5 scale
# (not the 0-500 scale some other services use) — mapped to a plain label
# instead of a made-up number.
_AQI_LABELS = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}



def _row_to_export_dict(r) -> dict:
    """
    Normalize one DB row into {"role","message","timestamp"} regardless
    of the concrete type PreferencesDB.get_recent_messages() returns.

    PRODUCTION-AUDIT FIX: this previously assumed a plain
    tuple/list-like row (`r[0]`, `r[1]`, `r[2]`). If the DB layer
    actually returns dict rows or sqlite3.Row objects (both common for
    a hand-rolled SQLite wrapper), `r[0]` either raises (dict — no
    integer keys) or silently returns a different column than intended
    depending on column order, which would corrupt or completely break
    every memory export without any visible error until the user opened
    the exported file. Now tries dict access, then key/index access
    (covers sqlite3.Row and positional tuples), then attribute access,
    before finally giving up with an empty field rather than crashing.
    """
    if isinstance(r, dict):
        return {
            "role": r.get("role", ""),
            "message": r.get("message", r.get("content", "")),
            "timestamp": r.get("timestamp", ""),
        }
    try:
        return {"role": r["role"], "message": r["message"], "timestamp": r["timestamp"]}
    except Exception:
        pass
    try:
        return {"role": r[0], "message": r[1], "timestamp": r[2]}
    except Exception:
        pass
    return {
        "role": getattr(r, "role", ""),
        "message": getattr(r, "message", getattr(r, "content", "")),
        "timestamp": getattr(r, "timestamp", ""),
    }
def _fetch_weather_from_api() -> dict:
    """
    Hits OpenWeatherMap's free "Current Weather" endpoint and, using the
    coordinates that response already includes, the free "Air Pollution"
    endpoint for an AQI reading — then normalizes both into the flat
    shape the frontend renders directly.

    Always called from a background thread (see Api.get_weather) so a
    slow or unreachable network can never block the JS<->Python bridge.
    """
    if not WEATHER_API_KEY:
        return {
            "ok": False,
            "error": (
                "No OpenWeatherMap API key configured. Set the WEATHER_API_KEY "
                "environment variable before launching SARA."
            ),
        }

    try:
        params = urllib.parse.urlencode(
            {"q": WEATHER_CITY, "appid": WEATHER_API_KEY, "units": "metric"}
        )
        url = f"https://api.openweathermap.org/data/2.5/weather?{params}"
        with urllib.request.urlopen(url, timeout=6) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        weather0 = (raw.get("weather") or [{}])[0]
        result = {
            "ok": True,
            "city": raw.get("name") or WEATHER_CITY,
            "temp": round(raw["main"]["temp"]),
            "temp_max": round(raw["main"]["temp_max"]),
            "temp_min": round(raw["main"]["temp_min"]),
            "humidity": raw["main"].get("humidity"),
            "condition": weather0.get("main", ""),
            "description": (weather0.get("description") or "").title(),
            "aqi_label": None,
        }

        # AQI is a separate free endpoint that needs the lat/lon the
        # weather call already returned. Best-effort only: the weather
        # part above still renders fine even if this fails.
        try:
            lat = raw["coord"]["lat"]
            lon = raw["coord"]["lon"]
            aqi_params = urllib.parse.urlencode(
                {"lat": lat, "lon": lon, "appid": WEATHER_API_KEY}
            )
            aqi_url = (
                f"https://api.openweathermap.org/data/2.5/air_pollution?{aqi_params}"
            )
            with urllib.request.urlopen(aqi_url, timeout=6) as resp2:
                aqi_raw = json.loads(resp2.read().decode("utf-8"))
            aqi_value = aqi_raw["list"][0]["main"]["aqi"]  # OWM scale: 1-5
            result["aqi_label"] = _AQI_LABELS.get(aqi_value, "—")
        except Exception as e:
            print(f"[weather] AQI fetch failed (non-fatal): {e}")

        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}
class _PrefWriter:
    """
    Single background worker that serializes all `db.set_preference`
    calls through one queue instead of spawning a new OS thread per
    write. This is what actually keeps the UI bridge thread instant:

      - One long-lived thread instead of a burst of short-lived ones
        (thread creation itself has real, measurable cost — spawning
        one per slider-drag event was adding jank, not removing it).
      - Writes are processed strictly in the order they were enqueued,
        so a slower write can never clobber a newer value.
      - Errors are actually caught and logged here instead of dying
        silently inside a fire-and-forget thread.
      - stop() drains any pending writes before the process exits, so
        a setting changed right before quitting isn't lost.
    """

    def __init__(self, set_preference_fn):
        self._set_preference = set_preference_fn
        self._q: "queue.Queue[tuple[str, str] | None]" = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="PrefWriter"
        )
        self._thread.start()

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:  # shutdown sentinel
                self._q.task_done()
                break
            key, value = item
            try:
                self._set_preference(key, value)
            except Exception as e:
                print(f"[pref write error] {key}={value}: {e}")
            finally:
                self._q.task_done()

    def enqueue(self, key: str, value: str) -> None:
        self._q.put((key, value))

    def stop(self, timeout: float = 3.0) -> None:
        """Flush pending writes and stop the worker. Call on app shutdown."""
        self._q.put(None)
        self._thread.join(timeout=timeout)
