"""Tests for sara.core.security.policy: check_url and is_sensitive_path."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sara.core.security import policy
from sara.core.security import policy_rules as rules

ROOT = "C:\\Users\\tester\\AppData\\Roaming\\SARA"
ATTACKS = Path(__file__).resolve().parents[1] / "bench" / "redteam" / "attacks.jsonl"


@pytest.fixture(autouse=True)
def _layout(monkeypatch):
    root = rules.path_parts(ROOT)
    monkeypatch.setattr(rules, "app_data_layout", lambda: (root, root + ("notes",)))


URL_CASES = [
    ("https://example.com/path", True),
    ("http://example.com", True),
    ("HTTP://EXAMPLE.COM/A", True),
    ("https://example.com:8443/x?q=hello+world", True),
    ("www.example.com/page", True),
    ("example.com:8080/x", True),
    ("https://example.com/news/a-very-long-article-slug-about-things-that-happened-in-the-city", True),
    ("ftp://example.com/f", False),
    ("javascript:alert(1)", False),
    ("file:///C:/Windows/win.ini", False),
    ("data:text/html,<b>x</b>", False),
    ("vbscript:msgbox(1)", False),
    ("mailto:a@example.com", False),
    ("//evil.example.com/x", False),
    ("http:evil.example.com", False),
    ("http://example.com\\@evil.example.com", False),
    ("https://user:pw@example.com/x", False),
    ("http://localhost:8000/", False),
    ("http://127.0.0.1/", False),
    ("http://192.168.1.5/admin", False),
    ("http://[::1]/", False),
    ("http://2130706433/", False),
    ("http://0x7f.0.0.1/", False),
    ("http://printer.local/", False),
    ("http://svc.internal/", False),
    ("http://intranet/", False),
    ("https://example.com/?q=" + "a" * 60, False),
    ("https://example.com/?d=" + "ab12" * 16, False),
    ("https://example.com/" + "a" * 2000, False),
    ("https://example.com/\x00", False),
    ("", False),
]


@pytest.mark.parametrize("url,ok", URL_CASES)
def test_check_url(url, ok):
    assert policy.check_url(url).ok is ok


def test_check_url_allow_local_and_non_string():
    assert policy.check_url("http://localhost:8000/x", allow_local=True).ok
    assert policy.check_url("http://127.0.0.1:5000/x", allow_local=True).ok
    assert not policy.check_url("file:///c:/x", allow_local=True).ok
    assert not policy.check_url("http://user:pw@localhost/", allow_local=True).ok
    assert not policy.check_url(None).ok  # type: ignore[arg-type]


SENSITIVE = [
    ".env",
    ".ENV",
    "C:\\proj\\.env.local",
    "D:\\x\\Credentials.JSON",
    "credentials_prod.json",
    "token.json",
    "token_gmail.json",
    "client_secret_123.json",
    "cert.pem",
    "server.KEY",
    "a\\b\\cert.pfx",
    "cert.p12",
    "C:\\Users\\a\\id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "vault.kdbx",
    "putty.ppk",
    "C:\\Users\\a\\.ssh\\config",
    "C:\\Users\\a\\.gnupg\\pubring.kbx",
    "C:\\Users\\a\\.aws\\config",
    "C:\\Users\\a\\.azure\\x.json",
    "sara_data.db",
    "x\\sara_data.db-wal",
    "x\\sara_data.db-shm",
    "telemetry.sqlite",
    "security.sqlite",
    ROOT + "\\data\\x.json",
    ROOT + "\\config.json",
    ROOT,
    "C:\\Windows\\System32\\config\\SAM",
    "c:/windows/system32/config/system",
    "C:\\Windows\\System32\\config",
    "C:\\Users\\a\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Login Data",
    "C:/Users/a/AppData/Local/Microsoft/Edge/User Data/Default/Cookies",
    "C:\\Users\\a\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\x.default\\logins.json",
    "C:\\Users\\a\\AppData\\Roaming\\Microsoft\\Credentials\\ABC",
    "C:\\Users\\a\\AppData\\Roaming\\Microsoft\\Protect\\S-1-5\\k",
    "C:\\Users\\a\\notes\\..\\..\\.ssh\\id_rsa",
    "C:/Users/a/.ssh/id_rsa",
    "\\\\?\\C:\\proj\\.env",
    "C:\\proj\\.env::$DATA",
    "\\\\?\\UNC\\srv\\share\\.ssh\\id_rsa",
    "\\\\srv\\share\\creds\\id_rsa",
    "C:\\proj\\.env. ",
    "C:\\USERS\\A\\.SSH\\ID_RSA",
    "~/.ssh/config",
    ROOT + "\\notes\\..\\data\\x.db",
]


@pytest.mark.parametrize("path", SENSITIVE)
def test_sensitive_paths(path):
    assert policy.is_sensitive_path(path)


NOT_SENSITIVE = [
    "",
    "C:\\Users\\a\\Documents\\report.pdf",
    ROOT + "\\notes\\todo.txt",
    ROOT + "\\notes",
    ROOT + "2\\x.txt",
    "D:\\SARA_backup\\x.txt",
    "environment.txt",
    "C:\\proj\\env",
    "monkey.txt",
    "keys.txt",
    "tokenizer.py",
    "token_notes.txt",
    "credentials.txt",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "C:\\Windows\\notepad.exe",
]


@pytest.mark.parametrize("path", NOT_SENSITIVE)
def test_not_sensitive_paths(path):
    assert not policy.is_sensitive_path(path)


def test_path_object_and_env_vars(monkeypatch):
    monkeypatch.setenv("SARA_T_HOME", "C:\\Users\\tester")
    ref = "%SARA_T_HOME%\\.ssh\\id_rsa" if os.name == "nt" else "$SARA_T_HOME\\.ssh\\id_rsa"
    assert policy.is_sensitive_path(ref)
    assert policy.is_sensitive_path(Path("C:\\x\\.env"))


def test_sensitive_path_fail_safe(monkeypatch):
    def boom(_path):
        raise RuntimeError("x")

    monkeypatch.setattr(rules, "path_parts", boom)
    assert policy.is_sensitive_path("a\\b")
    assert policy.is_sensitive_path("a/b")
    assert not policy.is_sensitive_path("plain")


def test_redteam_sensitive_paths():
    if not ATTACKS.exists():
        pytest.skip("attacks.jsonl not present")
    rows = [json.loads(line) for line in ATTACKS.read_text(encoding="utf-8").splitlines() if line.strip()]
    checked = 0
    for row in rows:
        if row.get("cat") == "path_sensitive" and row.get("covered", True) and row.get("path"):
            assert policy.is_sensitive_path(row["path"]), row.get("id")
            checked += 1
    assert checked >= 0