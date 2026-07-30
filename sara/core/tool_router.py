"""
sara.core.tool_router
LLM-driven tool routing for Sara AI, with a deterministic keyword-based
fallback.

WHY THIS FILE CHANGED (v2):
The previous version of this module was pure keyword/substring matching
("if 'weather' in lowered: ...") despite the module's own docstring and
config.py's TOOL_CALLING_ENABLED comment both describing it as an
"LLM-assisted" / "structured function-calling" step. It accepted a
`model_name` argument that was never actually used to call any model.
That mismatch is fixed here: `resolve_tool_call()` now makes a real,
bounded-time Ollama tool-calling request (native `tools=` support,
requires ollama>=0.4, this project pins 0.6.2) and only falls back to
the old keyword heuristic if the LLM call is unavailable, times out, or
fails for any reason -- same "never block, always degrade gracefully"
shape used everywhere else in this codebase (RAG, proactive rephrasing,
skills auto-discovery, etc).

Toggle via Config.TOOL_CALLING_MODE:
    "llm"       (default) -- try the real LLM tool-call first, fall back
                 to the keyword heuristic below on any failure/timeout.
    "heuristic" -- skip the LLM call entirely, use only the keyword
                 heuristic (old v1 behavior; useful if you don't want
                 an extra Ollama round-trip on every unmatched command,
                 e.g. on a slower machine).
"""

from __future__ import annotations

import concurrent.futures
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

# ══════════════════════════════════════════════════════════════════════
# Ollama-native tool schema (OpenAI-style function-calling shape) -- one
# entry per tool in TOOL_NAME_TO_INTENT above. Argument keys MUST match
# what build_fake_match() below expects for that tool_name.
# ══════════════════════════════════════════════════════════════════════

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Get the current weather or forecast for a place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City or place name, e.g. 'Ajmer' or 'Jaipur'. Empty string if the user didn't say a place.",
                    }
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "news",
            "description": "Get recent news headlines, optionally about a specific topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic to get news about, e.g. 'cricket' or 'technology'. Empty string for general headlines.",
                    }
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for a question or topic that isn't covered by another tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a specific website URL in the browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to open."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_youtube",
            "description": "Play a video or song on YouTube.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for / play on YouTube."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify",
            "description": "Play a song, artist, or playlist on Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for / play on Spotify."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_describe",
            "description": "Take a screenshot of the screen and describe what's on it.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_read",
            "description": "Read and report back whatever is currently on the clipboard.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_write",
            "description": "Copy the given text onto the clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to copy to the clipboard."}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open/launch a desktop application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Name of the application to open, e.g. 'chrome' or 'notepad'."}
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Close/quit a desktop application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Name of the application to close, e.g. 'chrome' or 'notepad'."}
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a math expression or arithmetic question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "The math expression to evaluate, e.g. '12 * (3 + 4)'."}
                },
                "required": ["expr"],
            },
        },
    },
]

_TOOL_ROUTER_SYSTEM_PROMPT = (
    "You are a tool router for a voice assistant. Read the user's message "
    "and decide if ONE of the available tools clearly fulfills it. If so, "
    "call that tool with the correct arguments inferred from the message. "
    "If no tool clearly applies, do not call any tool -- just don't respond "
    "with a function call."
)

# Bounded-size executor for the LLM tool-routing call, so a slow/hung
# Ollama request can be abandoned via future.result(timeout=...) without
# ever blocking the calling (voice-loop) thread indefinitely. Two workers
# is plenty -- this path only runs for unmatched ("chat" intent) commands,
# never for the fast regex-matched path, so overlap is rare.
_TOOL_CALL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="sara-tool-router"
)


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


# ══════════════════════════════════════════════════════════════════════
# Path A (default): real LLM tool-calling via Ollama's native tools= API
# ══════════════════════════════════════════════════════════════════════


def _resolve_tool_call_llm(user_input: str, model_name: str, cfg) -> Optional[Dict[str, Any]]:
    """
    Returns a resolved {"name", "arguments"} dict on success, or None if
    the LLM call itself couldn't be made (client unavailable) -- callers
    treat None the same as any other failure and fall back to the
    heuristic. Any error from the actual chat() call propagates to the
    caller's own try/except, which logs it and falls back (see
    resolve_tool_call() below) -- matching how every other optional
    LLM-assist path in this codebase behaves.
    """
    from sara.core.llm.clients import _get_ollama_client

    client = _get_ollama_client(cfg)
    if not client:
        return None

    resp = client.chat(
        model=model_name,
        messages=[
            {"role": "system", "content": _TOOL_ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ],
        tools=TOOLS_SCHEMA,
        options={"num_predict": 150},
        keep_alive=getattr(cfg, "OLLAMA_KEEP_ALIVE", "30m"),
    )

    tool_calls = getattr(resp.message, "tool_calls", None)
    if not tool_calls:
        # Model looked at the tools and correctly decided none applies --
        # this is a legitimate, confident "no tool" answer, not a failure.
        return {"name": "unknown", "arguments": {}}

    call = tool_calls[0]
    name = call.function.name
    arguments = dict(call.function.arguments or {})
    return {"name": name, "arguments": arguments}


# ══════════════════════════════════════════════════════════════════════
# Cheap pre-gate: is a tool call even plausible for this message?
# ══════════════════════════════════════════════════════════════════════
# Flattened from every keyword/phrase trigger used in
# _resolve_tool_call_heuristic() below -- kept as one frozenset (built
# once at import time) purely so has_probable_tool_intent() is a single
# fast "any of these substrings present?" scan, with zero I/O and no
# model call. This is NOT a replacement for the heuristic's per-tool
# logic (arguments still need the real function below, or the LLM path)
# -- it only answers "does this message even mention anything
# tool-shaped?", used by resolve_tool_call() to skip the LLM round-trip
# entirely for ordinary conversation that obviously isn't asking for
# weather/news/an app/a calculation/etc.
_TOOL_KEYWORD_GATE: frozenset[str] = frozenset(
    {
        "weather", "temperature", "rain", "forecast",
        "news", "headlines",
        "search for", "look up", "google", "find out",
        "open url", "visit", "http://", "https://",
        "youtube",
        "spotify",
        "screenshot", "describe",
        "clipboard",
        "open ", "launch ", "start ",
        "close ", "quit ", "exit ", "terminate ",
        "calculate", "what is", "what's", "how much",
    }
)


def has_probable_tool_intent(user_input: str) -> bool:
    """
    Fast, pure-string "is it even worth asking the LLM to consider a
    tool?" pre-check. Deliberately over-inclusive (a false positive here
    just costs one bounded LLM round-trip that would've happened anyway
    under the old always-ask behavior; a false negative costs a genuinely
    tool-worthy message getting answered conversationally instead) --
    the goal is only to skip the LLM tool-call for the common case of
    plain conversation that obviously isn't asking for any of these
    tools, not to replace the heuristic's real matching logic below.
    """
    lowered = (user_input or "").lower()
    return any(kw in lowered for kw in _TOOL_KEYWORD_GATE)


# ══════════════════════════════════════════════════════════════════════
# Path B (fallback): the original conservative keyword heuristic (v1).
# Used when TOOL_CALLING_MODE="heuristic", or whenever the LLM path above
# is unavailable/times out/errors for any reason.
# ══════════════════════════════════════════════════════════════════════


def _resolve_tool_call_heuristic(user_input: str) -> Dict[str, Any]:
    text = (user_input or "").strip()
    lowered = text.lower()

    if any(word in lowered for word in ("weather", "temperature", "rain", "forecast")):
        location = _extract_after_phrases(lowered, ("weather in", "weather at", "in", "at"))
        return {"name": "weather", "arguments": {"location": location}}

    if any(word in lowered for word in ("news", "headlines", "latest news")):
        topic = _extract_after_phrases(lowered, ("news about", "news on", "news for", "about"))
        return {"name": "news", "arguments": {"topic": topic}}

    if any(phrase in lowered for phrase in ("search for", "look up", "google", "find out")):
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

    if "screenshot" in lowered or ("screen" in lowered and "describe" in lowered):
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


# ══════════════════════════════════════════════════════════════════════
# Public entry point -- same signature callers already use
# (sara/orchestrator/intent_handlers.py: resolve_tool_call(user_input, brain.model_name))
# ══════════════════════════════════════════════════════════════════════


def resolve_tool_call(user_input: str, model_name: str, cfg=None) -> Dict[str, Any]:
    """Return a resolved tool call candidate for an unmatched ("chat" intent) command."""
    if cfg is None:
        # Lazy import, mirroring the pattern used throughout this codebase
        # (sara/skills/__init__.py, sara/orchestrator/proactive.py) to
        # avoid any import-order surprises for a module imported this early.
        from config import Config as cfg  # noqa: N813

    mode = getattr(cfg, "TOOL_CALLING_MODE", "llm")

    # PERF FIX: this used to unconditionally pay a real Ollama round-trip
    # (bounded by TOOL_CALLING_TIMEOUT_S, up to several seconds) for
    # EVERY unmatched "chat" message before generate_response_stream()
    # was even allowed to start -- including plain conversation that
    # obviously wasn't asking for weather/news/an app/etc. That extra,
    # fully sequential LLM call was the dominant source of "the response
    # takes forever to start" latency. has_probable_tool_intent() is a
    # near-zero-cost pure-string check; skipping straight to the
    # heuristic (itself just string matching, effectively free) for
    # anything that doesn't even mention a tool-shaped keyword removes
    # that entire round-trip from the common case, with no behavior
    # change for messages that actually do look tool-related.
    if mode == "llm" and not has_probable_tool_intent(user_input):
        return _resolve_tool_call_heuristic(user_input)

    if mode == "llm":
        timeout_s = float(getattr(cfg, "TOOL_CALLING_TIMEOUT_S", 5.0))
        try:
            future = _TOOL_CALL_EXECUTOR.submit(_resolve_tool_call_llm, user_input, model_name, cfg)
            result = future.result(timeout=timeout_s)
            if result is not None:
                return result
        except concurrent.futures.TimeoutError:
            # BUGFIX: bare `TimeoutError` stringifies to an EMPTY string,
            # so the debug print below used to read
            # "...using heuristic fallback: " with nothing after the
            # colon -- indistinguishable from a real, silent failure at a
            # glance. Naming it explicitly (with the actual budget that
            # was exceeded) makes this immediately diagnosable: it means
            # Ollama's tool-enabled chat() call for this model is
            # routinely taking longer than TOOL_CALLING_TIMEOUT_S, not
            # that anything is broken. Two ways to address that directly,
            # if the fallback firing often enough to be annoying: raise
            # Config.TOOL_CALLING_TIMEOUT_S in .env, or set
            # Config.TOOL_CALLING_MODE=heuristic to skip this LLM call
            # entirely.
            if getattr(cfg, "DEBUG_MODE", False):
                print(
                    f"[ToolRouter] LLM tool-call exceeded its "
                    f"{timeout_s:.1f}s budget, using heuristic fallback."
                )
        except Exception as e:  # noqa: BLE001 -- any other failure also degrades to the heuristic below
            if getattr(cfg, "DEBUG_MODE", False):
                print(f"[ToolRouter] LLM tool-call failed ({type(e).__name__}: {e}), using heuristic fallback.")

    return _resolve_tool_call_heuristic(user_input)


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