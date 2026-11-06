"""
sara/core/secret_store.py
DPAPI-encrypted local secret storage (P2.2) -- Windows-only, for secrets that
used to live as plaintext in app-data or the project root (token.json today;
a Gemini key entered via the Settings GUI once P4.1 exists). Priority is
always: an explicit env var (.env keeps working exactly as before) > this
encrypted store > nothing. Nothing here ever touches .env.

DPAPI (CryptProtectData/CryptUnprotectData, via pywin32's win32crypt, already
a project dependency) ties the ciphertext to the current Windows user account
-- it cannot be decrypted by copying the file to another machine or account,
without needing SARA to manage its own encryption key at all.

`_backend` is swappable (see `use_backend()`) so tests can run without real
DPAPI / on non-Windows CI: see tests/test_secret_store.py.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class _CryptoBackend(Protocol):
    def protect(self, data: bytes, description: str) -> bytes: ...
    def unprotect(self, blob: bytes) -> bytes: ...


class _DPAPIBackend:
    """Real backend: Windows DPAPI via pywin32."""

    def __init__(self) -> None:
        import win32crypt  # noqa: F401 -- import here so a missing/non-Windows pywin32 only breaks this backend, not the module

        self._win32crypt = win32crypt

    def protect(self, data: bytes, description: str) -> bytes:
        # CRYPTPROTECT_UI_FORBIDDEN (0x1): never pop a Windows UI prompt from a background path.
        return self._win32crypt.CryptProtectData(data, description, None, None, None, 0x1)

    def unprotect(self, blob: bytes) -> bytes:
        _desc, data = self._win32crypt.CryptUnprotectData(blob, None, None, None, 0x1)
        return data


_backend: Optional[_CryptoBackend] = None
_backend_tried = False


def _get_backend() -> Optional[_CryptoBackend]:
    global _backend, _backend_tried
    if _backend is not None or _backend_tried:
        return _backend
    _backend_tried = True
    try:
        _backend = _DPAPIBackend()
    except Exception as e:
        logger.info("[secret_store] DPAPI backend unavailable (%s) -- encrypted storage disabled.", type(e).__name__)
        _backend = None
    return _backend


def use_backend(backend: Optional[_CryptoBackend]) -> None:
    """Test-only hook: inject a fake backend (or None to force 'unavailable').
    Passing an explicit backend also marks detection as already-done, so
    _get_backend() won't try to import the real win32crypt and override it."""
    global _backend, _backend_tried
    _backend = backend
    _backend_tried = True


def available() -> bool:
    return _get_backend() is not None


def save_encrypted(path: Path, plaintext: str, description: str = "SARA-AI secret") -> bool:
    """Encrypts `plaintext` and writes it to `path`. Never raises; returns True on success."""
    backend = _get_backend()
    if backend is None:
        logger.warning("[secret_store] cannot save %s -- no crypto backend available.", path)
        return False
    try:
        blob = backend.protect(plaintext.encode("utf-8"), description)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        return True
    except Exception as e:
        logger.warning("[secret_store] encrypt/save failed for %s: %s", path, type(e).__name__)
        return False


def load_encrypted(path: Path) -> Optional[str]:
    """Returns the decrypted plaintext, or None if the file is missing, the backend is
    unavailable, or decryption fails for any reason (e.g. a different Windows account).
    Never raises."""
    backend = _get_backend()
    if backend is None or not path.exists():
        return None
    try:
        return backend.unprotect(path.read_bytes()).decode("utf-8")
    except Exception as e:
        logger.warning("[secret_store] decrypt failed for %s: %s", path, type(e).__name__)
        return None


def migrate_plaintext_file(plain_path: Path, enc_path: Path, description: str = "SARA-AI secret") -> dict:
    """One-time migration of an existing plaintext secret file into the encrypted store.
    NEVER deletes or modifies `plain_path` -- that is a decision for the user, not this
    function (see CLAUDE_CODE_PLAN.md P2.2). Returns a small status dict:
      {"migrated": bool, "reason": str}
    - Already-encrypted (enc_path exists): no-op, migrated=False, reason="already-migrated".
    - No plaintext to migrate: migrated=False, reason="no-plaintext-source".
    - Backend unavailable: migrated=False, reason="no-backend".
    - Round-trip verification (encrypt then immediately decrypt and compare) fails: the
      partially-written enc_path is removed and migrated=False, reason="verify-failed" --
      the plaintext is the only copy that survives a failed migration.
    - Success: migrated=True, reason="ok"."""
    if enc_path.exists():
        return {"migrated": False, "reason": "already-migrated"}
    if not plain_path.is_file():
        return {"migrated": False, "reason": "no-plaintext-source"}
    if _get_backend() is None:
        return {"migrated": False, "reason": "no-backend"}
    try:
        plaintext = plain_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[secret_store] could not read %s for migration: %s", plain_path, type(e).__name__)
        return {"migrated": False, "reason": "read-failed"}

    if not save_encrypted(enc_path, plaintext, description):
        return {"migrated": False, "reason": "no-backend"}

    verify = load_encrypted(enc_path)
    if verify != plaintext:
        try:
            enc_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.warning("[secret_store] migration of %s failed round-trip verification; kept the plaintext original.", plain_path)
        return {"migrated": False, "reason": "verify-failed"}

    logger.info("[secret_store] migrated %s -> %s (encrypted copy verified; the original was NOT deleted).", plain_path, enc_path)
    return {"migrated": True, "reason": "ok"}
