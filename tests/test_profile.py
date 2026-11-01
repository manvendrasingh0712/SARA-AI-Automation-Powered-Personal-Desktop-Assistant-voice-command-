"""Part 3, P1: SARA_PROFILE prod|dev decides DEBUG_MODE."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _debug(env):
    # the owner's real .env may set SARA_PROFILE / DEBUG_MODE: ignore it, only the env passed here counts
    code = (
        "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; "
        "import config; print(config.Config.SARA_PROFILE, config.Config.DEBUG_MODE)"
    )
    e = {k: v for k, v in os.environ.items() if k not in ("SARA_PROFILE", "DEBUG_MODE")}
    e.update({"LLM_BACKEND": "ollama", **env})
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True).stdout.split()
    return out[-2], out[-1]


def test_prod_is_the_default_and_forces_debug_off():
    assert _debug({"DEBUG_MODE": "true"}) == ("prod", "False")
    assert _debug({"DEBUG_MODE": "true", "SARA_PROFILE": "prod"}) == ("prod", "False")


def test_dev_profile_keeps_the_env_setting():
    assert _debug({"DEBUG_MODE": "true", "SARA_PROFILE": "dev"}) == ("dev", "True")
    assert _debug({"DEBUG_MODE": "false", "SARA_PROFILE": "dev"}) == ("dev", "False")
