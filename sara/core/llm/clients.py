"""
sara.core.llm.clients
Lazy client construction for the Ollama and Gemini backends.
"""
from __future__ import annotations



import logging
import re
import random
import threading
from collections import OrderedDict
import time
from sara.core.diag import swallowed

logger = logging.getLogger(__name__)

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


def ollama_load_options(cfg) -> dict:
    """Options that decide how Ollama LOADS the model (context size, GPU
    layers). Every Ollama call must send the same values: if two calls
    differ, Ollama unloads and reloads the model between them, which costs
    seconds on every switch. OLLAMA_NUM_GPU unset = Ollama picks how many
    layers fit; a large value (e.g. 99) forces every layer onto the GPU."""
    opts = {"num_ctx": int(getattr(cfg, "OLLAMA_NUM_CTX", 4096))}
    num_gpu = getattr(cfg, "OLLAMA_NUM_GPU", None)
    if num_gpu is not None:
        opts["num_gpu"] = int(num_gpu)
    return opts


_EMBED_COOLDOWN_S = 20.0

# Small LRU of finished embeddings: the same question is embedded more than once per turn (memory search, memory2
# retrieval, RAG hint) and again on a repeat. Keyed by provider + model + normalised text.
_EMBED_CACHE_MAX = 256
_embed_cache: "OrderedDict[tuple, list]" = OrderedDict()
_embed_cache_lock = threading.Lock()


def _embed_cache_key(cfg, text: str):
    backend = getattr(cfg, "EMBEDDING_BACKEND", "gemini")
    model = getattr(cfg, "OLLAMA_EMBEDDING_MODEL" if backend == "ollama" else "EMBEDDING_MODEL", "")
    return (backend, model, " ".join((text or "").lower().split()))
_embed_down_until = 0.0


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
    key = _embed_cache_key(cfg, text)
    with _embed_cache_lock:
        hit = _embed_cache.get(key)
        if hit is not None:
            _embed_cache.move_to_end(key)
            return list(hit)
    vec = _get_embedding_vector_uncached(cfg, text)
    if vec is not None:
        with _embed_cache_lock:
            _embed_cache[key] = list(vec)
            while len(_embed_cache) > _EMBED_CACHE_MAX:
                _embed_cache.popitem(last=False)
    return vec


def _get_embedding_vector_uncached(cfg, text: str):
    backend = getattr(cfg, "EMBEDDING_BACKEND", "gemini")
    if backend == "ollama":
        client = _get_ollama_client(cfg)
        if client is None:
            return None
        model = getattr(cfg, "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
        kwargs = {"keep_alive": getattr(cfg, "OLLAMA_KEEP_ALIVE", "5m")}
        num_gpu = getattr(cfg, "OLLAMA_EMBEDDING_NUM_GPU", None)
        if num_gpu is not None:
            # 0 keeps the embedder on CPU so it never evicts the chat
            # model from a small GPU.
            kwargs["options"] = {"num_gpu": int(num_gpu)}
        # Ollama just failed: skip embeddings for a short cool-down instead
        # of paying the failed connect on every RAG lookup in the turn.
        global _embed_down_until
        if time.monotonic() < _embed_down_until:
            return None
        try:
            resp = client.embed(model=model, input=text, **kwargs)
        except Exception as e:
            print(f"[Embeddings] Ollama embed() call failed: {e}")
            _embed_down_until = time.monotonic() + _EMBED_COOLDOWN_S
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

    max_retries = max(0, int(getattr(cfg, "EMBEDDING_MAX_RETRIES", 2)))
    base_delay_s = float(getattr(cfg, "EMBEDDING_RETRY_BASE_DELAY_S", 0.5))
    max_delay_s = 4.0
    result = None
    for attempt in range(max_retries + 1):
        try:
            if embed_config is not None:
                result = client.models.embed_content(
                    model=model, contents=text, config=embed_config
                )
            else:
                result = client.models.embed_content(model=model, contents=text)
            break
        except Exception as e:
            status = getattr(e, "code", None)
            transient = isinstance(status, int) and 500 <= status <= 599
            if transient and attempt < max_retries:
                delay_s = min(max_delay_s, base_delay_s * (2 ** attempt))
                delay_s += random.uniform(0, delay_s * 0.25)
                logger.warning(
                    "[Embeddings] Gemini embed_content() HTTP %s on attempt %d/%d "
                    "(model=%s, chars=%d); retrying in %.2fs: %s",
                    status, attempt + 1, max_retries + 1, model, len(text), delay_s, e,
                )
                time.sleep(delay_s)
                continue
            # D56: a 5xx from a remote service is an expected condition, not
            # a defect in this code, and the traceback is identical every
            # time -- the same three frames inside google.genai plus
            # tenacity. On the target machine gemini-embedding-001 returned
            # HTTP 500 on most turns, so this printed a full traceback per
            # turn and buried the failures that are worth reading.
            #
            # The status is already classified above, so use it: keep
            # exc_info for anything we could NOT classify as server-side,
            # where not knowing the call path is the actual problem. The
            # one-line message below still carries status, model, text
            # length and the exception type either way.
            server_side = isinstance(status, int) and 500 <= status <= 599
            logger.error(
                "[Embeddings] Gemini embed_content() FAILED after %d attempt(s) "
                "(model=%s, chars=%d, status=%s): %s: %s",
                attempt + 1, model, len(text), status, type(e).__name__, e,
                exc_info=not server_side,
            )
            return None
    embeddings = getattr(result, "embeddings", None)
    if not embeddings:
        logger.warning(
            "[Embeddings] Gemini returned no embeddings (model=%s, chars=%d).",
            model, len(text),
        )
        return None
    return embeddings[0].values


# Gemini 3.5 and newer (and 4+) reject the sampling parameters temperature / top_p / top_k (Google's migration
# notes for 3.5 Flash-Lite and 3.6+). Keep the rule in one place so a model the smoke test (bench/gemini_smoke.py)
# proves to reject them is a one-line addition.
_MODEL_REJECTS_SAMPLING = re.compile(r"^(?:models/)?gemini-(?:3\.(?:[5-9]|\d{2,})|[4-9])", re.IGNORECASE)
_SAMPLING_KEYS = ("temperature", "top_p", "top_k")
_thinking_logged = False


def _model_rejects_sampling(model) -> bool:
    return bool(model) and bool(_MODEL_REJECTS_SAMPLING.match(str(model).strip()))


def _build_gemini_generate_config(types_module, timeout_s, *, model=None, thinking_budget=None, **config_kwargs):
    """
    The one place a Gemini GenerateContentConfig is built (a test fails if another module calls
    GenerateContentConfig( directly).

    - `model`: when it is a model that rejects sampling parameters, temperature / top_p / top_k are dropped.
    - `thinking_budget`: optional token budget for the model's reasoning (0 or a small number keeps voice replies
      fast); skipped, with one log line, if the installed SDK has no ThinkingConfig.
    - `timeout_s`: real per-call I/O timeout (http_options, SDK expects milliseconds); falsy = none. `types_module`
      is the caller's own lazily-imported `google.genai.types`. Older SDKs without http_options get the same
      config without it.
    """
    global _thinking_logged
    if _model_rejects_sampling(model):
        for key in _SAMPLING_KEYS:
            config_kwargs.pop(key, None)
    if thinking_budget is not None:
        # Measured (bench/gemini_smoke.py): gemini-3.5-flash-lite answers HTTP 400 to a budget of 0 but accepts 128.
        if model and re.search(r"flash-lite", str(model), re.IGNORECASE) and int(thinking_budget) < 128:
            thinking_budget = 128
        try:
            config_kwargs["thinking_config"] = types_module.ThinkingConfig(thinking_budget=int(thinking_budget))
        except Exception:  # noqa: BLE001 -- SDK without ThinkingConfig
            if not _thinking_logged:
                _thinking_logged = True
                logger.info("[Gemini] this google-genai version has no ThinkingConfig; thinking budget not set")
    if timeout_s:
        try:
            return types_module.GenerateContentConfig(
                http_options=types_module.HttpOptions(timeout=int(float(timeout_s) * 1000)),
                **config_kwargs,
            )
        except Exception as _exc:  # noqa: BLE001 -- older SDK without http_options support
            swallowed(logger, "clients._build_gemini_generate_config:239", _exc)
    return types_module.GenerateContentConfig(**config_kwargs)


def backend_timeout(cfg, key: str, default: float) -> float:
    """Timeout `key`, or its `<KEY minus _S>_GEMINI_S` twin when the active backend is Gemini (a cloud model answers a
    tool call in ~1-2 s, so waiting 8 s for it only prolongs the silence before the fallback). Falls back to the
    plain key when the Gemini twin is not set."""
    base = float(getattr(cfg, key, default))
    if str(getattr(cfg, "LLM_BACKEND", "")).lower() != "gemini":
        return base
    twin = getattr(cfg, key[:-2] + "_GEMINI_S" if key.endswith("_S") else key + "_GEMINI", None)
    try:
        return float(twin) if twin is not None else base
    except (TypeError, ValueError):
        return base


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