"""
sara.gui.app.media
ApiMediaMixin -- media-player status/controls surfaced to the GUI's media widget.
"""
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor


# ── module-level session identity cache: keeps the media widget's
#    transport commands (play/pause/next/prev/seek/shuffle/repeat/stop)
#    operating on the SAME Windows media session across calls instead of
#    re-picking (and potentially landing on a different app) every time.
#    Keyed by source_app_user_model_id, the safest stable identifier the
#    WinRT session object exposes. ─────────────────────────────────────
_session_cache = {"session": None, "app_id": None, "title": None, "artist": None}

# ── album art cache: avoids re-reading + re-base64-encoding identical
#    artwork on every ~2s poll tick. Keyed by track identity; only
#    cleared when the identity actually changes. ────────────────────────
_art_cache = {"key": None, "data": None}


# ── module-level helpers (no winsdk import at module scope so this file
#    still imports cleanly on machines without winsdk installed) ──────
def _repeat_mode_to_str(mode):
    """MediaPlaybackAutoRepeatMode -> 'none' | 'track' | 'list'. `mode` is
    a nullable WinRT enum (many apps never set it), so anything we don't
    recognise just becomes 'none'."""
    if mode is None:
        return "none"
    try:
        val = int(mode)
    except (TypeError, ValueError):
        return "none"
    return {0: "none", 1: "track", 2: "list"}.get(val, "none")


def _sessions_list(mgr):
    """mgr.get_sessions() returns a WinRT vector view; normal iteration
    works on most winsdk builds but not all, so fall back to indexed
    access if a plain list() fails."""
    raw = mgr.get_sessions()
    try:
        return list(raw)
    except TypeError:
        return [raw.get_at(i) for i in range(raw.size)]


async def _pick_active_session(mgr):
    """
    THE FIX for "background music not detected": mgr.get_current_session()
    returns whichever app last *touched* its transport controls -- not
    whichever one is actually making sound right now. A track quietly
    playing in a minimized Spotify window or an unfocused browser tab is
    routinely NOT "current" by that definition, so the old code reported
    "Nothing playing" even while audio was clearly running.

    Scanning every registered session and preferring one whose
    playback_status is literally "Playing" (4) fixes this regardless of
    which app it is or whether its window has focus -- this is exactly
    how Spotify Connect / OS "Now Playing" widgets do it.

    SESSION STABILITY: once a session is picked, its
    source_app_user_model_id is cached and reused as long as that same
    app still has a live, queryable session -- so a run of related
    commands (play/pause, next, prev, seek, shuffle, repeat, stop) all
    land on the same app instead of silently jumping to a different one
    that happens to report "Playing" on a later call. The cache is only
    dropped once the cached app's session actually disappears, at which
    point discovery runs fresh and the cache is repopulated.
    """
    try:
        sessions = _sessions_list(mgr)
    except Exception:
        sessions = []

    cached_id = _session_cache.get("app_id")
    if cached_id:
        for s in sessions:
            try:
                if (getattr(s, "source_app_user_model_id", None) or "") == cached_id:
                    s.get_playback_info()  # confirm it's still alive/queryable
                    _session_cache["session"] = s
                    return s
            except Exception:
                continue
        # Cached app's session is gone -- invalidate and fall through to
        # a fresh discovery below.
        _session_cache["session"] = None
        _session_cache["app_id"] = None

    picked = None
    for s in sessions:
        try:
            pb = s.get_playback_info()
            if pb and int(pb.playback_status) == 4:  # Playing
                picked = s
                break
        except Exception:
            continue

    if picked is None:
        # Nothing is actively playing -- fall back to Windows' notion of
        # "current" (covers the paused-but-selected case), else just the
        # first session so the card still shows something instead of
        # nothing.
        try:
            current = mgr.get_current_session()
            if current is not None:
                picked = current
        except Exception:
            pass
        if picked is None and sessions:
            picked = sessions[0]

    if picked is not None:
        try:
            _session_cache["app_id"] = getattr(picked, "source_app_user_model_id", None) or None
        except Exception:
            _session_cache["app_id"] = None
        _session_cache["session"] = picked
    return picked


async def _extract_album_art(props):
    """
    Pulls the current track's cover art off its RandomAccessStreamReference
    and returns it as a ready-to-use `data:` URI. Best-effort only -- any
    failure (no thumbnail, unsupported app, stream-read error, older
    winsdk without this API) just means no art; it never breaks the rest
    of the status payload.
    """
    try:
        thumb_ref = getattr(props, "thumbnail", None)
        if thumb_ref is None:
            return None
        stream = await thumb_ref.open_read_async()
        size = int(getattr(stream, "size", 0) or 0)
        if size <= 0:
            return None
        from winsdk.windows.storage.streams import DataReader
        reader = DataReader(stream)
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)
        mime = getattr(stream, "content_type", None) or "image/jpeg"
        b64 = base64.b64encode(bytes(buf)).decode("ascii")
        # DEBUG (Task 1, item 1): confirms the art genuinely arrived from
        # WinRT before it's ever handed to the frontend. Remove once the
        # theming bug is confirmed fixed.
        print(f"[art] {len(b64)} bytes, mime={mime}")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"[album art skipped] {e}")
        return None


_FRIENDLY_APP_NAMES = {
    "spotify": "Spotify",
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "vlc": "VLC",
    "wmplayer": "Windows Media Player",
    "groove": "Groove Music",
    "itunes": "iTunes",
}


def _friendly_app_name(aumid):
    if not aumid:
        return ""
    low = aumid.lower()
    for key, label in _FRIENDLY_APP_NAMES.items():
        if key in low:
            return label
    return ""


# ── Volume control: see the long comment on ApiMediaMixin.get_media_volume
#    below for why this exists as a *separate* pycaw path instead of an
#    SMTC call. ───────────────────────────────────────────────────────────
def _process_name_from_aumid(aumid):
    """
    Best-effort mapping from an SMTC source_app_user_model_id to the
    Windows process name pycaw/psutil expect (e.g. 'Spotify.exe').

    Win32 desktop apps (Spotify, VLC, foobar2000, most browsers) publish
    their aumid as the exe's own path/name, so stripping any path and
    ensuring a '.exe' suffix is normally enough.

    Desktop-Bridge / MSIX-packaged apps -- Spotify's current Microsoft
    Store build included -- publish a UWP-STYLE aumid instead:
    "PackageFamilyName!AppId" (e.g.
    "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"). The real Win32
    process behind these is named after the AppId, not the package
    family name -- Core Audio reports it as plain "Spotify.exe" -- so the
    "!" must be split off FIRST, or the whole package family name gets
    appended with ".exe" and never matches anything (confirmed in the
    field: this was the actual reason Problem 1's volume writes were
    silently no-op'ing -- get_media_volume kept failing to match, which
    is what disabled the slider on the frontend).

    Genuine (non-Desktop-Bridge) UWP apps, where the AppId doesn't
    correspond to any real process name at all, still won't resolve --
    that remains a known, expected limitation (see callers below, which
    all treat "no matching session" as normal and not an error).
    """
    if not aumid:
        return None
    base = aumid.rsplit("!", 1)[-1]
    base = base.replace("/", "\\").rsplit("\\", 1)[-1]
    if not base:
        return None
    if not base.lower().endswith(".exe"):
        base += ".exe"
    return base


# ── Tier 1 helper: real per-process AUMID via GetApplicationUserModelId
#    (kernel32.dll). This is a PLAIN WIN32 API -- not COM -- so unlike
#    everything pycaw/comtypes-related in this file, it does NOT need to
#    run on `_com_executor` / `_run_on_com_thread`; it has no apartment
#    affinity and no STA/MTA requirement, and OpenProcess/CloseHandle are
#    safe to call from any thread. It's kept as a small, self-contained
#    module-level function anyway so a caller on the COM thread or off it
#    can use it identically. ────────────────────────────────────────────
def _real_aumid_for_pid(pid):
    """
    Returns the AUMID a running process explicitly registered for itself
    (via SetCurrentProcessExplicitAppUserModelID) -- the same identifier
    SMTC reports as source_app_user_model_id -- or None.

    This generalizes Tier 1 matching to EVERY MSIX/UWP/Desktop-Bridge app
    (not just Spotify's Microsoft Store build, which the old
    "!"-splitting logic in _process_name_from_aumid special-cased): if
    two independently-obtained strings (SMTC's aumid and this function's
    result) are byte-for-byte equal, that's an exact identity match with
    no parsing or guessing involved.

    Classic Win32 apps that never call
    SetCurrentProcessExplicitAppUserModelID -- VLC, Windows Media Player,
    non-Store Spotify, and (per Microsoft's own docs) Chromium browsers,
    which set the per-*tab* aumid only on the SMTC side, not on their own
    process -- will not have one; this returns None for them, which is
    expected and not an error. Tier 2 (_process_name_from_aumid, below)
    and Tier 3 (_session_display_name, below) exist specifically to cover
    those cases.
    """
    if not pid:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            length = wintypes.UINT(0)
            # First call with a NULL buffer just to learn the required
            # length (standard Win32 two-call pattern for this API); a
            # process with no registered AUMID reports length 0 here and
            # nothing further needs calling.
            kernel32.GetApplicationUserModelId(handle, ctypes.byref(length), None)
            if not length.value:
                return None
            buf = ctypes.create_unicode_buffer(length.value)
            res = kernel32.GetApplicationUserModelId(handle, ctypes.byref(length), buf)
            if res != 0:  # non-zero = error (e.g. APPMODEL_ERROR_NO_APPLICATION)
                return None
            return buf.value or None
        finally:
            kernel32.CloseHandle(handle)
    except Exception as e:
        # DEBUG (Task 1, Tier 1): remove once this is confirmed reliable
        # across VLC/WMP/Chrome/Edge/Spotify in the field.
        print(f"[aumid-tier1] GetApplicationUserModelId failed for pid={pid}: {e}")
        return None


# ── Tier 3 helper: Core Audio session display name, for disambiguating
#    MULTIPLE sessions owned by the same browser process (one per audible
#    tab -- this is exactly what Windows' own Volume Mixer shows as
#    separate sliders for separate tabs). Uses the session's raw
#    IAudioSessionControl2 pointer, which pycaw exposes as `_ctl` --
#    IAudioSessionControl2 extends IAudioSessionControl, which is where
#    GetDisplayName() itself is actually declared. ───────────────────────
def _session_display_name(session):
    """
    Best-effort read of a Core Audio session's display name (GetDisplayName).
    Returns '' (never raises) if the underlying `_ctl` isn't exposed by
    the installed pycaw version, or if the app never called
    SetDisplayName -- both are normal, not errors; Tier 3 just skips a
    session it can't get a name for.
    """
    ctl = getattr(session, "_ctl", None)
    if ctl is None:
        return ""
    try:
        name = ctl.GetDisplayName()
        return name or ""
    except Exception as e:
        # DEBUG (Task 1, Tier 3): remove once confirmed what Chrome/Edge
        # actually populate this with (page title vs domain vs nothing).
        print(f"[display-name] GetDisplayName failed: {e}")
        return ""


_BROWSER_PROCESS_NAMES = {"chrome.exe", "msedge.exe", "msedgewebview2.exe", "firefox.exe"}


def _pycaw_session_for(aumid, title=None, artist=None):
    """
    TIERED session resolution -- order matters, first match wins. This
    replaces the old single-strategy (exe-name-only) matcher, which only
    ever worked for classic Win32 apps and Spotify's Store build (via the
    "!" split in _process_name_from_aumid) and had no way at all to
    handle a browser process owning several simultaneous Core Audio
    sessions (one per playing tab).

    Tier 1 -- real per-process AUMID (_real_aumid_for_pid): generalizes
    to ALL MSIX/UWP/Desktop-Bridge apps, not just Spotify, via an exact
    string match with zero parsing.

    Tier 2 -- the ORIGINAL exe-name fallback (_process_name_from_aumid),
    UNCHANGED: still what resolves classic Win32 apps (VLC, WMP, non-
    Store Spotify) that never registered an AUMID. If exactly one Core
    Audio session has that process name, it's used directly, exactly as
    before. If MORE than one does (this is new: previously the first
    match by iteration order just silently won, which is wrong for
    multi-tab browsers), it falls through to Tier 3 instead of guessing.

    Tier 3 -- browser-tab display-name disambiguation
    (_session_display_name): only engaged when Tier 2 found a browser
    process (chrome.exe/msedge.exe/msedgewebview2.exe/firefox.exe) with
    MULTIPLE candidate sessions. Picks whichever candidate's display name
    best overlaps with the currently-playing SMTC title/artist. If
    nothing scores a match, this returns None rather than guess --
    leaving a session's volume untouched is preferred over changing the
    wrong tab's.
    """
    from pycaw.pycaw import AudioUtilities
    try:
        sessions = list(AudioUtilities.GetAllSessions())
    except Exception as e:
        print(f"[volume-match] GetAllSessions failed: {e}")
        return None

    # Collect PID / process name / display name for every session ONCE up
    # front -- used below by every tier, and also gives a single readable
    # debug line with everything Core Audio currently reports (Task 1,
    # item 1's requested extended debug helper).
    rows = []
    for session in sessions:
        pid, name = None, None
        try:
            proc = session.Process
            if proc is not None:
                name, pid = proc.name(), proc.pid
        except Exception:
            pass
        rows.append((session, pid, name, _session_display_name(session)))

    print(f"[volume-match] aumid={aumid!r} title={title!r} artist={artist!r} "
          f"sessions_seen={[(pid, name, disp) for _, pid, name, disp in rows]}")

    # ---- Tier 1: exact real-AUMID match -----------------------------
    if aumid:
        for session, pid, name, disp in rows:
            real_aumid = _real_aumid_for_pid(pid)
            if real_aumid and real_aumid == aumid:
                print(f"[volume-match] TIER1 real-aumid match pid={pid} proc={name!r} real_aumid={real_aumid!r}")
                return session

    # ---- Tier 2: exe-name fallback (original logic, unchanged) ------
    proc_name = _process_name_from_aumid(aumid)
    tier2_candidates = []
    if proc_name:
        for session, pid, name, disp in rows:
            if name is not None and name.lower() == proc_name.lower():
                tier2_candidates.append((session, pid, name, disp))

    if len(tier2_candidates) == 1:
        session, pid, name, disp = tier2_candidates[0]
        print(f"[volume-match] TIER2 exe-name match aumid={aumid!r} -> proc_name={proc_name!r} pid={pid} MATCHED")
        return session

    # ---- Tier 3: browser-tab display-name disambiguation ------------
    # Reached either because Tier 2 found >1 same-named session (the
    # classic "multiple browser tabs" case) or 0 (proc_name didn't match
    # anything by name, but the process might still be a known browser
    # under a name variant) -- so also widen to any row whose process
    # name is a known browser, not just the tier2_candidates list.
    candidates = tier2_candidates if len(tier2_candidates) > 1 else [
        (session, pid, name, disp) for session, pid, name, disp in rows
        if name and name.lower() in _BROWSER_PROCESS_NAMES
    ]
    if candidates and (title or artist):
        needles = [p.lower() for p in (title, artist) if p]
        best, best_session_info, best_score = None, None, 0
        for session, pid, name, disp in candidates:
            if not disp:
                continue
            hay = disp.lower()
            score = sum(1 for n in needles if n and n in hay)
            if score > best_score:
                best, best_session_info, best_score = session, (pid, name, disp), score
        if best is not None:
            print(f"[volume-match] TIER3 display-name match pid={best_session_info[0]} "
                  f"proc={best_session_info[1]!r} display_name={best_session_info[2]!r} score={best_score}")
            return best

    if len(tier2_candidates) > 1:
        # Ambiguous same-process-name match that Tier 3 couldn't resolve
        # (no display names available, or none overlapped the current
        # title/artist) -- refuse rather than risk moving the wrong tab's
        # volume. See the module docstring above for why "no match" beats
        # a wrong guess here.
        print(f"[volume-match] aumid={aumid!r} -> proc_name={proc_name!r} AMBIGUOUS "
              f"({len(tier2_candidates)} same-named sessions, Tier 3 could not disambiguate) NO MATCH")
        return None

    print(f"[volume-match] aumid={aumid!r} -> proc_name={proc_name!r} NO MATCH "
          f"(sessions seen: {[name for _, _, name, _ in rows]})")
    return None


# ── Dedicated COM thread for pycaw (Task 2 crash fix) ───────────────────
# Root cause (confirmed against known pycaw/comtypes issues -- e.g.
# AndreMiras/pycaw#1, #19, #52 all report this exact "access violation
# writing 0x...' inside comtypes' _compointer_base.__del__ -> Release()):
# pycaw's COM interface pointers (IAudioSessionControl2, ISimpleAudioVolume)
# are apartment-threaded (STA) -- they may only be created, called, AND
# released on the SAME OS thread that had CoInitialize() run on it.
#
# pywebview's JS-bridge does not guarantee get_media_volume/set_media_volume
# always land on the same thread, and this file's OTHER media calls
# (get_media_status, toggle_shuffle, ...) already run winsdk/WinRT async
# calls via a fresh asyncio.run() on whatever thread they're called from --
# winsdk initialises its own (MTA) COM context per call. If a
# get_media_volume/set_media_volume call then landed on a thread that had
# already been touched by one of those WinRT calls, comtypes' pycaw session
# ends up created against a mismatched apartment. The calls themselves
# (SetMasterVolume/GetMasterVolume) can silently no-op against a misrouted
# proxy -- this is Problem 1 -- and later, when Python's refcounting drops
# that proxy (often after the apartment/thread context has already moved
# on), Release() corrupts memory -- this is Problem 2's crash.
#
# Fix: route every pycaw/comtypes call through ONE persistent worker thread
# that calls comtypes.CoInitialize() exactly once, the first time it's
# used, and never anything else. Every session object pycaw hands back is
# created, used, and explicitly released (`del`) inside the same function
# that runs entirely on that thread -- so creation, use, and release always
# happen in the same, correctly-initialised apartment.
_com_executor = None
_com_executor_lock = threading.Lock()


def _com_thread_init():
    import comtypes
    comtypes.CoInitialize()


def _get_com_executor():
    global _com_executor
    with _com_executor_lock:
        if _com_executor is None:
            ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pycaw-com")
            ex.submit(_com_thread_init).result()
            _com_executor = ex
    return _com_executor


def _pycaw_master_volume_interface():
    """
    Module-level (not a method) because it's called bare, unqualified,
    from inside the `_work()` closures defined in get_master_volume /
    set_master_volume / toggle_master_mute below -- those closures run
    entirely on the dedicated COM thread via _run_on_com_thread, and need
    this to resolve as a plain module-scope name, not self.<method>.
    """
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


def _run_on_com_thread(fn):
    """Runs fn() on the dedicated single COM thread and returns its
    result (or re-raises whatever it raised) on the calling thread.
    `fn` must create, use, AND `del`ete any pycaw/comtypes objects
    entirely within its own body -- nothing COM-related should be
    returned from or held onto outside of it."""
    return _get_com_executor().submit(fn).result()


class ApiMediaMixin:

    # ── Mini music player: real OS media session (Windows SMTC) ───────
    # Requires: pip install winsdk
    # Works regardless of WHICH app is actually playing (Spotify desktop,
    # a YouTube tab in Chrome, VLC, etc.) since it reads the OS-level
    # "Now Playing" session instead of guessing at any one app's internals.
    def get_media_status(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _fetch():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return {"ok": True, "active": False}
                props = await session.try_get_media_properties_async()
                pb = session.get_playback_info()
                tl = session.get_timeline_properties()
                if props is None or pb is None or tl is None:
                    return {"ok": True, "active": False}

                status_map = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
                controls = getattr(pb, "controls", None)
                shuffle_active = getattr(pb, "is_shuffle_active", None)
                source_app = getattr(session, "source_app_user_model_id", "") or ""

                def _safe_seconds(val, fallback=0.0):
                    try:
                        secs = val.total_seconds() if val else fallback
                    except Exception:
                        return fallback
                    if secs != secs or secs in (float("inf"), float("-inf")):  # NaN/Infinity
                        return fallback
                    return max(0.0, secs)

                position_sec = _safe_seconds(getattr(tl, "position", None))
                duration_sec = _safe_seconds(getattr(tl, "end_time", None))
                if duration_sec > 0 and position_sec > duration_sec:
                    position_sec = duration_sec
                min_seek_sec = _safe_seconds(getattr(tl, "min_seek_time", None))
                max_seek_sec = _safe_seconds(getattr(tl, "max_seek_time", None), duration_sec)
                if max_seek_sec <= 0:
                    max_seek_sec = duration_sec

                rate = getattr(tl, "playback_rate", None)
                try:
                    playback_rate = float(rate) if rate is not None else 1.0
                    if playback_rate != playback_rate or playback_rate <= 0:  # NaN or invalid
                        playback_rate = 1.0
                except (TypeError, ValueError):
                    playback_rate = 1.0

                # BUG FIX (seek bar snapping back ~every 3s): this must be the
                # time the WinRT `position` above was ACTUALLY last updated,
                # not "now". Many apps (Spotify especially) don't push fresh
                # timeline updates every poll, so `position` is often already
                # stale by the time we read it -- stamping it with time.time()
                # told the frontend "this position was accurate just now",
                # which made its smooth interpolation drift ahead and then
                # snap back to the real (stale) value on the next poll.
                # `tl.last_updated_time` is WinRT's own answer to exactly this
                # (a timezone-aware datetime per the winsdk DateTime
                # projection); convert it to Unix-epoch seconds. Fall back to
                # time.time() only if the property is missing/unreadable.
                last_updated = getattr(tl, "last_updated_time", None)
                try:
                    timeline_updated_at = last_updated.timestamp() if last_updated is not None else time.time()
                except Exception:
                    timeline_updated_at = time.time()

                art_key = (props.title or "", props.artist or "", props.album_title or "", source_app)
                if _art_cache.get("key") == art_key:
                    art = _art_cache.get("data")
                else:
                    art = await _extract_album_art(props)
                    _art_cache["key"] = art_key
                    _art_cache["data"] = art

                # Cache the current title/artist alongside the session
                # identity cache above -- Tier 3 of _pycaw_session_for
                # (browser-tab display-name disambiguation) needs
                # something to match a Core Audio session's display name
                # against, and get_media_volume/set_media_volume have no
                # other route to "what's currently playing" (they're
                # called from the frontend with no track info attached).
                _session_cache["title"] = props.title or None
                _session_cache["artist"] = props.artist or None

                return {
                    "ok": True,
                    "active": True,
                    "title": props.title or "Unknown Track",
                    "artist": props.artist or "",
                    "album": props.album_title or "",
                    "art": art,
                    "app": _friendly_app_name(source_app),
                    "status": status_map.get(int(pb.playback_status), "unknown"),
                    "position_sec": position_sec,
                    "duration_sec": duration_sec,
                    "min_seek_sec": min_seek_sec,
                    "max_seek_sec": max_seek_sec,
                    "playback_rate": playback_rate,
                    "timeline_updated_at": timeline_updated_at,
                    "shuffle": bool(shuffle_active) if shuffle_active is not None else False,
                    "shuffle_supported": shuffle_active is not None,
                    "repeat": _repeat_mode_to_str(getattr(pb, "auto_repeat_mode", None)),
                    "caps": {
                        "can_next": bool(getattr(controls, "is_next_enabled", True)) if controls else True,
                        "can_prev": bool(getattr(controls, "is_previous_enabled", True)) if controls else True,
                        "can_seek": bool(getattr(controls, "is_playback_position_enabled", True)) if controls else True,
                        "can_shuffle": bool(getattr(controls, "is_shuffle_enabled", True)) if controls else True,
                        "can_repeat": bool(getattr(controls, "is_repeat_enabled", True)) if controls else True,
                    },
                    "track_id": "|".join(art_key),
                }

            return asyncio.run(_fetch())
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[get_media_status error] {e}")
            return {"ok": False, "error": str(e)}

    # ── Multi-session switcher (Feature: control ANY currently-playing
    #    app, not just whichever one _pick_active_session auto-selected).
    #    _pick_active_session's "prefer anything Playing" heuristic, plus
    #    its own stickiness (once picked, it stays picked until that
    #    session disappears -- see the big comment on _pick_active_session
    #    above), means that if two apps are playing at once (Spotify
    #    desktop + a YouTube tab, say), the user previously had NO way to
    #    tell the widget to switch to the other one. These two methods
    #    expose every live session so the frontend can render a picker,
    #    and let the user explicitly re-point the SAME cache
    #    _pick_active_session already uses -- so a user's choice "sticks"
    #    for every subsequent transport/volume call exactly like an
    #    auto-picked session does, with no separate pinning flag needed. ──
    def list_media_sessions(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _fetch():
                mgr = await MediaManager.request_async()
                sessions = _sessions_list(mgr)
                cached_id = _session_cache.get("app_id")
                out = []
                for s in sessions:
                    try:
                        aumid = getattr(s, "source_app_user_model_id", "") or ""
                        if not aumid:
                            continue
                        pb = s.get_playback_info()
                        status = int(pb.playback_status) if pb else 0
                        props = await s.try_get_media_properties_async()
                        out.append({
                            "app_id": aumid,
                            "app_name": _friendly_app_name(aumid) or _process_name_from_aumid(aumid) or aumid,
                            "title": (props.title if props else "") or "",
                            "artist": (props.artist if props else "") or "",
                            "playing": status == 4,
                            "current": aumid == cached_id,
                        })
                    except Exception:
                        continue
                return out

            sessions = asyncio.run(_fetch())
            return {"ok": True, "sessions": sessions}
        except ImportError:
            return {
                "ok": False,
                "sessions": [],
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[list_media_sessions error] {e}")
            return {"ok": False, "sessions": []}

    def select_media_session(self, app_id):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                for s in _sessions_list(mgr):
                    try:
                        if (getattr(s, "source_app_user_model_id", None) or "") == app_id:
                            s.get_playback_info()  # confirm it's alive/queryable
                            return s
                    except Exception:
                        continue
                return None

            session = asyncio.run(_do())
            if session is None:
                return {"ok": False, "error": "That session is no longer available."}
            # Repoint the SAME cache _pick_active_session reads first --
            # this is what makes the choice sticky for every later call.
            _session_cache["session"] = session
            _session_cache["app_id"] = app_id
            return {"ok": True}
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[select_media_session error] {e}")
            return {"ok": False}

    # ── NEW FEATURE: per-session mute toggle (Task 4) ────────────────────
    # Lets the user silence ONE specific app's audio -- e.g. mute a noisy
    # background YouTube tab while Spotify keeps playing -- directly from
    # a session-switcher chip, WITHOUT first calling select_media_session
    # to make it "current". This is deliberately independent of
    # _session_cache (unlike get_media_volume/set_media_volume above,
    # which always act on whichever session is currently selected): it
    # takes an explicit app_id and resolves it fresh every call.
    #
    # Reuses the exact same tiered _pycaw_session_for() matching that was
    # just generalized above -- Tier 1 real-AUMID, Tier 2 exe-name, Tier 3
    # browser-tab display-name -- so this works correctly for a specific
    # browser TAB too, not just single-session apps, for the same reason
    # get_media_volume/set_media_volume now do.
    def toggle_session_mute(self, app_id):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _lookup_title_artist():
                # Tier 3 needs *something* to match a candidate session's
                # display name against; pull the current title/artist
                # straight from this specific app's own SMTC session
                # rather than the (possibly different) currently-selected
                # one in _session_cache.
                mgr = await MediaManager.request_async()
                for s in _sessions_list(mgr):
                    try:
                        if (getattr(s, "source_app_user_model_id", None) or "") != app_id:
                            continue
                        props = await s.try_get_media_properties_async()
                        title = (props.title if props else "") or None
                        artist = (props.artist if props else "") or None
                        return title, artist
                    except Exception:
                        continue
                return None, None

            title, artist = asyncio.run(_lookup_title_artist())

            def _work():
                session = _pycaw_session_for(app_id, title, artist)
                if session is None:
                    return None
                vol = session.SimpleAudioVolume
                new_state = not bool(vol.GetMute())
                vol.SetMute(new_state, None)
                # Re-query the CONFIRMED mute state rather than trusting
                # our own `new_state` guess -- same "confirm, don't
                # assume" pattern as everywhere else in this file (some
                # apps could theoretically ignore the request).
                confirmed = bool(vol.GetMute())
                del vol, session
                return confirmed

            confirmed = _run_on_com_thread(_work)
            if confirmed is None:
                return {"ok": False, "error": "No matching Windows audio session for this app yet."}
            print(f"[session-mute] app_id={app_id!r} muted={confirmed}")
            return {"ok": True, "muted": confirmed}
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk/pycaw not installed.",
            }
        except Exception as e:
            print(f"[toggle_session_mute error] {e}")
            return {"ok": False}

    def toggle_music_playback(self, playing):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return False
                if playing:
                    return await session.try_play_async()
                return await session.try_pause_async()

            ok = asyncio.run(_do())
            if ok:
                self._pref_writer.enqueue("music_playing", "1" if playing else "0")
            return {"ok": bool(ok)}
        except Exception as e:
            print(f"[toggle_music_playback error] {e}")
            return {"ok": False}

    def stop_music(self):
        smtc_stopped = False
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is not None:
                    return bool(await session.try_stop_async())
                return False

            smtc_stopped = asyncio.run(_do())
        except Exception as e:
            print(f"[stop_music SMTC error] {e}")
        try:
            # Only fall back to the broader system_tools stop (which is not
            # scoped to a single session) when the targeted SMTC stop above
            # didn't succeed -- avoids stopping unrelated media whenever the
            # intended session's own stop already worked.
            message = "Stopped." if smtc_stopped else (
                self.system_tools.stop_media() if self.system_tools else "Stopped."
            )
            _session_cache["session"] = None
            _session_cache["app_id"] = None
            self._pref_writer.enqueue("music_playing", "0")
            return {"ok": True, "message": message}
        except Exception as e:
            print(f"[stop_music error] {e}")
            return {"ok": False}

    # ── Mini music player: skip / seek (real SMTC calls) ────────────────
    def skip_next_track(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return False
                return await session.try_skip_next_async()

            ok = asyncio.run(_do())
            return {"ok": bool(ok)}
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[skip_next_track error] {e}")
            return {"ok": False}

    def skip_previous_track(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return False
                return await session.try_skip_previous_async()

            ok = asyncio.run(_do())
            return {"ok": bool(ok)}
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[skip_previous_track error] {e}")
            return {"ok": False}

    def seek_media(self, position_sec):
        """
        Seeks the current OS media session to `position_sec` seconds via
        SMTC's TryChangePlaybackPositionAsync, which expects a position in
        100-nanosecond ticks (Windows' native time unit) -- so the incoming
        seconds value (a float from the frontend's <input type=range>) is
        converted with `int(position_sec * 10_000_000)`.

        Not every app that publishes an SMTC session supports seeking
        (this depends entirely on the playing app); in that case the
        Windows Runtime call itself returns False, which is surfaced here
        as {"ok": False} rather than raising.
        """
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            position_sec = max(0.0, float(position_sec))
            ticks = int(position_sec * 10_000_000)

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return False
                return await session.try_change_playback_position_async(ticks)

            ok = asyncio.run(_do())
            return {"ok": bool(ok)}
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid seek position."}
        except Exception as e:
            print(f"[seek_media error] {e}")
            return {"ok": False}

    # ── Mini music player: shuffle / repeat (Spotify-style controls) ────
    def toggle_shuffle(self, enable):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return False, None, False
                pb_before = session.get_playback_info()
                shuffle_supported = getattr(pb_before, "is_shuffle_active", None) is not None
                ok = await session.try_change_shuffle_active_async(bool(enable))
                # Re-query so the returned state is the CONFIRMED value from
                # the session, never an assumption of what we asked for --
                # some apps silently ignore the request or report a
                # different value than requested.
                pb_after = session.get_playback_info()
                confirmed = getattr(pb_after, "is_shuffle_active", None)
                return ok, confirmed, shuffle_supported

            ok, confirmed, shuffle_supported = asyncio.run(_do())
            return {
                "ok": bool(ok),
                "shuffle": bool(confirmed) if confirmed is not None else None,
                "shuffle_supported": shuffle_supported,
            }
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[toggle_shuffle error] {e}")
            return {"ok": False}

    def cycle_repeat_mode(self):
        """
        Cycles Off -> Track -> List -> Off, mirroring Spotify's repeat
        button. Returns the new mode as a string so the frontend can
        update its icon immediately without waiting for the next poll.
        """
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )
            from winsdk.windows.media import MediaPlaybackAutoRepeatMode as RepeatMode

            order = ["none", "track", "list"]

            def _mode_enum(name):
                if name == "track":
                    return RepeatMode.TRACK
                if name == "list":
                    return RepeatMode.LIST
                return getattr(RepeatMode, "NONE", getattr(RepeatMode, "NONE_", 0))

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is None:
                    return None
                pb = session.get_playback_info()
                current = _repeat_mode_to_str(getattr(pb, "auto_repeat_mode", None)) if pb else "none"
                nxt = order[(order.index(current) + 1) % len(order)]
                ok = await session.try_change_auto_repeat_mode_async(_mode_enum(nxt))
                return nxt if ok else None

            result = asyncio.run(_do())
            return {"ok": result is not None, "mode": result}
        except ImportError:
            return {
                "ok": False,
                "error": "winsdk not installed. Run: pip install winsdk",
            }
        except Exception as e:
            print(f"[cycle_repeat_mode error] {e}")
            return {"ok": False}

    # ── Mini music player: per-app volume (Task 2) ──────────────────────
    # SMTC (GlobalSystemMediaTransportControlsSession / *PlaybackInfo /
    # *TimelineProperties) has NO volume property or method anywhere in its
    # API surface -- confirmed against the current WinRT reference (only
    # play/pause/stop/skip/seek/shuffle/repeat/channel-up-down exist).
    # `MediaTransportControls.IsVolumeEnabled` is a UWP XAML *control*
    # setting (shows/hides a slider in an app's own UI) -- not something a
    # third party can read or drive for someone else's session. So this
    # genuinely cannot be built as "one more SMTC call" like the other
    # transport methods above; it needs a different Windows API entirely.
    #
    # Windows' per-app volume mixer (Core Audio / WASAPI session volume,
    # `ISimpleAudioVolume`) is a separate, unrelated API that sets the
    # volume of one process's audio session independent of the system
    # volume and of every other app -- this is what `pycaw` wraps. It has
    # no knowledge of "now playing" sessions, so it's bridged to SMTC here
    # via the TIERED matching in _pycaw_session_for above: an exact real
    # AUMID match first (generalizes to every MSIX/UWP/Desktop-Bridge
    # app), then the original exe-process-name match (classic Win32 apps),
    # then -- for browser processes that can own several simultaneous
    # Core Audio sessions, one per audible tab -- a display-name match
    # against the currently-playing SMTC title/artist.
    #
    # NEW DEPENDENCY: this requires `pip install pycaw` (pulls in
    # `comtypes`) -- add both to requirements.txt; they are not used
    # anywhere else in this file.
    #
    # Known limitation: genuine (non-Desktop-Bridge) UWP apps whose AppId
    # doesn't correspond to any real process name, and that never
    # registered a real AUMID either, still won't resolve. Also, a
    # process only gets a Core Audio session once it has actually
    # rendered audio at least once in this run, so right after launch
    # (before the first sound) get_media_volume can legitimately report
    # "no session yet".
    # ── System (master) volume control (Feature) ─────────────────────────
    # Separate from get_media_volume/set_media_volume above: those control
    # ONE app's Core Audio session (ISimpleAudioVolume); this controls the
    # actual Windows output device's master volume (IAudioEndpointVolume,
    # via AudioUtilities.GetSpeakers()). Same pycaw/comtypes dependency as
    # Task 2 -- nothing new to install -- and routed through the SAME
    # dedicated single COM thread (_run_on_com_thread) for the identical
    # apartment-safety reasons explained above _com_executor. See the
    # module-level _pycaw_master_volume_interface() helper above the
    # class for the actual interface lookup.
    def get_master_volume(self):
        try:
            def _work():
                vol = _pycaw_master_volume_interface()
                result = (round(float(vol.GetMasterVolumeLevelScalar()), 3), bool(vol.GetMute()))
                del vol
                return result

            volume, muted = _run_on_com_thread(_work)
            return {"ok": True, "volume": volume, "muted": muted}
        except ImportError:
            return {
                "ok": False,
                "error": "pycaw not installed. Run: pip install pycaw",
            }
        except Exception as e:
            print(f"[get_master_volume error] {e}")
            return {"ok": False}

    def set_master_volume(self, level):
        try:
            level = max(0.0, min(1.0, float(level)))

            def _work():
                vol = _pycaw_master_volume_interface()
                vol.SetMasterVolumeLevelScalar(level, None)
                confirmed = round(float(vol.GetMasterVolumeLevelScalar()), 3)
                del vol
                return confirmed

            confirmed = _run_on_com_thread(_work)
            # DEBUG: same "confirm the write actually landed" pattern as
            # set_media_volume above -- remove once verified end-to-end.
            print(f"[set_master_volume] requested={level} confirmed_by_endpoint={confirmed}")
            return {"ok": True, "volume": confirmed}
        except ImportError:
            return {
                "ok": False,
                "error": "pycaw not installed. Run: pip install pycaw",
            }
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid volume level."}
        except Exception as e:
            print(f"[set_master_volume error] {e}")
            return {"ok": False}

    def toggle_master_mute(self):
        try:
            def _work():
                vol = _pycaw_master_volume_interface()
                new_state = not bool(vol.GetMute())
                vol.SetMute(new_state, None)
                del vol
                return new_state

            muted = _run_on_com_thread(_work)
            return {"ok": True, "muted": muted}
        except ImportError:
            return {
                "ok": False,
                "error": "pycaw not installed. Run: pip install pycaw",
            }
        except Exception as e:
            print(f"[toggle_master_mute error] {e}")
            return {"ok": False}

    def get_media_volume(self):
        try:
            cached_id = _session_cache.get("app_id")
            if not cached_id:
                return {"ok": False, "error": "No active media session."}
            cached_title = _session_cache.get("title")
            cached_artist = _session_cache.get("artist")

            def _work():
                session = _pycaw_session_for(cached_id, cached_title, cached_artist)
                if session is None:
                    return None
                vol = session.SimpleAudioVolume
                result = (round(float(vol.GetMasterVolume()), 3), bool(vol.GetMute()))
                # Release the COM proxies HERE, still on the COM thread --
                # never let them live long enough to be GC'd elsewhere.
                del vol, session
                return result

            result = _run_on_com_thread(_work)
            if result is None:
                return {
                    "ok": False,
                    "error": "No matching Windows audio session for this app yet "
                             "(UWP app, or it hasn't produced sound this run).",
                }
            volume, muted = result
            return {"ok": True, "volume": volume, "muted": muted}
        except ImportError:
            return {
                "ok": False,
                "error": "pycaw not installed. Run: pip install pycaw",
            }
        except Exception as e:
            print(f"[get_media_volume error] {e}")
            return {"ok": False}

    def set_media_volume(self, level):
        try:
            level = max(0.0, min(1.0, float(level)))
            cached_id = _session_cache.get("app_id")
            if not cached_id:
                return {"ok": False, "error": "No active media session."}
            cached_title = _session_cache.get("title")
            cached_artist = _session_cache.get("artist")

            def _work():
                session = _pycaw_session_for(cached_id, cached_title, cached_artist)
                if session is None:
                    return None
                vol = session.SimpleAudioVolume
                vol.SetMasterVolume(level, None)
                # Re-query the CONFIRMED value from the session itself,
                # never assume the write landed just because the call
                # didn't throw (same pattern as toggle_shuffle above) --
                # this is what actually verifies Problem 1 is fixed, not
                # just that Problem 2's crash is gone.
                confirmed = round(float(vol.GetMasterVolume()), 3)
                del vol, session
                return confirmed

            confirmed = _run_on_com_thread(_work)
            if confirmed is None:
                return {"ok": False, "error": "No matching Windows audio session for this app yet."}
            # DEBUG (Task 2, item 1): remove once Problem 1 is confirmed
            # fixed end-to-end (Spotify's actual volume changing, not just
            # this call returning ok).
            print(f"[set_media_volume] requested={level} confirmed_by_session={confirmed}")
            return {"ok": True, "volume": confirmed}
        except ImportError:
            return {
                "ok": False,
                "error": "pycaw not installed. Run: pip install pycaw",
            }
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid volume level."}
        except Exception as e:
            print(f"[set_media_volume error] {e}")
            return {"ok": False}