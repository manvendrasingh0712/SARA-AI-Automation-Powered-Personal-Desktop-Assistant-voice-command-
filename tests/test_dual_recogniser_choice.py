from sara.audio.stt.helpers import choose_transcript


def _routes(text):
    return any(w in text.lower() for w in ("open", "kholo", "volume", "pause", "next"))


def _choose(a, lang="en", prob=0.95, lp=-0.2, b="", calls=None):
    def run_b():
        if calls is not None:
            calls.append(1)
        return b

    return choose_transcript(a, lang, prob, lp, run_b, _routes)


def test_clear_english_command_never_runs_the_second_model():
    calls = []
    assert _choose("Open Chrome.", b="Opan krom", calls=calls) == ("A", "Open Chrome.")
    assert calls == []


def test_hinglish_command_is_taken_from_the_hinglish_model():
    # the main model heard noise ("Drum kolu."), the Hinglish model heard a command
    assert _choose("Drum kolu.", lang="en", prob=0.6, lp=-1.1, b="Chrome kholo.") == ("B", "Chrome kholo.")


def test_confident_english_conversation_stays_with_the_main_model():
    assert _choose("Who is the prime minister of India?", b="Hu is the prime minister of India") == (
        "A", "Who is the prime minister of India?")


def test_unsure_conversation_goes_to_the_hinglish_model():
    assert _choose("Today's weather is so good.", lang="en", prob=0.5, lp=-0.9, b="Aaj mausam kitna achchha hai.") == (
        "B", "Aaj mausam kitna achchha hai.")


def test_devanagari_from_the_main_model_is_never_treated_as_english():
    assert _choose("क्रोम खोलो", lang="hi", prob=0.9, lp=-0.3, b="Chrome kholo")[0] == "B"


def test_empty_hinglish_result_falls_back_to_the_main_text():
    assert _choose("hello there", lang="en", prob=0.5, lp=-0.9, b="") == ("A", "hello there")


def test_both_empty():
    assert _choose("", lang=None, prob=0.0, lp=-9.0, b="") == ("A", "")


# ---------------------------------------------------------------- "Hinglish model first" ordering
from sara.audio.stt.helpers import need_main_model, pick_after_hinglish_first


def test_a_command_from_the_hinglish_model_is_final():
    assert not need_main_model("Chrome kholo.", -0.9, _routes)


def test_confident_plain_sentence_needs_no_second_opinion():
    assert not need_main_model("Tell me a story about a king", -0.2, _routes)


def test_unsure_or_short_or_empty_needs_the_main_model():
    assert need_main_model("Tell me a story about a king", -0.8, _routes)     # unsure
    assert need_main_model("hello there", -0.1, _routes)                      # two words
    assert need_main_model("", -9.0, _routes)
    assert need_main_model("Mujhe kahani sunao", -0.9, _routes)


def test_main_model_wins_with_an_english_command_or_a_confident_english_sentence():
    assert pick_after_hinglish_first("Positive music.", "Pause the music.", "en", 0.6, -0.9, _routes) == ("A", "Pause the music.")
    assert pick_after_hinglish_first("Tell me a story", "Tell me a story about a king.", "en", 0.97, -0.2, _routes)[0] == "A"


def test_hinglish_text_stays_when_the_main_model_is_unsure_or_not_english():
    assert pick_after_hinglish_first("Tum kaise ho", "Tun che sigo", "es", 0.4, -1.0, _routes) == ("B", "Tum kaise ho")
    assert pick_after_hinglish_first("Aaj mausam", "आज मौसम", "hi", 0.9, -0.2, _routes)[0] == "B"


def test_level_for_main_raises_quiet_clips_without_clipping():
    import numpy as np

    from sara.audio.stt.engine import SpeechToText

    quiet = (np.sin(np.arange(16000) / 7.0) * 300).astype(np.int16)           # RMS ~212, like the user's voice
    out = SpeechToText._level_for_main(quiet)
    assert 0.07 < float(np.sqrt(np.mean(out ** 2))) < 0.09 and float(np.abs(out).max()) <= 1.0
    assert float(np.abs(SpeechToText._level_for_main(np.zeros(100, np.int16))).max()) == 0.0
