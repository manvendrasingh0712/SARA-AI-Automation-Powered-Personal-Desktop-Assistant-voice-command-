"""
sara.audio.stt
Public package API for speech-to-text. External code should only ever do:

    from sara.audio.stt import SpeechToText

Internal layout:
    helpers.py  - stateless RMS/language-detection/hallucination-filter helpers
    buffers.py  - VAD, ring/pre-buffers, noise-floor, collection state machine
    engine.py   - SpeechToText, the class everything else wires into
"""
from .engine import SpeechToText

__all__ = ["SpeechToText"]
