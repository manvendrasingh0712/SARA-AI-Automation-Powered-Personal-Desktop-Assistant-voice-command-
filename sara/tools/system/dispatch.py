"""
sara.tools.system.dispatch
SIMPLE_ACTIONS -- a name -> zero-arg-callable lookup table, used by the
intent router for actions that need no arguments (e.g. "lock my pc").
Kept as its own file since it necessarily imports from every category
module in this package.
"""
from typing import Callable, Dict

from .power import (
    lock_pc, sleep_system, hibernate_system, log_off, shutdown_system,
    restart_system, cancel_shutdown,
)
from .audio_display import set_volume, adjust_brightness, set_brightness, get_brightness_status
from .window_mgmt import (
    show_desktop, minimize_all_windows, restore_windows, maximize_active_window,
    minimize_active_window, close_active_window, snap_window_left,
    snap_window_right, switch_window,
)
from .media_keys import play_pause_media, next_track, previous_track, stop_media
from .shortcuts import (
    copy_selection, paste_clipboard, select_all, undo, redo, new_tab, close_tab,
    next_tab, prev_tab, reload_page, zoom_in, zoom_out, zoom_reset, scroll_up,
    scroll_down, scroll_top, scroll_bottom,
)
from .connectivity import wifi_on, wifi_off, bluetooth_on, bluetooth_off, dark_mode, light_mode
from .files_notes import empty_recycle_bin, read_notes, clear_notes
from .folders import (
    open_downloads, open_documents, open_desktop_folder, open_pictures,
    open_music, open_videos, open_this_pc, open_recycle_bin,
    open_file_explorer, open_control_panel, open_task_manager,
)
from .settings_pages import (
    open_display_settings, open_sound_settings, open_bluetooth_settings,
    open_network_settings, open_update_settings, open_apps_settings,
    open_personalization_settings, open_privacy_settings, open_storage_settings,
    open_power_settings, open_about_settings,
)
from .system_info import get_disk_usage, get_uptime, get_local_ip
from .timers import cancel_timer

# ============================================================
# SIMPLE ACTIONS DISPATCH TABLE
# ============================================================
SIMPLE_ACTIONS: Dict[str, Callable[[], str]] = {
    # Power & session
    "lock_pc": lock_pc,
    "sleep_system": sleep_system,
    "hibernate_system": hibernate_system,
    "log_off": log_off,
    "shutdown_system": shutdown_system,
    "restart_system": restart_system,
    "cancel_shutdown": cancel_shutdown,
    # Volume (extremes only — numeric % goes through set_volume in gui_main)
    "max_volume": lambda: set_volume(100),
    "min_volume": lambda: set_volume(0),
    # Brightness
    "increase_brightness": lambda: adjust_brightness(10),
    "decrease_brightness": lambda: adjust_brightness(-10),
    "max_brightness": lambda: set_brightness(100),
    "min_brightness": lambda: set_brightness(10),
    "get_brightness_status": get_brightness_status,
    # Window management
    "show_desktop": show_desktop,
    "minimize_all_windows": minimize_all_windows,
    "restore_windows": restore_windows,
    "maximize_active_window": maximize_active_window,
    "minimize_active_window": minimize_active_window,
    "close_active_window": close_active_window,
    "snap_window_left": snap_window_left,
    "snap_window_right": snap_window_right,
    "switch_window": switch_window,
    # Media
    "play_pause_media": play_pause_media,
    "next_track": next_track,
    "previous_track": previous_track,
    "stop_media": stop_media,
    # Keyboard
    "copy_selection": copy_selection,
    "paste_clipboard": paste_clipboard,
    "select_all": select_all,
    "undo": undo,
    "redo": redo,
    # Browser tabs
    "new_tab": new_tab,
    "close_tab": close_tab,
    "next_tab": next_tab,
    "prev_tab": prev_tab,
    "reload_page": reload_page,
    # Zoom & scroll
    "zoom_in": zoom_in,
    "zoom_out": zoom_out,
    "zoom_reset": zoom_reset,
    "scroll_up": scroll_up,
    "scroll_down": scroll_down,
    "scroll_top": scroll_top,
    "scroll_bottom": scroll_bottom,
    # Network
    "wifi_on": wifi_on,
    "wifi_off": wifi_off,
    "bluetooth_on": bluetooth_on,
    "bluetooth_off": bluetooth_off,
    # Display
    "dark_mode": dark_mode,
    "light_mode": light_mode,
    # File ops
    "empty_recycle_bin": empty_recycle_bin,
    "read_notes": read_notes,
    "clear_notes": clear_notes,
    # Folders
    "open_downloads": open_downloads,
    "open_documents": open_documents,
    "open_desktop_folder": open_desktop_folder,
    "open_pictures": open_pictures,
    "open_music": open_music,
    "open_videos": open_videos,
    "open_this_pc": open_this_pc,
    "open_recycle_bin": open_recycle_bin,
    "open_file_explorer": open_file_explorer,
    "open_control_panel": open_control_panel,
    "open_task_manager": open_task_manager,
    # Windows Settings pages
    "open_display_settings": open_display_settings,
    "open_sound_settings": open_sound_settings,
    "open_bluetooth_settings": open_bluetooth_settings,
    "open_network_settings": open_network_settings,
    "open_update_settings": open_update_settings,
    "open_apps_settings": open_apps_settings,
    "open_personalization_settings": open_personalization_settings,
    "open_privacy_settings": open_privacy_settings,
    "open_storage_settings": open_storage_settings,
    "open_power_settings": open_power_settings,
    "open_about_settings": open_about_settings,
    # System info
    "disk_usage": lambda: get_disk_usage("C:\\"),
    "uptime": get_uptime,
    "local_ip": get_local_ip,
    # Timer cancel (set_timer needs args, handled in gui_main)
    "cancel_timer": cancel_timer,
}
