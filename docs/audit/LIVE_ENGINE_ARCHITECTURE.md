# SARA AI — Live Audio Engine: Phase 1 Architecture Decision

**Phase 1 deliverable. No production code was modified.**

- **Date:** 2026-10-28
- **HEAD:** `f2e3d51` "Complete SARA AI audit batches 6a-6b3" on `master`
- **Test baseline (this phase must not break it):** `2115 passed, 19 skipped, 0 failed` in 34.77 s
- **Companion document:** `VOICE_ARCHITECTURE_RESEARCH.md` (provider comparison, Hinglish evidence, cost, licensing)

Evidence tags: `[VERIFIED]` read or executed this session · `[DOC]` official documentation · `[MEAS]` measured, run recorded · `[EST]` reasoned estimate · `[GAP]` unverified, needs your Windows PC.

---

## 0. Headline: the integration is far smaller than it looks

Phase 1 set out to find integration points. It found that **most of the integration already exists.** Seven things a Gemini Live engine needs are already built, tested and shipping in SARA:

| What Live needs | Status in SARA today | Evidence |
| --- | --- | --- |
| Live API client | **`google-genai==1.23.0` already pins it** — `AsyncLive.connect`, `send_realtime_input`, `send_client_content`, `send_tool_response`, `receive`, `close` all present in the pinned wheel | `[VERIFIED]` |
| Async tool calling, VAD config, session resumption, context compression, transcription | **All types present in the pinned 1.23.0**: `NON_BLOCKING`, `FunctionResponseScheduling`, `Behavior`, `AutomaticActivityDetection`, `StartSensitivity`/`EndSensitivity`, `silence_duration_ms`, `prefix_padding_ms`, `SessionResumptionConfig`, `ContextWindowCompression`, `SlidingWindow`, `input_audio_transcription`, `output_audio_transcription`, `proactivity`, `enable_affective_dialog` | `[VERIFIED]` |
| Tool declarations in Gemini format | **`TOOLS_SCHEMA` + `_get_gemini_tools()`** already convert 67 tool entries to `types.FunctionDeclaration`, cached at module level | `[VERIFIED]` |
| Tool name → handler mapping | **`TOOL_NAME_TO_INTENT`** + `_INTENT_HANDLERS` | `[VERIFIED]` |
| Model args → handler match | **`build_fake_match(tool_name, args)`** | `[VERIFIED]` |
| Policy gate that is already provider-neutral | **`guard.check_tool_call(tool, origin, args=…, text=…, lang=…)`** — no assumption about the caller | `[VERIFIED]` |
| Per-turn latency telemetry with **ttfa p50/p95** | **`sara/core/telemetry`** — 13 stages, nearest-rank percentiles, SQLite-backed, on by default | `[VERIFIED]` |

**And the audio formats match exactly, in both directions, with zero resampling:**

| | SARA | Gemini Live | |
| --- | --- | --- | --- |
| Capture | `SAMPLE_RATE = 16000`, `int16`, mono, `CHUNK_SIZE = 512` (32 ms) | 16 kHz 16-bit LE PCM | **match** `[VERIFIED]`+`[DOC]` |
| Playback | `_SAMPLE_RATE = 24000`, `int16`, mono, 40 ms blocks | 24 kHz 16-bit LE PCM | **match** `[VERIFIED]`+`[DOC]` |

That second row is a coincidence worth naming: SARA plays at 24 kHz because that is *Kokoro v1.0's native rate* (`player.py:56`), and Gemini Live happens to emit at the same rate. Nothing was designed for this. It means Live's audio can go straight into the existing player.

**Consequences for your requirements:**

- Requirement 9 (*"Do not add unnecessary dependencies"*) — **fully satisfied. Zero new dependencies.** Not one line of `requirements.txt` changes for the vertical slice.
- Requirement 5 (*"The model must never bypass policy"*) — the enforcement path already exists at `route_chat.py:258–275` and is six lines. The Live engine calls the same six lines.
- The genuinely new work is **four things**: a WebSocket session lifecycle, an asyncio↔thread boundary, an engine interface, and a config/GUI surface. Everything else is wiring that already exists.

---

## 1. Repository state, verified

| Item | Value | How verified |
| --- | --- | --- |
| HEAD | `f2e3d5103f4a30b070bd55eff145dfda883544fc` | `.git/refs/heads/master` read directly `[VERIFIED]` |
| Branch | `master` | `.git/HEAD` `[VERIFIED]` |
| Last commit message | "Complete SARA AI audit batches 6a-6b3" | `.git/COMMIT_EDITMSG` `[VERIFIED]` |
| History | `144787c` → `2ac8bbe` → `f2e3d51` | `.git/logs/HEAD` `[VERIFIED]` |
| Uncommitted **code** | **none** | — |
| Uncommitted **docs** | `FINDINGS.md` (modified, D19 added), `VOICE_ARCHITECTURE_RESEARCH.md` (new) | written by the previous research turn |
| Test suite | **2115 passed, 19 skipped, 0 failed**, 34.77 s | run this session `[MEAS]` |

**Your uncommitted work is preserved.** Nothing was reset, discarded or overwritten. The two documents above are the only uncommitted items and both are in `docs/audit/`.

Earlier audit reports were **not** taken on trust: HEAD, the reflog and every claim below were re-read from the current tree.

---

## 2. Current voice flow, traced from source

### 2.1 The loop

`core_wiring.py:756–800` `[VERIFIED]`, synchronous, one thread:

```
while not stop_event:
    ui_update("status","sleeping")
    woke = wake_watcher.wait_for_wake()          # blocks
    _t_pending("wake")                            # ── telemetry
    tts.speak(ack, fast=True, block=True)         # ── BLOCKS (see D20)
    ears.wait_settle()                            # 0.3 s w/ AEC, 1.3 s without
    result = ears.listen(mode="command", on_partial_transcript=…)
        ├─ _collect_speech()  → VAD, endpointing, rolling preview
        │     _t_pending("speech_start")
        │     _t_pending("speech_end")
        └─ _transcribe()      → faster-whisper
              _t_pending("stt_done")
    dispatch(result.text)                         # router → guard → handler
    tts.speak(reply)
```

### 2.2 The shape mismatch, stated precisely

`ears.listen()` is **blocking, pull-based, and single-flight** (`_listen_lock.acquire(blocking=False)`, returning an empty `TranscriptionResult` if already active) `[VERIFIED]`. It returns one finished `TranscriptionResult` per call.

A Live session is **non-blocking, push-based, and continuous**: audio flows in while events flow out, independently.

This is the single real architectural problem. Everything else is plumbing. §4 resolves it with an engine interface that both shapes can satisfy, and §5 resolves the threading.

### 2.3 Capture and playback contracts

**Capture** — `sara/audio/stt/engine.py` `[VERIFIED]`: `sd.RawInputStream(samplerate=16000, blocksize=512, dtype="int16")`, with a PyAudio fallback. Frames arrive on `_audio_callback` / `_pa_callback`. AEC, when enabled, runs off the callback thread (`engine.py:407`, `911`).

**Playback** — `sara/audio/tts/player.py` `[VERIFIED]`:

```python
def enqueue(self, pcm: np.ndarray, volume=1.0,
            on_complete: Event|None=None, on_start: Event|None=None) -> None
def clear(self) -> None
```

`enqueue()` takes raw `int16` PCM, splits it into FIFO chunks, and is explicitly documented as safe to call for the next segment before the current one finishes — "both segments' sub-chunks land in the same FIFO queue in call order, so playback stays gap-free and strictly sequential with no overlap." `clear()` drops queued audio and fires pending completion events so nothing waits forever.

**This is already a streaming-audio sink with a barge-in primitive.** Live's output path is: `np.frombuffer(msg.data, dtype=np.int16)` → `enqueue()`. Live's interruption is: `clear()`.

**And AEC carries over for free.** `player.py:286` feeds every played block to `self._aec.feed_far_end(block, _SAMPLE_RATE)` `[VERIFIED]`. Because it hooks the *player*, not the synthesiser, it will cancel Gemini's voice exactly as it cancels Kokoro's. No echo work is needed for the Live path.

### 2.4 Tool dispatch and the policy gate

`guard.check_tool_call()` `[VERIFIED]`:

```python
check_tool_call(tool, origin, *, args=None, match=None, text="",
                tool_name=None, lang="english", ttl_s=60.0) -> GuardOutcome
# GuardOutcome(action ∈ {run, confirm, deny}, reason, tier, message, pending)
```

Properties that matter for this design:

- **Provider-neutral already.** It takes a tool name, an origin, args and a language. It has no idea whether the caller is the keyword router, the text box, or a model.
- **Validate-then-decide.** `policy.validate_args(tool, call_args)` runs *before* `policy.decide(tool, origin)`.
- **Never raises.** `_failsafe()` degrades on any internal error: tier < 2 → run, tier ≥ 2 → **deny**. Fail-safe, not fail-open.
- **Re-validates on replay.** `replay_confirmed()` calls `validate_args` *again* before executing a confirmed call — defence in depth.
- **Confirmation cannot be forged by the model.** The `pending` dict is issued by the gate with an `expires_at`, and execution happens only through `replay_confirmed()`. There is no model-writable `confirmed` parameter anywhere.
- **Bilingual by design.** `_CONFIRM_HI` / `_DENY_HI` carry Hinglish strings, selected by `_is_hinglish(lang)`.

Origins: `ORIGIN_USER_DIRECT`, `ORIGIN_LLM_TOOL`, `ORIGIN_USER_CONFIRMED`, `ORIGIN_PROACTIVE` `[VERIFIED]`.

**The existing LLM-tool seam — the exact template the Live engine reuses** (`route_chat.py:258–275`) `[VERIFIED]`:

```python
from .dispatcher import _INTENT_HANDLERS, _ORIGIN_LLM_TOOL, _guard_check, _guard_reply
mapped_intent = TOOL_NAME_TO_INTENT.get(tool_name)
tool_handler  = _INTENT_HANDLERS.get(mapped_intent)
blocked = _guard_check(mapped_intent, _ORIGIN_LLM_TOOL, args=tool_args, tool_name=tool_name)
if blocked is not None:
    return _guard_reply(ctx, blocked)
fake_match = build_fake_match(tool_name, tool_args)
return tool_handler(fake_match, ctx)
```

A Gemini Live `tool_call.function_calls[i]` provides `.name`, `.args`, `.id`. The mapping is one-to-one: `.name` → `tool_name`, `.args` → `tool_args`, origin → `_ORIGIN_LLM_TOOL`, and `.id` is carried back in the tool response. **No new security code. The Live engine calls this.**

### 2.5 Telemetry — the measurement requirement is already met

`sara/core/telemetry/` `[VERIFIED]`, enabled by default (`TELEMETRY_ENABLED=True`, `config.py:824`).

Stages: `wake`, `speech_start`, `speech_end`, `stt_done`, `route_done`, `llm_req`, `llm_first_token`, `llm_done`, `tool_start`, `tool_end`, `tts_first_audio`, `tts_done`, `end`.

Derived per turn: `stt_ms`, `route_ms`, `llm_ttft_ms`, `llm_total_ms`, `tool_ms`, `tts_start_ms`, **`ttfa_ms`**, `total_ms`.

Summary already computes **`ttfa_p50` and `ttfa_p95`** with a nearest-rank percentile that returns `None` below 5 samples `[VERIFIED]`.

Outcomes: `ok`, `error`, `cancelled`, **`barge_in`** — interruptions are already a first-class turn outcome.
Meta: `lang`, `route`, `intent`, `model`, `error_code` — so per-language slicing is available.

Persisted to `telemetry.sqlite` (`turn_trace` table, 20k-row cap, daily prune), with a dev CLI:

```
python -m sara.core.telemetry.dump --last 50
```

and a GUI surface at `sara/gui/app/perf_api.py`.

**Your measurement requirement #1 — "time to first audible response (p50 and p95)" — is implemented today.** It needs reading, not building. You may already have real baseline data from your own use.

**Privacy is correct by default** `[VERIFIED]`: `begin_turn()` stores `text_len` and a 12-char SHA-1 `text_hash`, and stores the transcript itself **only** if `TRACE_STORE_TEXT` is true. That flag is read via `getattr(Config, "TRACE_STORE_TEXT", False)` and **is not defined in `config.py`**, so it is off unless explicitly set in `.env`. `*.sqlite` is gitignored (`.gitignore:35`), and `telemetry.sqlite` is in `policy_rules._DB_NAMES`, so SARA's own file tools refuse to read it. Nothing needs changing to satisfy *"never commit private transcripts."*

### 2.6 GUI bridge and lifecycle

**Bridge** `[VERIFIED]`: `sara/gui/app/engine.py` composes `Api` from 16 `ApiXMixin` classes, then copies each mixin's public callables into `Api.__dict__` — a documented fix for pywebview's WinForms renderer, which only introspects the exposed class's own `__dict__`. **Adding voice-engine controls = one new mixin appended to `_MIXINS`.** `tests/test_api_surface.py` guards the method count, so the addition is caught by a test rather than drifting.

**Python → JS** `[VERIFIED]`: `ui_bridge._make_ui_update` pushes `(kind, *args)` onto a bounded `ui_queue` with `put_nowait` and **drops on full**; `_UICoalescer` suppresses duplicate `status`/`footer` events. UI updates can never block the producer — so requirement 4 (*"do not block the GUI thread or existing Python bridge"*) is already structurally satisfied on that side.

**Shutdown** `[VERIFIED]`: `shutdown_logic_components()` (the D16 fix from batch 6b2) runs each teardown step independently guarded and returns a list of `(step, exception)` failures. A Live engine adds one step to this function; a failure in it cannot prevent `db_writer.shutdown()` from flushing the conversation log.

---

## 3. Dependency map and integration points

### 3.1 Modules that change

| Module | Change | Risk |
| --- | --- | --- |
| **`sara/voice/engine_api.py`** *(new)* | The provider-neutral interface — protocol + event dataclasses. No logic | None |
| **`sara/voice/live_gemini.py`** *(new)* | Gemini Live engine: session, asyncio loop thread, event translation | Contained |
| **`sara/voice/cascade.py`** *(new)* | Thin adapter wrapping the **existing** `ears`/`tts` in the same interface. No audio logic moves | Low |
| **`sara/voice/tool_bridge.py`** *(new)* | Maps a provider tool call onto the `route_chat.py:258` pattern. The **only** place a model-requested call becomes an execution | Security-critical, tiny, heavily tested |
| `sara/orchestrator/core_wiring.py` | Select an engine at startup; add one teardown step to `shutdown_logic_components()` | Low — additive |
| `config.py` | `VOICE_ENGINE` (`"cascade"` \| `"live"`, default **`"cascade"`**), `LIVE_MODEL`, `LIVE_VAD_SILENCE_MS`, `LIVE_PRIVACY_ACK` | None |
| `sara/gui/app/voice_api.py` *(new mixin)* | Engine status, engine switch, privacy indicator | Low |
| `sara/gui/app/engine.py` | One entry in `_MIXINS` | None |
| `tests/test_api_surface.py` | Expected method count | None — the guard working as designed |

### 3.2 Modules that must NOT change

Stated explicitly because requirement 2 asks for it, and because the value of this design is how much it leaves alone:

`sara/audio/stt/engine.py` · `endpoint.py` · `endpoint_cues.py` · `aec.py` · all of `sara/audio/tts/` · `sara/core/security/**` · `sara/core/intent/**` · `sara/core/tool_router.py` (read-only reuse of `TOOLS_SCHEMA` and `_get_gemini_tools()`) · `sara/orchestrator/dispatcher.py` · `route_chat.py` · `sara/core/telemetry/**` · `bench/**` · the pywebview bridge internals · `requirements.txt`.

### 3.3 The seam, in one picture

```
┌─────────────────── UNCHANGED, always local ───────────────────┐
│  mic 16k/int16/512 ─► AEC ─► VAD ─► cue-aware endpointer      │
│  wake word · barge-in detect · player 24k/int16 · AEC far-end │
└───────┬──────────────────────────────────────────▲────────────┘
        │ PCM in                             PCM out│ (enqueue / clear)
        ▼                                           │
┌───────────────────────── NEW, thin ───────────────┴────────────┐
│  VoiceEngine  (sara/voice/engine_api.py)                       │
│     ├── CascadeEngine  → wraps existing ears.listen()/tts      │
│     └── LiveEngine     → google-genai AsyncLive, own loop thread│
│  emits: partial · final · assistant_text · audio · tool_call   │
│         turn_complete · interrupted · error · session_resumed   │
└───────┬────────────────────────────────────────────────────────┘
        │ tool_call(name, args, id)
        ▼
┌──────────────── UNCHANGED, mandatory ──────────────────────────┐
│  tool_bridge → TOOL_NAME_TO_INTENT → _guard_check(LLM_TOOL)    │
│                  ├ deny    → spoken refusal                    │
│                  ├ confirm → pending + spoken yes/cancel        │
│                  └ run     → build_fake_match → handler         │
│  guard.replay_confirmed() on "yes"  ·  outcome back to engine   │
└───────┬────────────────────────────────────────────────────────┘
        ▼
   telemetry.mark(...)  ·  ui_update(...)  ·  Windows action
```

**Every arrow into "Windows action" passes through the gate. There is no second path, and §7 adds a test that fails if one ever appears.**

---

## 4. The provider-neutral engine interface

Requirement 3 asks for provider-neutral. Worth naming the alternative and why it loses: the reference project deliberately made its interface *Gemini-Live-shaped* and had every provider conform to it, arguing that the incumbent should not be rewritten for symmetry. That reasoning is sound — but in SARA the incumbent **is the cascade**, not Gemini. A Gemini-shaped interface would force SARA's own working pipeline to imitate a vendor's event schema. So the interface is **SARA-shaped**, and *both* engines adapt into it. This satisfies "provider-neutral" literally and honours the same principle.

Design sketch — not final code, no production file is being written in this phase:

```python
# sara/voice/engine_api.py   (new; pure types, no I/O)

class EngineCaps(TypedDict):
    streaming_audio_in:  bool   # accepts continuous PCM
    streaming_audio_out: bool   # emits PCM rather than text for local TTS
    server_turn_detect:  bool   # provider decides end-of-turn
    server_barge_in:     bool   # provider cancels its own generation
    async_tools:         bool   # NON_BLOCKING function calls
    resumption:          bool   # provider-side session resume
    sends_audio_to_cloud: bool  # ◄── drives the privacy indicator
    max_tools:           int | None

EventKind = Literal[
    "partial_transcript",  # provisional user text        → live caption
    "final_transcript",    # settled user text            → history, telemetry
    "assistant_text",      # what the assistant is saying  → log, UI
    "audio",               # int16 PCM @24 kHz             → player.enqueue
    "tool_call",           # name, args, call_id           → tool_bridge
    "turn_complete",
    "interrupted",         # provider cancelled generation → player.clear
    "error",               # code, retryable, message
    "session_resumed",
]

@dataclass(frozen=True)
class VoiceEvent:
    kind: EventKind
    text: str = ""
    pcm: bytes = b""
    tool: str = ""
    args: Mapping[str, Any] = field(default_factory=dict)
    call_id: str = ""
    code: str = ""
    retryable: bool = False

class VoiceEngine(Protocol):
    caps: EngineCaps
    def start(self) -> None: ...
    def send_audio(self, pcm: bytes) -> None: ...        # 16 kHz int16 mono
    def send_text(self, text: str) -> None: ...
    def send_tool_result(self, call_id: str, result: str) -> None: ...
    def interrupt(self) -> None: ...
    def events(self) -> Iterator[VoiceEvent]: ...         # blocking iterator
    def close(self) -> None: ...
```

Three deliberate choices:

1. **`events()` is a plain blocking iterator, not an async generator.** SARA's orchestrator is thread-based. Exposing `async def` would push asyncio into `core_wiring.py` and from there across the codebase. The Live engine keeps asyncio strictly inside itself (§5).
2. **`sends_audio_to_cloud` is a capability, not a comment.** It drives the privacy indicator directly, so the UI cannot drift out of step with reality — a UI claim and the engine's actual behaviour read the same field.
3. **`send_tool_result` returns immediately.** See §5 — this is the deadlock guard.

**`CascadeEngine` is genuinely thin.** It runs the existing `ears.listen(mode="command", on_partial_transcript=…)` on a worker thread and emits `partial_transcript` / `final_transcript`; `caps.streaming_audio_out` is `False`, so the orchestrator keeps using `tts.speak()` for it exactly as today. **No audio code moves.** Its capabilities honestly declare `server_turn_detect: False`, `server_barge_in: False`, `async_tools: False`, `sends_audio_to_cloud: False`.

---

## 5. Concurrency, cancellation and the deadlock hazard

**The hazard.** `google-genai`'s Live session is asyncio. A naive integration awaits a tool's result inside the `receive()` loop. But the result arrives only after SARA's synchronous dispatcher runs, and in the naive design the dispatcher is reached *from* that loop — so the turn stops dead on the first tool call and presents as *"the cloud model is slow,"* not as a hang. This failure mode is documented in the reference project's own source `[VERIFIED]`, which is how I know to design against it rather than discovering it in Phase 3.

**The design.**

```
LiveEngine.start()
  └─ spawns ONE daemon thread owning a private asyncio loop
       ├─ task: pump  — drains an audio ring → session.send_realtime_input()
       └─ task: recv  — async for msg in session.receive():
                           translate → VoiceEvent → out_queue.put_nowait()
                           (never calls SARA code, never awaits a tool)

Orchestrator thread (unchanged shape)
  └─ for ev in engine.events():            # drains out_queue
         tool_call → tool_bridge → guard → handler (synchronous, as today)
                   → engine.send_tool_result(call_id, outcome)
                        └─ asyncio.run_coroutine_threadsafe(...); RETURNS
```

Four rules:

1. **The receive task never calls into SARA.** It only translates and enqueues. Dispatch happens on the orchestrator thread.
2. **Every `send_*` schedules and returns.** `send_tool_result` uses `run_coroutine_threadsafe` and does **not** wait on the future. A tool call therefore cannot block the receive loop.
3. **Bounded queues with explicit drop policy.** Audio in: a ring buffer that drops oldest on overflow — stale mic audio is worthless, and unbounded growth is worse. Events out: bounded; on overflow, drop `partial_transcript` first (cosmetic) and **never** drop `tool_call`, `interrupted` or `error`.
4. **Deterministic shutdown.** `close()` cancels both tasks, closes the session, joins the thread with a timeout, and drains the queues. It is registered as one more guarded step in `shutdown_logic_components()`, so a hung socket cannot block the conversation-log flush.

**Barge-in has two paths and they must not fight.** Live detects interruption server-side and sends an interruption message `[DOC]`, which becomes `interrupted` → `player.clear()`. SARA's local `barge_watch` must be **disabled** when `caps.server_barge_in` is true, or both will race to clear the queue. This is exactly the sort of double-ownership bug the capability dict exists to prevent.

---

## 6. Migration plan

Small, reversible, each with its own evidence. **Default stays `cascade` until Phase 3 numbers justify otherwise.**

| Batch | Work | Exit criteria |
| --- | --- | --- |
| **A** | **Baseline capture.** No code. Enable telemetry (already on), use SARA normally, `python -m sara.core.telemetry.dump --last 100`. Record `ttfa_p50/p95`, `stt_ms`, `route_ms`, `tts_start_ms` per language | A real `[MEAS]` baseline on your PC, from your voice |
| **B** | **D20 fix** (§8) — wake ack non-blocking when `aec_active`. One line + a test | `ttfa_p50` drops by the measured ack cost; no self-echo regression |
| **C** | **D19 fix** (§8) — the `→unmute` confusion cluster | `bench` accuracy ≥ 93.22%, confusions gone |
| **D** | `engine_api.py` + `CascadeEngine`. Behaviour identical; engine unused by default | 2115+ tests pass; telemetry unchanged vs batch A |
| **E** | `tool_bridge.py` with **policy tests first** | A test proves a tool call cannot reach a handler without a `GuardOutcome` |
| **F** | `LiveEngine`: connect, stream, transcripts, audio out, interrupt. Flag default **off** | Vertical slice works; `pytest` green; no asyncio outside `sara/voice/` |
| **G** | Tool calls over Live, through `tool_bridge`. Async `NON_BLOCKING` + scheduling | Deny/confirm/run all exercised over Live in tests |
| **H** | Session resumption, reconnect with backoff, quota/network error classes | Survives forced disconnect and the 15-min audio-only cap `[DOC]` |
| **I** | GUI: engine status, switch, **privacy indicator**; config surface | Indicator matches `caps.sends_audio_to_cloud`; no silent switching |
| **J** | **Phase 3 on your PC.** Compare A vs F/G/H | Evidence for or against flipping the default |
| **K** | Flip default **only if** J shows a win on `ttfa_p95` *and* no accuracy or safety regression | — |

Batches A–C are worth doing regardless of whether the Live engine ever ships. B in particular is a one-line change against a measured per-wake cost.

---

## 7. Test plan

Extends the existing suite; the current 2115/19/0 is the floor.

**Security — the non-negotiable set (batch E, before any Live code)**

| Test | Asserts |
| --- | --- |
| `test_live_tool_call_requires_guard` | A tool call through `tool_bridge` with the gate stubbed to `deny` **never** reaches the handler |
| `test_live_tool_call_confirm_path` | `confirm` yields a `pending` dict and no execution; execution happens only via `replay_confirmed()` |
| `test_no_handler_invocation_without_guard_outcome` | **Static/behavioural guard:** every path from a provider tool call to `_INTENT_HANDLERS` passes a `GuardOutcome`. Fails if a future engine adds a bypass |
| `test_model_cannot_forge_confirmation` | A `function_call` carrying `confirmed=True`-style args does not skip the gate |
| `test_guard_failsafe_on_live_path` | Gate raising → tier ≥ 2 denied, tier < 2 allowed (matches `_failsafe`) |
| `test_untrusted_origin_preserved` | Live tool calls are `ORIGIN_LLM_TOOL`, never `ORIGIN_USER_DIRECT` |

**Engine protocol and events (batches D, F)** — fakes only; no network. Event translation for each `EventKind`; `partial`→`final` ordering; `audio` → `enqueue` with correct dtype/rate; `interrupted` → `clear()`; malformed/missing fields never raise inside the receive loop (an exception there ends the session); unknown event kinds ignored, not fatal.

**Concurrency and lifecycle (F, H)** — `send_tool_result` returns without waiting (the deadlock regression test); audio ring drops oldest under overflow; `tool_call`/`interrupted`/`error` are never dropped; `close()` joins within timeout and is idempotent; `close()` during an in-flight tool call does not deadlock; a Live failure in `shutdown_logic_components()` still lets `db_writer.shutdown()` run (extends the existing D16 tests).

**Reconnect and errors (H)** — resumption handle reused; backoff bounded; quota / network-loss / server-close each classified and surfaced as `error` with the right `retryable`; **no silent privacy change on fallback** — an automatic cascade→live switch must be impossible without explicit config.

**Regression** — full `pytest` after every batch; `bench --mode router --compare bench/baseline.json` after C; `pyflakes` gate at 0 real issues; the three JS suites (19/11/47).

**Cannot be tested here, and will not be claimed** `[GAP]`: real microphone capture, real speaker playback, WebView2 behaviour, actual Hinglish recognition, real network conditions, CPU/RAM under load. Phase 3 covers these on your machine; §9 is explicit that no Windows claim is made from Linux.

---

## 8. Two defects found while tracing, both independent of this migration

### D20 — the wake ack blocks the loop, contrary to its own comment

**`core_wiring.py:781–790`** `[VERIFIED]`. The comment states:

> When AEC is actively cancelling echo (`aec_active`, computed above), fire the ack asynchronously (`block=False`) so `ears.wait_settle()`/`listen()` below start immediately instead of waiting for playback to finish — **this is what actually removes the fixed 1-1.5s tax per wake cycle.**

The call is:

```python
tts.speak(ack, fast=True, block=True)
```

Unconditional `block=True`, with no `aec_active` test. And `TTSWorker.speak(..., block=True)` does `done_event.wait()` `[VERIFIED]` — it waits for synthesis *and* playback. `aec_active` **is** computed at line 713 and **is** used, but only for `post_tts_settle_s` (0.3 s with AEC vs 1.3 s without).

So the designed optimisation is documented but not implemented. Every wake cycle pays the full ack before SARA starts listening. `block=True` is also the parameter default, so omitting the argument would not help — the fix must pass `block=not aec_active`.

**Honest sizing:** the comment claims 1–1.5 s. The TTS phrase cache makes synthesis ≈ 0 after the first cycle, so the standing cost is probably playback of "Yes?" plus queue latency — `[EST]` 0.4–0.6 s, not 1–1.5 s. The comment may predate the cache. **Batch A measures it; `ttfa_ms` already captures exactly this.**

This matters for your core complaint. A fixed per-wake delay *before listening even starts* is felt on every single interaction, which is precisely the kind of thing that makes one assistant feel slower than another regardless of model latency. One-line fix, gated on `aec_active`, with a test.

### D19 — three unrelated intents mis-route to `unmute`

Logged in `FINDINGS.md` during the research pass. From `bench/results/latest.json` `[MEAS]`: `bluetooth_on`, `open_airplane_mode_settings` and `restore_windows` all → `unmute` (2 each). Three semantically distant intents collapsing onto one target is a pattern-overlap bug, not classifier noise. `unmute` is harmless; the same mechanism on a tier-escalated intent would not be, and `false_trigger_adversarial` is 20.83% in the same run.

Both are **local, cheap, and worth fixing before any migration work** — batches B and C.

---

## 9. Risks and rollback

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | A Live tool call bypasses `guard.py` | **Critical** | `tool_bridge` is the only path; batch E's tests land **before** any Live code |
| 2 | Deadlock awaiting a tool inside `receive()` | **Critical** | §5 rules 1–2; explicit regression test |
| 3 | Hinglish regresses unnoticed | **Critical** | Baseline in batch A; speech-in `bench` mode; default stays `cascade` |
| 4 | Offline use lost | High | `CascadeEngine` keeps the local path behind the same interface; both tested in CI |
| 5 | Privacy posture changes silently | High | `caps.sends_audio_to_cloud` drives the indicator; auto local→cloud switching is **not implemented at all** |
| 6 | 15-min session cap / reconnect churn | High | Batch H; measure real drop rate on your network |
| 7 | Barge-in double-ownership | Medium | Disable local `barge_watch` when `caps.server_barge_in` |
| 8 | asyncio leaking into the orchestrator | Medium | `events()` is a sync iterator; a test asserts no asyncio import outside `sara/voice/` |
| 9 | `gemini-3.8-live` needs SDK types absent from 1.23.0 | Medium | All required types **verified present** `[VERIFIED]`. If a newer field is needed, a bounded upgrade gated on the full suite — not a rewrite |
| 10 | pywebview surface drift | Low | `test_api_surface.py` catches it by design |

**Rollback.** Three independent levels:

1. **Config:** `VOICE_ENGINE=cascade` — the default. Reverts to today's behaviour with no code change.
2. **Code:** all new code lives under `sara/voice/` plus one additive branch in `core_wiring.py`. Deleting the directory and that branch restores `f2e3d51` behaviour.
3. **Git:** every batch is its own commit on top of `f2e3d51`, with a pre-batch backup under `docs/audit/backups/` as in batches 1–6b3.

Because the default never changes until batch K, **every intermediate state is a working build** — requirement satisfied by construction rather than by care.

---

## 10. What I need from you, and what I do not

**Resolved from repository evidence and official docs, as instructed — not asked:** which API (Gemini Live, `gemini-3.8-live`), whether a dependency change is needed (no), where tool declarations come from (`TOOLS_SCHEMA`), how policy is enforced (`route_chat.py:258` pattern), audio formats (already matching), where the GUI surface goes (a new mixin), how to measure (existing telemetry), interface shape (SARA-shaped), threading model (owned loop thread + queues), default (cascade until measured).

**Genuinely needs your machine** `[GAP]` — Phase 3, not now: a `GEMINI_API_KEY` with Live access in `.env`, never committed; real mic/speaker latency; actual Hinglish quality; your network's RTT and stability; CPU/RAM under load.

**One thing I will flag rather than decide, because it is yours to make:** Gemini Live's **free tier is free because free-tier content may be used to improve Google's products; paid-tier content is not** `[DOC]`. For an assistant that hears your room, that is a privacy decision, not a billing one. My recommendation is the paid tier as the default for the Live path with the free tier an explicit opt-in — but I will implement whichever you choose, and the indicator will state it honestly either way.

---

## 11. Phase 1 conclusion

Approve and I start at **batch A** — baseline capture, which changes no code — then **B and C**, the two local defects, which are worth fixing whether or not the Live engine ever ships. The first new code is batch D, and it is behaviour-neutral by design.

The headline is that this integration is unusually cheap for what it buys: **no new dependencies, no resampling, no new security architecture, no changes to the audio stack, and a measurement harness that already exists.** What has to be built is a session, a thread boundary, an interface and a settings surface. That is a far smaller and more reversible change than "replace the voice pipeline," which is what this looked like before reading the code.

*No production code modified in Phase 1. Test baseline 2115 passed / 19 skipped / 0 failed, unchanged.*
