"""
Screenshot description keeps working when Gemini is out of quota, and the
STT energy measure does not depend on `audioop` (removed in Python 3.13).
"""
import struct
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from sara.audio.stt import helpers
from sara.tools import vision
from sara.tools.vision import VisionAssistant


def _assistant(local_model="moondream", gemini=True):
    a = VisionAssistant.__new__(VisionAssistant)
    a.client = mock.Mock() if gemini else None
    a.model_name = "gemini-x"
    a._gemini_blocked_until = 0.0
    a._local_model = local_model
    return a


class _Quota(vision.errors.APIError):
    def __init__(self):
        Exception.__init__(self, "429 RESOURCE_EXHAUSTED 'retryDelay': '48871s'")
        self.code = 429


def test_quota_error_blocks_gemini_for_the_server_given_time():
    a = _assistant()
    a.client.models.generate_content.side_effect = _Quota()
    assert a._describe_gemini(b"x", "q") is None
    assert a._gemini_blocked_until - time.monotonic() > 48000


def test_local_model_answers_when_gemini_is_blocked(monkeypatch):
    a = _assistant()
    a._gemini_blocked_until = time.monotonic() + 600
    a.capture_screenshot = lambda: SimpleNamespace(
        thumbnail=lambda size: None,
        save=lambda buf, format=None, quality=None: buf.write(b"jpeg"),
    )
    chat = mock.Mock(return_value=SimpleNamespace(message=SimpleNamespace(content="A code editor.")))
    monkeypatch.setattr("sara.core.llm.clients._get_ollama_client",
                        lambda cfg: SimpleNamespace(chat=chat))
    out = a.describe_screen()
    assert "code editor" in out
    a.client.models.generate_content.assert_not_called()
    assert chat.call_args.kwargs["model"] == "moondream"


def test_offline_only_when_neither_backend_exists():
    a = _assistant(local_model="", gemini=False)
    assert a.describe_screen() == "Vision system is currently offline."


def test_local_failure_is_none_not_an_exception(monkeypatch):
    a = _assistant()
    monkeypatch.setattr("sara.core.llm.clients._get_ollama_client",
                        lambda cfg: SimpleNamespace(chat=mock.Mock(side_effect=ConnectionError("down"))))
    assert a._describe_local(b"x", "q") is None


def test_rms_does_not_need_audioop(monkeypatch):
    monkeypatch.setattr(helpers, "_HAS_AUDIOOP", False)
    monkeypatch.setattr(helpers, "_audioop", None)
    monkeypatch.setattr(helpers, "_rms_numpy", mock.Mock(side_effect=RuntimeError("no numpy path")))
    buf = struct.pack("<4h", 100, -100, 100, -100)
    assert helpers._rms(buf) == pytest.approx(100.0)
