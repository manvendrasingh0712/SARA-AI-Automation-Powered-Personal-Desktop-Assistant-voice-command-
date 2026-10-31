"""Audit part 2, O10: the chat reply cannot claim an action that no tool performed, and cannot loop."""
import pytest

from sara.core.llm.guardrails import FALLBACK, claims_action, guard_stream


def _run(user, chunks):
    return list(guard_stream(iter(chunks), user))


@pytest.mark.parametrize("user,reply", [
    ("opn chrmoe", ["Chrome opened!", "Enjoy browsing."]),
    ("set a timer for ten minutes", ["Okay, I've set the timer for ten minutes."]),
    ("mute the volume", ["Done."]),
    ("youtube kholo", ["Maine youtube khol diya."]),
    ("turn on dark mode", ["I turned on dark mode."]),
])
def test_false_action_claims_become_one_honest_sentence(user, reply):
    out = _run(user, reply)
    assert out[0] in FALLBACK.values() and len(out) == 1


def test_hinglish_user_gets_the_hinglish_fallback():
    assert _run("chrome kholo", ["Chrome khol diya!"]) == [FALLBACK["hi"]]


@pytest.mark.parametrize("user,reply", [
    ("what is the capital of france", ["Paris."]),
    ("tell me a joke", ["I opened my fridge and found a joke inside. It was cold."]),  # not a command-shaped request
    ("who opened the first bank", ["Medici family opened the first bank in Florence."]),
    ("what does the word open mean", ["Open means not closed."]),
])
def test_normal_answers_pass_unchanged(user, reply):
    assert _run(user, reply) == reply


def test_repetition_loop_is_cut_after_the_second_copy():
    out = _run("tell me a story", ["Once upon a time."] + ["And then it rained."] * 10)
    assert out.count("And then it rained.") == 2


def test_claim_detector_unit():
    assert claims_action("Chrome opened!") and claims_action("Done.") and claims_action("Volume badha diya.")
    assert not claims_action("The door was opened by the wind and stayed open.")


def test_devanagari_reply_to_a_roman_user_is_regenerated_once():
    import sys

    called = []

    def regen():
        called.append(1)
        return iter(["Delhi is the capital."])

    out = list(guard_stream(iter(["दिल्ली भारत की राजधानी है।"]), "india ki rajdhani kya hai", expect_latin=True, regenerate=regen))
    assert out == ["Delhi is the capital."] and called == [1]
    # a Devanagari question keeps its Devanagari answer, and so does a manual Hindi mode (expect_latin False)
    out = list(guard_stream(iter(["दिल्ली।"]), "भारत की राजधानी क्या है", expect_latin=False, regenerate=regen))
    assert out == ["दिल्ली।"] and called == [1]
