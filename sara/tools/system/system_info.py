"""
sara.tools.system.system_info
Read-only system stats (battery, CPU/RAM/disk usage, uptime, IP, time/date,
GPU, temperature, top processes).
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

_NOTES_FILE = Config.NOTES_FILE_PATH
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


def get_gpu_status() -> str:
    """
    NVIDIA-only (via pynvml/nvidia-ml-py). Windows has no vendor-neutral
    public API for GPU usage/VRAM, so this degrades to a clear message
    instead of crashing when the package or an NVIDIA GPU isn't present.
    """
    def _fetch():
        try:
            import pynvml
        except ImportError:
            return (
                "GPU monitoring needs the 'nvidia-ml-py' package. "
                "Run: pip install nvidia-ml-py"
            )
        try:
            pynvml.nvmlInit()
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode()
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                mem_used_gb = mem.used / (1024**3)
                mem_total_gb = mem.total / (1024**3)
                try:
                    temp = pynvml.nvmlDeviceGetTemperature(
                        handle, pynvml.NVML_TEMPERATURE_GPU
                    )
                    temp_part = f", temperature {temp}°C"
                except Exception:
                    temp_part = ""
                return (
                    f"{name} is at {util.gpu}% usage, using "
                    f"{mem_used_gb:.1f} GB of {mem_total_gb:.1f} GB VRAM{temp_part}."
                )
            finally:
                pynvml.nvmlShutdown()
        except Exception as e:
            logger.error(f"get_gpu_status failed: {e}")
            return (
                "Sorry, I couldn't read the GPU status right now. "
                "Make sure an NVIDIA GPU and driver are installed."
            )

    return _get_cached("gpu", _fetch)


def get_temperature_status() -> str:
    """
    Reports GPU temperature (via pynvml, NVIDIA-only) and CPU/other sensor
    temperatures where the OS exposes them. NOTE: Windows does not expose
    CPU package temperature through any public, driver-independent API —
    psutil.sensors_temperatures() only works on Linux and raises
    AttributeError on Windows. Reading real CPU temp on Windows needs a
    third-party sensor driver (e.g. LibreHardwareMonitor via WMI), which
    is intentionally out of scope here to avoid an admin-rights-dependent,
    fragile dependency. This degrades gracefully rather than faking a number.
    """
    def _fetch():
        parts = []

        try:
            import pynvml
            pynvml.nvmlInit()
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                temp = pynvml.nvmlDeviceGetTemperature(
                    handle, pynvml.NVML_TEMPERATURE_GPU
                )
                parts.append(f"GPU is at {temp}°C")
            finally:
                pynvml.nvmlShutdown()
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"get_temperature_status GPU read failed: {e}")

        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for sensor_name, entries in temps.items():
                    for entry in entries:
                        label = entry.label or sensor_name
                        if entry.current is not None:
                            parts.append(f"{label} is at {entry.current:.0f}°C")
        except AttributeError:
            pass
        except Exception as e:
            logger.error(f"get_temperature_status CPU read failed: {e}")

        if not parts:
            return (
                "I can't read CPU temperature on Windows without a third-party "
                "sensor driver, and no GPU temperature sensor was found either."
            )
        return ", ".join(parts) + "."

    return _get_cached("temperature", _fetch)


def get_process_list(limit: int = 5) -> str:
    """Top processes by memory usage (CPU% is skipped: psutil needs a warmup
    call for a meaningful per-process cpu_percent, so a single instant read
    would misleadingly show 0.0% for everything)."""
    try:
        procs = []
        for proc in psutil.process_iter(["name", "memory_percent"]):
            try:
                info = proc.info
                if info.get("name"):
                    procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda p: p.get("memory_percent") or 0, reverse=True)
        top = procs[:limit]
        if not top:
            return "I couldn't read the process list right now."

        listing = ", ".join(
            f"{p['name']} at {p['memory_percent']:.1f}% memory" for p in top
        )
        return f"Top {len(top)} processes by memory usage: {listing}."
    except Exception as e:
        logger.error(f"get_process_list failed: {e}")
        return "Sorry, I couldn't retrieve the process list right now."


def get_system_summary() -> str:
    parts = [
        get_current_time(),
        get_current_date(),
        get_battery_status(),
        get_cpu_usage(),
        get_ram_usage(),
    ]
    return " | ".join(parts)