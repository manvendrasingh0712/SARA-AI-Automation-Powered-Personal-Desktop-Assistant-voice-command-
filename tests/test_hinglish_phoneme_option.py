"""KOKORO_HINGLISH_PHONEMES picks the phonemizer for romanized Hinglish only."""
from config import Config
from sara.audio.tts.voice_params import _build_params


def test_default_keeps_the_hindi_phonemizer(monkeypatch):
    monkeypatch.setattr(Config, "KOKORO_HINGLISH_PHONEMES", "hi", raising=False)
    p = _build_params("hinglish")
    assert p.voice == Config.KOKORO_VOICE_HI and p.lang_code == Config.KOKORO_LANG_HI


def test_en_option_uses_english_phonemes_with_the_hindi_voice(monkeypatch):
    monkeypatch.setattr(Config, "KOKORO_HINGLISH_PHONEMES", "en", raising=False)
    p = _build_params("hinglish")
    assert p.voice == Config.KOKORO_VOICE_HI and p.lang_code == Config.KOKORO_LANG_EN


def test_devanagari_always_keeps_hindi(monkeypatch):
    monkeypatch.setattr(Config, "KOKORO_HINGLISH_PHONEMES", "en", raising=False)
    assert _build_params("hi").lang_code == Config.KOKORO_LANG_HI
    assert _build_params("en").voice == Config.KOKORO_VOICE_EN
