"""
Minimal optional tool routing fallback for Sara AI.

This module is intentionally lightweight and self-contained so the app can
start even when more advanced LLM-assisted tool-calling is not available.
It exposes the same public interface that the existing optional imports
expect, and it contains a conservative rule-based fallback for a small set
of common tool commands.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

TOOL_NAME_TO_INTENT: Dict[str, str] = {
    "weather": "weather",
    "news": "news",
    "web_search": "web_search",
    "open_url": "open_url",
    "play_youtube": "play_youtube",
    "play_spotify": "play_spotify",
    "screenshot_describe": "screenshot_describe",
    "clipboard_read": "clipboard_read",
    "clipboard_write": "clipboard_write",
    "open_app": "open_app",
    "close_app": "close_app",
    "calculator": "calculator",
}


class _FakeMatch:
    def __init__(self, groups: tuple[Any, ...]) -> None:
        self._groups = groups
        self.lastindex = len(groups)

    def __bool__(self) -> bool:
        return True

    def group(self, index: int = 0) -> Optional[Any]:
        if index == 0:
            return self._groups[0] if self._groups else None
        if index < 0:
            raise IndexError("group index must be non-negative")
        try:
            return self._groups[index - 1]
        except IndexError:
            return None


def _extract_after_phrases(text: str, phrases: tuple[str, ...]) -> str:
    for phrase in phrases:
        match = re.search(rf"{re.escape(phrase)}\s+(.+?)(?:$|\?|\.|!|,)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def resolve_tool_call(user_input: str, model_name: str) -> Dict[str, Any]:
    """Return a conservative tool call candidate for an unmatched command."""
    text = (user_input or "").strip()
    lowered = text.lower()

    if any(word in lowered for word in ("weather", "temperature", "rain", "forecast")):
        location = _extract_after_phrases(lowered, ("weather in", "weather at", "in", "at"))
        return {"name": "weather", "arguments": {"location": location}}

    if any(word in lowered for word in ("news", "headlines", "latest news")):
        topic = _extract_after_phrases(lowered, ("news about", "news on", "news for", "about"))
        return {"name": "news", "arguments": {"topic": topic}}

    if any(phrase in lowered for phrase in ("search for", "look up", "google", "look up", "find out")):
        query = _extract_after_phrases(lowered, ("search for", "look up", "find out", "google"))
        return {"name": "web_search", "arguments": {"query": query or text}}

    if "open url" in lowered or "visit" in lowered or re.search(r"https?://", lowered):
        url = _extract_after_phrases(text, ("open url", "visit"))
        return {"name": "open_url", "arguments": {"url": url or text}}

    if "youtube" in lowered and any(keyword in lowered for keyword in ("play", "show", "open")):
        query = _extract_after_phrases(text, ("play", "show", "open"))
        return {"name": "play_youtube", "arguments": {"query": query or text}}

    if "spotify" in lowered and any(keyword in lowered for keyword in ("play", "listen to", "open")):
        query = _extract_after_phrases(text, ("play", "listen to", "open"))
        return {"name": "play_spotify", "arguments": {"query": query or text}}

    if "screenshot" in lowered or "screen" in lowered and "describe" in lowered:
        return {"name": "screenshot_describe", "arguments": {}}

    if "clipboard" in lowered and any(keyword in lowered for keyword in ("read", "show", "what's on", "what is on")):
        return {"name": "clipboard_read", "arguments": {}}

    if "clipboard" in lowered and any(keyword in lowered for keyword in ("copy", "write", "paste", "set")):
        snippet = _extract_after_phrases(text, ("copy", "write", "paste", "set"))
        return {"name": "clipboard_write", "arguments": {"text": snippet}}

    if any(keyword in lowered for keyword in ("open ", "launch ", "start ")):
        app = _extract_after_phrases(text, ("open", "launch", "start"))
        return {"name": "open_app", "arguments": {"target": app}}

    if any(keyword in lowered for keyword in ("close ", "quit ", "exit ", "terminate ")):
        app = _extract_after_phrases(text, ("close", "quit", "exit", "terminate"))
        return {"name": "close_app", "arguments": {"target": app}}

    if any(keyword in lowered for keyword in ("calculate", "what is", "what's", "how much")):
        expression = _extract_after_phrases(text, ("calculate", "what is", "what's", "how much is"))
        return {"name": "calculator", "arguments": {"expr": expression or text}}

    return {"name": "unknown", "arguments": {}}


def build_fake_match(tool_name: str, arguments: Dict[str, Any]) -> Optional[_FakeMatch]:
    """Construct a fake regex match object for an internal intent handler."""
    if tool_name == "weather":
        return _FakeMatch((arguments.get("location", ""),))
    if tool_name == "news":
        return _FakeMatch((arguments.get("topic", ""),))
    if tool_name == "web_search":
        return _FakeMatch((arguments.get("query", ""),))
    if tool_name == "open_url":
        return _FakeMatch((arguments.get("url", ""),))
    if tool_name == "play_youtube":
        return _FakeMatch((arguments.get("query", ""),))
    if tool_name == "play_spotify":
        return _FakeMatch((arguments.get("query", ""),))
    if tool_name == "screenshot_describe":
        return _FakeMatch(())
    if tool_name == "clipboard_read":
        return _FakeMatch(())
    if tool_name == "clipboard_write":
        return _FakeMatch((arguments.get("text", ""),))
    if tool_name in ("open_app", "close_app"):
        return _FakeMatch((arguments.get("target", ""),))
    if tool_name == "calculator":
        return _FakeMatch((arguments.get("expr", ""),))
    return None
