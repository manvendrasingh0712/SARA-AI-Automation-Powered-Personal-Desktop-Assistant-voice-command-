# Audit fix report (2026-10-31)

Baseline on this machine (Windows, real modules): **3363 passed, 1 skipped**. After all tasks: **3480 passed, 1 skipped**.

| Task | Status | Tests added | Before -> After | Notes |
|---|---|---|---|---|
| 0 Secrets hygiene | done (code side) | 4 | 5 `.env.bak*` in tree -> 0; tracked secrets 0 | `scripts/check_no_secrets.py`, `scripts/make_release_zip.py`. Owner must rotate keys (see below). |
| 1 Gemini config | done | 13 | 6 direct `GenerateContentConfig(` call sites -> 1 builder | Smoke test (`bench/gemini_smoke.py`): 2.5-flash, 3.5-flash-lite, 3.5-flash accept sampling params; 3.8-flash answered 503 in 3 runs. Lite rejects `thinking_budget=0` (HTTP 400), accepts 128. `GEMINI_MAX_OUTPUT_TOKENS=200` gave empty/cut replies on thinking models -> default 400. |
| 2 Router question guard | done | 51 | router acc 99.3 % -> 99.4 %, negative false triggers 0/60 -> 0/73, 7/7 false positives fixed | `what is the weather today` was `chat` (missing pattern) and now routes to weather. |
| 3 Spoken numbers + music | done | 44 | 13/13 phrases now deterministic; bench 590 -> 620 rows (30 held-out rows added) | Shared `sara/core/text/spoken_numbers.py`; `10 minute timer` (digits!) was also failing. |
| 4 Smarter replies | done | 5 | length on the local model: quick answers 9/10 in range, explanations 5/10 -> 10/10; on Gemini fast: explanations 0/10 -> 10/10; accuracy unchanged | `ROUTER_MODE=on` NOT recommended yet: only 200 shadow samples, all from the Gemini era with a static tier; see below. |
| 5 Latency | partly done | 1 | local TTFT with gloss +376 ms (no memory); LLM 12-28 -> 44-52 tok/s earlier today | Filler ack, backend-aware timeouts, baseline written. Voice-path numbers are **unverified** (needs spoken turns). |
| 6 Code health | partly done | 3 | `try/except/pass` 111 -> 72 (llm package: 8 -> 1) | See "not done". |

## Owner checklist (account side, not done by code)
- Rotate the Gemini API key and the weather API key; put the new values only in the local `.env`.
- Google Account -> Security -> Third-party access: revoke this app, then re-run the calendar auth to regenerate `token.json`.
- Set `GEMINI_MAX_OUTPUT_TOKENS=400` in `.env` (it is `200` there now; the new default only applies when the line is absent).

## Task 4a: shadow data
`router.sqlite`: 200 samples (16-29 Oct), tier = static tier = `cloud_main` in all 200, so "agreement" is trivially 100 %; the learner proposed
`cloud_pro` for 193 (97 %) and `escalated` 48 times; 122 labelled; median latency 2.25 s. That is a policy that has not learned yet. With
`LLM_BACKEND=ollama` the router only sees local tiers, so `ROUTER_MODE=on` would change nothing today. If the owner wants the cloud tier anyway:

    LLM_BACKEND=gemini
    LLM_FALLBACK_ENABLED=true        # Ollama stays the offline fallback (measured: first turn 1.8 s, then 0.3 s via the circuit breaker, no error shown)
    ROUTER_MODE=on
    GEMINI_FAST_MODEL=gemini-3.5-flash-lite    # measured ~1.1 s
    # GEMINI_PRO_MODEL=gemini-3.8-flash        # answered 503 in the smoke test; re-test before relying on it
    GEMINI_MAX_OUTPUT_TOKENS=400

## Task 5 findings
- 5a baseline: `bench/results/audit_baseline.txt` (telemetry, all history): voice TTFA p50 4047 ms (n=119); typed-turn TTFA p50 1610 ms (n=474); last 4 h typed-turn TTFA p50 2172 ms (n=154, includes TTS time).
- 5b flags: not measured (needs spoken turns). Recommendation from reading the code only: turn on `VOICE_ACK_CACHE_DISK=True` (pure cache); leave `VOICE_DUCKING` / `VOICE_SEMANTIC_BARGE_IN` off until A/B-ed with spoken turns.
- 5c done: `TOOL_CALLING_TIMEOUT_GEMINI_S=3.5`, `PLANNING_STEP_TIMEOUT_GEMINI_S=2.5`, `PLANNING_TOTAL_TIMEOUT_GEMINI_S=6.0` (used only when `LLM_BACKEND=gemini`); `VOICE_FILLER_DELAY_S=1.2` filler (once per turn, skipped when speech is playing, spoken turns only). Not heard live.
- 5d: the gloss already runs in a thread parallel to memory retrieval; measured cost without memory +376 ms (279 -> 655 ms median TTFT).
- 5e: AEC is **active** at start-up (`aec=True`, system-audio reference on the speakers), so the 0.9 s settle does not apply.
- 5f: Config B (cloud LLM + Whisper large-v3-turbo on CUDA + Kokoro on GPU) **not measured** (needs a 1.6 GB model and recorded speech). Measured facts for A: Kokoro on CPU 0.45 s vs 0.72 s on GPU for a short sentence; with Kokoro on the GPU the chat model drops from ~52 to 12-28 tok/s.
- 5g: Hindi samples at 1.15 / 1.25 / 1.40 in `C:\project\tts_samples\` (git-ignored) for the owner to listen to. `TTS_SD_LATENCY=low` not tried (needs listening); kept `high`.

## Not done / unverified
- Splitting the giant functions and files (plan in `SPLIT_PLAN.md`; needs owner approval, and some tests read those functions' source).
- vulture / bandit / ruff were not run (not installed); only pyflakes. The unused-import list is mostly deliberate re-exports (documented in the code).
- `sara_ai.spec` is not in the repository, so the build exclusion of `js/dev`, `js/tests` is documented in `BUILD.md` only.
- Remaining 72 `except: pass` are in cleanup/shutdown paths, audio callbacks or other files outside the Task-6 list.
- Voice-path latency, filler audio, barge-in flags and GPU-split config B need spoken turns / downloads: unverified.
