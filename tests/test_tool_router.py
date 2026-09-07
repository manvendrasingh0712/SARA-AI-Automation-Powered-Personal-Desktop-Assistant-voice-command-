import pytest
from sara.core.tool_router import has_probable_tool_intent, resolve_tool_call


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