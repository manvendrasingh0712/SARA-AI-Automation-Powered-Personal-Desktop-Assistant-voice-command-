import pytest
import sara.core.tool_router as tool_router
from sara.core.tool_router import _validate_tool_result, has_probable_tool_intent, resolve_tool_call


class _FakeCfg:
    TOOL_CALLING_MODE = "heuristic"  # no network calls in tests
    DEBUG_MODE = False
    TOOL_CALLING_TIMEOUT_S = 3.0


SHOULD_NOT_TRIGGER_CALCULATOR = [
    "what is the name of my babe",
    "what's my dog's name",
    "what is the capital of France",
    "how much do you love me",
    "what's the weather like",  # should trigger weather, not calculator
    "what is my favorite color",
]

SHOULD_TRIGGER_CALCULATOR = [
    "what is 12 * 4",
    "what's 20% of 400",
    "calculate 15 + 7",
    "how much is 100 divided by 4",
]


@pytest.mark.parametrize("text", SHOULD_NOT_TRIGGER_CALCULATOR)
def test_gate_rejects_non_math_what_is(text):
    # These should NOT even reach the LLM tool-routing pass on their own
    # "what is" phrasing (weather/news etc. still gate via their own
    # keywords, which is fine and expected).
    if "weather" not in text:
        assert has_probable_tool_intent(text) is False


@pytest.mark.parametrize("text", SHOULD_TRIGGER_CALCULATOR)
def test_gate_accepts_math_what_is(text):
    assert has_probable_tool_intent(text) is True


@pytest.mark.parametrize("text", SHOULD_NOT_TRIGGER_CALCULATOR)
def test_heuristic_never_returns_calculator_for_non_math(text):
    result = resolve_tool_call(text, model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] != "calculator"


@pytest.mark.parametrize("text", SHOULD_TRIGGER_CALCULATOR)
def test_heuristic_returns_calculator_for_math(text):
    result = resolve_tool_call(text, model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "calculator"
    assert result["arguments"]["expr"]


# ── Added: hyphen/slash false-trigger regression (Bug 1) ────────────────
HYPHEN_SLASH_FALSE_TRIGGERS = [
    "what's the well-known name of this place",
    "what's the state-of-the-art model called",
    "what is your up-to-date status",
    "pass/fail, what's the deal",
]


@pytest.mark.parametrize("text", HYPHEN_SLASH_FALSE_TRIGGERS)
def test_hyphen_slash_never_triggers_calculator(text):
    assert has_probable_tool_intent(text) is False
    result = resolve_tool_call(text, model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] != "calculator"


# ── Added: word-boundary substring false-trigger regression (Bug 2) ─────
WORD_BOUNDARY_FALSE_TRIGGERS = [
    "my training schedule for the marathon",
    "check out this newsletter",
    "restart the process please",
    "my visitor is coming over at 5",
]


@pytest.mark.parametrize("text", WORD_BOUNDARY_FALSE_TRIGGERS)
def test_word_boundary_false_triggers_resolve_to_unknown(text):
    result = resolve_tool_call(text, model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "unknown"


def test_weather_location_excludes_leading_in():
    result = resolve_tool_call("is it going to rain in july", model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "weather"
    assert result["arguments"]["location"] == "july"


# ── Added: open_url validation regression (Bug 3) ───────────────────────
def test_open_url_hallucination_downgrades_to_unknown():
    result = resolve_tool_call(
        "my visitor is coming over at 5", model_name="qwen3:4b", cfg=_FakeCfg()
    )
    assert result["name"] != "open_url"


def test_open_url_valid_url_passes_validation():
    result = resolve_tool_call(
        "please open url https://example.com", model_name="qwen3:4b", cfg=_FakeCfg()
    )
    assert result["name"] == "open_url"
    assert result["arguments"]["url"] == "https://example.com"


# ── Added: decimal/period truncation regression ─────────────────────────
def test_calculator_expr_keeps_decimal_point():
    result = resolve_tool_call("what is 3.5 + 2", model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "calculator"
    assert result["arguments"]["expr"] == "3.5 + 2"


def test_calculate_keyword_keeps_decimal_point():
    result = resolve_tool_call("calculate 12.5 * 4", model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "calculator"
    assert result["arguments"]["expr"] == "12.5 * 4"


def test_web_search_query_keeps_version_number():
    result = resolve_tool_call(
        "search for python 3.11 release notes", model_name="qwen3:4b", cfg=_FakeCfg()
    )
    assert result["name"] == "web_search"
    assert result["arguments"]["query"] == "python 3.11 release notes"


# ── Added: open_app / close_app still work after gate rename ────────────
def test_open_app_still_resolves():
    result = resolve_tool_call("open notepad", model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "open_app"
    assert result["arguments"]["target"] == "notepad"


def test_close_app_still_resolves():
    result = resolve_tool_call("close chrome", model_name="qwen3:4b", cfg=_FakeCfg())
    assert result["name"] == "close_app"
    assert result["arguments"]["target"] == "chrome"
    
class _FakeLLMCfg:
    TOOL_CALLING_MODE = "llm"
    DEBUG_MODE = False
    TOOL_CALLING_TIMEOUT_S = 2.0


@pytest.mark.parametrize("payload", [
    {"name": "restart_system", "arguments": {}},
    {"name": "open_app", "arguments": []},
    {"name": "open_app", "arguments": {}},
    {"name": "open_app", "arguments": {"target": ["chrome"]}},
    {"name": "open_app", "arguments": {"target": "chrome", "shell_command": "unexpected"}},
    {"name": "web_search", "arguments": {"query": "   "}},
    {"name": "calculator", "arguments": {"expr": "hello world"}},
    {"name": "open_url", "arguments": {"url": "javascript:alert(1)"}},
])
def test_invalid_tool_payloads_are_rejected(payload):
    assert _validate_tool_result(payload) == {"name": "unknown", "arguments": {}}


def test_non_dictionary_tool_result_is_rejected():
    assert _validate_tool_result(None) == {"name": "unknown", "arguments": {}}


def test_valid_tool_payload_is_preserved():
    payload = {"name": "open_app", "arguments": {"target": "chrome"}}
    assert _validate_tool_result(payload) == payload


def test_documented_empty_arguments_remain_valid():
    assert _validate_tool_result(
        {"name": "weather", "arguments": {"location": ""}}
    ) == {"name": "weather", "arguments": {"location": ""}}


@pytest.mark.parametrize("text", [
    "open chrome",
    "Chrome chalu kro",
    "chrom khol de",
    "google chrome start kar do",
])
def test_supported_app_phrasings_reach_llm_tool_router(monkeypatch, text):
    monkeypatch.setattr(tool_router, "_circuit_open_until", 0.0)
    monkeypatch.setattr(tool_router, "_consecutive_timeouts", 0)

    calls = []

    def fake_resolve(user_input, model_name, cfg):
        calls.append(user_input)
        return {
            "name": "open_app",
            "arguments": {"target": "chrome"},
        }

    monkeypatch.setattr(tool_router, "_resolve_tool_call_llm", fake_resolve)

    result = resolve_tool_call(
        text,
        model_name="mock-model",
        cfg=_FakeLLMCfg(),
    )

    assert calls == [text]
    assert result == {
        "name": "open_app",
        "arguments": {"target": "chrome"},
    }


def test_mocked_llm_malformed_tool_call_cannot_pass_router(monkeypatch):
    monkeypatch.setattr(tool_router, "_circuit_open_until", 0.0)
    monkeypatch.setattr(tool_router, "_consecutive_timeouts", 0)
    monkeypatch.setattr(tool_router, "_resolve_tool_call_llm", lambda *args: {
        "name": "open_app",
        "arguments": {"target": "chrome", "shell_command": "unexpected"},
    })

    result = resolve_tool_call("open chrome", model_name="mock-model", cfg=_FakeLLMCfg())
    assert result == {"name": "unknown", "arguments": {}}


@pytest.mark.parametrize("text", [
    "Chrome mat kholo",
    "Chrome chalu mat kar",
    "never restart my pc without asking me first",
    "don't clear memory",
    "कंप्यूटर बंद मत करो",
])
def test_tool_router_blocks_negated_commands_before_llm(monkeypatch, text):
    monkeypatch.setattr(
        tool_router,
        "_resolve_tool_call_llm",
        lambda *args: pytest.fail("Negated command must not reach the LLM"),
    )

    result = resolve_tool_call(
        text, model_name="mock-model", cfg=_FakeLLMCfg()
    )

    assert result == {"name": "unknown", "arguments": {}}


class _LocalFallbackCfg:
    TOOL_CALLING_MODE = "heuristic"
    DEBUG_MODE = False
    TOOL_CALLING_TIMEOUT_S = 3.0


@pytest.mark.parametrize(("text", "target"), [
    ("open chrome", "chrome"),
    ("Chrome chalu kro", "chrome"),
    ("chrom khol de", "chrome"),
    ("google chrome start kar do", "chrome"),
    ("file explorer kholo", "file explorer"),
])
def test_heuristic_fallback_resolves_app_open_variants(text, target):
    result = resolve_tool_call(
        text,
        model_name="mock-model",
        cfg=_LocalFallbackCfg(),
    )
    assert result == {
        "name": "open_app",
        "arguments": {"target": target},
    }


@pytest.mark.parametrize("text", [
    "Chrome kaise open karte hain?",
    "How do I open Chrome?",
    "Chrome mujhe pasand hai",
    "Google Chrome review batao",
])
def test_heuristic_fallback_does_not_treat_questions_or_discussion_as_app_open(text):
    result = resolve_tool_call(
        text,
        model_name="mock-model",
        cfg=_LocalFallbackCfg(),
    )
    assert result["name"] != "open_app"


@pytest.mark.parametrize(("text", "target"), [
    ("open chrome", "chrome"),
    ("Chrome chalu kro", "chrome"),
    ("chrom khol de", "chrome"),
    ("google chrome start kar do", "chrome"),
])
def test_llm_quota_failure_falls_back_to_local_app_open(
    monkeypatch, text, target
):
    monkeypatch.setattr(tool_router, "_circuit_open_until", 0.0)
    monkeypatch.setattr(tool_router, "_consecutive_timeouts", 0)

    calls = []

    def simulate_quota_error(user_input, model_name, cfg):
        calls.append(user_input)
        raise RuntimeError("simulated 429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(
        tool_router,
        "_resolve_tool_call_llm",
        simulate_quota_error,
    )

    result = resolve_tool_call(
        text,
        model_name="mock-model",
        cfg=_FakeLLMCfg(),
    )

    assert calls == [text]
    assert result == {
        "name": "open_app",
        "arguments": {"target": target},
    }