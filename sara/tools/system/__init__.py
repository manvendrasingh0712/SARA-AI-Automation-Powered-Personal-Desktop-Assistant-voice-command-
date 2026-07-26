"""
sara.tools.system
Public package API for OS-level system control. External code keeps using
this exactly as before:

    from sara.tools import system as system_tools
    system_tools.open_application("chrome")
    system_tools.SIMPLE_ACTIONS["lock_pc"]()

Every name that used to live in the single 1576-line system.py is re-exported
here unchanged, just re-organized internally by category:
    _shared.py         - _ensure_windows / _send_keys (used by nearly everything)
    apps.py             - open/close applications
    audio_display.py    - volume / brightness
    power.py            - lock/sleep/hibernate/shutdown/restart
    window_mgmt.py       - minimize/maximize/snap/switch
    media_keys.py         - play/pause/next/prev/stop
    shortcuts.py           - copy/paste/undo/tabs/zoom/scroll
    connectivity.py         - wifi/bluetooth/theme
    files_notes.py           - find_file, recycle bin, quick notes
    timers.py                 - voice-triggered countdown timers
    folders.py                 - well-known shell folders
    settings_pages.py           - ms-settings: deep links
    system_info.py                - battery/CPU/RAM/disk/uptime/IP
    dispatch.py                    - SIMPLE_ACTIONS table (imports from all of the above)
"""
from .apps import open_application, close_application
from .audio_display import (
    set_volume, adjust_volume, get_brightness_status, set_brightness, adjust_brightness,
)
from .power import (
    lock_pc, sleep_system, hibernate_system, log_off, shutdown_system,
    restart_system, cancel_shutdown,
)
from .window_mgmt import (
    show_desktop, minimize_all_windows, restore_windows, maximize_active_window,
    minimize_active_window, close_active_window, snap_window_left,
    snap_window_right, switch_window,
)
from .media_keys import play_pause_media, next_track, previous_track, stop_media
from .shortcuts import (
    type_text, press_key, copy_selection, paste_clipboard, select_all, undo, redo,
    new_tab, close_tab, next_tab, prev_tab, reload_page, zoom_in, zoom_out, zoom_reset,
    scroll_up, scroll_down, scroll_top, scroll_bottom,
)
from .connectivity import wifi_on, wifi_off, bluetooth_on, bluetooth_off, dark_mode, light_mode
from .files_notes import find_file, empty_recycle_bin, take_note, read_notes, clear_notes, get_notes
from .timers import set_timer, cancel_timer
from .folders import (
    open_downloads, open_documents, open_desktop_folder, open_pictures, open_music,
    open_videos, open_this_pc, open_recycle_bin, open_file_explorer,
    open_control_panel, open_task_manager,
)
from .settings_pages import (
    open_display_settings, open_sound_settings, open_bluetooth_settings,
    open_network_settings, open_update_settings, open_apps_settings,
    open_personalization_settings, open_privacy_settings, open_storage_settings,
    open_power_settings, open_about_settings,
)
from .system_info import (
    get_current_time, get_current_date, get_battery_status, get_cpu_usage,
    get_ram_usage, get_disk_usage, get_uptime, get_local_ip, get_system_summary,
)
from .dispatch import SIMPLE_ACTIONS

__all__ = [
    "open_application", "close_application", "set_volume", "adjust_volume",
    "get_brightness_status", "set_brightness", "adjust_brightness", "lock_pc",
    "sleep_system", "hibernate_system", "log_off", "shutdown_system",
    "restart_system", "cancel_shutdown", "show_desktop", "minimize_all_windows",
    "restore_windows", "maximize_active_window", "minimize_active_window",
    "close_active_window", "snap_window_left", "snap_window_right", "switch_window",
    "play_pause_media", "next_track", "previous_track", "stop_media", "type_text",
    "press_key", "copy_selection", "paste_clipboard", "select_all", "undo", "redo",
    "new_tab", "close_tab", "next_tab", "prev_tab", "reload_page", "zoom_in",
    "zoom_out", "zoom_reset", "scroll_up", "scroll_down", "scroll_top",
    "scroll_bottom", "wifi_on", "wifi_off", "bluetooth_on", "bluetooth_off",
    "dark_mode", "light_mode", "find_file", "empty_recycle_bin", "take_note",
    "read_notes", "clear_notes", "get_notes", "set_timer", "cancel_timer",
    "open_downloads", "open_documents", "open_desktop_folder", "open_pictures",
    "open_music", "open_videos", "open_this_pc", "open_recycle_bin",
    "open_file_explorer", "open_control_panel", "open_task_manager",
    "open_display_settings", "open_sound_settings", "open_bluetooth_settings",
    "open_network_settings", "open_update_settings", "open_apps_settings",
    "open_personalization_settings", "open_privacy_settings", "open_storage_settings",
    "open_power_settings", "open_about_settings", "get_current_time",
    "get_current_date", "get_battery_status", "get_cpu_usage", "get_ram_usage",
    "get_disk_usage", "get_uptime", "get_local_ip", "get_system_summary",
    "SIMPLE_ACTIONS",
]
