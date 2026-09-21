/* ==========================================================================
   routine-actions.js -- DATA ONLY: the list of one-tap actions a routine step can run.
   Mirrors the keys of dispatch.py's SIMPLE_ACTIONS exactly (copied from the old app.js). The backend
   (routines_api.py -> _validate_routine_steps) is the source of truth: a key that no longer exists there
   is rejected with a clear error at Save time, so a stale entry here can never silently do nothing.
   Used by: js/routine-builder.js, js/automation.js (SARA.routineActions.{groups,labels}).
   ========================================================================== */
(function () {
  'use strict';
var GROUPS = [
  { label: 'Power & Session', keys: ['lock_pc', 'sleep_system', 'hibernate_system', 'log_off', 'shutdown_system', 'restart_system', 'cancel_shutdown'] },
  { label: 'Volume', keys: ['max_volume', 'min_volume'] },
  { label: 'Brightness', keys: ['increase_brightness', 'decrease_brightness', 'max_brightness', 'min_brightness', 'get_brightness_status'] },
  { label: 'Window Management', keys: ['show_desktop', 'minimize_all_windows', 'restore_windows', 'maximize_active_window', 'minimize_active_window', 'close_active_window', 'snap_window_left', 'snap_window_right', 'switch_window', 'toggle_fullscreen'] },
  { label: 'Media', keys: ['play_pause_media', 'next_track', 'previous_track', 'stop_media'] },
  { label: 'Keyboard', keys: ['copy_selection', 'paste_clipboard', 'select_all', 'undo', 'redo'] },
  { label: 'Browser Tabs', keys: ['new_tab', 'close_tab', 'next_tab', 'prev_tab', 'reload_page'] },
  { label: 'Zoom & Scroll', keys: ['zoom_in', 'zoom_out', 'zoom_reset', 'scroll_up', 'scroll_down', 'scroll_top', 'scroll_bottom'] },
  { label: 'Network', keys: ['wifi_on', 'wifi_off', 'bluetooth_on', 'bluetooth_off'] },
  { label: 'Display', keys: ['dark_mode', 'light_mode'] },
  { label: 'Files & Notes', keys: ['empty_recycle_bin', 'read_notes', 'clear_notes'] },
  { label: 'Folders', keys: ['open_downloads', 'open_documents', 'open_desktop_folder', 'open_pictures', 'open_music', 'open_videos', 'open_this_pc', 'open_recycle_bin', 'open_file_explorer', 'open_control_panel', 'open_task_manager'] },
  { label: 'Windows Settings', keys: ['open_display_settings', 'open_sound_settings', 'open_bluetooth_settings', 'open_network_settings', 'open_update_settings', 'open_apps_settings', 'open_personalization_settings', 'open_privacy_settings', 'open_storage_settings', 'open_power_settings', 'open_about_settings', 'open_nightlight_settings', 'open_airplane_mode_settings'] },
  { label: 'System Info', keys: ['disk_usage', 'uptime', 'local_ip', 'gpu_status', 'temperature_status', 'process_list', 'list_services'] },
  { label: 'Timer', keys: ['cancel_timer'] },
];
var LABELS = {
  lock_pc: 'Lock PC', sleep_system: 'Sleep', hibernate_system: 'Hibernate', log_off: 'Log Off',
  shutdown_system: 'Shutdown', restart_system: 'Restart', cancel_shutdown: 'Cancel Shutdown',
  max_volume: 'Max Volume', min_volume: 'Mute Volume',
  increase_brightness: 'Increase Brightness', decrease_brightness: 'Decrease Brightness',
  max_brightness: 'Max Brightness', min_brightness: 'Min Brightness', get_brightness_status: 'Brightness Status',
  show_desktop: 'Show Desktop', minimize_all_windows: 'Minimize All Windows', restore_windows: 'Restore Windows',
  maximize_active_window: 'Maximize Active Window', minimize_active_window: 'Minimize Active Window',
  close_active_window: 'Close Active Window', snap_window_left: 'Snap Window Left', snap_window_right: 'Snap Window Right',
  switch_window: 'Switch Window', toggle_fullscreen: 'Toggle Fullscreen',
  play_pause_media: 'Play/Pause Media', next_track: 'Next Track', previous_track: 'Previous Track', stop_media: 'Stop Media',
  copy_selection: 'Copy', paste_clipboard: 'Paste', select_all: 'Select All', undo: 'Undo', redo: 'Redo',
  new_tab: 'New Tab', close_tab: 'Close Tab', next_tab: 'Next Tab', prev_tab: 'Previous Tab', reload_page: 'Reload Page',
  zoom_in: 'Zoom In', zoom_out: 'Zoom Out', zoom_reset: 'Reset Zoom', scroll_up: 'Scroll Up', scroll_down: 'Scroll Down',
  scroll_top: 'Scroll to Top', scroll_bottom: 'Scroll to Bottom',
  wifi_on: 'Wi-Fi On', wifi_off: 'Wi-Fi Off', bluetooth_on: 'Bluetooth On', bluetooth_off: 'Bluetooth Off',
  dark_mode: 'Dark Mode', light_mode: 'Light Mode',
  empty_recycle_bin: 'Empty Recycle Bin', read_notes: 'Read Notes', clear_notes: 'Clear Notes',
  open_downloads: 'Open Downloads', open_documents: 'Open Documents', open_desktop_folder: 'Open Desktop',
  open_pictures: 'Open Pictures', open_music: 'Open Music', open_videos: 'Open Videos', open_this_pc: 'Open This PC',
  open_recycle_bin: 'Open Recycle Bin', open_file_explorer: 'Open File Explorer', open_control_panel: 'Open Control Panel',
  open_task_manager: 'Open Task Manager',
  open_display_settings: 'Open Display Settings', open_sound_settings: 'Open Sound Settings',
  open_bluetooth_settings: 'Open Bluetooth Settings', open_network_settings: 'Open Network Settings',
  open_update_settings: 'Open Update Settings', open_apps_settings: 'Open Apps Settings',
  open_personalization_settings: 'Open Personalization Settings', open_privacy_settings: 'Open Privacy Settings',
  open_storage_settings: 'Open Storage Settings', open_power_settings: 'Open Power Settings',
  open_about_settings: 'Open About Settings', open_nightlight_settings: 'Open Night Light Settings',
  open_airplane_mode_settings: 'Open Airplane Mode Settings',
  disk_usage: 'Disk Usage', uptime: 'System Uptime', local_ip: 'Local IP Address', gpu_status: 'GPU Status',
  temperature_status: 'Temperature Status', process_list: 'Process List', list_services: 'Running Services',
  cancel_timer: 'Cancel Timer',
};
  window.SARA.routineActions = { groups: GROUPS, labels: LABELS };
})();
