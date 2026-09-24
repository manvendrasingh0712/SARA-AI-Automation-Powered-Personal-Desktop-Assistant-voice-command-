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


def _get_embedding_vector(cfg, text: str):
    """
    Branches on cfg.EMBEDDING_BACKEND ("gemini" default, or "ollama") to
    return a raw list[float] embedding for `text`, or None on any
    failure. Shared by sara.core.rag's embed_text()/LongTermMemory so
    both callers use the exact same provider-selection logic instead of
    duplicating it. Reuses the existing _get_gemini_client()/
    _get_ollama_client() lazy accessors above -- no new client-
    construction pattern.
    """
    backend = getattr(cfg, "EMBEDDING_BACKEND", "gemini")
    if backend == "ollama":
        client = _get_ollama_client(cfg)
        if client is None:
            return None
        model = getattr(cfg, "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
        try:
            resp = client.embed(model=model, input=text)
        except Exception as e:
            print(f"[Embeddings] Ollama embed() call failed: {e}")
            return None
        embeddings = getattr(resp, "embeddings", None)
        if not embeddings:
            return None
        return embeddings[0]

    client = _get_gemini_client(cfg)
    if client is None:
        return None
    model = getattr(cfg, "EMBEDDING_MODEL", "gemini-embedding-001")
    # Per-call timeout from Config.EMBEDDING_TIMEOUT_S (SDK expects
    # milliseconds). Built defensively: if the installed google-genai
    # doesn't support http_options on EmbedContentConfig, fall back to the
    # old unbounded call instead of breaking embeddings.
    embed_config = None
    try:
        from google.genai import types as _gtypes

        timeout_s = float(getattr(cfg, "EMBEDDING_TIMEOUT_S", 4.0))
        embed_config = _gtypes.EmbedContentConfig(
            http_options=_gtypes.HttpOptions(timeout=int(timeout_s * 1000))
        )
    except Exception as e:
        print(f"[Embeddings] per-call timeout unsupported by this SDK, continuing without it: {e}")
        embed_config = None

    try:
        if embed_config is not None:
            result = client.models.embed_content(
                model=model, contents=text, config=embed_config
            )
        else:
            result = client.models.embed_content(model=model, contents=text)
    except Exception as e:
        print(f"[Embeddings] Gemini embed_content() call failed: {e}")
        return None
    embeddings = getattr(result, "embeddings", None)
    if not embeddings:
        return None
    return embeddings[0].values


def _select_llm_client(cfg, ollama_getter, gemini_getter):
    """
    Shared provider-selection helper for tool-calling call sites
    (sara.core.planning.planner / sara.core.planning.executor), so both
    modules branch on Config.LLM_BACKEND identically instead of each
    duplicating the same if/else. Callers pass their OWN module-level
    _get_ollama_client/_get_gemini_client references (not this module's)
    so tests can still mock.patch those names on the calling module.
    Returns (backend_name, client) -- backend_name is "ollama" or
    "gemini"; client may be None if that backend's client failed to
    initialize (callers must check for None themselves, same as before
    this helper existed).
    """
    backend = getattr(cfg, "LLM_BACKEND", "gemini")
    if backend == "ollama":
        return "ollama", ollama_getter(cfg)
    return "gemini", gemini_getter(cfg)