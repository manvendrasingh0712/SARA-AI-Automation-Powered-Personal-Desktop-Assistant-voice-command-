# Optimizations report (audit part 2) — 2026-10-31

Baseline before this part: **3480 passed, 1 skipped**. After: **3635 passed, 1 skipped**. "verified" = measured or tested on this machine.

| ID | Status | Tests | Before -> After | Notes |
|---|---|---|---|---|
| D1 cancel/alarm | done, verified live | 8 | `alarm band karo` -> close_app; `cancel timer` etc. -> chat  =>  all 5 -> `cancel_timer_alarm`; "close alarm app" still closes an app | skill `conversation.py` |
| D2 switch to hindi | done, verified live | 5 | -> switch_to_application  =>  `set_reply_language` | |
| D3 repeat/continue/rate/language/stop | done, verified live | 25 | all `chat` -> deterministic skills; `repeat that` makes **0** LLM calls (tested) | `stop` while SARA is speaking is handled by barge-in (classifier now also knows chup/quiet/shut up/bas karo/stop it); while idle the new `stop_speaking` skill stops TTS. Bare "continue" is left as media-resume. |
| D4 regex perf / O11 | done, verified | 4 | worst case 93 ms (3,000 chars) -> <20 ms (cap `INTENT_MAX_CHARS=300`); no pattern >20 ms on 300-char adversarial input | leading `(.+?)` not rewritten: the cap makes it unnecessary |
| D5 TTS text | done, tests; **listening unverified** | 32 | dates, versions, units, ordinals, abbreviations, AM/PM, phone numbers now words, one script per phrase | `25°C` in a Hindi sentence still says "degrees Celsius" in English (pre-existing, not changed) |
| D6 prompts | done, verified | 1 + eval | English 812 -> 536 tokens, Hindi 1005 -> 549; local model: explanations in 3-5 sentences 5/10 -> 10/10 (both languages), accuracy unchanged, 1.0 -> 0.7-0.95 s/question | `bench/llm_prompt_eval.py --lang english|hindi` |
| D7 env keys | done, verified | 3 | 47 undocumented keys -> 0; profiles section added | `scripts/gen_env_example.py` |
| D7b hot-path env reads | checked | – | no `getenv`/`getattr(Config)` inside per-frame/per-token loops in stt/tts/llm hot files (only cold paths) | |
| D8 incomplete / unsupported | done, verified live | 12 | `fan chalao` etc. and bare `volume`/`timer` -> fixed spoken replies, no LLM | |
| D9 stop words | done | 8 | stop/ruko/chup/bas karo/stop it/quiet/shut up -> interrupt; `tts.stop()` <150 ms in the fake harness | live barge-in timing unverified |
| O1 semantic intent fallback | **built, NOT wired** | 3 | measured: at tau 0.80 it answers 30-38 % of held-out phrases with only 53 % accuracy and 2-3/68 false triggers on chat | too few examples per intent (1.6 avg); needs the O8 loop first. `bench/semantic_intent_eval.py` |
| O2 structured tool output | built, **off** (`TOOL_CALLING_STRUCTURED=False`) | 4 | native tool calling already 20/20 valid, 18/20 correct, 10/10 no-tool-on-chat, 558 ms; structured: 13/20 correct, 5/10 on chat -> **worse** | `bench/tool_router_structured.py` |
| O3 exact answers | done, verified live | 29 | unit conversion, dates, percentages, square roots in code (<1 ms); calculator bug "2 to the power 8" = 2 fixed | |
| O4 grounded answers | **not done** | – | – | |
| O5 follow-up state | done | 4 | `last_intent`, `prev_intent`, `last_user_input`, `last_reply` in the shared context | |
| O6 adaptive style | not done | – | – | |
| O7 PC context line | done, **opt-in** `CONTEXT_AWARE_ENABLED` | 5 | privacy deny-list (banks, passwords, incognito ...), after the history | active-window title only; now-playing not implemented |
| O8 active-learning loop | not done | – | – | |
| O9 risk-tiered confirmation | done | 5 | low-confidence tier-2 commands are read back ("Did you say ...?"); tier 0-1 just run | "offer undo" for tier 1 not done |
| O10 reply guardrails | done, verified live | 12 | `opn chrmoe` -> no false "Chrome opened!" (reply replaced); loop cut after 2nd repeat | wrong-language regenerate not done. Note: in the live app "opn chrmoe" is fuzzy-corrected by the router and really opened Chrome, which is a different path |
| O12 parallel pre-token work | done (partly existing) | 1 | gloss and memory2 already ran in parallel; added a **0.8 s deadline** (`RAG_DEADLINE_S`) on the memory search so a slow embedding cannot hold the first word | TTFT delta not measured |
| O13 embedding LRU | done, verified | 1 | repeated question: 2 embedding calls -> 1 | |
| O14 Ollama tuning | measured, **no change** | – | `OLLAMA_MAX_LOADED_MODELS=1` made every chat+embed call 3.6 s (eviction thrash) instead of 0.18 s / 0.02 s; `NUM_PARALLEL=1` no change; flash attention + q8 KV already set by `ollama_manager` | do not set MAX_LOADED_MODELS=1 |
| O15 prefix stability | done, verified | 3 | tests: prefix identical across turns, notes after history; log warning when a long prompt took >1.5 s to read | |
| O16 speculative generation | not done | – | – | |
| O17 app index | not done | – | – | |
| O18 news cache | done, verified | 1 | cold headlines fetch 5.5 s -> cached 0 ms; background warmer (`NEWS_WARMER_ENABLED`) | |
| O19 lazy imports | done, verified | – | `dateparser` (0.44 s) and `google.genai` (0.73 s) no longer imported at start-up; `import main` ~7.2 s (with -X importtime) -> 5.3-5.6 s | remaining top costs: openwakeword->sklearn 1.0 s (inside the library), `intent.engine` 0.8 s (regex compile), sounddevice 0.27 s |
| O20-O22 STT/TTS/plan parallel | not done | – | needs spoken audio / model downloads | |
| O23-O26 live feel | not done | – | needs listening and screen checks | |

## Unverified
Anything involving a microphone, speakers or listening (D5 audio quality, D9 live barge-in timing, filler/ack audio, O23-O26), GPU split config B, and the TTFT effect of O12.

## Round 2 (the items that were left) — 2026-10-31

Tests: **3668 passed, 1 skipped**.

| ID | Status | Evidence |
|---|---|---|
| O17 app index | done, verified | 105 apps indexed in 1.5 s at start-up (Start Menu + Get-StartApps); lookup <1 ms vs ~2.8 s of fixed Windows-Search waits; `open_application` tries the index before Search. Tests use fake Start Menu folders. Not live-launched an unlisted app (would open a real window). |
| O4 grounded answers | done, verified live | "latest iphone ki price kya hai" -> a search block with Smartprix results reached the model and the reply quoted a price; failures/timeouts (the PM query returned no results from DuckDuckGo) fall back to the normal answer. One search, 4 s limit. |
| O6 adaptive style | done, tested | counters in prefs `style_*`; one cached prompt line, rebuilt only when it changes (prefix cache untouched). Barge-in is **not** used as a signal (`last_interrupt_info()` is never cleared). A/B on scripted sessions not run. |
| O8 active-learning tool | done | `scripts/review_unmatched.py` clusters the log (395 utterances / 219 distinct on this PC) and `--accept N:intent` writes golden candidates. GUI thumbs not wired. |
| O10 wrong-language | done, tested | Devanagari reply to a Roman-script user in auto mode is dropped and regenerated once. |
| O22 parallel plan steps | already in the code | `_group_into_batches` runs independent steps concurrently; a test now pins it (2 x 0.4 s steps finish in <0.7 s). |
| Task 6 `config.validate` | first split done, verified | 100 plain clamps moved into the `_CLAMPS` table: **675 -> 423 lines**; compared old vs new on 600 cases (every clamp at below-low/above-high/mid/zero/edges): 0 differences. |

Still not done (and why): O16 speculative generation (needs spoken turns to prove zero wrong executions), O20/O21 STT/TTS model comparisons
(downloads + recordings), O23 per-intent acks (cached-ack audio must be re-synthesised and listened to), O24-O26 GUI/orb/barge-in/earcons (need a screen
recording and live audio), O9 "offer undo" (would add speech to every volume change; the `undo` command already exists), the other giant-function
splits in `SPLIT_PLAN.md`, vulture/bandit/ruff (not installed).

## Part 3 (SARA_OPTIMIZATIONS_PART3_PROMPT.md, P1-P20) - 2026-10-31

Tests: **3668 -> 3760 passed, 1 skipped** (+92 after round 2). `scripts/check_no_secrets.py`: 0 findings. `gen_env_example.py --check`: 0 undocumented (284 keys).
"verified" = measured or seen on this PC; "unverified" = not measured here. Measurements use this PC (qwen3:4b-instruct on Ollama, Windows 11).

| ID | Status | Tests added | Before -> After | Notes |
|---|---|---|---|---|
| P1 profile | done | `test_profile.py` | - | `SARA_PROFILE=dev|prod`; `DEBUG_MODE` only follows `.env` in the dev profile (owner line below). |
| P2 logging | done, **verified live** | `test_log_pipeline.py` (+ an end-to-end fresh-process test) | `logger.info` on the caller thread: median 285 us -> 42 us, p99 586 us -> 129 us (n=5000, file handler only; the old console handler made it slower still) | queue + one writer thread, redaction of keys/tokens/GOCSPX before any file, same-call-site rate limit, `sara.jsonl` (ts, level, component, turn_id, msg), async stdout. **Live start-up found a real bug** (`root` undefined in `logging_config.setup_logging`) that the unit tests missed; fixed and pinned by the new end-to-end test. |
| P3 GUI rendering budget | done in part, measured (round 2) | `test_gui_perf_budget.py` | idle home page, renderer CPU (share of one core) high/balanced/low: **20.8 / 16.4 / 13.4 % -> 10.4 / 8.7 / 8.4 %**; script time 9.8 / 7.9 / 7.1 % -> 4.1 / 3.5 / 3.5 % (144 Hz display, one 6 s sample per tier) | cause: the home orb loop drew at the display refresh (144 fps) and animates per frame; now capped at about 72 fps (60 Hz displays unchanged). **Visible change:** on a 144 Hz screen the orb moves at half its old speed (still 1.2x the 60 Hz pace). Frame times were already healthy in every tier (p95 7 ms), so no auto-switch to a lighter tier was added. Not needed: hidden pages are already `display:none`, and the Low/Balanced/High quality tiers, FPS meter and long-task counter already exist (`quality.js`). Remaining idle cost is the orb draw itself (233 ms of 8 s in a CPU profile). |
| P4 bundle size | measured, **not done on purpose** | - | window load 1.5 s total; `app.css` 307 KB took 196 ms and `app.js` 534 KB 245 ms during start-up | served from localhost, so bytes are not the cost (the time is the Python server busy booting); a minifier would add a build dependency for no measurable gain. |
| P5 | **not done** | - | - | needs profiling evidence first. |
| P6 SQLite | done, measured | `test_db_common.py` | conversation time-range query 13.2 ms -> 0.11 ms; per-action count 12.3 ms -> 0.21 ms (100k synthetic rows, median of 30; `EXPLAIN QUERY PLAN` SCAN -> SEARCH USING INDEX) | shared pragmas (WAL, synchronous=NORMAL, busy_timeout 5 s, temp_store=MEMORY, 20 MB cache), `PRAGMA optimize` + WAL truncate on close. A reminders `(triggered, due_at)` index changed the row order of an unordered query, so it was **removed**. Real-DB gain depends on its size (the real DB has 700 rows: no visible change expected). |
| P7 response cache | done, **verified live** | `test_response_cache.py` (48) | the same question twice: 2nd answer logged `[Cache] answered from the response cache`; "what is the time right now" was not cached | strict rules: never time/date/weather/news/price, personal or memory words, pronoun follow-ups, digits, tool turns, content hints (tone hints are allowed), bad/short replies. Key = text + language + persona hash + model; 7-day TTL, 500-entry LRU, SQLite `response_cache.db`. Time saved per hit = the LLM call (about 0.9 s in the logs for a short reply); audio start was **not** measured. Optional embedding near-duplicate match is implemented and tested but not wired to an embedder. "Forget cache" in Settings is **not** built (delete the rows of `response_cache.db`, or set `RESPONSE_CACHE_ENABLED=false`). |
| P8 rolling summary | done, measured (small sample) | `test_rolling_summary.py` (6) | recall of a fact told 12 turns earlier: **0/5 -> 4/5**; prompt +85-205 chars, hard cap 420 (flat, not growing) | summary of dropped turns by the local model in the background every 2 dropped turns, kept in the system prompt. First version summarised trivia and scored 0/5; the prompt was changed to keep only facts about the user. n=5 is small: treat as "clearly helps", not as a benchmark. Entity extraction into memory2 **not** done. |
| P9 KV prime on wake | **measured, reverted** | - | time-to-first-token 101 ms -> 95 ms (n=12 each, alternating order); prefill of the whole 983-token prompt is only 41 ms cold vs 27 ms cached | the ceiling is about 14 ms, so the feature was removed (the rules say to revert what does not help). |
| P10 wake-word verifier | **not done** | - | - | needs your recordings of the wake word and negatives. |
| P11 AEC / front-end | **not done** | - | - | needs a recorded noisy/echo test set. |
| P12 personal lexicon | **not done** | - | - | RapidFuzz is not installed; app names are already fuzzy-resolved by the app index (difflib); contacts/songs need a held-out misrecognition set that does not exist. |
| P13 ASR correction | **not done** | - | - | needs recorded audio + transcripts. |
| P14 distilled intent model | **not done** | - | - | needs a training run (Colab) and labelled data. |
| P15 Gemini gate | done, tested (**not exercised live**: the backend on this PC is Ollama) | `test_gemini_gate.py` | simulated 429 storm: 203 turns -> **3** Gemini calls (breaker), the rest go to the local model with no backoff | per-model RPM/RPD buckets (`GEMINI_RPM/RPD`, 0 = no local cap), breaker with Retry-After, failover chain, full-jitter backoff, **cancel/barge-in interrupts the retry sleep**, in-flight coalescing (class + tests; not yet used by the engine), usage meter (not shown in the GUI yet). |
| P16 process tuning | partly done | `test_proc_tuning.py` | `SetProcessInformation(ProcessPowerThrottling)` accepted by Windows here (True); idle/minimised latency effect **unverified** | EcoQoS opt-out on by default (`POWER_THROTTLING_OPT_OUT`), ABOVE_NORMAL opt-in, Ollama keep-warm (`KEEP_WARM_INTERVAL_S`, off by default, skipped on battery). Whisper/Kokoro keep-warm, the audio-device power request and the tray note are not done. |
| P17 GC | done, measured live | `test_gc_tuning.py` (4) | GC pauses over about 100 collections: off n=109 p99 5.9 ms, max 124 ms; on n=81 p50 0.16 ms, one deliberate 188 ms collect at idle | `gc.freeze` + higher thresholds after warm-up, histogram logged every 60 s as `[GC]`. The capture-loop buffers were not changed (no jitter measured). |
| P18 watchdogs | partly done, **verified live** (round 2) | `test_ollama_watchdog.py` (5) | Ollama killed mid-session: before = stays dead until SARA restarts; after = back up in **27 s** (log: `Ollama stopped answering; restarting it (1/3)`) | checks every `OLLAMA_WATCHDOG_INTERVAL_S` (15 s), restarts after 2 failed checks, max 3 per run, never before it was first seen up. Not done: microphone hot-plug, hung STT/TTS worker detection. |
| P19 replay gates | **not done** | - | - | needs a consented audio corpus. |
| P20 installer / clean-VM | **not done** | - | - | needs a VM. |

### Housekeeping done during the live checks
- The live tests wrote 28 conversation rows and 6 memory rows into your database; they were removed after a full backup (`sara_data.before_cleanup_20261031_225027.bak` next to the database). Three short mic-noise rows ("Thik hai", "Haan haan") and the derived `consolidation` memories were left because they cannot be told apart from real use.
- The Ollama server is stopped; no test app is running.

### Lines for the owner (not applied by me)
```
GEMINI_MAX_OUTPUT_TOKENS=400     # .env still has 200; replies can be cut short
GEMINI_RPM=10                    # optional: your plan's per-minute limit, so SARA falls back before Google answers 429
GEMINI_RPD=250                   # optional: your plan's per-day limit
SARA_PROFILE=dev                 # only if you want DEBUG_MODE=true in .env to take effect
```
Key rotation / Google OAuth revocation stay yours.

### Part 3 - leftovers round (2026-11-01)
| ID | Status | Evidence |
|---|---|---|
| P7 "Forget cache" | done | Settings > Memory: button "Forget cached answers" (new bridge method `clear_response_cache`, API list now 95). Verified live: click -> toast "No cached answers to forget"; unit test seeds one answer and checks it is cleared. |
| P8 entities into memory2 | **not new code, existing pipeline** | Memory 2.0 (enabled in your `.env`) already extracts facts, events and entities from your messages with relative-time resolution ("kal", "parso"). I did not re-test the exact phrase "Rahul ko kal call karna" through it. |
| P15 usage meter | done | `get_perf_summary` now carries `gemini_usage` ({model: [requests today, daily cap]}) once Gemini was used; Settings > System shows "Gemini requests - ... today". Unit-tested; the live UI line stays hidden on Ollama. Request coalescing stays a tested helper in `gemini_gate.py`, **not used by the engine**: it fits one-shot calls, not a streamed chat reply. |
| P16 keep-warm | measured, **left off** | After a 5 min gap the first token took 0.351 s vs 0.114 s warm (one sample); with the keep-warm ping every 60 s it was 0.282 s (one sample). About 70 ms is inside the noise of single samples, so `KEEP_WARM_INTERVAL_S` stays 0 by default. Whisper/Kokoro keep-warm, the battery "degrade" mode and the tray note were not built (no evidence they help). |
| P18 mic stall | done in part | `_stream_is_healthy` now also treats a stream that says "active" but delivered no callback for 6 s as dead (the watchdog then reopens it); the capture callback runs ~31x/s even in silence, so a quiet room is not a stall. 5 unit tests; live run: no false alarms. **Not done:** following a newly plugged-in default microphone (PortAudio does not refresh its device list without re-initialising, which I could not test without plugging hardware) and detection of a hung TTS worker. |
| P12 personal lexicon | **not done, reason** | The backend has no contacts and no played-song history to build a lexicon from; installed-app names are already fuzzy-matched by the app index. |
| P5 | **not done** | the part-3 prompt file is no longer on disk, so I could not re-read what P5 asked for; nothing was changed. |
