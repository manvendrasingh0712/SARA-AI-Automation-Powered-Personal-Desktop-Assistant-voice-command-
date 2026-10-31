"""Audit part 2, D7: every environment variable the code reads is described in .env.example."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _gen():
    spec = importlib.util.spec_from_file_location("gen_env_example", ROOT / "scripts" / "gen_env_example.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_undocumented_env_key():
    missing = _gen().undocumented()
    assert not missing, f"add these to .env.example (run: python scripts/gen_env_example.py): {missing}"


def test_profiles_are_documented():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("offline-first", "cloud-fast", "low-latency-gpu"):
        assert name in text


def test_the_generator_finds_the_keys_added_in_this_audit():
    keys = _gen().find_keys()
    for k in ("INTENT_MAX_CHARS", "VOICE_FILLER_DELAY_S", "GEMINI_THINKING_BUDGET", "TOOL_CALLING_TIMEOUT_GEMINI_S"):
        assert k in keys
