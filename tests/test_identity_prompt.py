"""'Who made you' used to be answered with the name of the underlying model's company; every prompt language now carries the rule."""
import pytest

from sara.core.llm.prompt import _build_base_prompt


@pytest.mark.parametrize("lang", ["english", "hindi", "hinglish"])
def test_every_language_tells_the_model_not_to_claim_a_company_built_it(lang):
    prompt = _build_base_prompt("SARA", "It is evening.", lang, None)
    assert "who made or trained you" in prompt
    assert "do not claim any company built you" in prompt
