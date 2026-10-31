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
