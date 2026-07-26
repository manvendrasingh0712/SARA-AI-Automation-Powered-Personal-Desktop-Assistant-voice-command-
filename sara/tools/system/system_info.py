"""
sara.tools.system.system_info
Read-only system stats (battery, CPU/RAM/disk usage, uptime, IP, time/date).
"""

import logging
import re
import socket
import platform

from datetime import datetime

import psutil

from config import Config

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# PRODUCTION-AUDIT FIX: previously computed as os.path.join(os.getcwd(),
# "sara_notes.txt"), which meant launching the app from a different
# working directory could silently point at a different physical file
# than the one database.py/reminders.py use. Now resolved from a single,
# CWD-independent, project-root-based path defined once in config.py.
_NOTES_FILE = Config.NOTES_FILE_PATH

# FINAL PRODUCTION POLISH: single canonical definition, used by
# get_notes() below to parse each "[timestamp] text" line back out of
# sara_notes.txt. Previously this regex existed in two places — one
# unreachable/dead copy mis-indented inside clear_notes(), and one real
# copy declared AFTER get_notes() (which only worked because Python
# resolves names inside a function body at call time, not definition
# time — fragile and confusing to read). Consolidated here, declared
# before anything references it.
_NOTE_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s?(?P<text>.*)$")




# ============================================================
# SYSTEM INFO
# ============================================================

_info_cache = {}
_CACHE_TTL_SECONDS = 5


def _get_cached(key: str, fetch_fn):
    now = datetime.now().timestamp()
    cached = _info_cache.get(key)
    if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]
    value = fetch_fn()
    _info_cache[key] = (value, now)
    return value


def get_current_time() -> str:
    return datetime.now().strftime("%I:%M %p")


def get_current_date() -> str:
    return datetime.now().strftime("%A, %B %d, %Y")


def get_battery_status() -> str:
    def _fetch():
        try:
            battery = psutil.sensors_battery()
            if battery is None:
                return "No battery detected. This device may be a desktop."
            percent = round(battery.percent)
            status = "charging" if battery.power_plugged else "on battery power"
            return f"Battery is at {percent}% and currently {status}."
        except Exception as e:
            logger.error(f"get_battery_status failed: {e}")
            return "Sorry, I couldn't retrieve the battery status right now."

    return _get_cached("battery", _fetch)


def get_battery_raw():
    """
    Numeric counterpart to get_battery_status(), for callers that need to
    compare against a threshold (e.g. sara/orchestrator/proactive.py's
    low-battery trigger) instead of a spoken sentence.

    Returns (percent: int, plugged: bool), or None if there's no battery
    (desktop) or the read fails. Uses the same 5s cache as the rest of
    this module so this can be polled frequently without extra overhead.
    """
    def _fetch():
        try:
            battery = psutil.sensors_battery()
            if battery is None:
                return None
            return (round(battery.percent), bool(battery.power_plugged))
        except Exception as e:
            logger.error(f"get_battery_raw failed: {e}")
            return None

    return _get_cached("battery_raw", _fetch)


def get_cpu_usage() -> str:
    try:
        # LATENCY FIX: interval=None returns usage since the last call
        # instantly instead of blocking the caller for 300ms.
        usage = psutil.cpu_percent(interval=None)
        return f"CPU usage is currently at {usage}%."
    except Exception as e:
        logger.error(f"get_cpu_usage failed: {e}")
        return "Sorry, I couldn't retrieve CPU usage right now."


def get_ram_usage() -> str:
    def _fetch():
        try:
            mem = psutil.virtual_memory()
            used_gb = mem.used / (1024**3)
            total_gb = mem.total / (1024**3)
            return f"RAM usage is at {mem.percent}% ({used_gb:.1f} GB of {total_gb:.1f} GB used)."
        except Exception as e:
            logger.error(f"get_ram_usage failed: {e}")
            return "Sorry, I couldn't retrieve RAM usage right now."

    return _get_cached("ram", _fetch)


def get_disk_usage(drive: str = "C:\\") -> str:
    # BUG FIX: was not actually using the drive param in cache key
    def _fetch():
        try:
            usage = psutil.disk_usage(drive)
            used_gb = usage.used / (1024**3)
            total_gb = usage.total / (1024**3)
            return (
                f"Disk {drive} is at {usage.percent}% usage "
                f"({used_gb:.1f} GB of {total_gb:.1f} GB used)."
            )
        except Exception as e:
            logger.error(f"get_disk_usage failed for drive '{drive}': {e}")
            return f"Sorry, I couldn't retrieve disk usage for {drive} right now."

    return _get_cached(f"disk:{drive}", _fetch)


def get_uptime() -> str:
    try:
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        delta = datetime.now() - boot_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f"Your system has been running for {hours} hours and {minutes} minutes."
    except Exception as e:
        logger.error(f"get_uptime failed: {e}")
        return "Sorry, I couldn't retrieve the system uptime right now."


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return f"Your local IP address is {ip}."
    except Exception as e:
        logger.error(f"get_local_ip failed: {e}")
        return "Sorry, I couldn't retrieve the local IP address right now."


def get_system_summary() -> str:
    parts = [
        get_current_time(),
        get_current_date(),
        get_battery_status(),
        get_cpu_usage(),
        get_ram_usage(),
    ]
    return " | ".join(parts)