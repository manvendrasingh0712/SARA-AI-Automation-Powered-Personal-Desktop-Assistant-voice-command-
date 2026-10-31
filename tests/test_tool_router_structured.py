"""Audit part 2, O2: the JSON-schema constrained tool call (opt-in; measured WORSE than native tool calling here)."""
import json
from types import SimpleNamespace

from sara.core import tool_router as T


class Client:
    def __init__(self, content=None, boom=False):
        self.content, self.boom, self.kw = content, boom, None

    def chat(self, **kw):
        self.kw = kw
        if self.boom:
            raise RuntimeError("format not supported")
        return SimpleNamespace(message=SimpleNamespace(content=self.content, tool_calls=None))


class Cfg:
    OLLAMA_NUM_CTX = 4096
    OLLAMA_KEEP_ALIVE = "5m"
    TOOL_CALLING_STRUCTURED = True

    def __getattr__(self, n):
        raise AttributeError(n)


def test_schema_only_allows_known_tool_names():
    enum = T._structured_schema()["properties"]["name"]["enum"]
    assert "none" in enum and "weather" in enum and len(enum) == len(T.TOOLS_SCHEMA) + 1


def test_structured_answer_is_parsed_and_none_becomes_unknown():
    c = Client(json.dumps({"name": "weather", "arguments": {"location": "Jaipur"}}))
    assert T._ollama_structured_call(c, "m", "jaipur ka mausam", Cfg()) == {"name": "weather", "arguments": {"location": "Jaipur"}}
    assert c.kw["format"]["required"] == ["name", "arguments"]
    assert T._ollama_structured_call(Client('{"name": "none", "arguments": {}}'), "m", "hello", Cfg()) == {"name": "unknown", "arguments": {}}


def test_a_failing_structured_call_falls_back_to_native():
    class Native(Client):
        def chat(self, **kw):
            if "format" in kw:
                raise RuntimeError("no format support")
            return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[SimpleNamespace(function=SimpleNamespace(name="news", arguments={"topic": ""}))]))

    assert T._ollama_tool_call(Native(), "m", "khabrein", Cfg())["name"] == "news"


def test_it_is_off_by_default():
    from config import Config

    assert Config.TOOL_CALLING_STRUCTURED is False
