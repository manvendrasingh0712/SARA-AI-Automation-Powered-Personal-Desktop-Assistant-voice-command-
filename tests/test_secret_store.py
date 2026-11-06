"""P2.2: DPAPI-encrypted secret storage. Uses a fake crypto backend (no real
DPAPI / Windows dependency) so these run anywhere."""
import pytest

from sara.core import secret_store as S


class _FakeBackend:
    """XOR 'encryption' with a fixed key -- good enough to prove round-tripping,
    tampering-detection shape, and per-call description plumbing without touching
    real Windows DPAPI."""

    def __init__(self):
        self.protect_calls = []

    def protect(self, data: bytes, description: str) -> bytes:
        self.protect_calls.append(description)
        return bytes(b ^ 0x5A for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in blob)


class _BrokenBackend:
    def protect(self, data, description):
        raise OSError("DPAPI unavailable in this test")

    def unprotect(self, blob):
        raise OSError("DPAPI unavailable in this test")


@pytest.fixture(autouse=True)
def _restore_backend():
    yield
    S.use_backend(None)
    S._backend_tried = False  # let the next test (or real usage) re-detect from scratch


def test_available_reflects_the_injected_backend():
    S.use_backend(None)
    assert not S.available()
    S.use_backend(_FakeBackend())
    assert S.available()


def test_save_then_load_round_trips(tmp_path):
    S.use_backend(_FakeBackend())
    p = tmp_path / "secret.bin"
    assert S.save_encrypted(p, "super-secret-token-value")
    assert p.exists()
    assert S.load_encrypted(p) == "super-secret-token-value"


def test_saved_file_never_contains_the_plaintext(tmp_path):
    S.use_backend(_FakeBackend())
    p = tmp_path / "secret.bin"
    S.save_encrypted(p, "AIzaSecretGeminiKeyValue12345")
    raw = p.read_bytes()
    assert b"AIzaSecretGeminiKeyValue12345" not in raw


def test_load_missing_file_returns_none(tmp_path):
    S.use_backend(_FakeBackend())
    assert S.load_encrypted(tmp_path / "does_not_exist.bin") is None


def test_save_without_a_backend_fails_safely(tmp_path):
    S.use_backend(None)
    p = tmp_path / "secret.bin"
    assert S.save_encrypted(p, "x") is False
    assert not p.exists()


def test_load_without_a_backend_returns_none(tmp_path):
    S.use_backend(_FakeBackend())
    p = tmp_path / "secret.bin"
    S.save_encrypted(p, "x")
    S.use_backend(None)
    assert S.load_encrypted(p) is None


def test_decrypt_failure_returns_none_not_raise(tmp_path):
    S.use_backend(_FakeBackend())
    p = tmp_path / "secret.bin"
    p.write_bytes(b"\x00\x01garbage-that-is-fine-for-xor-but-simulate-broken-backend")
    S.use_backend(_BrokenBackend())
    assert S.load_encrypted(p) is None


def test_description_is_passed_through_to_the_backend(tmp_path):
    backend = _FakeBackend()
    S.use_backend(backend)
    S.save_encrypted(tmp_path / "s.bin", "x", description="my custom description")
    assert backend.protect_calls == ["my custom description"]


# ── migrate_plaintext_file ────────────────────────────────────────────────

def test_migrate_encrypts_and_keeps_the_plaintext_original(tmp_path):
    S.use_backend(_FakeBackend())
    plain = tmp_path / "token.json"
    plain.write_text('{"refresh_token": "abc123"}', encoding="utf-8")
    enc = tmp_path / "token.enc"

    result = S.migrate_plaintext_file(plain, enc)

    assert result == {"migrated": True, "reason": "ok"}
    assert enc.exists()
    assert plain.exists()  # never deleted
    assert plain.read_text(encoding="utf-8") == '{"refresh_token": "abc123"}'  # never modified
    assert S.load_encrypted(enc) == '{"refresh_token": "abc123"}'


def test_migrate_is_a_noop_if_already_migrated(tmp_path):
    S.use_backend(_FakeBackend())
    plain = tmp_path / "token.json"
    plain.write_text("original", encoding="utf-8")
    enc = tmp_path / "token.enc"
    S.save_encrypted(enc, "already here")

    result = S.migrate_plaintext_file(plain, enc)

    assert result == {"migrated": False, "reason": "already-migrated"}
    assert S.load_encrypted(enc) == "already here"  # untouched, not overwritten


def test_migrate_with_no_plaintext_source(tmp_path):
    S.use_backend(_FakeBackend())
    result = S.migrate_plaintext_file(tmp_path / "missing.json", tmp_path / "out.enc")
    assert result == {"migrated": False, "reason": "no-plaintext-source"}


def test_migrate_with_no_backend_leaves_plaintext_untouched(tmp_path):
    S.use_backend(None)
    plain = tmp_path / "token.json"
    plain.write_text("original", encoding="utf-8")
    enc = tmp_path / "token.enc"

    result = S.migrate_plaintext_file(plain, enc)

    assert result == {"migrated": False, "reason": "no-backend"}
    assert not enc.exists()
    assert plain.read_text(encoding="utf-8") == "original"


def test_migrate_failed_verification_removes_the_partial_encrypted_file(tmp_path):
    class _LyingBackend:
        """Encrypts fine but decrypts to the WRONG value -- simulates a backend that is
        silently broken, which migrate_plaintext_file must catch via round-trip verify."""

        def protect(self, data, description):
            return data

        def unprotect(self, blob):
            return b"not-what-was-encrypted"

    S.use_backend(_LyingBackend())
    plain = tmp_path / "token.json"
    plain.write_text("the real content", encoding="utf-8")
    enc = tmp_path / "token.enc"

    result = S.migrate_plaintext_file(plain, enc)

    assert result == {"migrated": False, "reason": "verify-failed"}
    assert not enc.exists()  # partial file cleaned up
    assert plain.read_text(encoding="utf-8") == "the real content"  # plaintext is the only surviving copy
