"""
sara.audio.ducking
Lowers the volume of OTHER applications while SARA is listening, then puts it back.

Why: with YouTube or Spotify playing, the microphone hears the music/video together with the user.
SARA's echo canceller only knows SARA's own voice, so that background audio is transcribed as if the user
had said it ("kahaan kahaan kahaan ...", song lyrics) and the user's real words are lost. Voice assistants
solve this by "ducking": the other audio is turned down while the assistant listens. Done here through
the Windows per-application volume (Core Audio sessions, via pycaw), so nothing else on the PC is touched.

Safety:
  * SARA's own processes (and its children, e.g. the webview) are never touched;
  * the original volumes are written to disk before anything is changed, and restored on the next start if
    SARA was killed while ducking;
  * a session whose volume the user changed meanwhile is left alone when restoring;
  * everything is best-effort: if pycaw / COM is unavailable the call does nothing.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_STATE_FILE: Optional[Path] = None
_TOLERANCE = 0.03


def _state_file() -> Path:
    global _STATE_FILE
    if _STATE_FILE is None:
        try:
            from config import app_data_subdir

            _STATE_FILE = Path(str(app_data_subdir("data"))) / "duck_state.json"
        except Exception:
            _STATE_FILE = Path(os.environ.get("LOCALAPPDATA", ".")) / "SARA-AI" / "data" / "duck_state.json"
    return _STATE_FILE


def _own_pids() -> Set[int]:
    pids = {os.getpid()}
    try:
        import psutil

        me = psutil.Process(os.getpid())
        pids.update(c.pid for c in me.children(recursive=True))
        pids.update(p.pid for p in me.parents())
    except Exception:
        pass
    return pids


def _sessions():
    """(key, pid, name, SimpleAudioVolume) for every Core Audio session of the default output device."""
    from pycaw.pycaw import AudioUtilities

    out = []
    for s in AudioUtilities.GetAllSessions():
        try:
            pid = int(s.ProcessId or 0)
            key = f"{pid}:{s.Identifier or ''}"
            name = s.Process.name() if s.Process else ""
            out.append((key, pid, name, s.SimpleAudioVolume))
        except Exception:
            continue
    return out


def _on_com_thread(fn):
    """Core Audio session pointers are created, used and dropped on ONE COM thread (sara/core/com_thread.py): dropping them from
    another thread (garbage collector) crashed the whole process with an access violation in the live test."""
    try:
        from sara.core.com_thread import run_on_com_thread

        return run_on_com_thread(fn)
    except RuntimeError:                 # interpreter shutting down: the executor refuses new work, run it inline
        return fn()


class AudioDucker:
    def __init__(self, level: float = 0.15, only_active: bool = True) -> None:
        self.level = max(0.0, min(1.0, float(level)))
        self.only_active = only_active
        self._saved: Dict[str, dict] = {}     # key -> {"orig": float, "set": float, "name": str}
        self._since = 0.0

    # ------------------------------------------------------------------ public
    @property
    def active(self) -> bool:
        return bool(self._saved)

    def duck(self) -> int:
        """Turn other applications down. Returns how many sessions were lowered (0 when nothing to do)."""
        return _on_com_thread(self._duck_impl)

    def _duck_impl(self) -> int:
        with _LOCK:
            if self._saved:
                return 0
            try:
                import comtypes

                comtypes.CoInitialize()
            except Exception:
                pass
            try:
                from pycaw.pycaw import AudioUtilities

                mine = _own_pids()
                plan = []
                # One pass over the sessions: read, remember, lower. (Looking each session up again by key
                # cost 400 ms with a few sessions open.)
                for s in AudioUtilities.GetAllSessions():
                    try:
                        pid = int(s.ProcessId or 0)
                        if pid == 0 or pid in mine:
                            continue
                        if self.only_active and int(s.State) != 1:     # AudioSessionStateActive
                            continue
                        vol = s.SimpleAudioVolume
                        cur = float(vol.GetMasterVolume())
                        key = f"{pid}:{s.Identifier or ''}"
                        name = s.Process.name() if s.Process else ""
                        plan.append((key, name, cur, round(cur * self.level, 4), vol))
                    except Exception:
                        continue
                for key, name, cur, new, _vol in plan:
                    self._saved[key] = {"orig": cur, "set": new, "name": name}
                self._write_state()          # before changing anything: a crash must be recoverable
                for _key, _name, _cur, new, vol in plan:
                    try:
                        vol.SetMasterVolume(float(new), None)
                    except Exception:
                        pass
                self._since = time.monotonic()
            except Exception as e:
                logger.debug("duck failed: %s", e)
                self.restore()
            return len(self._saved)

    def restore(self) -> int:
        """Put the volumes back (only where the user did not change them meanwhile)."""
        return _on_com_thread(self._restore_impl)

    def _restore_impl(self) -> int:
        with _LOCK:
            n = 0
            if not self._saved:
                return 0
            try:
                import comtypes

                comtypes.CoInitialize()
            except Exception:
                pass
            current = {}
            try:
                for key, _pid, _name, vol in _sessions():
                    current[key] = vol
            except Exception as e:
                logger.debug("restore: could not list sessions: %s", e)
            for key, rec in self._saved.items():
                vol = current.get(key)
                if vol is None:
                    continue
                try:
                    if abs(float(vol.GetMasterVolume()) - rec["set"]) <= _TOLERANCE:
                        vol.SetMasterVolume(rec["orig"], None)
                        n += 1
                except Exception:
                    continue
            self._saved = {}
            self._clear_state()
            return n

    def __enter__(self) -> "AudioDucker":
        self.duck()
        return self

    def __exit__(self, *_exc) -> None:
        self.restore()

    # ------------------------------------------------------------------ helpers
    def _write_state(self) -> None:
        try:
            p = _state_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"saved": self._saved, "ts": time.time()}), encoding="utf-8")
        except Exception:
            pass

    def _clear_state(self) -> None:
        try:
            _state_file().unlink()
        except Exception:
            pass


def recover_after_crash() -> int:
    """Call once at start: if the last run died while ducking, put those volumes back."""
    try:
        p = _state_file()
        if not p.exists():
            return 0
        data = json.loads(p.read_text(encoding="utf-8"))
        d = AudioDucker()
        d._saved = {k: v for k, v in (data.get("saved") or {}).items() if isinstance(v, dict) and "orig" in v and "set" in v}
        n = d.restore()
        d._clear_state()
        return n
    except Exception:
        return 0
