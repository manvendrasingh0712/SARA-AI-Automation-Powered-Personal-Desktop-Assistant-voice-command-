"""Audit part 2, D5: dates, versions, units, ordinals, abbreviations, AM/PM, phone numbers in TTS text."""
import pytest

from sara.audio.tts.text_prep import clean_for_tts as c


@pytest.mark.parametrize("text,expected", [
    ("15/08/2026", "fifteenth of August twenty twenty-six"),
    ("31-10-2026", "thirty-first of October twenty twenty-six"),
    ("2026-10-31", "thirty-first of October twenty twenty-six"),
    ("31 October 2026", "thirty-first of October twenty twenty-six"),
    ("October 31, 2026", "thirty-first of October twenty twenty-six"),
    ("v2.3.1", "version two point three point one"),
    ("a 16GB card", "a sixteen gigabytes card"),
    ("70kg", "seventy kilograms"),
    ("a 5GHz CPU", "a five gigahertz CPU"),
    ("512 GB", "five hundred twelve gigabytes"),
    ("1 GB", "one gigabyte"),
    ("2.5 GB", "two point five gigabytes"),
    ("60 km/h", "sixty kilometres per hour"),
    ("the 5th floor", "the fifth floor"),
    ("3rd and 1st", "third and first"),
    ("22nd", "twenty-second"),
    ("e.g. apples", "for example apples"),
    ("i.e. fruit", "that is fruit"),
    ("Dr. Rao", "Doctor Rao"),
    ("Mr. Rao", "Mister Rao"),
    ("3:15 PM", "three fifteen P M"),
    ("7:00 AM", "seven o'clock A M"),
    ("call 9876543210", "call nine eight seven, six five four, three two one zero"),
])
def test_english_forms(text, expected):
    assert c(text) == expected


@pytest.mark.parametrize("text", ["3rd", "AI302", "B2", "version 2", "there are 5 apples"])
def test_things_that_must_not_be_mangled(text):
    out = c(text)
    assert "threerd" not in out and "fortytwo" not in out.lower()
    assert c("there are 5 apples") == "there are five apples"


def test_no_digits_no_units_in_the_wrong_places():
    assert c("a 5 g pack") != "a five grams pack"      # a spaced single letter is not a unit
    assert c("12 gb of 3 l") .count("gigabyte") == 1


def test_hindi_phrase_stays_in_one_script():
    out = c("मेरे पास 16GB रैम है")
    assert "GB" not in out and "गीगाबाइट" in out
    out = c("आज 31 October 2026 है")
    assert "October" not in out and "अक्टूबर" in out
    out = c("बैठक 3:15 PM पर है")
    assert "PM" not in out and "दोपहर" in out


def test_phone_number_without_a_cue_word_is_left_alone():
    assert c("order 9876543210 shipped") == "order 9876543210 shipped"


def test_international_phone_number_after_call():
    out = c("Call me on +91 98765 43210")
    assert out.startswith("Call me on plus ") and "ninety-eight thousand" not in out
