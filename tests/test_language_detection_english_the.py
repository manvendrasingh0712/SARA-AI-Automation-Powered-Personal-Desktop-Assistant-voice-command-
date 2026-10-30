"""
English must not be labelled Hinglish just because it contains "the".

E2E log 2026-10-30: "Do you know the character of the character of the
character?" -> Language this turn (auto): 'hinglish' -> answered in the
Hindi voice. The marker list contained "the" (Hindi "were"); 7 of 9 plain
English sentences were mislabelled. The same detector chooses the reply
language and the TTS voice.
"""
import pytest

from sara.audio.stt.helpers import _detect_language

ENGLISH = [
    "Do you know the character of the character of the character?",
    "tell me about the history of the roman empire",
    "what is the capital of the country",
    "the weather in the city is nice",
    "How can the PM be elected by the people",
    "open the file and read the first line",
    "I want to find the best route to the airport",
    "hey bro what is the time",
    "Tell me the story, Resini and Revi.",
]

HINGLISH = [
    "wo log ghar par the",
    "mujhe kal subah chhe baje uthna hai",
    "kya tum mujhe ek joke suna sakti ho",
    "aaj ka din kaisa tha",
    "mera mood kharab hai, kuch accha bolo",
    "tum kahan the kal",
    "yaar ye kya hai bhai",
]


@pytest.mark.parametrize("text", ENGLISH)
def test_plain_english_is_english(text):
    assert _detect_language(text) == "en"


@pytest.mark.parametrize("text", HINGLISH)
def test_hinglish_is_still_hinglish(text):
    assert _detect_language(text) == "hinglish"


def test_devanagari_is_unchanged():
    assert _detect_language("मुझे एक कहानी सुनाओ") == "hi"
