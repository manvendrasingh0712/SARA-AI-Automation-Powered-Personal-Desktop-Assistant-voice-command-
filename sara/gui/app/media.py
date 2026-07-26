"""
sara.gui.app.media
ApiMediaMixin -- media-player status/controls surfaced to the GUI's media widget.
"""

class ApiMediaMixin:

    # ── Mini music player: real OS media session (Windows SMTC) ───────
    # Requires: pip install winsdk
    # Works regardless of WHICH app is actually playing (Spotify desktop,
    # a YouTube tab in Chrome, etc.) since it reads the OS-level "Now
    # Playing" session instead of guessing at any one app's internals.
    def get_media_status(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _fetch():
                mgr = await MediaManager.request_async()
                session = mgr.get_current_session()
                if session is None:
                    return {"ok": True, "active": False}
                props = await session.try_get_media_properties_async()
                pb = session.get_playback_info()
                tl = session.get_timeline_properties()
                if props is None or pb is None or tl is None:
                    return {"ok": True, "active": False}
                status_map = {3: "stopped", 4: "playing", 5: "paused"}
                return {
                    "ok": True,
                    "active": True,
                    "title": props.title or "Unknown Track",
                    "artist": props.artist or "",
                    "status": status_map.get(int(pb.playback_status), "unknown"),
                    "position_sec": tl.position.total_seconds() if tl.position else 0,
                    "duration_sec": tl.end_time.total_seconds() if tl.end_time else 0,
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
                session = mgr.get_current_session()
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
                session = mgr.get_current_session()
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
    # NEW UI WIRING: the redesigned player card has explicit prev/next
    # buttons and a seekable progress bar, which previously had no
    # backend methods at all.
    def skip_next_track(self):
        try:
            import asyncio
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            )

            async def _do():
                mgr = await MediaManager.request_async()
                session = mgr.get_current_session()
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
                session = mgr.get_current_session()
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
        100-nanosecond ticks (Windows' native time unit) — so the incoming
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
                session = mgr.get_current_session()
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