"""
Ollama must be addressed as 127.0.0.1, not "localhost".

E2E 2026-10-30 (voice session + reproduction): every request to Ollama made
after ~5 s idle cost an extra ~2.1 s -- embedding 2072 ms vs 37 ms, and the
chat call too -- so each voice chat turn paid it twice. The pooled HTTP
connection expires after ~5 s; a fresh one to "localhost" tries IPv6 (::1)
first, Ollama listens on 127.0.0.1 only, and Windows waits ~2 s before the
IPv4 fallback.
"""
import pytest

from config import Config, _ipv4_loopback


@pytest.mark.parametrize("given, expected", [
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("HTTP://LocalHost/x", "HTTP://127.0.0.1/x"),
    ("https://localhost:8443", "https://127.0.0.1:8443"),
    ("http://192.168.1.5:11434", "http://192.168.1.5:11434"),
    ("http://localhosting:1", "http://localhosting:1"),
    ("http://[::1]:11434", "http://[::1]:11434"),
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
    ("", ""),
])
def test_only_the_localhost_name_is_rewritten(given, expected):
    assert _ipv4_loopback(given) == expected


def test_the_configured_host_never_says_localhost():
    assert "localhost" not in Config.OLLAMA_HOST.lower()


def test_the_client_factory_uses_the_configured_host():
    import inspect

    from sara.core.llm import clients

    assert 'getattr(cfg, "OLLAMA_HOST"' in inspect.getsource(clients._get_ollama_client)
