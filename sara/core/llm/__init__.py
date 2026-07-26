"""
sara.core.llm
Public package API for the LLM engine. External code should only ever do:

    from sara.core.llm import SaraLLM

Internal layout:
    prompt.py     - system-prompt construction (persona, time-of-day, language)
    streaming.py  - sentence/clause-boundary helpers for streamed output
    clients.py    - lazy Ollama / Gemini client construction
    engine.py     - SaraLLM, the class everything else wires into
"""
from .engine import SaraLLM

__all__ = ["SaraLLM"]
