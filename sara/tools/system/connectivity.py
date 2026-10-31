"""
sara.tools.system.connectivity
WiFi / Bluetooth toggles and dark/light theme switching.
"""
from ._shared import _ensure_windows

import ctypes
import logging
import os
import re
import subprocess
import platform



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
# NETWORK — WiFi & Bluetooth
# ============================================================


# ------------------------------------------------------------
# Radios (Wi-Fi / Bluetooth) through the Windows radio API.
#
# `netsh interface set interface` needs Administrator. The Windows.Devices.Radios API is what the
# Settings quick-toggle uses and works for a normal user, so it is tried first; netsh stays as a
# fallback for machines where the API is unavailable.
# ------------------------------------------------------------

_RADIO_KINDS = {"wifi": 1, "bluetooth": 2}   # RadioKind.WiFi / RadioKind.Bluetooth


def _radio_call(kind: str, want):
    """
    want: True (on), False (off) or None (just report). Returns {"found", "was_on", "now_on", "error"}.
    Never raises: callers fall back to another method when "found" is False or "error" is set.
    """
    result = {"found": False, "was_on": None, "now_on": None, "error": ""}
    if not _IS_WINDOWS:
        result["error"] = "not windows"
        return result

    def _run():
        import asyncio

        from winsdk.windows.devices.radios import Radio, RadioAccessStatus, RadioState

        async def _go():
            status = await Radio.request_access_async()
            if status != RadioAccessStatus.ALLOWED:
                return {"found": True, "error": "access denied (%s)" % status}
            radios = [r for r in await Radio.get_radios_async() if int(r.kind) == _RADIO_KINDS[kind]]
            if not radios:
                return {"found": False, "error": "no %s radio" % kind}
            radio = radios[0]
            was_on = radio.state == RadioState.ON
            out = {"found": True, "was_on": was_on, "now_on": was_on, "error": ""}
            if want is not None and want != was_on:
                res = await radio.set_state_async(RadioState.ON if want else RadioState.OFF)
                if res != RadioAccessStatus.ALLOWED:
                    out["error"] = "set refused (%s)" % res
                else:
                    out["now_on"] = bool(want)
            return out

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_go())
        finally:
            loop.close()

    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            result.update(pool.submit(_run).result(timeout=8.0))
    except Exception as e:  # winsdk missing, timeout, ...
        logger.debug(f"radio api unavailable for {kind}: {e}")
        result["error"] = str(e) or type(e).__name__
    return result


def set_radio(kind: str, on: bool):
    """Turn the Wi-Fi / Bluetooth radio on or off without admin rights. Returns the _radio_call dict."""
    return _radio_call(kind, bool(on))


def toggle_radio(kind: str):
    """Flip the radio. Returns the _radio_call dict (check "error" and "now_on")."""
    state = _radio_call(kind, None)
    if state.get("error") or state.get("was_on") is None:
        return state
    return _radio_call(kind, not state["was_on"])


def wifi_on() -> str:
    """Enables WiFi (radio API first, netsh as the fallback)."""
    _ensure_windows()
    radio = set_radio("wifi", True)
    if radio.get("found") and not radio.get("error"):
        return "WiFi has been turned on."
    try:
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", "Wi-Fi", "enabled"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "WiFi has been turned on."
        # Some systems use different interface names; try opening Settings
        os.startfile("ms-settings:network-wifi")
        return "Opened WiFi settings. Please enable WiFi from there."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."
    except Exception as e:
        logger.error(f"wifi_on failed: {e}")
        return "Sorry, I couldn't turn WiFi on right now."


def wifi_off() -> str:
    """Disables WiFi (radio API first, netsh as the fallback)."""
    _ensure_windows()
    radio = set_radio("wifi", False)
    if radio.get("found") and not radio.get("error"):
        return "WiFi has been turned off."
    try:
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", "Wi-Fi", "disabled"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "WiFi has been turned off."
        os.startfile("ms-settings:network-wifi")
        return "Opened WiFi settings. Please disable WiFi from there."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."
    except Exception as e:
        logger.error(f"wifi_off failed: {e}")
        return "Sorry, I couldn't turn WiFi off right now."


def bluetooth_on() -> str:
    """Turns Bluetooth on through the radio API; opens Settings if that is not possible."""
    _ensure_windows()
    radio = set_radio("bluetooth", True)
    if radio.get("found") and not radio.get("error"):
        return "Bluetooth has been turned on."
    try:
        # Windows has no reliable CLI for Bluetooth toggle without third-party tools.
        # Open the Settings page — most reliable cross-version approach.
        os.startfile("ms-settings:bluetooth")
        return "Opened Bluetooth settings. Please turn Bluetooth on from there."
    except Exception as e:
        logger.error(f"bluetooth_on failed: {e}")
        return "Sorry, I couldn't open Bluetooth settings right now."


def bluetooth_off() -> str:
    """Turns Bluetooth off through the radio API; opens Settings if that is not possible."""
    _ensure_windows()
    radio = set_radio("bluetooth", False)
    if radio.get("found") and not radio.get("error"):
        return "Bluetooth has been turned off."
    try:
        os.startfile("ms-settings:bluetooth")
        return "Opened Bluetooth settings. Please turn Bluetooth off from there."
    except Exception as e:
        logger.error(f"bluetooth_off failed: {e}")
        return "Sorry, I couldn't open Bluetooth settings right now."


# ============================================================
# DISPLAY — Dark Mode / Light Mode
# ============================================================


def dark_mode() -> str:
    """Switches Windows to dark mode by writing to the registry."""
    _ensure_windows()
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 0)
        # Broadcast the change so apps pick it up
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 5000, None
        )
        return "Dark mode has been enabled."
    except Exception as e:
        logger.error(f"dark_mode failed: {e}")
        return "Sorry, I couldn't enable dark mode right now."


def light_mode() -> str:
    """Switches Windows to light mode by writing to the registry."""
    _ensure_windows()
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1)
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 5000, None
        )
        return "Light mode has been enabled."
    except Exception as e:
        logger.error(f"light_mode failed: {e}")
        return "Sorry, I couldn't enable light mode right now."