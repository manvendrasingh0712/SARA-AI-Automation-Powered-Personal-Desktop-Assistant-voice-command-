"""
sara.audio.tts
Public package API for text-to-speech. External code should only ever do:

    from sara.audio.tts import TextToSpeech, clean_for_tts

Internal layout (each file is one concern, not one giant class):
    voice_params.py  - per-language speed/pitch presets
    text_prep.py      - text normalization + adaptive sentence/chunk splitting
    cache.py           - short-phrase synthesis cache
    synth.py            - Kokoro ONNX synthesis + volume shaping
    player.py            - persistent audio playback worker
    engine.py             - TextToSpeech, the class everything else wires into
"""
from .engine import TextToSpeech
from .text_prep import clean_for_tts

__all__ = ["TextToSpeech", "clean_for_tts"]
