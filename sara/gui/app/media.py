"""
sara.gui.app.media
ApiMediaMixin -- media-player status/controls surfaced to the GUI's media widget.
"""
import base64


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
    """
    try:
        sessions = _sessions_list(mgr)
    except Exception:
        sessions = []

    for s in sessions:
        try:
            pb = s.get_playback_info()
            if pb and int(pb.playback_status) == 4:  # Playing
                return s
        except Exception:
            continue

    # Nothing is actively playing -- fall back to Windows' notion of
    # "current" (covers the paused-but-selected case), else just the
    # first session so the card still shows something instead of nothing.
    try:
        current = mgr.get_current_session()
        if current is not None:
            return current
    except Exception:
        pass
    return sessions[0] if sessions else None


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
                art = await _extract_album_art(props)
                source_app = getattr(session, "source_app_user_model_id", "") or ""

                return {
                    "ok": True,
                    "active": True,
                    "title": props.title or "Unknown Track",
                    "artist": props.artist or "",
                    "album": props.album_title or "",
                    "art": art,
                    "app": _friendly_app_name(source_app),
                    "status": status_map.get(int(pb.playback_status), "unknown"),
                    "position_sec": tl.position.total_seconds() if tl.position else 0,
                    "duration_sec": tl.end_time.total_seconds() if tl.end_time else 0,
                    "shuffle": bool(shuffle_active) if shuffle_active is not None else False,
                    "shuffle_supported": shuffle_active is not None,
                    "repeat": _repeat_mode_to_str(getattr(pb, "auto_repeat_mode", None)),
                    "caps": {
                        "can_next": bool(getattr(controls, "is_next_enabled", True)) if controls else True,
                        "can_prev": bool(getattr(controls, "is_previous_enabled", True)) if controls else True,
                        "can_seek": bool(getattr(controls, "is_playback_position_enabled", True)) if controls else True,
                        "can_shuffle": bool(getattr(controls, "is_shuffle_enabled", False)) if controls else False,
                        "can_repeat": bool(getattr(controls, "is_repeat_enabled", False)) if controls else False,
                    },
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

    def toggle_music_playback(self, playing):
        self._pref_writer.enqueue("music_playing", "1" if playing else "0")
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
            return {"ok": bool(ok)}
        except Exception as e:
            print(f"[toggle_music_playback error] {e}")
            return {"ok": False}

    def stop_music(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = await _pick_active_session(mgr)
                if session is not None:
                    await session.try_stop_async()

            asyncio.run(_do())
        except Exception as e:
            print(f"[stop_music SMTC error] {e}")
        try:
            message = (
                self.system_tools.stop_media() if self.system_tools else "Stopped."
            )
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
                    return False
                return await session.try_change_shuffle_active_async(bool(enable))

            ok = asyncio.run(_do())
            return {"ok": bool(ok), "shuffle": bool(enable) if ok else None}
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