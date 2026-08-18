import pytest
from sara.core.tool_router import has_probable_tool_intent, resolve_tool_call


class _FakeCfg:
    TOOL_CALLING_MODE = "heuristic"  # no network calls in tests
    DEBUG_MODE = False
    TOOL_CALLING_TIMEOUT_S = 3.0


SHOULD_NOT_TRIGGER_CALCULATOR = [
    "what is the name of my girlfriend",
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