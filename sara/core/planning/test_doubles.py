"""
sara.core.planning.test_doubles
Deterministic test doubles for unit-testing the planner and executor
without a live LLM backend (Gemini in production) or network access.

Not imported by any production code path -- this module exists solely
to support tests/test_planning.py and any future test module that needs
to exercise sara.core.planning's behavior under controlled conditions
(malformed model output, timeouts, tool failures, etc.) deterministically
and offline.

Design goals:
  - BOTH backend call shapes are modelled, because planner.py and
    executor.py branch on Config.LLM_BACKEND and each branch reads a
    different response shape:
      * FakeOllamaClient  -> `client.chat(...)`, read as
        `resp.message.tool_calls[0].function.name` / `.function.arguments`.
        This is the NON-default `if backend == "ollama":` branch.
      * FakeGeminiClient  -> `client.models.generate_content(...)`, read as
        `resp.function_calls[i].name` / `.args`. This is the DEFAULT
        production path: Config.LLM_BACKEND defaults to "gemini" and
        _select_llm_client() also falls back to gemini.
    The two are mode-for-mode identical, so the same scenario can be
    asserted against both branches and any divergence shows up as a test
    difference rather than a production surprise. (Before the D4 repair
    only the Ollama fake existed, so the default path was untested.)
  - Every "mode" corresponds to one specific real-world failure class
    the planner/executor code paths are built to handle, named
    explicitly so a test reading `FakeOllamaClient(mode="hallucinated_tool")`
    is self-documenting about which scenario it exercises.
  - Dispatch-callback factories cover every StepResult outcome the
    executor can produce: immediate success, immediate failure, timeout,
    fail-then-recover (exercises the retry path), and per-tool routing
    for multi-step plans with mixed outcomes.

IMPORTANT: default fixture tool names must be real tools registered in
sara.core.tool_router.TOOL_NAME_TO_INTENT (weather, news, web_search,
open_url, play_youtube, play_spotify, screenshot_describe,
clipboard_read, clipboard_write, open_app, close_app, calculator) --
that is the exact universe of tools the real planner.propose_plan()
restricts itself to (see planner.py's allowed_tool_names). Reminders,
notes, timers, and calendar events are fast-path-only intents (matched
by regex in sara/core/intent/patterns.py) and are NEVER offered to the
LLM as callable tools, so a plan step naming one of them would be
correctly dropped by schema.parse_plan_from_llm() as an unknown tool --
using such a name in a default/example fixture here would misrepresent
what a real plan can actually contain.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


# ══════════════════════════════════════════════════════════════════════
# Fake Ollama response shapes (mirrors what the real `ollama` package
# returns from Client.chat(): resp.message.tool_calls[i].function.name /
# .arguments). This is the Ollama shape, used by the non-default
# `if backend == "ollama":` branch. The Gemini shape production uses by
# default is modelled further down by _FakeGeminiResponse.
# ══════════════════════════════════════════════════════════════════════


@dataclass
class _FakeFunctionCall:
    name: str
    arguments: Dict[str, Any]


@dataclass
class _FakeToolCall:
    function: _FakeFunctionCall


@dataclass
class _FakeMessage:
    content: Optional[str] = None
    tool_calls: Optional[List[_FakeToolCall]] = None


@dataclass
class _FakeResponse:
    message: _FakeMessage


# ══════════════════════════════════════════════════════════════════════
# FakeOllamaClient
# ══════════════════════════════════════════════════════════════════════


class FakeOllamaClient:
    """
    Configurable fake of the LEGACY Ollama client interface: it exposes
    `.chat(**kwargs)` and returns `resp.message.tool_calls[i].function`
    objects. The class name is kept because it still describes what
    this models (an Ollama-shaped client).

    This covers the NON-default branch: it is reached only when
    Config.LLM_BACKEND == "ollama". For the default Gemini path
    (`client.models.generate_content(...)` -> `resp.function_calls`) use
    FakeGeminiClient below, which has the same modes. This fake has no
    `.models` attribute and no `.function_calls`, so it is deliberately
    NOT a stand-in for the Gemini client.

    Behavior modes (set via constructor `mode`):
      - "valid_plan": returns a well-formed propose_plan tool call using
        `steps` passed to the constructor, and a well-formed
        corrected_step tool call using `corrected_arguments` /
        `corrected_tool` when a correction-shaped request comes in.
      - "hallucinated_tool": returns a tool call naming a tool not in
        the allowed set (for propose_plan) or an invalid tool for
        corrected_step -- simulates a model inventing a nonexistent
        function/tool name.
      - "malformed_json": returns tool_calls with an `arguments` field
        that is structurally wrong (a string instead of a list/dict) --
        simulates a model emitting broken structure that still parses as
        JSON but fails schema validation.
      - "no_tool_call": returns a message with tool_calls=None --
        simulates a model that answered in plain text instead of
        calling the offered function.
      - "empty_steps": returns propose_plan with steps=[] -- simulates a
        model that called the function correctly but proposed nothing.
      - "timeout": chat() sleeps for `sleep_s` seconds (default long
        enough to exceed any reasonable test timeout), so a caller
        bounding it with future.result(timeout=...) will observe a
        concurrent.futures.TimeoutError.
      - "raises": chat() raises a RuntimeError immediately, simulating a
        transport-level failure (connection refused, etc.).
      - "raises_after_delay": chat() sleeps for `sleep_s` seconds and
        THEN raises -- exercises the case where a slow failure still
        arrives before any timeout would fire.

    `call_count` and `call_log` let tests assert exactly how many times
    (and with what kwargs) chat() was invoked -- useful for verifying
    that, e.g., a correction call only happens once per failed step.
    """

    def __init__(
        self,
        mode: str = "valid_plan",
        steps: Optional[List[Dict[str, Any]]] = None,
        corrected_arguments: Optional[Dict[str, Any]] = None,
        corrected_tool: str = "weather",
        sleep_s: float = 30.0,
    ) -> None:
        self.mode = mode
        # FIX: default steps previously used "reminder_add" as the
        # second tool, but reminder_add is NOT a member of
        # sara.core.tool_router.TOOL_NAME_TO_INTENT (reminders are
        # fast-path-only, never exposed to the LLM planner). A real
        # propose_plan() call restricts allowed_tools to that exact set,
        # so "reminder_add" would always be silently dropped during
        # validation -- using "news" here instead makes this default
        # fixture representative of a plan the real system could
        # actually produce and execute end-to-end.
        self._steps = (
            steps
            if steps is not None
            else [
                {
                    "tool": "weather",
                    "arguments": {"location": "Jaipur"},
                    "depends_on_previous": False,
                },
                {
                    "tool": "news",
                    "arguments": {"topic": "cricket"},
                    "depends_on_previous": False,
                },
            ]
        )
        self._corrected_arguments = corrected_arguments or {"location": "Jaipur"}
        self._corrected_tool = corrected_tool
        self._sleep_s = sleep_s
        self.call_count = 0
        self.call_log: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def _record_call(self, kwargs: Dict[str, Any]) -> None:
        with self._lock:
            self.call_count += 1
            self.call_log.append(kwargs)

    @staticmethod
    def _is_correction_request(kwargs: Dict[str, Any]) -> bool:
        tools = kwargs.get("tools") or []
        names = {
            entry.get("function", {}).get("name")
            for entry in tools
            if isinstance(entry, dict)
        }
        return "corrected_step" in names

    def chat(self, **kwargs: Any) -> _FakeResponse:
        self._record_call(kwargs)
        is_correction_call = self._is_correction_request(kwargs)

        if self.mode == "raises":
            raise RuntimeError("simulated Ollama transport failure")

        if self.mode == "raises_after_delay":
            time.sleep(self._sleep_s)
            raise RuntimeError("simulated Ollama transport failure after delay")

        if self.mode == "timeout":
            time.sleep(self._sleep_s)
            return _FakeResponse(message=_FakeMessage(tool_calls=None))

        if self.mode == "no_tool_call":
            return _FakeResponse(
                message=_FakeMessage(content="just chatting", tool_calls=None)
            )

        if self.mode == "empty_steps":
            call = _FakeToolCall(
                function=_FakeFunctionCall(name="propose_plan", arguments={"steps": []})
            )
            return _FakeResponse(message=_FakeMessage(tool_calls=[call]))

        if self.mode == "hallucinated_tool":
            if is_correction_call:
                call = _FakeToolCall(
                    function=_FakeFunctionCall(
                        name="corrected_step",
                        arguments={"tool": "not_a_real_tool", "arguments": {}},
                    )
                )
            else:
                call = _FakeToolCall(
                    function=_FakeFunctionCall(
                        name="propose_plan",
                        arguments={
                            "steps": [
                                {"tool": "definitely_not_registered", "arguments": {}}
                            ]
                        },
                    )
                )
            return _FakeResponse(message=_FakeMessage(tool_calls=[call]))

        if self.mode == "malformed_json":
            if is_correction_call:
                call = _FakeToolCall(
                    function=_FakeFunctionCall(
                        name="corrected_step",
                        arguments={"tool": self._corrected_tool, "arguments": "not-a-dict"},
                    )
                )
            else:
                call = _FakeToolCall(
                    function=_FakeFunctionCall(
                        name="propose_plan",
                        arguments={"steps": "not-a-list"},
                    )
                )
            return _FakeResponse(message=_FakeMessage(tool_calls=[call]))

        # "valid_plan" (default)
        if is_correction_call:
            call = _FakeToolCall(
                function=_FakeFunctionCall(
                    name="corrected_step",
                    arguments={
                        "tool": self._corrected_tool,
                        "arguments": self._corrected_arguments,
                    },
                )
            )
        else:
            call = _FakeToolCall(
                function=_FakeFunctionCall(
                    name="propose_plan",
                    arguments={"steps": self._steps},
                )
            )
        return _FakeResponse(message=_FakeMessage(tool_calls=[call]))


# ══════════════════════════════════════════════════════════════════════
# FakeConfig
# ══════════════════════════════════════════════════════════════════════


class FakeConfig:
    """
    Minimal Config stand-in exposing only what sara.core.planning code
    reads via getattr(). Every attribute mirrors the real config.py's
    default value exactly, so tests using FakeConfig() with no overrides
    behave identically to a freshly validated real Config for anything
    this package touches.

    Pass keyword overrides to test a specific non-default configuration,
    e.g. FakeConfig(PLANNING_MAX_STEPS=2, PLANNING_STEP_RETRY_ENABLED=False).
    """

    def __init__(self, **overrides: Any) -> None:
        self.PLANNING_ENABLED = True
        self.PLANNING_MAX_STEPS = 4
        self.PLANNING_STEP_TIMEOUT_S = 2.0
        self.PLANNING_TOTAL_TIMEOUT_S = 6.0
        self.PLANNING_STEP_RETRY_ENABLED = True
        self.APP_LAUNCH_ALLOWLIST_ENABLED = True
        self.APP_LAUNCH_ALLOWLIST: List[str] = ["chrome", "notepad", "calculator", "spotify"]
        self.TOOL_CALLING_TIMEOUT_S = 3.0
        self.LLM_BACKEND = "ollama"
        self.OLLAMA_KEEP_ALIVE = "30m"
        self.DEBUG_MODE = False
        for key, value in overrides.items():
            setattr(self, key, value)


# ══════════════════════════════════════════════════════════════════════
# Dispatch-callback factories
# ══════════════════════════════════════════════════════════════════════


def make_dispatch_success(return_value: str = "done") -> Callable[[str, Dict[str, Any]], str]:
    """Every dispatch call succeeds immediately with a deterministic string."""

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        return f"{return_value}:{tool_name}"

    return _dispatch


def make_dispatch_raises(
    exception_factory: Callable[[], Exception] = lambda: RuntimeError("tool failed"),
) -> Callable[[str, Dict[str, Any]], str]:
    """
    Every dispatch call raises. `exception_factory` is called fresh on
    each invocation (rather than reusing one Exception instance) so
    tracebacks/state never leak across repeated calls in a single test.
    """

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        raise exception_factory()

    return _dispatch


def make_dispatch_timeout(sleep_s: float = 30.0) -> Callable[[str, Dict[str, Any]], str]:
    """Every dispatch call blocks for `sleep_s` seconds before (if ever) returning."""

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        time.sleep(sleep_s)
        return "should never get here"

    return _dispatch


def make_dispatch_fails_then_succeeds(
    fail_tools: Optional[List[str]] = None,
) -> Callable[[str, Dict[str, Any]], str]:
    """
    Each listed tool fails exactly once, then succeeds on every
    subsequent call for that same tool -- exercises the executor's
    self-correction retry path deterministically (first attempt fails,
    corrected retry succeeds). Thread-safe: uses a lock around the
    per-tool "already failed once" bookkeeping since execute_plan()
    dispatches on a background executor thread, not the calling thread.
    """
    lock = threading.Lock()
    failed_once: Dict[str, bool] = {}
    fail_set = set(fail_tools or ["weather"])

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        with lock:
            already_failed = failed_once.get(tool_name, False)
            if tool_name in fail_set and not already_failed:
                failed_once[tool_name] = True
                should_fail = True
            else:
                should_fail = False
        if should_fail:
            raise RuntimeError(f"{tool_name} failed on first attempt")
        return f"recovered:{tool_name}"

    return _dispatch


def make_dispatch_always_fails(
    fail_tools: Optional[List[str]] = None,
) -> Callable[[str, Dict[str, Any]], str]:
    """
    Listed tools always fail (every attempt, including retries);
    unlisted tools always succeed -- exercises the "retry also failed"
    path deterministically, distinct from make_dispatch_fails_then_succeeds
    which recovers on the second attempt.
    """
    fail_set = set(fail_tools or ["weather"])

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        if tool_name in fail_set:
            raise RuntimeError(f"{tool_name} always fails in this test")
        return f"success:{tool_name}"

    return _dispatch


def make_dispatch_selective(
    mapping: Dict[str, Callable[[Dict[str, Any]], str]]
) -> Callable[[str, Dict[str, Any]], str]:
    """
    Routes each tool name to its own callable for full control over a
    multi-step plan's per-step outcome (e.g. reminder_add succeeds,
    weather raises, news raises a different error) -- use this when the
    fail/succeed-by-tool-name granularity of the other factories isn't
    enough. Raises RuntimeError for any tool not present in `mapping`,
    so an unexpected step in a test plan fails loudly rather than
    silently succeeding.

    NOTE: unlike FakeOllamaClient's default fixtures, this factory is
    commonly used with directly-constructed PlanStep/Plan objects (see
    ExecutorTests._make_plan() in tests/test_planning.py), which bypass
    planner.propose_plan()'s allowed-tools restriction entirely -- so
    it is safe to route arbitrary tool names here (including
    fast-path-only ones like "reminder_add") when a test is exercising
    executor.execute_plan() directly rather than the full
    propose-then-execute pipeline.
    """

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        if tool_name not in mapping:
            raise RuntimeError(f"unexpected tool in test dispatch: {tool_name}")
        return mapping[tool_name](arguments)

    return _dispatch


def make_dispatch_counting() -> tuple:
    """
    Returns (dispatch_fn, call_counts) where call_counts is a dict that
    accumulates {tool_name: number_of_times_called} as the plan
    executes -- useful for asserting exact dispatch counts (e.g. "the
    weather tool was called exactly twice: once initial, once retry")
    without needing to inspect StepResult.attempts indirectly.
    Thread-safe.
    """
    lock = threading.Lock()
    call_counts: Dict[str, int] = {}

    def _dispatch(tool_name: str, arguments: Dict[str, Any]) -> str:
        with lock:
            call_counts[tool_name] = call_counts.get(tool_name, 0) + 1
        return f"ok:{tool_name}"

    return _dispatch, call_counts

# ══════════════════════════════════════════════════════════════════════
# Gemini response shape  (resp.function_calls[i].name / .args)
#
# This is the shape production ACTUALLY uses by default: Config.LLM_BACKEND
# defaults to "gemini" (config.py), and _select_llm_client() also falls back
# to gemini, so planner._call_planner_llm() and executor's correction call
# both go through client.models.generate_content(...) and read
# resp.function_calls[i].name / .args.
#
# Until this block existed, every planning test used FakeOllamaClient, which
# exercises the NON-default `if backend == "ollama":` branch only -- so the
# default production path had no coverage at all (audit finding D4).
# ══════════════════════════════════════════════════════════════════════


@dataclass
class _FakeGeminiFunctionCall:
    """Mirrors google.genai types.FunctionCall as the callers read it."""

    name: str
    args: Dict[str, Any]


@dataclass
class _FakeGeminiResponse:
    """
    Mirrors the only part of GenerateContentResponse the planning code
    touches: `.function_calls` (None or a list). `.text` is carried for
    realism when the model answers in prose instead of calling a function.
    """

    function_calls: Optional[List[_FakeGeminiFunctionCall]] = None
    text: str = ""


class _FakeGeminiModels:
    """Backs the `client.models.generate_content(...)` call shape."""

    def __init__(self, client: "FakeGeminiClient") -> None:
        self._client = client

    def generate_content(self, **kwargs: Any) -> _FakeGeminiResponse:
        return self._client._generate_content(**kwargs)


class FakeGeminiClient:
    """
    Configurable fake of the google-genai client, exposing
    `.models.generate_content(**kwargs)` and returning objects shaped like
    `resp.function_calls[i].name` / `.args`.

    Deliberately mode-for-mode identical to FakeOllamaClient above, so the
    same scenario can be asserted against BOTH backends and any behavioural
    divergence between the two branches shows up as a test difference rather
    than as a production surprise. See FakeOllamaClient's docstring for what
    each mode simulates:

      valid_plan | hallucinated_tool | malformed_json | no_tool_call
      empty_steps | timeout | raises | raises_after_delay

    `call_count` / `call_log` record every generate_content() invocation, so
    tests can assert e.g. that a correction is attempted exactly once.
    """

    def __init__(
        self,
        mode: str = "valid_plan",
        steps: Optional[List[Dict[str, Any]]] = None,
        corrected_arguments: Optional[Dict[str, Any]] = None,
        corrected_tool: str = "weather",
        sleep_s: float = 30.0,
    ) -> None:
        self.mode = mode
        self._steps = (
            steps
            if steps is not None
            else [
                {
                    "tool": "weather",
                    "arguments": {"location": "Jaipur"},
                    "depends_on_previous": False,
                },
                {
                    "tool": "news",
                    "arguments": {"topic": "cricket"},
                    "depends_on_previous": False,
                },
            ]
        )
        self._corrected_arguments = corrected_arguments or {"location": "Jaipur"}
        self._corrected_tool = corrected_tool
        self._sleep_s = sleep_s
        self.call_count = 0
        self.call_log: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        # The attribute production reaches through: client.models.generate_content
        self.models = _FakeGeminiModels(self)

    def _record_call(self, kwargs: Dict[str, Any]) -> None:
        with self._lock:
            self.call_count += 1
            self.call_log.append(kwargs)

    @staticmethod
    def _is_correction_request(kwargs: Dict[str, Any]) -> bool:
        """
        The planner offers propose_plan; the executor's step-correction call
        offers corrected_step. Both arrive as
        config=GenerateContentConfig(tools=[Tool(function_declarations=[...])]),
        so the offered declaration names are what distinguish them -- the
        Gemini equivalent of FakeOllamaClient._is_correction_request().
        """
        config = kwargs.get("config")
        names = set()
        for tool in getattr(config, "tools", None) or []:
            for decl in getattr(tool, "function_declarations", None) or []:
                name = getattr(decl, "name", None)
                if name:
                    names.add(name)
        return "corrected_step" in names

    def _generate_content(self, **kwargs: Any) -> _FakeGeminiResponse:
        self._record_call(kwargs)
        is_correction_call = self._is_correction_request(kwargs)

        if self.mode == "raises":
            raise RuntimeError("simulated Gemini transport failure")

        if self.mode == "raises_after_delay":
            time.sleep(self._sleep_s)
            raise RuntimeError("simulated Gemini transport failure after delay")

        if self.mode == "timeout":
            time.sleep(self._sleep_s)
            return _FakeGeminiResponse(function_calls=None)

        if self.mode == "no_tool_call":
            return _FakeGeminiResponse(function_calls=None, text="just chatting")

        if self.mode == "empty_steps":
            return _FakeGeminiResponse(
                function_calls=[
                    _FakeGeminiFunctionCall(name="propose_plan", args={"steps": []})
                ]
            )

        if self.mode == "hallucinated_tool":
            if is_correction_call:
                call = _FakeGeminiFunctionCall(
                    name="corrected_step",
                    args={"tool": "not_a_real_tool", "arguments": {}},
                )
            else:
                call = _FakeGeminiFunctionCall(
                    name="propose_plan",
                    args={"steps": [{"tool": "definitely_not_registered", "arguments": {}}]},
                )
            return _FakeGeminiResponse(function_calls=[call])

        if self.mode == "malformed_json":
            if is_correction_call:
                call = _FakeGeminiFunctionCall(
                    name="corrected_step",
                    args={"tool": self._corrected_tool, "arguments": "not-a-dict"},
                )
            else:
                call = _FakeGeminiFunctionCall(
                    name="propose_plan", args={"steps": "not-a-list"}
                )
            return _FakeGeminiResponse(function_calls=[call])

        # "valid_plan" (default)
        if is_correction_call:
            call = _FakeGeminiFunctionCall(
                name="corrected_step",
                args={"tool": self._corrected_tool, "arguments": self._corrected_arguments},
            )
        else:
            call = _FakeGeminiFunctionCall(
                name="propose_plan", args={"steps": self._steps}
            )
        return _FakeGeminiResponse(function_calls=[call])


class FakeGeminiWrongFunctionClient(FakeGeminiClient):
    """
    Calls a function the planner never offered. planner._call_planner_llm()
    raises PlanValidationError for this case explicitly rather than assuming
    `tools=` makes it impossible, so the branch needs its own cover.
    """

    def _generate_content(self, **kwargs: Any) -> _FakeGeminiResponse:
        self._record_call(kwargs)
        return _FakeGeminiResponse(
            function_calls=[_FakeGeminiFunctionCall(name="some_other_function", args={})]
        )
