"""
sara.audio.tts.disk_cache
Atomic, bounded on-disk store (.npy) for pre-synthesised phrase audio.
Every function is failure-safe: it returns None / False / 0 instead of raising.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

CACHE_SCHEMA_VERSION = 1
MAX_FILES = 200
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_ENTRY_BYTES = 5 * 1024 * 1024
_HEADER_ALLOWANCE = 4096
_STALE_TMP_S = 3600.0
_ALLOWED_DTYPES = (np.dtype("float32"), np.dtype("int16"))


def cache_dir() -> Optional[Path]:
    """<app data>/data/tts_cache, created on demand; None when unavailable."""
    try:
        import config as _config

        resolver = getattr(_config, "app_data_subdir", None) or getattr(
            _config.Config, "app_data_subdir", None
        )
        if resolver is None:
            return None
        base = Path(resolver("data")) / "tts_cache"
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        return None


def model_fingerprint(model_path: str) -> str:
    """Model file path + size + mtime, so a replaced model misses the cache."""
    try:
        path = Path(model_path)
        stat = path.stat()
        return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"
    except Exception:
        return "missing"


def entry_hash(
    text: str,
    lang: str,
    fast: bool,
    speed: Optional[float],
    voice: str,
    voice_speed: float,
    model_fp: str,
) -> str:
    """Stable sha1 over everything that changes the synthesised audio."""
    payload = json.dumps(
        [CACHE_SCHEMA_VERSION, text, lang, bool(fast), speed, voice, voice_speed, model_fp],
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def path_for(directory: Path, digest: str) -> Path:
    return directory / f"{digest}.npy"


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except Exception:
        pass


def _valid(arr: object) -> bool:
    return (
        isinstance(arr, np.ndarray)
        and arr.dtype in _ALLOWED_DTYPES
        and arr.size > 0
        and arr.nbytes <= MAX_ENTRY_BYTES
        and (arr.dtype.kind != "f" or bool(np.isfinite(arr).all()))
    )


def load(directory: Path, digest: str) -> Optional[np.ndarray]:
    """Validated audio for digest, or None. A corrupt file is deleted."""
    path = path_for(directory, digest)
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_ENTRY_BYTES + _HEADER_ALLOWANCE:
            _unlink(path)
            return None
        arr = np.load(path, allow_pickle=False)
        if not _valid(arr):
            _unlink(path)
            return None
        try:
            os.utime(path)
        except OSError:
            pass
        return arr
    except Exception:
        _unlink(path)
        return None


def prune(
    directory: Path,
    max_files: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> int:
    """Delete oldest entries until within the count and size limits; returns removed."""
    max_files = MAX_FILES if max_files is None else max_files
    max_bytes = MAX_TOTAL_BYTES if max_bytes is None else max_bytes
    removed = 0
    try:
        now = time.time()
        entries: list[tuple[float, int, Path]] = []
        for item in directory.iterdir():
            try:
                stat = item.stat()
            except OSError:
                continue
            if item.name.endswith(".tmp"):
                if now - stat.st_mtime > _STALE_TMP_S:
                    _unlink(item)
                continue
            if item.suffix == ".npy":
                entries.append((stat.st_mtime, stat.st_size, item))
        entries.sort(key=lambda e: (e[0], e[2].name))
        total = sum(size for _, size, _ in entries)
        count = len(entries)
        for _, size, item in entries:
            if count <= max_files and total <= max_bytes:
                break
            _unlink(item)
            count -= 1
            total -= size
            removed += 1
    except Exception:
        return removed
    return removed


def store(directory: Path, digest: str, pcm: np.ndarray) -> bool:
    """Atomically write pcm (temp file + os.replace), then prune. True on success."""
    tmp_name: Optional[str] = None
    try:
        arr = np.asarray(pcm)
        if not _valid(arr):
            return False
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".w-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as fh:
            np.save(fh, arr, allow_pickle=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path_for(directory, digest))
        tmp_name = None
        prune(directory)
        return True
    except Exception:
        return False
    finally:
        if tmp_name is not None:
            _unlink(Path(tmp_name))