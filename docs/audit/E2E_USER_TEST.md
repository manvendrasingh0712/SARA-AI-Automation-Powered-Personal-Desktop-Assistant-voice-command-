# SARA AI — End-to-end user test & improvement plan

Date: 2026-10-29 · commit `f566963` · machine: RTX 3050 Laptop (4 GB), Python 3.11

Goal: **faster, smarter, more accurate.** Every number below was measured in
this session or read from your own telemetry (`telemetry.sqlite`, 101 real
voice turns) and `logs/sara.log`. Nothing was executed on the PC (no volume
changes, no app launches) — routing was tested in dry-run.

---

## 1. Health snapshot

| Area | Result | Verdict |
| --- | --- | --- |
| Test suite | 2737 passed, 1 skipped | ✅ |
| Router, golden set (590 rows) | 99.3 %, 0 false triggers, p95 1.2 ms | ✅ but overfit (see §3.4) |
| Router, **98 fresh held-out phrasings** | **74.5 %**, 2/20 false triggers | ❌ |
| Security red-team | 100 % recall on covered, 0 % FPR; **1 attack succeeds** (A041), 9 uncovered | ⚠️ |
| Memory 2.0 (gold extraction) | 90 % overall; **multi-hop 45 %**, **temporal 68 %** (Hindi temporal 0/3) | ⚠️ |
| Regex voice commands (real use) | STT ~0.2 s, route 0–47 ms, outcome `ok` | ✅ fast |
| **LLM chat voice turns (real use)** | route **2.7 s** + first token **5.3 s**; **73 of 77 ended in `barge_in`** | ❌ broken |
| Startup | **~46 s** to ready (41 s waiting for Ollama) | ❌ slow |

**One-line summary:** commands that hit a regex are fast and good. Everything
that needs the "brain" (LLM, memory, embeddings) is slow, half-dead, or gets
cut off — and that is where all the big wins are.

---

## 2. What a real chat turn costs today (from telemetry + log)

```
speech end ─► STT 0.2-0.6 s ─► "routing" 2.7 s ─► LLM first token 5.3 s ─► TTS ─► (barge-in kills it)
                                   │                    │
                                   │                    └─ Gemini quota dead → ollama-fallback, 4B model half on CPU
                                   └─ 4-5 Gemini *network* embedding calls, 500-error retries, Memory2 timeout
```

Time to first audio on a chat turn ≈ **8 s**. A user perceives anything
above ~1.5 s as "slow" and starts talking again — which is part of why
barge-in fires.

---

## 3. Findings (ranked by impact)

### 3.1 [CRITICAL] The LLM brain is on a 20-requests/day free tier
- Live API response: `429 RESOURCE_EXHAUSTED … limit: 20, model: gemini-2.5-flash … retry in 13h34m`.
- `LLM_BACKEND=gemini`, so the main brain is dead for most of the day; every
  chat turn in telemetry shows `model=ollama-fallback`.
- The tool router (`resolve_tool_call`) also uses `gemini-2.5-flash`; when
  it fails, the heuristic fallback returned `unknown` for 9 of 10 missed
  commands, so they become plain chat.
- **`.env` bug:** `GEMINI_FAST_MODEL=gemini-3.5-flash-lite` (line 34) is
  overwritten by `GEMINI_FAST_MODEL=` (line 39). Effective value is `''`, so
  smart routing is off and the GUI shows "Only one model is configured".
  Also duplicated: `GEMINI_API_KEY`, `WEATHER_API_KEY`, `VOICE_ACK_CACHE`.
- Tested live: `gemini-3.5-flash-lite` works (but 8.3 s cold, with
  thinking on); `gemini-3.8-flash` returned 503 (overloaded).

**Best fix**
1. Make **local Ollama the primary** for chat + tool calls (it is already
   what answers every turn; stop paying a failed Gemini round-trip first).
   Keep Gemini as *opt-in* "pro" for long/complex questions only.
2. Quota-aware circuit breaker: on 429 read `RetryInfo.retryDelay` and open
   the breaker until then (today: 3 failures → 15 min, then it tries again).
3. If Gemini stays: use a flash-lite model with `thinking_budget=0` (much
   faster first token), fix the `.env` duplicates.

### 3.2 [CRITICAL] 95 % of LLM voice turns end in `barge_in`
- Telemetry: `llm/chat/barge_in` = 73 turns, `llm/chat/ok` = 3.
- Most barge-in rows have **no first-audio timestamp** → SARA was cut off
  before (or right as) the answer started.
- Log: `calibration declined: the room averaged RMS 664 …`, while
  `BARGE_IN_ENERGY_THRESHOLD=300`. The barge-in detector
  (`tts_worker._watch_loop`) only needs `is_user_speaking(0.3 s)` on an
  **energy** test — room noise/music/echo above 300 trips it.
- `VOICE_SEMANTIC_BARGE_IN=False` — the smarter detector exists but is off.
- *(Hypothesis with strong evidence; confirm with one quiet-room session.)*

**Best fix**
1. Barge-in threshold = `max(configured, noise_floor × k)` using the
   calibration you already compute; require WebRTC-VAD speech frames, not
   just energy.
2. Turn on `VOICE_SEMANTIC_BARGE_IN` after a test session (it stops only if
   real words are recognised).
3. Never record a barge-in during the "thinking" ack — and fixing §3.1/§3.3
   shrinks the silent wait that invites the user to talk over SARA.

### 3.3 [HIGH] Network embeddings on the hot path; personal memory never used
- `EMBEDDING_BACKEND=gemini`: 4–5 `embedContent` HTTP calls per chat turn.
  Measured 2.4 s cold, ~0.56 s warm each; on HTTP 500 the retries block
  ~3 s (log 22:49:30–33).
- `[Memory2] retrieval timed out after 400 ms` on **every** turn → Memory2
  context never reaches the prompt. SARA is "forgetting" by design today.

**Best fix**
1. Local embedder: a small multilingual ONNX model (e.g.
   `multilingual-e5-small`, ~10–20 ms on CPU, works for Hindi/Hinglish) or
   Ollama `nomic-embed-text` (not pulled yet). Keeps everything offline.
2. Embed the user text **once per turn** and share the vector between
   RAG follow-up and Memory2 (today each re-embeds).
3. One-time re-embed of stored memories after switching (the code comment
   in `sara/core/rag.py::_get_embedding` already documents this).

Expected: routing stage 2.7 s → **< 100 ms**, Memory2 actually active.

### 3.4 [HIGH] Router is overfit: 99.3 % golden vs 74.5 % on fresh phrasing
98 new utterances (EN / Hinglish / Devanagari / ASR noise / traps),
script in the session scratchpad. Misses that fall to chat with **no tool
fallback** (`_TOOL_KEYWORD_GATE`/`_TOOL_SIGNAL_RE` lack the words):

| Said | Got |
| --- | --- |
| turn the volume down a bit · make it louder · volume thoda kam karo · awaaz badha do · आवाज़ कम करो | chat |
| remind me in 20 minutes to check the oven · mujhe 7 baje yaad dilana… · remind me tomorrow morning… | chat |
| timer 90 seconds | chat |
| will it rain today · aaj mausam kaisa hai · मौसम कैसा है | chat |
| how much battery do i have | chat |
| type hello world · press enter | chat |
| hey can you open chrome for me | chat |
| स्क्रीनशॉट लो · screenshot le lo | chat |
| add finish resume to my todo list | list_todos (wrong) |
| find my resume pdf | web_search (wrong) |

False triggers: `what does mute mean in hindi` → **mute**;
`how to take a screenshot on mac` → **screenshot**.

**Best fix (layered, keeps regex speed)**
1. Short term: add the missing keyword families (volume/awaaz/aawaz,
   remind/yaad, timer, mausam/rain, battery, type/press, Devanagari forms)
   to the gate + patterns; add a "question about X" guard
   (`what does … mean`, `how to …`) before action intents.
2. **Stop scoring on the tuning set.** Freeze a held-out set (start with the
   98 here) that patterns are never edited against; report both numbers.
3. Medium term: a **semantic second stage** before the LLM — kNN over the
   590 golden utterances using the same local embedder from §3.3
   (~5 ms). Paraphrases like "make it louder" land on `set_volume`
   without a new regex each time. Then LLM tool-calling via local Ollama
   (qwen3 supports tools) for the rest.

### 3.5 [HIGH] GPU is oversubscribed (4 GB for STT + TTS + LLM)
- Ollama `qwen3:4b q4_K_M`: 3.37 GB needed, **2.24 GB on GPU**, rest on CPU
  → **21 tok/s**, 1.7–2.4 s for one sentence. VRAM 3.8/4.0 GB used.
- Whisper preview re-decodes the growing utterance 10+ times per command
  (log: 0.2 s → 0.68 → … → 3.9 s), competing for the same GPU.

**Best fix**
1. `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`,
   `OLLAMA_NUM_CTX=2048` → model fits fully on GPU (expect ~2–3× tok/s).
2. Or a 1.5–2 B model for chat speed, keep 4 B only for planning.
3. Cap preview re-decodes (decode only every N ms of new audio) or run the
   preview model on CPU int8 so the GPU is left to the LLM.

### 3.6 [MEDIUM] Startup ~46 s
41 s of it blocking on Ollama start (`Ollama started successfully by SARA`
at +41 s). **Fix:** show the GUI and accept regex commands immediately;
start/warm Ollama in the background (`keep_alive`) and announce "brain
ready" when it is.

### 3.7 [MEDIUM] Memory reasoning gaps
Memory bench: multi-hop **45 %** (6/11 fail), temporal **68 %**, Hindi
temporal **0/3**. **Fix:** temporal parsing for Hindi/Hinglish time words
(kal, parso, pichle hafte…) at extraction; for multi-hop, retrieve top-k
then a second hop on the entities of the first hits.

### 3.8 [MEDIUM] Security: one successful attack
`A041` (exfil via a note: ask the agent to send user name/city to a website)
succeeds; 9 attack rows have no detector at all (recall 0 % on uncovered).
**Fix:** a policy rule — never put profile/PII fields into outbound
tool arguments (URLs, search queries, typing) when the turn is tainted by
untrusted content.

### 3.9 [LOW] Polish
- Kokoro English voice phonemises Hindi text (`language switches … "hi"
  phoneset` warnings) → mispronounced Hinglish. Route Devanagari/Hinglish
  replies to a Hindi voice, or transliterate first.
- `audioop` removed in Python 3.13 (D13).
- `bench/results/RESULTS.md` is stale (Oct 2, 41.6 %) — regenerate.

---

## 4. Recommended order

| # | Change | Effort | Expected effect |
| --- | --- | --- | --- |
| 1 | Fix `.env` duplicates; Ollama primary + quota-aware breaker | S | LLM works all day; −1–3 s per turn |
| 2 | Local embedder + embed-once-per-turn | M | routing 2.7 s → <0.1 s; Memory2 alive |
| 3 | Noise-relative + VAD barge-in | M | chat answers stop getting cut off |
| 4 | Ollama VRAM settings / smaller chat model | S | 21 → ~50+ tok/s |
| 5 | Router keywords + held-out set + semantic stage | M | 74.5 % → 90 %+ on fresh speech |
| 6 | Background Ollama start | S | ready in seconds, not 46 s |
| 7 | Memory temporal/multi-hop, A041 rule, Hindi TTS | M | smarter + safer |

Items 1–4 together should take a chat voice turn from **~8 s to ~1.5–2 s**
time-to-first-audio and make it actually finish.

---

## 5. Status after the first fix batch (2026-10-29)

Suite: **2774 passed**, 1 skipped (+37 new tests). Benches unchanged
(router 99.3 %, red-team 0 % ASR on covered, memory recall@3 0.876).

| # | Fix | Where | Measured |
| --- | --- | --- | --- |
| 1 | `.env`: 4 duplicate keys removed (backup `.env.bak`); `GEMINI_FAST_MODEL` active again; `LLM_BACKEND=ollama` | `.env` | warm chat first token **0.19–0.35 s** (was 5.3 s p50), reply 0.9–1.5 s |
| 1 | Quota-aware breaker: a 429 with `retryDelay` trips the shared breaker until the reset (chat + short generators) | `llm/engine.py`, `llm/provider_runtime.py`, `orchestrator/core_wiring.py` | tests: `test_quota_aware_breaker.py` |
| 1 | Tool router falls back to local Ollama instead of the keyword heuristic, and skips Gemini while it is blocked | `core/tool_router.py` | "open chrome for me" → `open_app(chrome)`; later calls 0.4–0.6 s |
| 2 | RAG used the Gemini model name with the Ollama backend (bug); per-text embedding cache; automatic re-embed when the vector size changes; optional CPU-only embedder (`OLLAMA_EMBEDDING_NUM_GPU`) | `core/rag.py`, `llm/clients.py`, `config.py`, `.env` | **on**: `paraphrase-multilingual` (562 MB) on CPU, 0 MB VRAM. Embed **27–32 ms** (was 560–2400 ms), search 26–28 ms. Related EN/Hinglish/Hindi pairs 0.42–0.74 vs unrelated 0.08–0.13, so `RAG_MIN_SIMILARITY` 0.45 → 0.40. Re-embed of the 97 stored memories tested on a DB copy: 10.5 s, all rows 3072 → 768 |
| 3 | Barge-in must also be `BARGE_IN_NOISE_RATIO` (3×) louder than the room level captured when TTS starts | `audio/stt/engine.py`, `config.py` | still interruptible at every measured floor/speech pair; needs a real voice session to confirm |
| 4 | Spawned Ollama gets flash attention + q8 KV cache; one `ollama_load_options()` for all 7 call sites (no reloads); optional `OLLAMA_NUM_GPU` | `ollama_manager.py`, `llm/clients.py` + call sites | 22 → 28 tok/s; `OLLAMA_NUM_GPU=99` measured **55 tok/s** with SARA closed |

Not yet done: §3.6 background start, §3.7 memory, §3.8 A041, §3.9 Hindi
voice.

---

## 6. Second batch — found by driving the real pipeline (2026-10-30)

Harness: the GUI text path (`_handle_command`) with the real Ollama brain,
RAG, Memory2 and tool router, on a copy of the app data. Side-effect
handlers and `SIMPLE_ACTIONS` are recorded instead of run, and
`subprocess`/`os.system`/`os.startfile`/`webbrowser` are blocked in-process.

**Incident.** The first version of the harness stubbed `_INTENT_HANDLERS`
but not `SIMPLE_ACTIONS`; "shutdown the computer" ran a real
`shutdown /s /t 10` and the PC powered off (System log: Kernel-Power 109
at 00:16:08, reboot 00:20:33). Fixed in the harness (hard safety net) and
in SARA (below, #C2).

| # | You said | Was | Now | Where |
| --- | --- | --- | --- | --- |
| A | "mera naam kya hai" | saved the name "Kya Hai"; "I am a fool" had saved "A Fool" (still in your DB) | questions, idioms and a trailing "hai" are never a name | `orchestrator/text_utils.py` |
| B | "what is 15 percent of 2400" | "The answer is 15." | 360; also "15% of 2400", "2400 ka 15 percent" | `intent/patterns.py`, `orchestrator/calc_utils.py` |
| C | "shutdown the computer", "spotify band karo", "quit spotify", "exit full screen", "gaana/wifi band karo" | quit SARA | exit words only when they are the whole sentence | `text_utils._is_exit_command`, `dispatcher.py` |
| C2 | "shutdown the computer" | powered off after 10 s, no question | always "Are you sure…? Say yes or cancel." (`CONFIRM_DESTRUCTIVE_ACTIONS`) for shutdown/restart/log off/empty recycle bin | `dispatcher.py`, `config.py` |
| D | any weather question | wttr.in sent HTML to the browser User-Agent → "couldn't fetch" | curl User-Agent; no place = your own location | `tools/web.py` |
| E | chat | first words 1.7–3.6 s | 1.0–1.8 s warm: stable prompt prefix first (Ollama prefix cache), first chunk cut early (`VOICE_CHUNKER_V2`, was overridden to False in `.env`) | `llm/engine.py`, `.env` |
| F | held-out phrasings | 74.5 % | 94.9 % on the tuned set, **94.7 % on a second set written after the fix**; golden bench unchanged 99.3 %, 0/60 | `intent/patterns.py`, `intent/engine.py`, `handlers/system.py` |

Still open: "i want to find my purpose in life" → web_search and "kal ka
mausam bahut accha tha" → weather (both older patterns); a reminder with no
time ("remind me tomorrow morning…"); colon-free typing stays off (D79).

Suite: **2880 passed**, 1 skipped.

---

## 7. Third batch (2026-10-30)

| # | Problem | Fix | Measured |
| --- | --- | --- | --- |
| G | Window waited for `ollama serve` (41 s cold, 9 s warm) | Ollama starts on a background thread; the brain polls for it (`OLLAMA_STARTUP_WAIT_S`, 90 s) and warms with the real system prompt | brain ready 0.5 s after launch, warm-up done ~14 s later with no error; 2nd question 0.44 s |
| H | "i want to find my purpose in life" → web search; "kal ka mausam bahut accha tha" → weather for city "Kal" | "find" only as the opening verb; time words are not places | 2nd held-out set 38/38, 0/11 false triggers; golden 99.3 %, 0/60 |
| H2 | "search for python tutorials" searched "for python tutorials"; "find me a recipe" searched "me a recipe" | alternation order, "me" dropped | — |
| I | Red-team A041 succeeded ("tell the website owner what the user's name and city are") | `exfil_personal` detector rule (possessive "user's" + personal field) | overall ASR 1.39 % → **0 %**, FPR 0 % on 102 benign rows |
| J | English questions answered in Hinglish (typed turns never changed the brain language; Whisper never reports Hinglish) | auto mode: reply language detected from the message text; GUI text path records the mode; manual EN/HI untouched | English → English, Hinglish → Hinglish, Devanagari → Hindi |

Known cost of J (fixed, K below): a turn that **switched** language rebuilt
the system prompt in the new language, so Ollama's prefix cache was lost —
~3.3 s to first word on a switching turn vs ~1 s otherwise.

**K — language switch without a prompt rebuild.** The system prompt now
stays in the base language (`SARA_LANGUAGE` or the manual GUI choice); an
auto-detected turn language becomes a one-line note placed after the
history, right before the message (`SaraLLM.set_turn_language`). Voice
turns use it too (they used to call `set_language` from Whisper's en/hi
guess every turn). A manual choice clears any leftover note. Measured,
alternating languages every turn on a Hinglish base prompt: first word
**0.4–1.5 s**, prompt processing **91–247 ms**, reply language right 7/7
(English → English, Hinglish → Hinglish, Devanagari → Hindi).

Suite: **2917 passed**, 1 skipped. (The batch-1 observation "in Hinglish
mode the 4 B local model answered an English question in poor Hinglish" is
resolved by J and K.)

---

## 8. Fourth batch — memory, reminders, voice, vision (2026-10-30)

| # | Problem | Fix | Measured |
| --- | --- | --- | --- |
| L | **Memory2 had never saved a fact.** Extraction went through the chat path: the spoken-reply cap (`OLLAMA_NUM_PREDICT=300`) cut the JSON, and `parse_json()` returned an inner object (`{"name":"Jaipur"}`) so a broken reply counted as "ok, 0 facts". It also wrote every extraction prompt into the chat history and lost `lives_in`'s underscore | `SaraLLM.generate_task()` (stateless, own token budget, newlines kept, shares the quota breaker); a payload must contain `entities`/`facts`/`events`; consolidation uses it too | 7 facts + 1 event saved from 7 sentences; recall 26–30 ms |
| M | Relatives had no predicate, so "where does my sister live?" could not follow "Riya lives in Pune" | `has_<relation>` predicates (+ Hindi/Hinglish aliases), rule-based `relations.py` backs up the LLM, descriptions ("a teacher in Delhi") are rejected as names | multi-hop **45 % → 100 %** |
| N | "do din pehle", "दो दिन पहले" had no time window | Hindi/Hinglish number words in `timeparse.py` | temporal **68 % → 100 %** |
| O | "where do I work?" answered "Rohan works at Google"; "mera favorite khana" answered "favorite color is blue" | first-person questions never take another person's fact; a different known kind of fact is not a stand-in | false-memory rate 5.3 % → **0 %** |
| P | `dateparser` read "tomorrow at 9" as 01:30; could not read "tomorrow morning", "tonight", "7 baje", "5 baje shaam", "kal subah", "2 ghante" | `tools/reminder_time.py` first, `dateparser` as fallback; day-only reminders route | 25 tests |
| Q | Romanized Hinglish goes through the Hindi phonemizer, which falls back to English rules for Latin words | option `KOKORO_HINGLISH_PHONEMES=en` (English phonemes, Hindi voice); default unchanged | samples in `tts_samples/` (git-ignored) — **choose by ear** |
| R | "what's on my screen" dies with Gemini's 20-requests/day quota | Gemini skipped for the server-given retry time; optional local fallback `OLLAMA_VISION_MODEL` | off until a vision model is pulled |
| S | `audioop` (removed in Python 3.13) | already optional (numpy first, pure-Python fallback); now pinned by a test | — |

**Memory benchmark (130 questions):** app embedder (multilingual, local)
**100 %**, hash embedder 98.5 %; baseline vector-only 81.9 % / 54.3 %;
forgetting leak 0. Gold data fixed in two places: one wrong fact in D04, and
`bench/memory/loader.py` now reads naive timestamps as local time like the
app (it used UTC, shifting every scenario 5:30 h on this machine).
`python -m bench.run_bench --mode memory --embedder app` runs it.

Suite: **2982 passed**, 1 skipped. Router golden 99.3 % (0/60 false
triggers); red-team ASR 0 %; real-pipeline regression unchanged.

### Still open
- **A real voice session** (barge-in with/without music, speech timing).
- **`OLLAMA_NUM_GPU=99`** beside Whisper + Kokoro on the 4 GB GPU (55 vs 28
  tok/s with SARA closed) — needs one run with SARA open.
- **Vision fallback** needs `ollama pull moondream` + `OLLAMA_VISION_MODEL`.
- **Hinglish phonemes** — listen to `tts_samples/` and pick.
- Saved user name is "A Fool" — say "my name is Khushhal" once.

### GPU fit check (2026-10-30)

Whisper tiny (CUDA, int8_float16) + Kokoro + Ollama `qwen3:4b` loaded at the
same time on the RTX 3050 (4 GB; ~0.75 GB used by Windows before anything
starts). `OLLAMA_NUM_GPU=99` (all layers on the GPU): model 2762 MB (vs 2260
MB with Ollama's own split), **34–38 tok/s vs ~24**, total 3.7 of 4.1 GB,
Whisper ~230 ms for 2 s of audio and Kokoro ~300 ms per sentence — neither
slowed down. Headroom is only ~360 MB: if another program takes GPU memory
and the model fails to load, delete `OLLAMA_NUM_GPU` from `.env`. Caveat:
Ollama kept the already-loaded model when `num_gpu` changed between rows, so
the comparison is against the earlier default-split run, not back-to-back.
Set in `.env` (local only).

---

## 9. First real voice session after the fixes (2026-10-30, 08:28–08:32)

`python -m bench.voice_latency` aggregates ALL history (169 voice turns),
so the new turns were isolated by time (12 turns, all with the fixes in
place except the full-GPU setting, which was applied later).

| | Before (101 turns, 29 Oct) | New (12 turns) |
| --- | --- | --- |
| outcome | **barge_in 85**, ok 15 | **ok 12 / 12** |
| regex commands, speech end → first audio | 0.2–0.5 s | 0.2–0.4 s (one 0.9 s) |
| STT | p50 594 ms | p50 **250 ms** |
| chat: model first token | p50 5.3 s | 1.6–6.7 s (n = 5, noisy) |
| chat: first audio | ~8 s | 4.8–11.0 s (n = 5) |

**Barge-in is fixed** (the main goal of fix 3). Chat latency is not yet where
the text-path tests are (~1 s). Two things stand out in the 5 chat turns:

1. `route_ms` is **2.06–2.09 s on every chat turn**. In the text harness the
   same step costs 35–142 ms *after* the first turn, and ~2.1 s on the first
   turn only: that is the local embedding model loading (2.6 s measured,
   cold). Both Ollama models stay resident afterwards (checked with the
   chat model on the default split and on `num_gpu=99`), so eviction does
   not explain a per-turn 2 s. **Fix applied:** the embedder is now warmed at
   startup (first lookup 2.6 s → 58 ms). **New diagnostic:** each chat turn
   writes `[Timing] chat route=… decide=…ms later_steps=…ms` to
   `logs/sara.log`, so the next session shows which step costs what.
2. About **3–4 s between first model token and first sound**
   (`tts_start_ms` 0.9–2.0 s plus playback start). Not measured in the text
   path (no speech there). The text-path model speed (~1 s) also does not
   include Whisper/Kokoro/GUI competing for the CPU; this session ran with
   part of the model on the CPU (before `OLLAMA_NUM_GPU=99`).

**Next session, to settle it:** `python main.py`, ask 6–8 chat questions
(not commands), then send `logs/sara.log` lines containing `[Timing]` and
re-run `python -m bench.voice_latency`.

---

## 10. The 2-second mystery: found and fixed (2026-10-30)

The second voice session (08:48–08:52, YouTube playing, with
`OLLAMA_NUM_GPU=99` already active — `.env` was edited at 08:33, so the
first session at 08:28 ran *before* it) logged `[Timing] later_steps≈2060–2100 ms`
on almost every chat turn, with an `/api/embed` call right before it; one
turn right after another embed took 58 ms. That pattern pointed at idle time,
not at the models.

**Reproduced with a stand-alone idle-gap test** (Ollama started the way SARA
does it): embedding after 1 s idle **38 ms**, after ≥4 s idle **2070–2130
ms**; the same for the chat model alone, the embedder alone, any
`num_gpu`/`num_thread`/`use_mmap` option, and with both models resident
(`/api/ps` shows them loaded for 10 more minutes, so it is not unloading).

**Cause:** the Python client's pooled connection expires after ~5 s. A new
connection to `localhost` tries IPv6 (`::1`) first; Ollama listens on
`127.0.0.1` only; Windows waits ~2 s before falling back to IPv4. With
`http://127.0.0.1:11434`: embedding after 6 s idle **37–51 ms**.

**Fix:** `Config.OLLAMA_HOST` rewrites the name `localhost` to `127.0.0.1`
(`config._ipv4_loopback`; other hosts untouched). Every Ollama client reads
that value. Each voice chat turn used to pay the delay twice (memory
lookup + model call).

| Measured through the real engine, 8 s pauses between turns | Before | After |
| --- | --- | --- |
| memory lookup | ~2060 ms | **45–89 ms** |
| first word of the reply | 4–7 s in the session | **530–740 ms** |

The "Memory2 retrieval timed out after 400 ms" warnings on tool-route turns
had the same cause. Also: the `[volume-match]` debug line (hundreds per
minute while a video plays) now prints only when it changes or every 30 s.

### Seen in the same log, not fixed
- Whisper **tiny** mishears names ("Jaipur" → "jippur", "script to jappur",
  "He is a rarah") — the main accuracy lever left is `WHISPER_MODEL_SIZE`
  `base` or `small` (preview ran ~200 ms on tiny; needs a latency check on
  the shared 4 GB GPU).
- (Retracted) I first listed "Sorry, I don't understand" after a mis-heard
  word as a context bug. Re-reading the log: the Jaipur trip had not been
  mentioned yet in that session, so asking to rephrase was a fair answer to
  a garbled word. The fix for that case is the name vocabulary in section 11.

---

## 11. Speech accuracy: names (2026-10-30)

Whisper tiny turned names into near-misses ("Jaipur" -> "Jypur"/"jippur",
"Arijit Singh" -> "Arogyt sing"). Test set: 10 phrases spoken by Kokoro
(8 English, 2 Hinglish) with added noise. English word error rate:

| Option | clean (20 dB) | noisy (8 dB) | decode p50 | extra VRAM |
| --- | --- | --- | --- | --- |
| tiny, app prompt (current) | 5.7 % | 9.3 % | 183 ms | — |
| medium (cached locally) | similar names still wrong | similar | **627 ms** | **+1 GB** |
| **tiny + names in the prompt** | **2.1 %** | **7.7 %** | 195 ms | — |
| tiny + `hotwords` only | 4.2 % | 7.7 % | 199 ms | — |

All sizes, all noise levels: the errors are proper names, and a bigger model
does not fix them (medium 21.9 % vs tiny 24.6 % word errors including the
synthetic-Hinglish rows, 3x slower, would not fit beside the chat model).
Risk check: with the names in the prompt, no vocabulary word appeared on
silence, white noise or ordinary speech (0/18 clips, same as without).

**Built:** `sara/audio/stt/vocabulary.py` appends
` Names that may come up: …` (≤ 24 words, cached 60 s, never raises) to every
Whisper prompt. Sources, in order: `STT_EXTRA_VOCABULARY` in `.env` (your
own words), the user's name / relatives / people, places and orgs from
Memory 2.0, 16 Indian cities, a few brands (YouTube, Spotify, WhatsApp…).
Switches: `STT_VOCABULARY_PROMPT=False`, `STT_VOCABULARY_MAX_WORDS`.

**Not done:** Whisper `base` / `small` were not tested — they are not on
this PC (only tiny + medium are cached) and need a download. The synthetic
test also cannot judge real Hinglish speech; check it in a voice session.
Suite: **3002 passed**.

---

## 12. Third voice session (2026-10-30, 11:06–11:10, after the push)

**Confirmed fixed:** memory lookup after the first turn **2–100 ms** (was
2060 ms); replies start ~2–3.5 s after the end of speech (was 5–11 s).
The first turn after launch still paid ~5 s (`later_steps=4952ms`): at that
moment Kokoro's phrase pre-warm (52 phrases, 37 s on the GPU), the memory
consolidation and the re-embed were all running — a start-up contention, not
the idle delay. Not changed.

**New defects found in the same log**
| # | Defect | Cause | Fix |
| --- | --- | --- | --- |
| T | "Do you know the character of the character of the character?" was spoken in the **Hindi voice** (`Language this turn (auto): 'hinglish'`); 7 of 9 plain English sentences were labelled Hinglish | the Hinglish marker list contained the English word **"the"** (and "bro"); the same detector also picks the reply language | removed both, added unambiguous Hindi words (wo, ghar, tum, kuch, bhi, mein…); English false positives 7/9 → **0/20**, Hinglish still detected 7/7 |
| U | 0.3 s noise blips heard as "Yes." / "Thank you." were answered and **saved as long-term memories** (9 junk memories in minutes) | those words were not in the trivial-turn list | filler/acknowledgement words added; trivial turns skip the memory lookup and are not stored |

Existing junk memories (ids ~152–160) are still in `sara_data.db`; delete
them on the Memory page of the GUI if you want them gone.

Suite: **3038 passed**.

### 12b. Where the last seconds go (measured 2026-10-30)

From the third session's telemetry, 5 chat turns: route → first model token
**2.6–3.9 s**, first token → first sound **1.3–2.0 s**. In isolated tests the
same model gives its first token in ~0.5 s, so something in the running app
is slower; the cause is not yet known. **New diagnostic:** every chat turn now
logs `[LLM] ollama: prompt=N tok in X ms | load=Y ms | gen=Z tok @ R tok/s |
total=T ms` (Ollama's own numbers), which separates a long prompt, a model
reload and slow generation (GPU shared with Whisper, Kokoro and the video).

Speech side, measured through the app's own TTS engine on a quiet machine:
Kokoro inference ≈ **0.55 s per sentence chunk** (≈ 4× faster than real time),
plus lock-waiting when the start-up phrase pre-warm (37 s, 52 phrases) is
running. The pre-warm already pauses while speech plays, so it can delay a
real answer by at most one phrase (~0.7 s) in the first ~40 s after launch.
Further speed-up needs a model-level change (not attempted).

## 13. Second end-to-end audit (practical, real dispatcher)

Method: drove the real dispatcher on a copy of user data with side-effect handlers stubbed; all 140 intents, reminders/to-dos/notes, memory chain, injection and odd inputs, follow-ups, compound commands.

| # | Finding | Fix |
|---|---------|-----|
| 1 | Tests wrote junk to the user's real to-do file | `tests/conftest.py` isolates LOCALAPPDATA/DB/notes/todo paths |
| 2 | Stopwatch intents were stubs | Real start/lap/stop (`tools/system/timers.py`) |
| 3 | To-do delete/complete matched loosely (wrong item) | Strict word-coverage match, ties ask, spoken list capped at 6 |
| 4 | "sleep"/"forget" inside sentences triggered shortcuts | Whole-utterance exit-word check |
| 5 | Wake ack "Yes?" re-heard as a command | `_is_ack_echo` guard (4 s window) |
| 6 | Spoken calculator ("2400 divided by 8") wrong | Spoken-operator normaliser + patterns |
| 7 | Typos / homophones ("teh", "opn") missed | Extended homophones + trigger vocab |
| 8 | LLM claimed actions it never did; invented the time | Action-honesty prompt rules + current-time note |
| 9 | "X and then Y" ran only X | Safe compound splitting (never destructive) + planner fallback |
| 10 | "delete all notes" ran without confirmation | Added to confirm-gated actions |
| 11 | Weather failed when wttr.in was slow | 4 s timeout + Open-Meteo backup |
| 12 | "+22°C" read as "plus 22 C" | TTS speaks "22 degrees Celsius" |
| 13 | Long history caused a rare 15 s stall | History clip (700 chars) + tool timeout 8 s |
| 14 | "close it"/"close the app" with no context tried to close an app named "app" | Generic app words treated as references; asks "which app?" |

Known, unfixed (low priority): bare "search for" / "weather in"; "open source software is great" routes to open_app; slow first token inside the running app (needs the new `[LLM] ollama:` / `[Timing]` log lines from a voice session).

Suite: 3180 passed, 1 skipped. Router golden bench 99.3 %, held-out 38/38, red-team ASR 0 %.

### 13b. Closing fixes

- Incomplete commands: "search for", "look up for" no longer search for the word "for"; bare "weather" / "the weather" gives local weather; "search the web for dogs" keeps a clean query; "open source software is great" is chat, not open_app.
- Ollama down: connection-refused fails fast (no backoff), falls over to Gemini when a key is set (previously only Gemini→Ollama existed), and embedding lookups pause 20 s after a failure (reply time with both backends unavailable fell from ~9.6 s to ~2.6 s).
- Suite: 3188 passed, 1 skipped; router bench 99.3 %, red-team ASR 0 %.

## 14. From the real unmatched-queries log (2026-10-30)

`logs/sara_unmatched_queries.jsonl` (193 distinct lines that fell through to the chat model) and the stored replies in `conversation_log` showed why the LLM "could not answer":

| Cause | Evidence | Fix |
|---|---|---|
| Whisper junk answered as if spoken (30 of 193 lines): its own style prompt echoed back ("Transcribe exactly what is spoken ...", "Keep proper names and technical terms exactly as spoken"), loops ("ndndnd...", "vay,vay,..."), ". .", "Thank you very much for watching", "Copyright WDR 2021" | the chat model replied to them ("Tell me in this story.") | `_is_prompt_echo`, `_is_degenerate_text`, wider `_is_known_hallucination` in `sara/audio/stt/helpers.py`, applied in `engine.py` before the router |
| Questions about the user's own data went to a model that has no access: "Is there any event today?" -> "I don't have access to calendars"; "tell me about my daily brief" -> same; "What do you know about me?" -> "I don't store any personal information" | stored replies | routed to the calendar tool, the daily-briefing skill and `memory_recall`; recall now lists only saved facts (raw chat turns are no longer read back) |
| Commands the router missed: "panihari song chalao spotify per" (the model answered "playing" and did nothing), "Offen Chrome.", "nest track", "type : hello", "Maximize this video.", "screen vision" | log | new patterns / homophones, see `tests/test_log_driven_fixes.py` |
| `play_youtube` timed out ("taking longer than expected") for "play prada", "Search YouTube" | 3 stored replies | 14 s limit and, on timeout, the YouTube search page is opened instead of an apology |
| Long-term memory polluted: activity logs ("User toggled play and pause several times"), world-knowledge answers saved as facts, names from garbled transcripts ("User's name is Kya H") | 34 of ~61 consolidated/fact rows | stricter `_is_durable_fact` (activity verbs, hedges, rambling), extraction prompt ignores commands; `python -m sara.core.memory_cleanup` (dry run) / `--apply` (backs the DB up first) removes the existing ones. Not applied automatically: close SARA, review, then run it |

Open: the small model (Whisper `tiny`) still mishears Indian names in English speech ("India's prime minister" -> "English Prime's turn and endrumathi"); trying `base` or `small` needs a model download.

## 15. Speech recogniser: English + Hindi + Hinglish (2026-10-30)

**Problem.** `tiny` understood almost no Hindi/Hinglish commands ("Chrome kholo" -> "Drum kolu."), and every Whisper transcript ends with "." / "?" / "।", which the router did not accept ("Volume down." and "Increase the brightness." went to the chat model).

**Test set.** 51 clips spoken by Windows voices (US English Zira/David, Hindi Kalpana/Hemant reading Devanagari, which is how Hinglish loanwords like "volume", "screenshot" sound) at clean / 15 dB / 8 dB noise. Score = the app's own router must give the same intent as for the written sentence (so it measures "understood the words", not letters). Kokoro-made Hinglish was rejected as a test source: its Hindi voice does not speak romanised Hinglish like a person.

| model (faster-whisper int8) | EN commands | Hindi/Hinglish commands (clean / 15 dB / 8 dB) | VRAM | decode |
|---|---|---|---|---|
| tiny (was) | 88 % | 17 / 11 / 6 % | ~110 MB | 0.25 s |
| base | 100 % | 11 / 6 / 11 % | 214 MB | 0.23 s |
| small | 100 % | 33 / 11 / 17 % | 441 MB | 0.36 s |
| medium (cached before) | 88 % | 39 / 22 / 6 % | ~1 GB | 0.6 s |
| tiny-Hinglish (abhishekgautamm) | 76 % | 61 / 28 / 22 % | 204 MB | 0.2 s |
| **Hinglish-Swift CT2** (`MSpider3/whisper-hindi2hinglish-swift-ct2`, 78 MB) | 94 % (WER 12 %) | **78-89 / 44-67 / 33-39 %** | 260 MB GPU or **0 on CPU, 1.1 s** | |

**Chosen: two models.** `base` (GPU) for English and `Hinglish-Swift` (CPU, only when needed). Rule (`choose_transcript` in `sara/audio/stt/helpers.py`): a clear English command from `base` is final (the second model is never run); otherwise the Hinglish model's text is used if the router understands it; else `base` if it is confidently English; else the Hinglish text. Result on the 51 clips through the real engine method: commands understood **94 %** (base alone 54 %, Hinglish alone 91 %), English sentences keep base accuracy (WER 2.4 % vs 6.5 %), Hindi sentences come out as readable Roman Hinglish ("Mujhe ek kahaani sunao"). With white noise at 15 / 8 dB: 74 / 69 %.

Settings (`.env`): `WHISPER_MODEL_SIZE=base`, `WHISPER_HINGLISH_MODEL=MSpider3/whisper-hindi2hinglish-swift-ct2`, `WHISPER_HINGLISH_DEVICE=cpu`. Leave `WHISPER_HINGLISH_MODEL` empty to go back to one model.

Also fixed on the way: the router now strips trailing `. ! ? । ,` (dictation excepted), Hinglish spellings "Aavaaz"/"pahunch" are normalised, and "awaaz/volume band karo" means mute instead of "close the app awaaz".

Caveats: the clips are synthetic speech, not your microphone; the Hinglish model's own accuracy claims come from its author. Real-world numbers will differ; `bench/stt_doctor.py` is for measuring on your voice. The Hinglish model is a community conversion (not from Systran/OpenAI) of Oriserve's fine-tune; it is a plain CTranslate2 weights file, no code.

## 16. STT pipeline audit after real use (2026-10-30, 23:00 session log)

Evidence: the live log of a session with YouTube playing in the background (`[volume-match] ... TMKOC ...`) and the dual-model recogniser on.

| Finding | Evidence | Change |
|---|---|---|
| Background audio is the main reason "my voice is not heard": the echo canceller only knows SARA's own voice, so YouTube / Spotify is captured and transcribed | captures of 6.1 s, 9.7 s, 18.3 s ending in loops: "kahaan kahaan kahaan ...", "li, li, li ...", "yayaya ...", song lyrics earlier | `sara/audio/ducking.py`: while a command is being captured, every OTHER playing app is turned to 15 % (per-app Windows volume, pycaw) and restored right after; original volumes are saved to disk first and restored on the next start after a crash; a volume the user changed meanwhile is left alone. Verified live: tone session 1.0 -> 0.15 during `wake_now`, back to 1.0 after |
| The Hinglish model invented text where the main model heard nothing | `main='' hinglish='Haan haan haan.'`, the loops above | it is skipped when nothing was heard; its result is dropped when it loops, is contentless or its log-probability is below -1.0; beam 1 |
| Rule inputs were invisible | one could not tell why B won | log line now shows main text, language, probability, log-prob, Hinglish text, its log-prob and decode time |
| Hinglish pass costs 0.8-1.0 s on the CPU (the encoder always works on a 30 s window; 8 threads 0.82 s, 4 threads 0.98 s) | measured | `WHISPER_HINGLISH_DEVICE=auto` uses the GPU when 900 MB are free (4 GB card: stays on CPU) |
| Ambient noise is tiny (RMS median 6, max 12 in this room), the capture gate is 278 (mic sensitivity slider 23 %) | measured with the microphone | unchanged: a lower gate would not help against background audio, ducking does |
| No way to replay a real failure | | `STT_DEBUG_DUMP_AUDIO=True` keeps the last 200 captures (WAV + text + gate) in `logs/stt_dumps` |

Offline check after the changes (51 spoken clips, real engine method): commands understood 34 of 35 clean; guards covered by `tests/test_stt_ducking_and_hinglish_guards.py`.
Not solved: music louder than the ducked level, or voices in the room (TV with people) still reach the microphone; a real fix would feed the system audio (WASAPI loopback) to the echo canceller as the far-end signal, which needs a loopback capture library that is not installed.

## 17. System audio as the echo-canceller reference + calibration on the user's voice (2026-10-31)

**System audio removal (WASAPI loopback).** `sara/audio/system_reference.py` reads the final mix going to the speakers (library `soundcard`, added to requirements) and `AECProcessor.feed_external_reference()` hands it to the WebRTC canceller as the far-end reference, so YouTube / Spotify / calls are removed from the microphone instead of being transcribed.
* Device: the physical output when the default one is a virtual enhancer (FxSound here); its loopback carries the signal after the enhancer's gain (peak 0.54 vs 0.12 on the virtual one).
* Timing problem found by measurement: the loopback copy reaches us 20-30 ms AFTER the echo reaches the microphone (noise-burst onsets), and the canceller needs the reference first. Fix: while other audio is playing the microphone path runs through an 80 ms delay line (engaged with silence, drained at 2 frames per frame afterwards).
* Windows resumes loopback data ~0.5 s late after a silent gap, so the reference stays "on" for 2 s after the last sound (without that the delay line engaged at every pause in a video).
* SARA's own speech keeps the old direct feed (the mix would contain it twice).
* Results, speech from a video through the laptop speakers (live path, real `AECProcessor`): echo removed 0 dB -> **50 dB**, words Whisper still hears 39 -> **0**. Offline sweep: 4 dB with the reference arriving late, 26 dB with the 80 ms delay line. User speaking over the video (6 commands, user louder than the echo): understood 4/6 -> 5/6 (user as quiet as the echo: 3/6 -> 2/6, within noise for 6 clips). With the volume ducking from section 16 the echo is already ~6x quieter during a command.
* Cost: about 4 % of one core while idle (30 ms reads); no GPU. `AEC_SYSTEM_REFERENCE=False` turns it off.

**Calibration with your voice.** `python -m bench.stt_calibrate [--apply]` records 38 sentences you read (English / Hinglish commands, English / Hindi conversation), recognises each with both models, replays every threshold pair of the dual-model rule and recommends the one with the best score and the fewest slow Hinglish-model runs (ties resolve towards the shipped defaults); `--replay` tunes again from the saved recordings. Verified on the synthetic corpus (main model 62 %, Hinglish model 95 %, rule 97 %). Real numbers need your voice.

## 18. Calibration on the user's real voice (2026-10-31)

38 sentences read by the user into the laptop microphone (`bench/stt_calibrate`, recordings in `logs/stt_calib`, references known). What the real data showed, none of which the synthetic speech had:

| Finding | Number | Action |
|---|---|---|
| The voice is quiet: RMS median **197**, 10th percentile 144, peak median 0.07 | capture gate was 278 (mic sensitivity 23 %) | gate lowered to 73 (`mic_sensitivity` 88 %, threshold 208; room noise is RMS 6-12). At gate 278 the first syllable of the quietest clips was missed by up to 832 ms (the pre-roll keeps only 300 ms) |
| Real voice is much harder than synthetic speech | main model `base`: 41 %, `small` 51 %, `medium` 57-63 %; Hinglish model 72 % | order reversed: **Hinglish model first**, main model only when needed |
| Beam size of the Hinglish model | beam 1: 62 %, beam 2: 72 % (3: 70 %, 5/8: 72 %) | beam 1 (set earlier for speed) removed; beam 2; on the GPU it takes 0.2 s |
| Level boost | hurts the Hinglish model (72 -> 64 %), helps `small` (46 -> 51 %) | Hinglish model gets the raw clip, the main model a levelled clip (`WHISPER_MAIN_GAIN`) |
| Rule | A-first (previous): 66 %; B-first + small: 80 %; tuned on the recordings: **82.9 %** | `WHISPER_HINGLISH_MIN_EN_PROB=0.5`, `..._MIN_LOGPROB=-0.5`, `..._CONFIDENT_LP=-0.2` (written to `.env` by the tool) |
| Cost | the main model runs for 29 % of the sentences instead of the Hinglish model for 76 % | Hinglish model on the GPU (0.2 s, replaces `base` there: GPU 3815 / 4096 MiB), `small` on the CPU (2.4 s, `WHISPER_MAIN_DEVICE=cpu`) |

Only 38 sentences (5 English conversation, 5 Hindi conversation): the thresholds will be re-tuned with `python -m bench.stt_calibrate --replay --apply` after more recordings. `STT_DEBUG_DUMP_AUDIO=True` collects real captures during normal use.

## 19. Live chat test on the real PC (2026-10-31)

449 commands / LLM questions were sent one by one through the running app's own chat (next one only after the
previous reply), covering all 140 intents, the 14 skills, compound commands and ~79 LLM prompts (English, Hinglish,
Hindi, maths, code, prompt-injection, odd input). Power actions (lock/sleep/hibernate/shutdown/restart/recycle bin,
service start/stop) reached their function but were stubbed so the PC stayed on. Volume, brightness, theme and
clipboard were restored afterwards; test notes/reminders/memories were removed.

Result: 420 PASS first time; of the 29 others most were test-set-up artefacts (no "milk" note, no "morning" routine,
Spotify not open, idle "Going back to sleep" message arriving after a correct answer, "bye" intentionally quits the app).
Real bugs found and fixed (tests in `tests/test_live_test_fixes.py`):

- "open my desktop folder" / "pictures folder" failed when the folders are redirected to OneDrive (now uses `shell:` names)
- "what is the boiling point of water in fahrenheit" was answered as weather ("water" was typo-corrected to "weather")
- Hindi number words/units: "बारह गुणा चार कितना होता है", "दो घंटे का टाइमर"
- Hinglish alarms "kal subah 7 baje", "सुबह छह बजे" were not understood
- alarm confirmation said "29.0 hours and 11.0 minutes"
- "switch to focus mode" tried to switch to a window called "focus mode"

## 20. LLM pipeline audit: latency and accuracy (2026-10-31)

Measured in the real app on the 4 GB GPU, plain questions, text chat, send -> final reply:
before 3.4-5.4 s, after 0.8-1.5 s (maths/time go to the calculator route in 0.2 s).

Causes found and fixed:
- **Kokoro on the GPU** slowed the chat model from 52 to 12-28 tokens/s (and synthesised slower than the CPU:
  0.45 s vs 0.72 s for a short sentence). `KOKORO_USE_GPU=False`, `ORT_INTRA_THREADS=12` -> 44-52 tokens/s.
- **History window slid by one turn every turn**, so Ollama's prompt cache missed and re-read ~1,500 tokens each
  reply (2.7-3.8 s). The window now keeps its first turn fixed and moves in big steps (`LLM_HISTORY_MAX_TOKENS`, 700).
- A second memory search + extra prompt block in the dispatcher duplicated the one in the LLM (extra embedding call).
  RAG block capped at `LLM_RAG_MAX_TOKENS` (320).
- **Empty reply** (HTTP 200, zero tokens) counted as success and the user got nothing (#416 of the live test):
  now retried, then the normal "try again" message.
- `proactive.py` chat call used different load options than all others (would make Ollama reload the model).
- `OLLAMA_KEEP_ALIVE=24h` so the model is never unloaded between questions.
- Emoji are stripped from replies and from the history (they were synthesised as separate TTS chunks and copied by the model).
- Hinglish system prompt replaced by a compact English-worded one: English facts/maths 37/39 -> 39/39, ~420 fewer tokens.

Open: Roman-script Hindi/Hinglish factual questions are ~65-70 % correct (e.g. "pani kitne degree par ubalta hai")
whatever the prompt or sampling (tested 6 prompts, 3 sampling sets); the 4B model's Hindi is the limit.

Model comparison (same 64 questions, same prompt, 4 GB GPU): qwen3:4b-instruct-2507 EN 39/39, Hinglish 17-18/25;
gemma3:4b EN 36/39 (wrong percentages: 15 % of 240 = "sixty", 25 % off 800 = "520"), Hinglish 20/25 but still garbled Roman Hindi,
prompt processing 3x slower (no prefix reuse). qwen3 kept; gemma3:4b removed again.

Hinglish accuracy step (2026-10-31): for Roman-script Hindi questions the engine first asks the model for a one-line English
meaning (`_english_gloss`, ~0.25 s, temperature 0, same load options so no reload) and adds it as a hint before the user message,
with the instruction to keep nouns/facts/numbers in English. Roman-Hindi facts: ~65 % -> ~90 % (23/25 in the harness, 9/10 through the
real engine). Also: sampling set to temperature 0.2 / top_k 30 / repeat_penalty 1.08 (`.env`), and old conversation memories no longer
quote the model's earlier answer (wrong answers were repeated). Setting `LLM_HINGLISH_GLOSS=False` turns the gloss off.

## 21. Final verification run (2026-10-31, after the LLM changes)

449 commands again through the app's chat: 418 PASS, 23 FAIL, 8 SKIP (skips: Notepad not in front / Snap Assist). Most FAILs are set-up
artefacts as in section 19 (no "milk" note, no routines, Spotify/VSCode not open, "bye" quits the app). Real bug found: 8 chat questions got
no reply at all. Cause: the voice loop's "Going back to sleep" timer only counted spoken turns, so while the user worked through the chat box the
voice session was put to sleep and the reply of the command running at that moment was dropped. Typed commands now restart that clock
(`note_typed_activity`). A re-run of 14 rapid back-to-back questions: 14/14 answered, no sleep message.

## 22. Problems fixed after the final verification run (2026-10-31)

- chat replies lost when the voice "sleep" timer fired during typed commands (section 21)
- "cricket ki latest news batao" / "technology ke baare mein khabar": Hinglish topics now parsed, "aaj ki news" is plain news;
  Google News (India) is the main source for topic-less news and the backup when DuckDuckGo fails
- "not funny", "that was not good" were refused as a "negated command"
- "run my evening routine" / Devanagari routine names; a clear "no routines yet" message
- "switch to Spotify/VSCode" starts the app when it is not open
- the 8 commands that the test had skipped (scroll, snap, close this window) were run with Notepad in front: 8/8 pass
- tone hints no longer ask the model for "shorter" at night / a fixed English percentage
Test rows made by the live runs were removed from the memory/chat database again (backup kept next to it).
