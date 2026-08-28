"""
sara.core.llm.clients
Lazy client construction for the Ollama and Gemini backends.
"""
from __future__ import annotations



import threading

# ══════════════════════════════════════════════════════════════════════
# Lazy backend client accessors
# ══════════════════════════════════════════════════════════════════════
_ollama_client = None
_gemini_client = None
_client_lock = threading.Lock()


def _get_ollama_client(cfg):
    global _ollama_client
    if _ollama_client is not None:
        return _ollama_client
    with _client_lock:
        if _ollama_client is not None:
            return _ollama_client
        try:
            import ollama as _lib

            _ollama_client = _lib.Client(
                host=getattr(cfg, "OLLAMA_HOST", "http://localhost:11434"),
                # v7: fallback default now matches Config.OLLAMA_TIMEOUT's
                # own default (30s) instead of a stale, inconsistent 10.0.
                timeout=getattr(cfg, "OLLAMA_TIMEOUT", 30.0),
            )
        except ImportError:
            print("[LLM Error] 'ollama' missing. Run: pip install ollama")
        except Exception as e:
            print(f"[LLM Error] Ollama client init failed: {e}")
    return _ollama_client


def _get_gemini_client(cfg):
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    with _client_lock:
        if _gemini_client is not None:
            return _gemini_client
        try:
            from google import genai as _genai

            _gemini_client = _genai.Client(api_key=cfg.GEMINI_API_KEY)
        except Exception as e:
            print(f"[LLM Error] Gemini init failed: {e}")
    return _gemini_client