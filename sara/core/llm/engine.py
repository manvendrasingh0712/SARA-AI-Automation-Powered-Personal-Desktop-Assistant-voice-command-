"""
sara.core.llm.engine
SaraLLM -- the public class. Wires prompt + streaming + clients together
into generate_response()/generate_response_stream().
"""
from __future__ import annotations

from .prompt import _build_base_prompt, _time_of_day
from .streaming import WarmupResult, _split_sentences, _clause_flush, _clean_markdown, _estimate_tokens
from .clients import _get_ollama_client, _get_gemini_client


import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator, List, Optional, Tuple

from config import Config

# ══════════════════════════════════════════════════════════════════════
# Module-level compiled regexes
# ══════════════════════════════════════════════════════════════════════

logger = logging.getLogger("sara.llm.engine")


# PRIORITY-7 FIX: dedicated subclass (still a RuntimeError, so any
# existing external `except RuntimeError` handling elsewhere keeps
# working unchanged) used ONLY for the two deterministic "client isn't
# available at all" cases below. This lets _stream_generic() fail-fast
# on exactly that case instead of blanket-matching RuntimeError, which
# would also swallow-and-not-retry genuinely transient RuntimeErrors
# raised deeper inside the ollama/genai client libraries.
class _ClientUnavailableError(RuntimeError):
    """Raised when the Ollama/Gemini client itself failed to load or
    initialize. Retrying this is pointless — the client won't spawn
    into existence on attempt 2 — so it's treated as non-retryable."""


# ══════════════════════════════════════════════════════════════════════
# Localized fallback messages (v7) — used instead of raw exception text
# anywhere a reply could reach speak_stream()/speak() and be read aloud.
# ══════════════════════════════════════════════════════════════════════

_STREAM_FAIL_MESSAGES = {
    "english": "Sorry, I'm having trouble reaching my brain right now — could you try that again in a moment?",
    "hindi": "Maafi chahta hoon, abhi thodi dikkat aa rahi hai — thodi der baad phir try karo.",
    "hinglish": "Sorry yaar, abhi thoda glitch ho raha hai — thodi der baad dobara try karna.",
}

_STREAM_INTERRUPTED_MESSAGES = {
    "english": "Hmm, my connection glitched mid-thought — that's all I've got for now.",
    "hindi": "Hmm, beech mein connection mein dikkat aa gayi — abhi itna hi keh sakta hoon.",
    "hinglish": "Hmm, beech mein thoda glitch ho gaya — abhi bas itna hi.",
}


# ══════════════════════════════════════════════════════════════════════
# Language-aware system prompt templates
# ══════════════════════════════════════════════════════════════════════




_SUMMARY_SYSTEM_PROMPTS = {
    "english": (
        "You are Sara, a voice assistant. Summarize the following article in "
        "2 to 3 short, natural sentences suitable for being read aloud. "
        "Do NOT use markdown. Be concise and conversational. "
        "Respond in English."
    ),
    "hindi": (
        "Aap Sara hain, ek voice assistant. Neeche diye gaye article ko "
        "2 se 3 chhote, natural vaakyon mein summarize karo, jo bolkar "
        "sunaye ja sakein. Markdown ka use mat karo. Chhota aur seedha "
        "jawab do. Hindi mein jawab do."
    ),
    "hinglish": (
        "Tu Sara hai, ek voice assistant. Neeche diye article ko 2 se 3 "
        "chhote, natural sentences mein summarize kar — jo bolkar sunaya "
        "ja sake. Markdown use nahi karna. Chhota aur seedha rakh. "
        "Hinglish mein jawab de."
    ),
}

_SUMMARY_FAIL_MESSAGES = {
    "english": "Sorry, I couldn't summarize that right now — give it another try in a bit.",
    "hindi": "Maafi chahta hoon, abhi summarize nahi ho paaya — thodi der baad try karo.",
    "hinglish": "Sorry yaar, abhi summarize nahi ho paya — thodi der baad phir try karna.",
}


# ══════════════════════════════════════════════════════════════════════
# Main class
# ══════════════════════════════════════════════════════════════════════


class SaraLLM:
    """
    Unified LLM wrapper for Ollama (local) and Gemini API.
    Supports English, Hindi, and Hinglish personality modes.
    """

    # BUGFIX (root cause of "preview mode, no backend connected" hanging
    # forever, plus audio input/output overflow/underflow around startup):
    # pywebview's inject_pywebview()/get_functions() walks dir(Api_instance)
    # and RECURSES into every non-underscore, non-callable attribute that
    # has a __module__ (i.e. every plain class instance) to look for nested
    # API classes. self.brain (this SaraLLM instance) is reachable directly
    # off the Api object, so pywebview was recursively enumerating this
    # entire live LLM engine's internals (locks, queues, client handles) on
    # a background thread during window creation -- slow at best, and a
    # deadlock at worst if that walk ever touches something holding a lock
    # the audio threads need. `_serializable = False` is pywebview's own
    # documented flag for "don't walk into this object" -- see
    # https://pywebview.flowrl.com/guide/interdomain (nested classes with
    # _serializable = False are omitted).
    _serializable = False

    _VALID_LANGS = {"english", "hindi", "hinglish"}

    # PRIORITY-2 FIX (revised): fixed whitelist instead of a word-count
    # cutoff — a plain "<=2 words" rule wrongly caught short-but-real
    # memory-dependent queries like "my name?" or "remember me". Only
    # exact matches to obvious greetings/acknowledgements skip RAG.
    _TRIVIAL_PROMPTS = {
        "hi", "hello", "hey", "yo", "hii", "hiya",
        "ok", "okay", "k", "kk", "fine",
        "haan", "han", "ha", "haa", "nahi", "nahin", "no",
        "hmm", "hm", "achha", "acha", "theek hai", "thik hai",
        "thanks", "thank you", "thanks yaar", "thank you yaar", "ty",
        "bye", "bye bye", "goodbye", "good bye", "namaste", "ok bye",
    }

    def __init__(self, cfg=None, memory=None) -> None:
        self._cfg = cfg if cfg is not None else Config
        # PRODUCTION-AUDIT ADDITION (Phase 2 — RAG): optional
        # sara.core.rag.LongTermMemory instance, shared with the rest of
        # the app (constructed once in gui_main.py's build_core_objects()).
        # None is a fully supported value — every retrieval call below is
        # guarded, so SaraLLM behaves EXACTLY as before this feature
        # existed if no memory store is provided (e.g. in tests).
        self._memory = memory
        # PRIORITY-3 FIX: single bounded background worker (stdlib
        # ThreadPoolExecutor, max_workers=1) reused across every call,
        # instead of spawning a brand-new daemon thread per request for
        # maybe_extract_fact(). Created lazily (see _fact_extract_pool
        # property) only the first time it's actually needed, and only
        # when a memory store is configured at all — no project-wide
        # worker/queue mechanism was found in the files available to
        # audit, so this is the smallest bounded stdlib option.
        self._fact_executor: Optional[ThreadPoolExecutor] = None
        # LIFECYCLE FIX: dedicated lock so _get_fact_extract_pool() can't
        # race and create two executors if two requests hit it at once.
        self._fact_executor_lock = threading.Lock()

        self.user_name: Optional[str] = None
        self._tz: str = getattr(self._cfg, "SARA_TIMEZONE", "local")

        raw_lang = getattr(self._cfg, "SARA_LANGUAGE", "english").lower().strip()
        self._lang: str = raw_lang if raw_lang in self._VALID_LANGS else "english"

        self._tod: str = _time_of_day(self._tz, self._lang)
        self.system_instruction: str = ""
        self._sys_prompt_tokens: int = 0

        self._build_and_cache_system_instruction()

        self._history: deque[Tuple[str, str]] = deque(
            maxlen=self._cfg.MAX_MEMORY_EXCHANGES
        )
        self._history_lock = threading.Lock()

        self._warm_event = threading.Event()
        self._warmup_error: Optional[str] = None

        if getattr(self._cfg, "LLM_BACKEND", "ollama") == "ollama":
            self.model_name = getattr(self._cfg, "OLLAMA_MODEL", "llama3")
            self._check_ollama()
        else:
            self.model_name = getattr(self._cfg, "GEMINI_MODEL", "gemini-2.5-flash")
            # Pre-load Gemini imports in background to kill cold-start latency
            threading.Thread(
                target=self._preload_gemini, daemon=True, name="gemini-preload"
            ).start()
            self._warm_event.set()

        self._log_init()

    # ── Context manager ────────────────────────────────────────────────

    def __enter__(self) -> "SaraLLM":
        return self

    def __exit__(self, *_) -> None:
        self._shutdown_fact_executor()

    def _shutdown_fact_executor(self) -> None:
        # LIFECYCLE FIX: only tears down the pool if one was actually
        # created (lazy init means many instances never need one).
        # wait=True blocks until any in-flight fact-extraction finishes,
        # but spawns no new thread and doesn't touch the LLM request path.
        if self._fact_executor is not None:
            self._fact_executor.shutdown(wait=True)

    # ── System prompt (Cached) ────────────────────────────────────────

    def _build_and_cache_system_instruction(self) -> None:
        name = getattr(self._cfg, "SARA_NAME", "Sara")
        self.system_instruction = _build_base_prompt(
            name, self._tod, self._lang, self.user_name
        )
        self._sys_prompt_tokens = _estimate_tokens(self.system_instruction)

    # ── Runtime switches ──────────────────────────────────────────────

    def set_language(self, lang: str) -> None:
        lang = lang.lower().strip()
        if lang not in self._VALID_LANGS:
            if getattr(self._cfg, "DEBUG_MODE", False):
                print(f"[LLM] Unknown language '{lang}'. valid: {self._VALID_LANGS}")
            return
        self._lang = lang
        self._tod = _time_of_day(self._tz, self._lang)
        self._build_and_cache_system_instruction()
        if getattr(self._cfg, "DEBUG_MODE", False):
            print(f"[LLM] Language switched to '{self._lang}'.")

    def get_language(self) -> str:
        return self._lang

    def set_user_name(self, name: str) -> None:
        self.user_name = name.strip() or None
        self._build_and_cache_system_instruction()

    def get_system_prompt(self) -> str:
        return self.system_instruction

    # ── Warm-up logic ─────────────────────────────────────────────────

    def _preload_gemini(self) -> None:
        try:
            _get_gemini_client(self._cfg)
        except Exception:
            pass  # Fail silently, real error will hit during generation

    def _check_ollama(self) -> None:
        client = _get_ollama_client(self._cfg)
        if not client:
            self._warmup_error = "Ollama client not loaded."
            self._warm_event.set()
            return
        try:
            client.list()
        except Exception as e:
            self._warmup_error = f"Ollama unreachable: {e}"
            self._warm_event.set()
            return
        threading.Thread(
            target=self._warm_up_model, daemon=True, name="llm-warmup"
        ).start()

    def _warm_up_model(self) -> None:
        client = _get_ollama_client(self._cfg)
        try:
            client.chat(
                model=self.model_name,
                messages=[{"role": "user", "content": "hi"}],
                think=False,
                options={
                    "num_predict": 1,
                    "num_ctx": getattr(self._cfg, "OLLAMA_NUM_CTX", 4096),
                },
                keep_alive=getattr(self._cfg, "OLLAMA_KEEP_ALIVE", "5m"),
            )
        except Exception as e:
            self._warmup_error = str(e)
        finally:
            self._warm_event.set()

    def wait_until_warm(self, timeout: float = 30.0) -> WarmupResult:
        fired = self._warm_event.wait(timeout=timeout)
        if not fired:
            return WarmupResult(ok=False, error="Warm-up timed out.")
        if self._warmup_error:
            return WarmupResult(ok=False, error=self._warmup_error)
        return WarmupResult(ok=True)

    # ── Token budget ───────────────────────────────────────────────────

    def _trim_history_to_budget(
        self, prompt: str, extra_tokens: int = 0
    ) -> List[Tuple[str, str]]:
        if getattr(self._cfg, "LLM_BACKEND", "ollama") == "gemini":
            ctx_tokens = int(getattr(self._cfg, "GEMINI_MAX_HISTORY_TOKENS", 30_000))
            gen_tokens = 1000
        else:
            ctx_tokens = int(getattr(self._cfg, "OLLAMA_NUM_CTX", 4096))
            gen_tokens = int(getattr(self._cfg, "OLLAMA_NUM_PREDICT", 300))

        # PRIORITY-1 FIX: extra_tokens reserves room for the RAG
        # memory_context system message, which is injected into the
        # prompt (_build_messages_ollama/_build_contents_gemini) but was
        # never accounted for here before — on a small 4096-token ctx
        # this could silently overflow the model's context window.
        fixed_cost = self._sys_prompt_tokens + _estimate_tokens(prompt) + extra_tokens
        available = ctx_tokens - gen_tokens - fixed_cost

        if available <= 0:
            return []

        # Minimize lock duration
        with self._history_lock:
            snap = list(self._history)

        kept: list[Tuple[str, str]] = []
        used = 0
        for u, a in reversed(snap):
            cost = _estimate_tokens(u) + _estimate_tokens(a)
            if used + cost > available:
                break
            kept.append((u, a))
            used += cost

        return list(reversed(kept))

    # ── Message builders ───────────────────────────────────────────────

    def _build_messages_ollama(
        self,
        prompt: str,
        history: List[Tuple[str, str]],
        memory_context: Optional[str] = None,
    ) -> list:
        msgs: list = [{"role": "system", "content": self.system_instruction}]
        # PRODUCTION-AUDIT ADDITION (Phase 2 — RAG): retrieved long-term
        # memories are injected as a SEPARATE system message, not merged
        # into self.system_instruction — that instruction string is
        # cached (_build_and_cache_system_instruction) and shared across
        # every call, so mutating it per-turn would either require
        # rebuilding+re-caching on every single message (defeating the
        # cache) or leak one turn's retrieved memories into the next
        # turn's prompt even after they're no longer relevant.
        if memory_context:
            msgs.append(
                {
                    "role": "system",
                    "content": (
                        "Relevant things you remember about this user from "
                        "past conversations (use only if actually relevant "
                        "to the current message; ignore anything that isn't):\n"
                        f"{memory_context}"
                    ),
                }
            )
        for u, a in history:
            msgs.append({"role": "user", "content": u})
            msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _build_contents_gemini(
        self,
        prompt: str,
        history: List[Tuple[str, str]],
        memory_context: Optional[str] = None,
    ) -> list:
        # PRIORITY-4 FIX: system_instruction used to ALSO be injected here
        # as a fake user/"Understood." turn, on top of being passed via
        # GenerateContentConfig(system_instruction=...) in
        # _open_gemini_stream(). That was pure duplication (same prompt
        # sent twice, doubling its token cost every single call) — the
        # config field is the correct/native way to send it, so it's the
        # only place it's sent now.
        contents: list = []
        if memory_context:
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "[System] Relevant things you remember about "
                                f"this user from past conversations:\n{memory_context}"
                            )
                        }
                    ],
                }
            )
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        for u, a in history:
            contents.append({"role": "user", "parts": [{"text": u}]})
            contents.append({"role": "model", "parts": [{"text": a}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        return contents

    # ── History ────────────────────────────────────────────────────────

    def _append_history(self, prompt: str, reply: str) -> None:
        with self._history_lock:
            if self._history and self._history[-1][0] == prompt:
                self._history.pop()
            self._history.append((prompt, reply))

    def load_history(self, history: List[Tuple[str, str]]) -> None:
        if not history:
            return
        with self._history_lock:
            self._history.clear()
            limit = self._cfg.MAX_MEMORY_EXCHANGES
            for user_prompt, assistant_reply in history[-limit:]:
                u_clean = (user_prompt or "").strip()
                a_clean = (assistant_reply or "").strip()
                if u_clean and a_clean:
                    self._history.append((u_clean, a_clean))

    # ── Streaming core (Highly Optimized) ──────────────────────────────

    @staticmethod
    def _flush_sentences(buffer_str: str) -> tuple[list[str], str]:
        parts = _split_sentences(buffer_str)
        if len(parts) > 1:
            ready = [_clean_markdown(s) for s in parts[:-1] if s.strip()]
            return ready, parts[-1]
        return [], buffer_str

    @staticmethod
    def _flush_clause(buffer_str: str) -> tuple[list[str], str]:
        ready, remainder = _clause_flush(buffer_str)
        clean_ready = [_clean_markdown(s) for s in ready if s.strip()]
        return clean_ready, remainder

    def _stream_generic(
        self,
        prompt: str,
        open_stream,
        max_retries: Optional[int] = None,
    ) -> Iterator[str]:
        if max_retries is None:
            max_retries = int(getattr(self._cfg, "LLM_MAX_RETRIES", 2))
        base_delay = float(getattr(self._cfg, "LLM_RETRY_BASE_DELAY_S", 1.5))
        max_delay = float(getattr(self._cfg, "LLM_RETRY_MAX_DELAY_S", 8.0))

        buffer_str: str = ""
        reply_parts: list[str] = []
        is_debug = getattr(self._cfg, "DEBUG_MODE", False)

        for attempt in range(max_retries + 1):
            buffer_str = ""
            reply_parts.clear()
            yielded_any = False
            stream_ok = False
            stream_iter = None

            try:
                stream_iter = open_stream(attempt)

                for piece in stream_iter:
                    if not piece:
                        continue

                    buffer_str += piece
                    reply_parts.append(piece)
                    yielded_any = True

                    # Fast-path heuristic: avoid regex processing unless a boundary
                    # trigger (whitespace/newline) is present in the new chunk.
                    if " " in piece or "\n" in piece:
                        ready, buffer_str = self._flush_sentences(buffer_str)
                        for s in ready:
                            yield s

                        ready, buffer_str = self._flush_clause(buffer_str)
                        for s in ready:
                            yield s

                # Yield final remainder
                remainder = _clean_markdown(buffer_str)
                if remainder:
                    yield remainder

                stream_ok = True
                break

            except Exception as e:
                if is_debug:
                    print(f"[LLM] stream error on attempt {attempt+1}: {e}")

                if yielded_any:
                    # v7: never speak the raw exception — the user already
                    # heard/received a partial reply, so just add a short,
                    # natural-language note instead of literal error text.
                    yield _STREAM_INTERRUPTED_MESSAGES.get(
                        self._lang, _STREAM_INTERRUPTED_MESSAGES["english"]
                    )
                    break

                # PRIORITY-7 FIX: a missing/uninitialized client is a
                # deterministic config problem, not a transient blip —
                # retrying it just burns the backoff delay (up to ~4.5s
                # here) for zero chance of success. Fail fast instead.
                # Any other exception (network hiccup, timeout, etc.)
                # is retried exactly as before.
                non_transient = isinstance(e, _ClientUnavailableError)

                if attempt < max_retries and not non_transient:
                    delay = min(max_delay, base_delay * (2**attempt))
                    if is_debug:
                        print(
                            f"[LLM] Retry {attempt+1}/{max_retries} in {delay:.1f}s: {e}"
                        )
                    time.sleep(delay)
                else:
                    # v7: retries exhausted (or non-retryable) with zero
                    # tokens ever received — give a friendly localized
                    # message instead of raw exception text (this used
                    # to be spoken verbatim by TTS).
                    yield _STREAM_FAIL_MESSAGES.get(
                        self._lang, _STREAM_FAIL_MESSAGES["english"]
                    )
                    if non_transient:
                        break

            finally:
                if stream_iter is not None and hasattr(stream_iter, "close"):
                    try:
                        stream_iter.close()
                    except Exception:
                        pass

        full_reply = "".join(reply_parts).strip()
        # CRITICAL FIX: was `stream_ok or yielded_any` — a stream that
        # yielded SOME tokens before failing (yielded_any=True) still
        # got its incomplete text appended to history. Only a fully
        # completed stream (stream_ok) should be persisted; a partial/
        # interrupted reply is never appended, and the interrupted
        # fallback message itself was never in reply_parts to begin
        # with, so nothing further is needed to keep it out of history
        # or (downstream) long-term memory.
        if full_reply and stream_ok:
            self._append_history(prompt, full_reply)

    # ── Hot-path helpers (PRIORITY 2/3/8/9) ─────────────────────────────

    def _rag_context_budget(self) -> int:
        # PRIORITY-2 FIX: hard cap on how many tokens the ENTIRE RAG
        # memory block (wrapper text included) may consume, so a
        # handful of long/highly-relevant hits can never crowd out the
        # whole 4096-token Ollama context. Uses an explicit config value
        # if the project defines one; otherwise falls back to a small,
        # conservative fraction of whatever context size is already
        # configured (does NOT touch OLLAMA_NUM_CTX itself).
        configured = getattr(self._cfg, "RAG_MAX_CONTEXT_TOKENS", None)
        if configured is not None:
            return int(configured)
        if getattr(self._cfg, "LLM_BACKEND", "ollama") == "gemini":
            ctx_tokens = int(getattr(self._cfg, "GEMINI_MAX_HISTORY_TOKENS", 30_000))
        else:
            ctx_tokens = int(getattr(self._cfg, "OLLAMA_NUM_CTX", 4096))
        return max(64, int(ctx_tokens * 0.15))

    def _build_capped_memory_context(
        self, hits: list
    ) -> Tuple[Optional[str], int]:
        # PRIORITY-2 FIX: greedily include hits (highest-relevance first,
        # as returned by search()) only while the running token count —
        # PLUS a fixed allowance for the surrounding instructional
        # wrapper text added in _build_messages_ollama/
        # _build_contents_gemini — stays under _rag_context_budget().
        if not hits:
            return None, 0
        cap = self._rag_context_budget()
        wrapper_overhead = 40  # short fixed wrapper sentence, both backends
        available = max(0, cap - wrapper_overhead)

        kept_lines: list[str] = []
        used = 0
        for h in hits:
            line = f"- {h.text}"
            cost = _estimate_tokens(line)
            if used + cost > available:
                break
            kept_lines.append(line)
            used += cost

        if not kept_lines:
            return None, 0
        return "\n".join(kept_lines), used + wrapper_overhead

    def _get_fact_extract_pool(self) -> ThreadPoolExecutor:
        # LIFECYCLE FIX: double-checked locking — the fast path (executor
        # already exists) stays lock-free, and the lock is only taken on
        # the rare first-call race, so this doesn't add contention to
        # the hot path on every normal request.
        if self._fact_executor is None:
            with self._fact_executor_lock:
                if self._fact_executor is None:
                    self._fact_executor = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="sara-fact-extract"
                    )
        return self._fact_executor

    def _safe_extract_fact(self, prompt: str) -> None:
        # Runs on the background pool thread, not the hot path. Wrapped
        # in try/except here (rather than relying on callers to inspect
        # the Future) so a failure never surfaces anywhere.
        try:
            self._memory.maybe_extract_fact(prompt)
        except Exception as e:
            if getattr(self._cfg, "DEBUG_MODE", False):
                print(f"[LLM] Fact extraction failed (continuing): {e}")

    @staticmethod
    def _is_trivial_prompt(prompt: str) -> bool:
        # See _TRIVIAL_PROMPTS above for rationale. Lightweight
        # normalization only (lowercase + strip trailing punctuation) —
        # no regex/classifier/new dependency.
        normalized = prompt.strip().lower().strip(".,!?~ ")
        return normalized in SaraLLM._TRIVIAL_PROMPTS

    def _is_fallback_reply(self, reply: str) -> bool:
        # PRIORITY-8 FIX: used to decide whether a reply is one of our
        # own localized error/interrupted messages, so we don't pollute
        # long-term memory with "sorry, I'm having trouble" exchanges.
        reply = (reply or "").strip()
        return reply in _STREAM_FAIL_MESSAGES.values() or reply in _STREAM_INTERRUPTED_MESSAGES.values()

    def _filter_history_duplicates(self, hits: list, history: List[Tuple[str, str]]) -> list:
        # PRIORITY-9 FIX: drop retrieved long-term-memory hits whose text
        # is already present verbatim in the just-trimmed short-term
        # history window, so the same exchange isn't sent to the model
        # twice (once via history, once via the memory_context block).
        if not hits or not history:
            return hits
        existing = {f"User said: {u}\nSara replied: {a}" for u, a in history}
        return [h for h in hits if getattr(h, "text", None) not in existing]

    # ── Backend stream openers ─────────────────────────────────────────

    def _open_ollama_stream(
        self,
        prompt: str,
        history: List[Tuple[str, str]],
        memory_context: Optional[str] = None,
    ):
        client = _get_ollama_client(self._cfg)
        if not client:
            # PRIORITY-7 FIX: was a bare RuntimeError, indistinguishable
            # from a transient one — see _ClientUnavailableError above.
            raise _ClientUnavailableError("Ollama client not loaded.")

        messages = self._build_messages_ollama(prompt, history, memory_context)
        raw_stream = client.chat(
            model=self.model_name,
            messages=messages,
            stream=True,
            think=False,
            options={
                "num_predict": int(getattr(self._cfg, "OLLAMA_NUM_PREDICT", 300)),
                "num_ctx": int(getattr(self._cfg, "OLLAMA_NUM_CTX", 4096)),
                "temperature": float(getattr(self._cfg, "OLLAMA_TEMPERATURE", 0.4)),
                "top_k": int(getattr(self._cfg, "OLLAMA_TOP_K", 40)),
                "top_p": float(getattr(self._cfg, "OLLAMA_TOP_P", 0.9)),
                "repeat_penalty": float(
                    getattr(self._cfg, "OLLAMA_REPEAT_PENALTY", 1.15)
                ),
            },
            keep_alive=getattr(self._cfg, "OLLAMA_KEEP_ALIVE", "5m"),
        )

        def _iter():
            for chunk in raw_stream:
                yield getattr(getattr(chunk, "message", None), "content", None)

        return _iter()

    def _open_gemini_stream(
        self,
        prompt: str,
        history: List[Tuple[str, str]],
        memory_context: Optional[str] = None,
    ):
        client = _get_gemini_client(self._cfg)
        if not client:
            # PRIORITY-7 FIX: was a bare RuntimeError — see _ClientUnavailableError above.
            raise _ClientUnavailableError("Gemini client not initialized.")

        from google.genai import types

        contents = self._build_contents_gemini(prompt, history, memory_context)

        raw_stream = client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self.system_instruction,
                temperature=0.7,
            ),
        )

        def _iter():
            for chunk in raw_stream:
                yield getattr(chunk, "text", None)

        return _iter()

    # ── Public streaming API ───────────────────────────────────────────

    def generate_response_stream(self, prompt: str) -> Iterator[str]:
        prompt = (prompt or "").strip()
        if not prompt:
            nudges = {
                "hindi": "Bhai kuch toh poochho, main yahaan khali nahi baitha.",
                "hinglish": "Bol yaar kuch — main sun raha hoon.",
            }
            yield nudges.get(self._lang, "Please provide a valid question or command.")
            return

        # v7: absorb any still-in-progress cold-start warm-up HERE, with a
        # bounded wait, instead of racing it. Every call after the first
        # returns instantly (the Event is already set), so this adds real
        # latency exactly once — the first time the brain is actually used
        # — rather than letting a cold Ollama model produce a spurious
        # timeout on whatever happens to be the first real user command.
        warm_wait_s = float(getattr(self._cfg, "LLM_WARMUP_WAIT_S", 20.0))
        warm = self.wait_until_warm(timeout=warm_wait_s)
        if not warm.ok and getattr(self._cfg, "DEBUG_MODE", False):
            print(
                f"[LLM] Proceeding without confirmed warm-up ({warm.error}) — "
                f"first request may be slower than usual."
            )

        # PRIORITY-5 FIX: _tod previously only refreshed on set_language()/
        # clear_memory(), so a long-running session could keep saying
        # "good morning" into the evening. _time_of_day() is a cheap call
        # (time-of-day bucket lookup) so it's fine to call every turn —
        # the EXPENSIVE part (_build_and_cache_system_instruction, which
        # re-renders the whole prompt and re-estimates its tokens) only
        # runs when the bucket actually changed.
        current_tod = _time_of_day(self._tz, self._lang)
        if current_tod != self._tod:
            self._tod = current_tod
            self._build_and_cache_system_instruction()

        is_ollama = getattr(self._cfg, "LLM_BACKEND", "ollama") == "ollama"
        trivial = self._is_trivial_prompt(prompt)

        # PRODUCTION-AUDIT ADDITION (Phase 2 — RAG): retrieval happens on
        # the hot path right before the LLM call, so it must be bounded
        # and must never raise into the response — LongTermMemory.search()
        # already guarantees both (internal embedding-call timeout,
        # returns [] on any failure) so no extra try/except is needed
        # here, but it's added anyway as defense-in-depth since this is
        # a novel integration point.
        #
        # PRIORITY-1/2/4 FIX: history is trimmed FIRST, reserving the
        # RAG hard-cap (_rag_context_budget()) up front rather than the
        # exact-but-not-yet-known memory_context size. This sidesteps
        # the chicken-and-egg problem (trimming needs the memory token
        # cost; deduping the memory hits needs the trimmed history) with
        # no extra pass: the reserved amount is always >= what RAG will
        # actually use (memory_context is hard-capped to the same
        # budget), so the context-budget math stays correct, and
        # dedup below now runs against the REAL trimmed history.
        rag_budget = self._rag_context_budget() if (self._memory is not None and not trivial) else 0
        history = self._trim_history_to_budget(prompt, extra_tokens=rag_budget)

        memory_context = None
        memory_context_tokens = 0
        if self._memory is not None and not trivial:
            try:
                hits = self._memory.search(prompt)
                # PRIORITY-9 FIX (now against the actual trimmed history,
                # not the full untrimmed deque — see PRIORITY-4 above):
                # drop hits that duplicate what's already going into the
                # history window being sent to the model.
                hits = self._filter_history_duplicates(hits, history)
                memory_context, memory_context_tokens = self._build_capped_memory_context(hits)
                if memory_context and getattr(self._cfg, "DEBUG_MODE", False):
                    print(
                        f"[LLM] RAG retrieved {len(hits)} memor{'y' if len(hits)==1 else 'ies'} "
                        f"(top score={hits[0].score:.2f})"
                    )
            except Exception as e:
                if getattr(self._cfg, "DEBUG_MODE", False):
                    print(f"[LLM] RAG retrieval failed (continuing without it): {e}")

            # PRIORITY-3 FIX: fact extraction submitted to the bounded
            # background pool instead of running inline — keeps this a
            # true fire-and-forget as the original comment intended,
            # without blocking the hot path even briefly, and without
            # spawning an unbounded thread per request.
            self._get_fact_extract_pool().submit(self._safe_extract_fact, prompt)

        def _open(attempt: int):
            return (
                self._open_ollama_stream(prompt, history, memory_context)
                if is_ollama
                else self._open_gemini_stream(prompt, history, memory_context)
            )

        yield from self._stream_generic(prompt, _open)

        # PRODUCTION-AUDIT ADDITION (Phase 2 — RAG): ingest this exchange
        # into long-term memory AFTER it completes, so future turns (even
        # in a later session) can recall it. Fire-and-forget — add_memory()
        # never blocks or raises. Uses self._history's own dedup rule (the
        # most recently appended pair) rather than re-deriving the reply
        # text here, since _stream_generic() already appended it via
        # _append_history() as a side effect of the yield above.
        if self._memory is not None and not trivial:
            with self._history_lock:
                last_pair = self._history[-1] if self._history else None
            if (
                last_pair is not None
                and last_pair[0] == prompt
                and not self._is_fallback_reply(last_pair[1])
            ):
                exchange_text = (
                    f"User said: {last_pair[0]}\nSara replied: {last_pair[1]}"
                )
                self._memory.add_memory(exchange_text, source="conversation")

    # ── Non-streaming ──────────────────────────────────────────────────

    def generate_response(self, prompt: str) -> str:
        return " ".join(
            s.strip() for s in self.generate_response_stream(prompt)
        ).strip()

    # ── Summarization ─────────────────────────────────────────────────

    def summarize_text(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            empties = {
                "hindi": "Bhai content hi nahi hai summarize karne ke liye.",
                "hinglish": "Yaar kuch diya hi nahi summarize karne ko.",
            }
            return empties.get(self._lang, "There's no content to summarize.")

        max_tokens = int(getattr(self._cfg, "OLLAMA_SUMMARY_NUM_CTX", 8192)) - 300
        max_words = int(max_tokens * 0.75)
        words = text.split()
        if len(words) > max_words:
            text = " ".join(words[:max_words]) + "..."

        if getattr(self._cfg, "LLM_BACKEND", "ollama") == "ollama":
            return self._summarize_ollama(text)
        return self._summarize_gemini(text)

    def _summarize_ollama(self, text: str) -> str:
        client = _get_ollama_client(self._cfg)
        if not client:
            return _SUMMARY_FAIL_MESSAGES.get(
                self._lang, _SUMMARY_FAIL_MESSAGES["english"]
            )
        system_prompt = _SUMMARY_SYSTEM_PROMPTS.get(
            self._lang, _SUMMARY_SYSTEM_PROMPTS["english"]
        )

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            if attempt:
                time.sleep(1.0 * attempt)
            try:
                resp = client.chat(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    think=False,
                    options={
                        "num_predict": 200,
                        "num_ctx": getattr(self._cfg, "OLLAMA_SUMMARY_NUM_CTX", 8192),
                    },
                    keep_alive=getattr(self._cfg, "OLLAMA_KEEP_ALIVE", "5m"),
                )
                return (resp.message.content or "").strip() or "Could not summarize."
            except Exception as e:
                last_exc = e
                if getattr(self._cfg, "DEBUG_MODE", False):
                    print(f"[LLM] summarize_ollama attempt {attempt+1} failed: {e}")
        # BUGFIX: last_exc used to be assigned every attempt and then
        # discarded — in production (DEBUG_MODE off) the real reason for
        # the failure was never recorded anywhere, only the debug print
        # above showed it. Log it unconditionally now so ops/logs always
        # capture the actual cause, while the TTS-safe friendly message
        # below is still what gets returned/spoken (never the raw
        # exception text).
        logger.error(
            f"[LLM] summarize_ollama failed after 3 attempts: {last_exc}"
        )
        return _SUMMARY_FAIL_MESSAGES.get(self._lang, _SUMMARY_FAIL_MESSAGES["english"])

    def _summarize_gemini(self, text: str) -> str:
        client = _get_gemini_client(self._cfg)
        if not client:
            return _SUMMARY_FAIL_MESSAGES.get(
                self._lang, _SUMMARY_FAIL_MESSAGES["english"]
            )
        system_prompt = _SUMMARY_SYSTEM_PROMPTS.get(
            self._lang, _SUMMARY_SYSTEM_PROMPTS["english"]
        )

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            if attempt:
                time.sleep(1.0 * attempt)
            try:
                from google.genai import types

                resp = client.models.generate_content(
                    model=self.model_name,
                    contents=[{"role": "user", "parts": [{"text": text}]}],
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.5,
                    ),
                )
                return (resp.text or "").strip() or "Could not summarize."
            except Exception as e:
                last_exc = e
                if getattr(self._cfg, "DEBUG_MODE", False):
                    print(f"[LLM] summarize_gemini attempt {attempt+1} failed: {e}")
        # BUGFIX: same as _summarize_ollama above — surface the real
        # exception unconditionally via logger, keep the spoken/returned
        # text friendly and TTS-safe.
        logger.error(
            f"[LLM] summarize_gemini failed after 3 attempts: {last_exc}"
        )
        return _SUMMARY_FAIL_MESSAGES.get(self._lang, _SUMMARY_FAIL_MESSAGES["english"])

    # ── Memory ────────────────────────────────────────────────────────

    def clear_memory(self) -> None:
        with self._history_lock:
            self._history.clear()
        self._tod = _time_of_day(self._tz, self._lang)
        self._build_and_cache_system_instruction()

    def get_history_length(self) -> int:
        with self._history_lock:
            return len(self._history)

    def get_history_snapshot(self) -> List[Tuple[str, str]]:
        with self._history_lock:
            return list(self._history)

    # ── Debug ─────────────────────────────────────────────────────────

    def _log_init(self) -> None:
        print(
            f"[LLM] Ready — backend={getattr(self._cfg, 'LLM_BACKEND', 'ollama')} | "
            f"model={self.model_name} | "
            f"memory={self._cfg.MAX_MEMORY_EXCHANGES} exchanges | "
            f"lang={self._lang}"
        )