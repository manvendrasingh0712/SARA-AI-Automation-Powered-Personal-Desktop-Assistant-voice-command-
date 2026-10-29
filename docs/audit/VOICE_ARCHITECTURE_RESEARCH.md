# SARA AI — Real-Time Voice Architecture Research & Decision

Research-only deliverable. **No production code was changed to produce this.**

- **Research date:** 2026-10-28
- **Repository:** `C:\project`, HEAD `f2e3d51` "Complete SARA AI audit batches 6a-6b3" on `master`
- **Reference studied:** `FatihMakes/Mark-LVI` @ `4d4035c` (shallow clone, read-only)
- **Author's knowledge cutoff:** May 2026 — every model name, price and limit below was re-verified against live primary sources because the landscape moved past that cutoff.

## Evidence tags used throughout

Every material claim carries one. This is the single most important convention in the document: it separates what is known from what is hoped.

| Tag | Meaning |
| --- | --- |
| `[DOC]` | Official vendor documentation, read this session |
| `[PUB]` | Published, peer-reviewed or archived benchmark |
| `[REPO]` | Read directly from source — SARA's or Mark-LVI's |
| `[MEAS]` | Measured on real hardware, with the run recorded |
| `[EST]` | My reasoned estimate — explicitly not a measurement |
| `[GAP]` | **Unverified. Must be measured locally before it is relied on.** |

---

## 1. Executive summary

**Recommended architecture: a hybrid, with Gemini Live native audio as the conversational path and SARA's existing deterministic intent router retained — unchanged — as the fast path.**

This is not the diplomatic answer. It is the answer the evidence forces, and one specific piece of evidence decides it:

> **SARA's intent router already classifies text → intent at p95 = 0.997 ms, p99 = 1.99 ms, with 100% accuracy on 160 Hinglish utterances.** `[MEAS]` — `bench/results/latest.json`, 590 rows, run 2026-10-10 on your own AMD Zen 3 Windows machine.

That number reframes the entire engagement. The 50 ms aspiration is not a stretch goal for the part of the system that routes commands — **that part is already 50× inside budget and has been for some time.** No cloud architecture will beat 1 ms, and replacing the router with an LLM call would make SARA's best-measured component its slowest. The router stays.

What the router cannot do is hear. Every millisecond SARA actually spends waiting is in four places: end-of-speech detection, Whisper transcription, the LLM round trip for non-deterministic requests, and speech synthesis. That is where a native speech-to-speech engine wins, and it wins structurally rather than marginally — it removes whole stages instead of speeding them up.

**The three findings that drive the decision:**

1. **The cascade's floor is high and is not an implementation defect.** Mark-LVI measured its own local pipeline at **1.5–4 s** against Gemini Live at **0.3–0.6 s**, and stated plainly that local mode "is **not faster**." `[REPO]` Its reasoning is architectural: a cascade has three serial stages and a tool-calling turn costs *two* model round trips, because a stateless chat API must be re-asked with the result. Tuning does not remove a round trip.

2. **Hinglish is where the cascade is structurally weakest, and it is SARA's hardest requirement.** Whisper emits **one language label per utterance**; a code-switched sentence has two languages. Published work reports a **30–50% relative WER increase** on Hindi-English code-switched audio over the Hindi baseline `[PUB]`, and the dominant failure mode is language-ID routing sending a whole sentence to the wrong engine `[PUB]`. Gemini Live's native-audio models instead **"switch languages automatically and don't accept an explicit language code"** `[DOC]` — code-switching is handled inside the model, in both directions, with no language-detection step to get wrong.

3. **SARA's safety layer is better than the reference project's and is the main thing a migration could break.** `sara/core/security/guard.py` gates consequential actions behind a spoken yes/cancel replayed through `replay_confirmed()`, bilingually, and `detector_rules.py` carries a regex that detects a *page* claiming the user "has already agreed/approved/authorized/consented". `[REPO]` Mark-LVI shipped a forgeable gate — a `confirmed` tool parameter the model filled in itself — and had to fix it. SARA never had that bug. This layer must remain the mandatory path for every tool call under either engine.

**On 50 ms, stated honestly:** ~50 ms is achievable, and already achieved, for local deterministic stages — intent routing (0.997 ms p95, measured), barge-in detection, wake-word scoring, audio-frame processing. It is **not** achievable for full speech understanding under any architecture available today, because the floor is end-of-speech detection, and Google's own guidance is **≥ 500 ms of end-of-speech silence** for client-side VAD `[DOC]`. A sub-50 ms "speech to verified action" claim would be false whoever made it. Realistic targets replace it in §10.

**What I could not settle from public evidence, and must be measured on your PC:** whether Gemini Live's native audio actually transcribes and speaks *your* Hinglish well. No public per-language code-switching evaluation of Gemini Live native audio exists that I could find `[GAP]`. §18 specifies the smallest experiment that resolves it, and the recommendation is explicitly conditional on its outcome.

---

## 2. Research date and scope

**Date:** 2026-10-28. **In scope:** real-time voice architectures; streaming ASR; VAD and endpointing; reasoning and tool-use strategy; streaming TTS; audio transport; Hindi/English/Hinglish quality; latency decomposition; privacy, licensing and cost; migration risk.

**Out of scope, deliberately:** any production code change; any dependency install or migration (§3 of your brief); Windows-only runtime verification, which this Linux session cannot perform at all.

**Method:** primary vendor documentation and the two repositories' actual source, preferred over articles throughout. Where only vendor marketing was available it is labelled as such and not used to support a decision.

---

## 3. Current SARA architecture, verified from source

Read from `C:\project` at `f2e3d51`, not taken from earlier audit reports.

### 3.1 The voice path exists and is not naive

This matters because the obvious recommendation — "replace the hand-rolled pipeline with a real-time API" — rests on an assumption about SARA that is **false**. SARA already implements most of what the brief lists under "natural live conversation":

| Module | Lines | What it already does |
| --- | --- | --- |
| `sara/audio/aec.py` | 448 | Acoustic echo cancellation |
| `sara/audio/stt/endpoint.py` | 223 | **Adaptive** endpointing — pure decision rules, no I/O |
| `sara/audio/stt/endpoint_cues.py` | 83 | Continuation words and fillers for **`en`, `hi` and `hinglish`** |
| `sara/audio/stt/engine.py` | 2154 | Mic stream, wake word, rolling live-caption preview, hallucination guards |
| `sara/audio/tts/chunker.py` + `player.py` + `synth.py` | 171+559+187 | Chunked streaming synthesis and playback |
| `sara/audio/tts/prewarm.py` | 236 | Model pre-warm |
| `sara/audio/tts/duck.py` | 91 | Output ducking |
| `sara/audio/tts/barge_classify.py` | 140 | Barge-in classification |
| `sara/orchestrator/barge_watch.py` | 213 | Barge-in watcher |
| `sara/orchestrator/tts_worker.py` | 414 | TTS worker thread |

Streaming TTS, pre-warm, ducking, AEC, barge-in and adaptive endpointing are **present**. A recommendation that treats SARA as a naive request/response loop would be factually wrong.

### 3.2 The endpointer is Hinglish-aware already

`endpoint_cues.py` `[REPO]` carries three cue sets. The `hinglish` set is romanized Hindi:

```
aur, phir, fir, ke liye, ko, se, ka, ki, ke, mein, me, par, pe, matlab, toh, to, ya, jo, kyunki
```

plus a Devanagari `hi` set and an English set. Endpointing waits longer when an utterance ends on a continuation word — `ENDPOINT_CONTINUATION_EXTRA_MS = 400` — so "SARA, Chrome kholo **aur**…" is not cut off mid-thought. This is genuine, working Hinglish engineering and it is **more** linguistically informed than Mark-LVI's pure-energy VAD (noise floor × 3.5, 700 ms silence) `[REPO]`.

### 3.3 Whisper language handling has already been tuned empirically

`sara/audio/stt/engine.py:1579–1640` `[REPO]` encodes a policy arrived at by measurement, and the code comments record the finding:

- For `SARA_LANGUAGE == "hinglish"` (the default), `_resolve_forced_language()` returns **`None`** — Whisper auto-detects per utterance. Forcing `"hi"` was removed because it "**used to ALSO force language='hi' on every turn, biasing clearly-English and English-heavy mixed speech toward Hindi transcription**."
- Two `initial_prompt` variants exist. The Hindi-styled one is used **only** when the turn is known to be Hindi, because its romanized examples "**were found to measurably bias Whisper's vocabulary/style even for clearly-English or English-heavy input**."
- The neutral prompt instructs: transcribe "without translating between languages, **without switching script**, and without inventing words."

That last clause is the tell. Script instability on code-switched input is a problem SARA has already met and partially mitigated with prompt steering. Mitigated is not solved — `[GAP]`, quantified in §9.

### 3.4 Current timing parameters

From `config.py` `[REPO]`:

| Parameter | Default | Consequence |
| --- | --- | --- |
| `ENDPOINT_MIN_MS` | 350 | Floor on end-of-speech silence |
| `ENDPOINT_MAX_MS` | 900 | Ceiling |
| `ENDPOINT_CONTINUATION_EXTRA_MS` | 400 | Added after a continuation cue |
| `ENDPOINT_NOISE_EXTRA_MS` | 150 | Added in noise |
| `ENDPOINT_PREVIEW_AT_MS` | 150 | Live-caption preview cadence trigger |
| `WHISPER_MODEL_SIZE` | `medium` | 769M params — same base IndicWhisper fine-tunes `[PUB]` |
| `WHISPER_BEAM_SIZE` | 2 | Latency/accuracy trade already taken |
| `TTS_PLAYBACK_BUFFER_MS` | 40 | Playback buffer |
| `BARGE_COLLECT_MS` | 550 | Barge-in collection window |
| `STT_HARD_MUTE_DURING_TTS` | `True` | **See §12 — this costs barge-in responsiveness** |

### 3.5 The live preview is a caption, not an early action

`engine.py:256–404` `[REPO]`: a bounded rolling window is re-transcribed on roughly a 900 ms cadence, single-flight guarded (`_preview_busy`), with a `_preview_generation` counter that discards results from a superseded session. It is explicitly "best-effort 'live caption' preview."

**So SARA does not currently act on partial transcripts.** Objective A of your brief — "begin processing speech while I am still speaking" and "avoid waiting for full utterance finalization when a safe, sufficiently reliable action can be identified earlier" — is **not yet implemented**. The display plumbing exists; the early-dispatch decision does not. This is a real gap and §16 addresses it.

### 3.6 The security layer, which is the asset most at risk

`sara/core/security/` `[REPO]`:

- `guard.py` — `ACTION_CONFIRM`, `ORIGIN_CONFIRMED = "user_confirmed"`, and `replay_confirmed()`: "Run a call the user just confirmed; None when it cannot be replayed." Confirmation is a **spoken yes/cancel**, bilingual (`_CONFIRM_HI`: "ye request {label} se aayi hai. Yes bolo ya cancel.").
- `detector_rules.py:33` — a regex matching `user (has) (already) (agreed|approved|authorised|consented|confirmed|said yes)`. This defends against untrusted content *claiming* prior consent, which is the exact prompt-injection shape your brief names.
- `events.py` — audited event vocabulary: `injection_detected`, `blocked`, `confirm_asked`, `confirmed`, `arg_rejected`.
- `policy_rules.py` / `policy.py` — path classification and tiered argument validation, hardened in audit batch 6b(3) (D17).

**Compare the reference project.** Mark-LVI's `readme.md` documents that its old gate read `params.get("confirmed")` — "`confirmed` is a **tool parameter, which means the model fills it in.** Nothing stopped it sending `confirmed=yes` on the first call" `[REPO]`. It now issues the token from the interface instead. **SARA never had that defect.** On authorization architecture SARA is ahead of its reference, and this is the single component a careless migration is most likely to destroy.

### 3.7 Measured accuracy baseline — SARA's strongest existing asset

`bench/` is a real evaluation harness: 141 intents, golden sets for `en`/`hi`/`hinglish`/`en_noisy`, plus `negatives.jsonl`, `adversarial.jsonl` and a `redteam/` corpus. `bench/README.md` states router mode "classifies every labelled utterance with the real intent engine and never runs a handler."

`bench/results/latest.json`, 590 rows, 2026-10-10, Python 3.11.9, Windows 10, AMD Zen 3 `[MEAS]`:

| Metric | Value |
| --- | --- |
| Overall top-1 accuracy | **93.22%** |
| Macro-F1 | 94.72% |
| **Latency p50 / p95 / p99** | **0.041 / 0.997 / 1.99 ms** |
| Composite | 0.916 |

**By language:**

| Language | Rows | Accuracy |
| --- | --- | --- |
| `hinglish` | 160 | **100.00%** |
| `hi` | 93 | 95.70% |
| `en` | 337 | 89.32% |

**By kind and style — where the weaknesses actually are:**

| Slice | Rows | Accuracy |
| --- | --- | --- |
| negative | 60 | 100.00% |
| positive | 490 | 94.49% |
| **adversarial** | 40 | **67.50%** |
| casual | 401 | 97.51% |
| short | 29 | 89.66% |
| noisy | 150 | 84.67% |
| formal | 10 | 60.00% *(n=10 — too small to act on)* |
| **grouped / multi-step** | 74 | **81.08%** |

| Safety rate | Value |
| --- | --- |
| false_trigger_negative | **0.00%** |
| false_trigger_adversarial | **20.83%** |
| gate_false_open_rate | 3.57% |

**Three things follow, and they are the core of the architecture decision.**

**(a) Hinglish is SARA's *best* language at the text→intent layer, not its worst.** 100% on 160 rows. Any proposal that routes Hinglish commands through a slower, less accurate path is a regression. This is the strongest single argument for keeping the local router.

**(b) The benchmark is text-in and therefore says nothing about speech.** This is the most important caveat in this document. The 100% Hinglish figure measures *romanized Hinglish text → intent*. It does not measure Whisper's ability to **produce** that romanized text from audio. The entire Hinglish risk sits in the ASR layer and is **unmeasured** `[GAP]`. §9 and §18 treat this as the top-priority experiment.

**(c) The weak slices are exactly the ones an LLM is good at, and the strong slices are exactly the ones it is not.** Adversarial 67.5%, grouped multi-step 81.1%, noisy 84.7% — ambiguity, multi-step planning and recovery. Deterministic routing is strong on crisp commands at 1 ms and weak on these. That is not an argument to replace the router; it is the empirical case for a hybrid in which each path handles what it measurably does better.

### 3.8 A concrete defect surfaced while reading the confusion matrix

From `latest.json` `[MEAS]`, three unrelated intents all mis-route to the **same** wrong target:

```
bluetooth_on               -> unmute   (2)
open_airplane_mode_settings-> unmute   (2)
restore_windows            -> unmute   (2)
```

Three semantically distant intents collapsing onto one target is not the shape of random classifier error; it is the shape of a pattern-overlap bug in the intent table. Separately, a cluster falls back to `chat` (`open_app`×3, `calculator`×2, `paste_clipboard`×2, `set_volume`×2, `typing_text`×2, `minimize_active_window`×2).

**Not fixed here** — this is a research phase and a fix needs its own batch with a red-green test. Logged as **D19** for the findings register. It is cheap, local, and worth more per hour than any migration work.

---

## 4. Mark-LVI architecture analysis

Studied at `4d4035c`: 75 files, ~15.5k lines across `core/`, `providers/`, `actions/`, `plugins/`.

### 4.1 License — a hard constraint, stated first

`LICENSE` `[REPO]`:

> MARK 56 — JARVIS, Copyright (c) 2026 FatihMakes. Licensed under the Creative Commons **Attribution-NonCommercial 4.0** International License (CC BY-NC 4.0). **Commercial use is not permitted.**

Two consequences, and both matter to you specifically:

1. **No code reuse if SARA is ever commercial.** Your stated direction is entrepreneurial. CC BY-NC forbids commercial use outright — not "requires attribution," *forbids*. Copying any non-trivial portion would make SARA permanently non-commercialisable.
2. **CC BY-NC is not a software licence even setting that aside.** It grants no patent licence, has no source/object distinction and no warranty disclaimer tailored to code. Creative Commons itself recommends against using it for software.

**Therefore: design inspiration only, independent implementation, no copied code.** Everything below is read as a *design document*. Where I recommend a pattern it is because the reasoning is sound and the pattern is general — adapter protocols and status enums are not copyrightable ideas — and SARA's implementation must be written from scratch against SARA's own types. I have copied no code into this report beyond short quotations of prose and the five status words, for attribution and comparison.

### 4.2 The two engines

**Primary — Gemini Live.** One WebSocket carrying audio in, audio out, transcripts and tool calls together. `core/gemini.py` `[REPO]` pins `LIVE_MODELS = ("models/gemini-3.1-flash-live-preview", "models/gemini-2.5-flash-native-audio-preview-12-2025")`. **Both are now legacy** — see §6.1.

**Secondary — `providers/` cascade.** faster-whisper → an OpenAI-protocol LLM → Piper/ElevenLabs/system TTS. One `LLM` implementation covers OpenAI, Groq, DeepSeek, Mistral, xAI, OpenRouter, Together, Cerebras, Ollama, LM Studio, llama.cpp and vLLM, "all of them serve `/v1/chat/completions` and differ only in base_url and key" `[REPO]`.

### 4.3 The adapter direction — the one idea most worth adopting

`providers/base.py` `[REPO]` states the rule as a design law:

> **A provider speaks Gemini Live's dialect. Never the reverse.** […] It is tempting to invent a neutral interface and translate both sides onto it. That would mean rewriting main.py's receive loop, the viseme extractor, the echo guard, the avatar's mouth, the tool dispatcher and the interrupt path — eight thousand lines that already work — so that a folder which may not even be installed can feel symmetrical. The new thing adapts to the old thing, because the old thing is the one with the users.

The surface is deliberately tiny — four methods and five event fields, verified rather than assumed ("`self.session` appears 29 times in main.py and every one of them is one of four operations"):

| Method | Contract |
| --- | --- |
| `send_realtime_input(audio=)` | 16 kHz mono PCM, called continuously |
| `send_client_content(turns=, turn_complete=)` | Text/image injected as a user turn |
| `send_tool_response(function_responses=)` | Tool results |
| `receive() -> AsyncIterator[Msg]` | Event stream |

Events: `data` (24 kHz PCM), `server_content.output_transcription`, `.input_transcription`, `.turn_complete`, `.interrupted`, `tool_call.function_calls[]`, `session_resumption_update`.

**And a deadlock warning worth more than the rest of the file** `[REPO]`:

> `main.py` answers a tool call from *inside* its loop over `receive()`. An engine that waited there for the generation it triggers would be waiting for events only that loop can consume — the turn would stop dead on the first tool call and look exactly like a hung model. **Every send method records, schedules, and returns.**

SARA's dispatcher is synchronous. This is precisely the trap a Live integration would fall into, and it is the kind of bug that presents as "the cloud model is slow" rather than as a deadlock. Noted in §14 as the top migration hazard.

### 4.4 The capability dict

`DEFAULT_CAPS` `[REPO]`: `vision`, `tools`, `tool_format`, `max_tools`, `audio_out`, `barge_in`, `non_blocking`, `resumption`. It feeds **two** consumers, which is what makes it more than documentation: the tool-declaration assembly (an eyeless engine is never offered `screen_process`) **and** the system prompt's self-knowledge block, so the model *knows* it cannot see "and says so plainly instead of inventing a description of a screen it never received."

That second consumer is the honesty mechanism your brief asks for under "explain limitations honestly," implemented as data rather than as a pile of conditionals.

### 4.5 Outcome verification — adopt the concept, write it independently

`core/outcome.py` `[REPO]` exists because `open_app` reported `"Opened Spotify."` whenever `subprocess.Popen(..., shell=True)` returned, "which succeeds because a SHELL started, not because an application did."

Three outcomes, not two:

| Status | Meaning |
| --- | --- |
| `done` | It happened, and it was **checked** — never assumed from a return code |
| `unverified` | The request went through and could not be confirmed. **Not a failure, and never to be spoken as a success** |
| `failed` | It did not happen, and here is why |

Plus `started`, `ask`, `untrusted`. And a rationale that matters directly for Hinglish:

> **WHY FACTS AND NOT SENTENCES.** The return value is read by a model that must then speak to a person in THEIR language […] Handing it a finished English sentence invites it to translate or simply repeat that sentence.

**This is the single most transferable idea in the reference project for SARA's multilingual requirement.** If a tool returns `"Opened Chrome"`, a model asked to reply in Hinglish will often echo the English or translate it awkwardly. If it returns structured facts, the model composes naturally in whatever the user spoke. SARA's security layer already returns structured outcomes; its *tools* largely do not `[GAP] — per-tool audit still owed`.

### 4.6 Other patterns, assessed

| Pattern | Adopt for SARA? |
| --- | --- |
| **Tool budget — two budgets, count *and* size** | **Yes, if a small local LLM is ever used.** Measured: 20→8 tools cut the payload 18,039→9,050 chars, "because the tools worth keeping are the fat ones." Gemini gets no budget. |
| **Error-class-specific cooldowns** | **Yes.** Quota 300 s, unavailable 30 min, gone 6 h. Measured 11.2 s saved per call once 503/504 were cooled. |
| **Lazy provider loading via `find_spec`** | **Yes.** Answers "is it installed?" without executing. Took plugin discovery 211→39 ms. |
| **Live API leads the one-shot ladder** | **Investigate.** Rationale is quota-pool separation, not speed: "REST is two to four times quicker while it lasts, and the whole point of the ladder is that it does not last." |
| **Band-energy echo subtraction + learned echo gain** | **Compare against SARA's `aec.py` before changing anything.** Sample-rate agnostic by design (16 kHz mic, 24 kHz playback). SARA already has AEC; no evidence yet that either is better. |
| **Whisper's language code picks the TTS voice** | **No — this is the one design to reject outright.** See §4.7. |
| **Energy-only VAD** | **No.** SARA's cue-aware endpointer is better, and the author concedes "a neural detector would be better." |
| **Avatar, visemes, face mesh** | **No.** Out of scope, and 1,300+ lines of unrelated surface. |

### 4.7 Where the reference project's design is wrong for SARA

**"The voice follows the language you speak."** Mark-LVI passes Whisper's detected language code down the pipeline to select a per-language Piper voice `[REPO]`:

```
[TR] heard 'Sistemin durumu nedir acaba?'       -> tr -> tr_TR-dfki-medium
[EN] heard 'What is the current system status?' -> en -> en_US-lessac-medium
```

The author presents this as elegant — information the pipeline already held, no extra dependency. **For monolingual users it is elegant. For your primary use case it breaks.**

"Yaar SARA, VS Code mein mera project open karke check karo ki import time itna slow kyun hai" is **one utterance containing both languages**. Whisper returns **one** label. Whichever it returns, a per-language voice is wrong for roughly half the sentence: a Hindi voice reading "VS Code", "import time" and "benchmark", or an English voice reading "karke check karo". The architecture has no representation for "both," because the design assumes language is a property of an utterance rather than of a span within it.

This is exactly the trap your brief named: *"Never claim excellent Hinglish support merely because a provider supports Hindi and English individually."* Mark-LVI supports ~50 languages and would still mispronounce your normal speech. The honest conclusion is that **single-label language routing is incompatible with code-switched speech**, and that is a property of the approach, not of its tuning.

### 4.8 Measurement methodology — credible, with a caveat

The author's numbers are unusually trustworthy for a hobby project: specific hardware, before/after pairs, and several entries documenting that the *obvious* explanation was wrong ("the panel used to stutter… the obvious explanation — too many buttons — was wrong. Measured, the **first** wake-word state check took **2.104 seconds**"). The cold-start finding is the standout: model load 1.4 s but **first transcription 8.5 s** — "it is the recogniser bringing up its kernels, not the load" — fixed by a throwaway recognition on 0.1 s of silence, **8.92 s → 1.16 s** `[REPO]`.

**Caveat:** no sample counts, no percentiles, single-machine, and the headline 0.3–0.6 s vs 1.5–4 s comparison is a range without n or distribution. Directionally strong, not a substitute for your own numbers. Treat all of it as `[REPO]`-grade: a competent engineer's measurements on one machine.

---

## 5. Alternative architectures considered

| | **A. Native speech-to-speech** | **B. Streaming cascade** | **C. Hybrid (recommended)** | **D. Cascade + realtime ASR only** |
| --- | --- | --- | --- | --- |
| Shape | Audio in → audio out, one socket, tools inline | Audio → ASR → router/LLM → tools → TTS | Local router first; Live for conversation | Streaming cloud ASR, local LLM+TTS |
| Latency to first audio | **0.3–0.6 s** `[REPO]` | 1.5–4 s `[REPO]` | **~1 ms** routed / 0.3–0.6 s conversational | ~0.8–2 s `[EST]` |
| Tool turn round trips | **1** `[DOC]` | 2 `[REPO]` | 1 (Live) / 0 (routed) | 2 |
| Hinglish handling | **In-model, automatic** `[DOC]` | Single label per utterance `[PUB]` | In-model + 100% router `[DOC]`+`[MEAS]` | Single label |
| Barge-in | Server-side `[DOC]` | Must self-implement (SARA has) | Both | Local |
| Works offline | No | **Yes** | **Yes, degraded** | No |
| Audio leaves machine | Yes | **No** | Configurable | Yes |
| Recurring cost | $0.023/min, free tier `[DOC]` | **Zero** | Zero routed / $0.023/min conversational | ASR cost |
| Vendor lock-in | High | **None** | Medium, contained by adapter | Medium |

**Why C and not A.** Pure A throws away a 0.997 ms p95 router with 100% Hinglish accuracy and makes every "volume kam karo" a network round trip. It also makes SARA unusable offline and sends all audio to Google unconditionally. A is better than B on conversation and strictly worse on commands; C takes both.

**Why C and not B.** B cannot reach the latency goals — not because SARA's implementation is poor but because three serial stages and two round trips per tool turn are the architecture. And B carries the structural Hinglish weakness in §4.7.

**Why not D.** Pays the network cost and the privacy cost of cloud ASR while keeping the cascade's two-round-trip tool penalty and its single-label language problem. Worst of both.

---

## 6. Component-by-component comparison

### 6.1 Native speech-to-speech

**Gemini Live** `[DOC]` — ai.google.dev/gemini-api/docs/live-api/capabilities, read 2026-10-28:

| Property | Value |
| --- | --- |
| Current models | **`gemini-3.8-live`** (default for low-latency voice agents), `gemini-3.8-live-extended-thinking` |
| Legacy | `gemini-3.1-flash-live-preview` — **the model Mark-LVI pins** |
| Audio in | 16-bit LE PCM, native 16 kHz, rate declared in MIME (`audio/pcm;rate=16000`) |
| Audio out | 16-bit LE PCM, **24 kHz** |
| VAD | Automatic, on by default; `silenceDurationMs`, start/end sensitivity, `prefixPaddingMs` configurable. **Recommended 500–800 ms; server default ~800 ms** |
| Manual VAD | Can be disabled → `activityStart`/`activityEnd`. **Client-side VAD should use ≥500 ms end-of-speech silence** |
| **Hybrid VAD** | **Client-side end-of-speech + server-side fallback** — the key to cutting endpoint latency |
| Barge-in | Server cancels and discards generation, sends an interruption message, **discards pending function calls and reports the cancelled IDs** |
| Function calling | **Async (`NON_BLOCKING`) by default** on 3.8-live; scheduling `SILENT` / `WHEN_IDLE` / `INTERRUPTED`. *Not available on 3.1-flash-live-preview* |
| Session limits | **Audio-only 15 min**; audio+video 2 min |
| Context | 128k (native audio out) / 32k (other Live models) |
| Languages | 99 incl. Hindi (`hi`). **Native audio models switch languages automatically and do not accept an explicit language code**; restrict via system instructions |
| Extras | `proactive_audio`, `enable_affective_dialog` — both require `v1beta` |

Two of these deserve emphasis. **Async function calling** means a tool call need not stall the turn — SARA can acknowledge and keep talking while a slow action runs, which is your brief's "appropriate acknowledgement of long-running tasks without falsely claiming completion," available as a protocol feature rather than something to build. And **hybrid VAD** lets SARA's existing cue-aware endpointer act as the client-side detector with the server as a safety net — SARA's best audio component stays in the loop instead of being discarded.

**The 15-minute session cap is a real constraint**, not a footnote: SARA is a resident assistant, so reconnection and session resumption are mandatory engineering, not a nice-to-have.

**OpenAI Realtime** `[DOC]` — developers.openai.com, `gpt-realtime-2`: 128k context, 32k max output, text+audio+image in / text+audio out, function calling, configurable reasoning effort. Knowledge cutoff Sep 30 2024. No streaming, structured outputs or fine-tuning. Rate limits by tier (Build 400 RPM / 200k TPM).

**Cost — the decisive gap** `[DOC]`:

| | Audio in / 1M | Audio out / 1M | Google's per-minute |
| --- | --- | --- | --- |
| **Gemini 3.8 Live** | **$3.00** | **$12.00** | **$0.005 + $0.018 ≈ $0.023/min** |
| `gpt-realtime-2` | **$32.00** | **$64.00** | not published per-minute |
| Gemini 3.5 Transcribe Live | $3.50 | $21.00 (text out) | ~$0.009/min |

Roughly **10× on input and 5× on output** in Gemini's favour, and **Gemini Live has a free tier** (input and output free of charge) `[DOC]`. For a student-built personal assistant this is close to decisive on its own.

**The free tier's price is privacy:** free-tier content "may be used to improve Google's products"; paid-tier content is not `[DOC]`. For an always-on assistant that hears your room, that is a real decision, not a checkbox — see §13.

### 6.2 Speech recognition

| Option | Hindi/Hinglish evidence | Verdict for SARA |
| --- | --- | --- |
| **Gemini Live native audio** | Auto language switching, no language code `[DOC]`; **no public Hinglish WER** `[GAP]` | **Primary conversational path, conditional on §18's experiment** |
| **faster-whisper `medium`** (current) | Whisper's Indian-language representation is low and performance "significantly poor" `[PUB]`; SARA mitigates with neutral `initial_prompt` + no forced language `[REPO]` | **Keep as the offline path.** Already tuned; do not disturb |
| **IndicWhisper** | Hindi avg **13.6% WER** vs Nvidia-large 18.6%, Azure 20.0%, Google STT 23.9% `[PUB]` | **Rejected** — see below |
| **Whisper `large-v3` / `turbo`** | Better than `medium` in general; no Hinglish figure found `[GAP]` | Worth one local A/B against `medium` |
| Cloud streaming ASR (Deepgram, AssemblyAI, Gladia) | Vendor claims only; no credible per-pair Hinglish WER `[GAP]` | Not justified — pays cloud cost without Live's benefits |

**Why IndicWhisper is rejected despite the best published Hindi WER.** The Vistaar paper is explicit: IndicWhisper is Whisper-medium fine-tuned **one model per language** `[PUB]`. A per-language model cannot code-switch *by construction* — it is the §4.7 failure mode baked into the weights. Its 13.6% Hindi average is on **monolingual** Hindi benchmarks. For "VS Code mein mera project open karke check karo" a Hindi-only model must either drop or mangle every English token. Excellent at a task SARA does not have.

Two further caveats on that table: Vistaar is Interspeech **2023**, so roughly three years stale as of this writing, and its Gramvaani (spontaneous/telephony Hindi) column is brutal across the board — **26.8% even for IndicWhisper**, 42–60% for everything else `[PUB]`. Spontaneous Hindi speech is simply hard, and that is the register you actually speak in.

### 6.3 Intent understanding and reasoning

**Keep the deterministic router as the first stage. This is the highest-confidence recommendation in the document** — it rests on a measurement from your own machine rather than on any vendor's claim: p95 0.997 ms, 100% Hinglish, 100% on negatives, 93.2% overall `[MEAS]`.

Route to a model only when the router declines or the request is conversational, multi-step or ambiguous — which is precisely where the router measures weakest (adversarial 67.5%, grouped 81.1%) and a reasoning model is strongest.

**On local LLMs:** not recommended as the primary reasoning path. An 8B model "handed forty tools picks the wrong one, or emits JSON that does not parse" `[REPO]`, and SARA declares a large tool surface. If one is ever used, the two-budget tool trimming in §4.6 becomes mandatory.

### 6.4 Speech output

| Option | Evidence | Verdict |
| --- | --- | --- |
| **Gemini Live native audio out** | 24 kHz PCM; language follows the model automatically `[DOC]`; **Hinglish prosody unverified** `[GAP]` | **Primary, conditional on §18** |
| **Kokoro ONNX** (current) | Already integrated with chunking, pre-warm, ducking, caching `[REPO]`; **no Hinglish quality measurement** `[GAP]` | **Keep as offline path.** No evidence to replace it |
| **Piper** | 0.34 s for 5.15 s of speech, **15× real time on CPU**; 176 voices / 52 languages `[REPO]` | **Do not migrate on current evidence.** Per-language voices are the §4.7 problem; Kokoro already works |
| ElevenLabs | Needs account, key and connection per sentence `[REPO]` | No — cost and offline loss |
| Windows SAPI | On a stock Win10 install: two voices, both English; a Russian sentence produced **zero bytes with a success code** `[REPO]` | **Never as a silent fallback.** Silence reporting success is the worst failure mode |

On **Kokoro vs Piper**: the brief says "consider the existing Kokoro ONNX pipeline but do not retain it if better measured options are available." I found no measurement showing Piper is better **for Hinglish**, and Piper's per-language voice model is the design §4.7 rejects. Kokoro stays until measured otherwise. Preserving it is also what keeps the offline path alive.

### 6.5 Audio transport

Gemini Live uses a **WebSocket**; the `google-genai` SDK manages it `[DOC]`. SARA already depends on `google-genai` 1.23.0 `[REPO]` — **no new transport dependency**. WebRTC is unnecessary: it solves NAT traversal and peer-to-peer jitter, neither of which applies to one desktop talking to one API. Your brief's warning against "browser-only techniques" is well placed; this is the case it was aimed at.

Format alignment is favourable and worth noting because it is the kind of thing that costs days when it goes the other way: Live wants **16 kHz PCM in / 24 kHz PCM out** `[DOC]`, and Mark-LVI's TTS protocol independently documents 24 kHz as "the rate `_play_audio` already opens the speaker at, so nothing downstream has to resample" `[REPO]`. SARA's current rates need confirming against this `[GAP]`, but the target is standard.

### 6.6 Local vs remote

| Workload | Where | Why |
| --- | --- | --- |
| Wake word | **Local** | Privacy floor: nothing streams before the wake word `[REPO]` |
| VAD / endpointing | **Local** | Already cue-aware and Hinglish-tuned; feeds Live's hybrid VAD |
| AEC / barge-in | **Local** | Must be sub-frame; network cannot participate |
| **Intent routing** | **Local** | **0.997 ms p95, 100% Hinglish** `[MEAS]` |
| Policy / confirmation / verification | **Local, always** | Authorization must never depend on a network |
| Conversational reasoning | **Remote** | Quality and latency both better |
| Offline fallback | **Local cascade** | Whisper + Kokoro, already built |

**No GPU is assumed.** `requirements.txt` ships CPU and GPU variants and wraps CUDA imports in `try/except` `[REPO]`, and Mark-LVI's STT protocol states "CPU is the default target […] a provider that quietly needs one breaks that promise" `[REPO]`. Right call; keep it.

---

## 7. Sources

**Primary documentation (read 2026-10-28)**
- [Live API capabilities — Gemini API](https://ai.google.dev/gemini-api/docs/live-api/capabilities) — models, VAD, barge-in, sessions, languages, async tools
- [Gemini Developer API pricing](https://aistudio.google.com/docs/pricing) — Live pricing and free tier
- [GPT-Realtime-2 model — OpenAI](https://developers.openai.com/api/docs/models/gpt-realtime-2) — pricing, context, limits
- [Gemini API models](https://ai.google.dev/gemini-api/docs/models)
- [Migrate from Gemini 2.5 Live Native Audio to Gemini 3.8 Live](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/migrate-from-gemini-2-5-to-gemini-3-8-live)
- [Gemini Live API overview — Google Cloud](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api)

**Published research**
- [Vistaar: Diverse Benchmarks and Training Sets for Indian Language ASR (Interspeech 2023)](https://www.isca-archive.org/interspeech_2023/bhogale23_interspeech.pdf) — Hindi WER table; IndicWhisper is per-language
- [Vistaar — arXiv 2305.15386](https://arxiv.org/pdf/2305.15386)
- [AI4Bharat/vistaar](https://github.com/AI4Bharat/vistaar)
- [HiACC: Hinglish adult & children code-switched corpus (PubMed)](https://pubmed.ncbi.nlm.nih.gov/40778380/) — candidate eval corpus, **not yet inspected** `[GAP]`
- [Code-switching classification on Hinglish (U. Buffalo TR 2026-41)](https://cse.buffalo.edu/tech-reports/2026-41.pdf)

**Secondary / vendor — used only for failure-mode taxonomy, not for decisions**
- [Gladia — Code-switching across 100+ languages](https://www.gladia.io/blog/code-switching-language-coverage-limitations) — cites 30–50% relative WER increase for Hindi-English; language-ID routing failure mode
- [Deepgram — Hinglish Voice AI](https://deepgram.com/learn/hinglish-voice-ai-speech-recognition)

**Repositories**
- `FatihMakes/Mark-LVI` @ `4d4035c` — architecture, measurements, CC BY-NC 4.0 licence
- `C:\project` @ `f2e3d51` — SARA source, `bench/results/latest.json`

---

## 8. Model and API limitations

| Limitation | Source | Consequence for SARA |
| --- | --- | --- |
| Live sessions: **15 min** audio-only | `[DOC]` | Resumption is **mandatory** for a resident assistant |
| Native-audio models support **AUDIO modality only** | `[DOC]` | Text must come from `output_transcription` |
| Native audio **rejects an explicit language code** | `[DOC]` | Cannot pin Hindi; must steer via system instructions |
| Native-audio Live refuses `response_modalities=["TEXT"]` with **error 1007** | `[REPO]` | Mark-LVI's workaround: read the transcription. **Verify against 3.8-live** `[GAP]` |
| Live cannot carry grounding metadata | `[REPO]` | Grounded search stays on REST |
| Async function calling absent on `3.1-flash-live-preview` | `[DOC]` | Use `gemini-3.8-live`, not the reference project's pin |
| Proactive audio / affective dialog need `v1beta` | `[DOC]` | API-version decision, not a feature flag |
| Live API rate limits not on the capabilities page | `[DOC]` | **Unknown; must be checked** `[GAP]` |
| `gpt-realtime-2` knowledge cutoff Sep 2024 | `[DOC]` | Stale for current tooling |
| Whisper: one language label per utterance | `[PUB]`+`[REPO]` | The core Hinglish risk |
| Whisper cold start: first transcription 8.5 s | `[REPO]` | Warm-up required. **Check SARA's `prewarm.py` covers STT, not only TTS** `[GAP]` |
| `audioop` removed in Python 3.13 | `[REPO]` D13 | Blocks interpreter upgrade (`sara/audio/stt/helpers.py:75`) |
| CC BY-NC 4.0 on Mark-LVI | `[REPO]` | **No code reuse. Ever, if SARA is commercial** |

---

## 9. Hindi / English / Hinglish quality analysis

**This section is where I must be most careful, because it is the requirement you called core and the one with the least public evidence.**

### What is established

- SARA's **text → intent** layer is excellent on Hinglish: **100% on 160 romanized Hinglish utterances**, better than its English (89.32%) `[MEAS]`.
- SARA's endpointer carries real romanized-Hindi continuation cues and extends silence tolerance accordingly `[REPO]`.
- SARA's Whisper configuration has already been tuned against the bias failure mode, with the finding recorded in code `[REPO]`.
- Code-switched Hindi-English ASR costs **30–50% relative WER over the Hindi baseline** `[PUB]`.
- The dominant failure mode is **language-ID routing**: detection over a window can send a whole sentence to the wrong language engine `[PUB]`.
- Whisper's Indian-language training representation is low; base performance on Indian languages is "significantly poor" `[PUB]`.
- IndicWhisper is best-in-class on **monolingual** Hindi (13.6% avg) and is **one model per language** — structurally unable to code-switch `[PUB]`.
- Spontaneous/telephony Hindi (Gramvaani) is 26.8% WER even for the best system `[PUB]`.
- Gemini Live native audio **switches languages automatically and accepts no language code** `[DOC]`.

### What is NOT established — stated plainly

1. **`[GAP]` No public per-language or code-switching WER for Gemini Live native audio.** I searched specifically for it and found only vendor marketing. The documented capability is *automatic language switching*, which is a design property, **not a measured accuracy**. Your brief's exact warning applies: supporting Hindi and English individually is not evidence of Hinglish competence. **I am not claiming Gemini Live is good at Hinglish. I am claiming its architecture has no language-routing step to get wrong, and that this must be measured.**
2. **`[GAP]` SARA's actual speech→text Hinglish accuracy is unmeasured.** The 100% figure is text-in. The script-instability risk — Whisper writing Devanagari where the `hinglish` cue set and the router expect romanization — is **plausible and untested**. If Whisper returns Devanagari for a code-switched utterance, the `hinglish` cue set in `endpoint_cues.py` will not match, and the router's 100% accuracy is measured on input it may not receive.
3. **`[GAP]` Hinglish TTS quality is unmeasured for both Kokoro and Gemini Live.** Intelligibility of English technical terms inside a Hindi sentence ("import time", "benchmark", "VS Code") is the specific thing to judge, and no benchmark covers it.
4. **`[GAP]` Vistaar is ~3 years old** and predates Whisper large-v3 and current multilingual models.

### The experiment that resolves it

§18 specifies it. In brief: 60–100 utterances in **your own voice**, drawn from `bench/golden/intents_hinglish.jsonl` so the labels already exist, recorded once and replayed through every candidate. This is the highest-value experiment in the whole programme, and until it is run **every Hinglish claim in any architecture proposal — including mine — is an assumption.**

---

## 10. Latency budget

Your brief's twelve stages, with current evidence and honest targets. **`[GAP]` marks what must be measured on your PC; I will not invent numbers for them.**

| # | Stage | Current | Live target | Floor and why |
| --- | --- | --- | --- | --- |
| 1 | Audio capture + buffering | `[GAP]` | 10–30 ms | Frame size; `TTS_PLAYBACK_BUFFER_MS=40` on output |
| 2 | VAD decision | `[GAP]` | **< 10 ms** | Local arithmetic — **50 ms feasible** |
| 3 | First useful partial transcript | ~900 ms preview cadence `[REPO]` | 100–300 ms (Live streams) | Whisper needs a buffer; Live does not |
| 4 | Incremental intent interpretation | **not implemented** `[REPO]` | **~1 ms** once text exists `[MEAS]` | Router is 0.997 ms p95 — **50 ms feasible** |
| 5 | End-of-utterance detection | 350–900 ms (+400 cue, +150 noise) `[REPO]` | 500–800 ms `[DOC]` | **Hard floor. ≥500 ms is Google's own client-VAD guidance.** The single largest irreducible cost |
| 6 | Final intent + argument validation | **0.997 ms p95** `[MEAS]` | unchanged | Already **50× inside 50 ms** |
| 7 | Model / planner latency | `[GAP]` | 0.3–0.6 s `[REPO]` | Network + inference |
| 8 | Tool dispatch | `[GAP]` | < 10 ms | Local |
| 9 | Action begins | `[GAP]` | +10–50 ms | OS-dependent |
| 10 | Action verified | `[GAP]` | 150 ms–2 s | Mark-LVI: process table answers in **150 ms** `[REPO]` |
| 11 | First assistant audio | `[GAP]` | 0.3–0.6 s `[REPO]` | Live streams; cascade waits on synthesis |
| 12 | Total task completion | `[GAP]` | see below | — |

### The 50 ms question, answered scientifically

**Achievable at ~50 ms or better — and stages 2, 4 and 6 are already there, measured:**
- Intent routing: **0.997 ms p95, 1.99 ms p99** `[MEAS]`
- VAD decision, barge-in detection, wake-word scoring, audio-frame processing `[EST]`, local arithmetic

**Not achievable for "speech → verified action," under any architecture available today.** The binding constraint is physics plus protocol, not code quality:
- End-of-speech detection needs **≥500 ms** of silence `[DOC]`. Shorten it and you cut users off mid-sentence — and your brief forbids trading accuracy for latency.
- A model round trip is 0.3–0.6 s `[REPO]`.
- Verification takes ~150 ms minimum `[REPO]`.

**Honest targets to replace the 50 ms claim:**

| Category | Target | Basis |
| --- | --- | --- |
| Deterministic command, **push-to-talk** (no endpoint wait) | **< 150 ms** to action start | Router 1 ms + dispatch + verify `[EST]` |
| Deterministic command, **voice-endpointed** | **600–900 ms** | 500 ms endpoint floor + ~1 ms router + verify |
| Conversational reply, first audio | **400–700 ms** | Live 0.3–0.6 s + capture `[REPO]`+`[DOC]` |
| Multi-step task, acknowledged | **< 700 ms to spoken ack**, then progress | Async function calling `[DOC]` |
| Barge-in → audio stops | **< 100 ms** | Local detection; SARA has the machinery |
| Offline cascade, any command | **1.5–4 s** | `[REPO]` — stated so nobody expects otherwise |

**The design conclusion that follows:** push-to-talk is the only configuration in which SARA can plausibly feel near-instant, because it is the only one that removes the 500 ms endpoint floor by replacing inference with a key release. Mark-LVI reached the same conclusion independently — Ctrl+Space, "the key release is the signal" `[REPO]`. **If sub-second response is the priority, a push-to-talk path deserves first-class support alongside wake-word mode.**

---

## 11. Hardware and network assumptions

**Hardware** `[MEAS]`, from the benchmark meta: Windows 10 (10.0.26300), Python 3.11.9, AMD64 Family 25 Model 68 Stepping 1 — Zen 3. **No GPU is assumed anywhere**; CUDA imports are guarded `[REPO]`. Whisper `medium` (769M) on CPU with `beam_size=2` is the current configuration; its actual per-utterance cost on this machine is `[GAP]`.

**Network** `[GAP]` — and this is a first-class risk, not a footnote. Gemini Live is a **persistent WebSocket**, so it is sensitive to jitter and disconnection in a way that request/response APIs are not. For a user in Rajasthan on consumer broadband or mobile tethering, round-trip time and stability to the nearest Google endpoint are **decision-grade unknowns**. If the connection drops frequently, the 15-minute session cap plus reconnection churn could make the Live path feel worse than the 1.5–4 s cascade. §18 includes the measurement.

Two Windows-specific traps from the reference project, both worth checking in SARA `[REPO]`: Windows resolves `localhost` to `::1` first while local model servers bind IPv4-only (normalise to `127.0.0.1`), and a machine-wide `HTTP_PROXY` that `httpx` honours by default will be used even for loopback, producing a timeout that reads as "the server is not running."

---

## 12. Accuracy and reliability trade-offs

**The brief's rule — never trade safety for speed — has a concrete application here.** SARA's `STT_CONFIDENCE_CONFIRM_THRESHOLD = 0.55` makes low ASR confidence require a spoken yes/cancel for destructive actions `[REPO]`. That is the correct shape: latency rises *only* in the ambiguous-and-dangerous case. Early dispatch on partial transcripts (§3.5) must respect the same rule — **never dispatch a tier-escalated or irreversible action from a partial transcript, under any latency pressure.**

**Measured weaknesses to improve before any migration** `[MEAS]`:

| Weakness | Value | Note |
| --- | --- | --- |
| `false_trigger_adversarial` | **20.83%** | 1 in 5 adversarial inputs reaches a tool |
| adversarial accuracy | 67.50% | Weakest slice |
| grouped / multi-step | 81.08% | Multi-step is a stated goal |
| noisy | 84.67% | Real rooms are noisy |
| D19 confusion cluster | 3 intents → `unmute` | §3.8 — looks like a table bug |

The 20.83% adversarial false-trigger rate is the most serious number in this document. `false_trigger_negative` is **0.00%** — ordinary chat never triggers a tool — so this is specifically *adversarial* input, which is exactly the prompt-injection surface. It deserves a batch of its own, and it is **independent of the architecture decision**: worth fixing whichever engine wins.

**One trade-off to revisit.** `STT_HARD_MUTE_DURING_TTS = True` `[REPO]` hard-mutes the mic while SARA speaks. That guarantees no self-echo but **costs barge-in responsiveness** — you cannot interrupt a mic that is muted. Mark-LVI deliberately rejected this: its echo module "lets the tail drop our own voice **while still passing a user who replies instantly**, instead of blanket-muting" `[REPO]`. SARA has `aec.py`, `barge_classify.py` and `barge_watch.py` — if AEC works, the hard mute is redundant and is suppressing a feature SARA already paid for. **Worth an A/B.** Note that Live's server-side barge-in would make this moot on the Live path, which is itself an argument for the recommendation.

---

## 13. Privacy, security, licensing and cost

### Privacy — the sharpest trade in the whole decision

| Mode | Audio leaves machine | Used for training | Cost |
| --- | --- | --- | --- |
| Local cascade | **No** | No | **Zero** |
| Gemini Live, **free tier** | Yes | **Yes — "may be used to improve Google's products"** `[DOC]` | **Zero** |
| Gemini Live, **paid tier** | Yes | **No** `[DOC]` | ~$0.023/min |

**The free tier's price is your conversations.** For an always-on assistant in your room this is a genuine decision and I will not soften it. If the Live path is adopted, **the paid tier is the privacy-correct default**, and the free tier should be an explicit, informed opt-in rather than the path of least resistance.

**The fallback must never silently change the privacy posture.** Your brief names this and it is the right instinct: a mode that fails over from local to cloud without saying so has silently started streaming the user's room. The capability dict (§4.4) is the mechanism — make the active engine's privacy posture visible in the UI and announced on any switch, and make an automatic local→cloud failover **opt-in, never a default**. A failure that says "I cannot reach the cloud and your audio stays here" is honest; one that quietly reconnects is not.

### Security

**Preserve SARA's existing layer unchanged, and route every tool call through it.** It is ahead of the reference project (§3.6). The specific hazard of a Live migration: `gemini-3.8-live` defaults to **async `NON_BLOCKING`** function calling `[DOC]`, which makes it easy to wire tool calls straight from the event loop to handlers, **bypassing `guard.py`**. That would silently delete the confirmation gate, the injection detector and the tier validation. It is the highest-severity migration risk in §14.

Also inherit the **untrusted-content boundary** concept (§4.5, `outcome.untrusted`): fetched web and file content wrapped as data with a non-forgeable closing marker. SARA has injection detection; a structural data boundary is complementary, and Mark-LVI is honest that it is "a boundary, not a guarantee."

### Licensing

| Component | Licence | Note |
| --- | --- | --- |
| **Mark-LVI** | **CC BY-NC 4.0** | **No code reuse. Blocks commercial use entirely** |
| faster-whisper | MIT | Fine |
| Kokoro ONNX | Apache-2.0 `[GAP] verify` | Check before relying |
| `google-genai` | Apache-2.0 | Already a dependency |
| Piper | MIT `[GAP] verify` | Only if adopted |

### Cost

Gemini Live at ~$0.023/min `[DOC]`. At 30 min/day of *actual conversation* (not idle time — Live is only billed while a session streams) that is roughly **$0.70/month**, versus **$10+/month** at `gpt-realtime-2`'s token prices `[EST]`. The hybrid reduces it further: every routed command costs **nothing** because it never reaches the network.

---

## 14. Migration risks

Ordered by severity. The first three are the ones that would actually hurt.

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | **Live's async tool calls bypass `guard.py`** | **Critical** | Route **every** tool call through `guard.validate` → `replay_confirmed`. Add a test that fails if any engine path reaches a handler without a policy decision |
| 2 | **Deadlock: awaiting generation inside the `receive()` loop** | **Critical** | "Every send method records, schedules, and returns" `[REPO]`. SARA's dispatcher is synchronous — needs a scheduling boundary. Presents as "the model is slow," not as a hang |
| 3 | **Hinglish regresses and nobody notices** | **Critical** | Run §18 **before** migrating. Extend `bench/` with a speech-in mode so it is caught by CI, not by you |
| 4 | Offline capability lost | High | Keep the cascade behind the same adapter; test both in CI |
| 5 | 15-min session cap / reconnection churn | High | Session resumption from day one; measure real-world drop rate |
| 6 | Privacy posture changes silently on fallback | High | §13 — opt-in, announced, visible |
| 7 | Barge-in regression | Medium | Live is server-side; keep local barge-in for the cascade |
| 8 | Adapter rewrite destabilises working audio | Medium | Adapt the new to the old (§4.3). Do **not** invent a neutral interface |
| 9 | Vendor lock-in | Medium | The adapter *is* the mitigation |
| 10 | Tool-declaration payload | Low-Medium | No budget for Gemini; two budgets for small models |
| 11 | Cold-start cost | Low | Pre-warm; verify `prewarm.py` covers STT `[GAP]` |

**Rollback.** Checkpoint `f2e3d51` is clean. Every batch goes behind a config flag defaulting to the current behaviour, so rollback is a flag flip rather than a revert. The adapter boundary means the Live engine can be deleted without touching the cascade.

---

## 15. Benchmark methodology

**Extend `bench/`. Do not build a new harness.** It already has the golden sets, the per-language/kind/style slicing, percentile latency, a threshold gate and `--accept-baseline` `[REPO]`. The work is adding a speech-in mode and latency stages.

**Why recorded audio, not live speaking:** every candidate must receive **bit-identical input**, or you are measuring the day's mood rather than the architecture. Record once, replay forever.

### Phase 3a — Hinglish speech→text (highest priority)

1. Select 60–100 utterances from `bench/golden/intents_hinglish.jsonl` + `intents_hi.jsonl` + `intents_en.jsonl`. **Labels already exist** — this is why the cost is low.
2. Record each in **your own voice**, 16 kHz mono WAV: normal pace, fast, and with background noise. Commit the manifest, not necessarily the audio.
3. Replay through each candidate: faster-whisper `medium` (baseline), `large-v3`, `turbo`, Gemini Live `input_transcription`.
4. Report **WER and CER**, plus two metrics the standard ones miss:
   - **Script-consistency rate** — fraction transcribed in the expected script. This is the §9 hypothesis, and WER alone can hide it.
   - **End-to-end intent accuracy** — feed each transcript to the real router and compare against the 100% text-in baseline. **This is the number that matters**, because it measures the pipeline you actually ship.

### Phase 3b — latency stages

Instrument the twelve stages of §10 with one monotonic clock. Report **P50/P90/P95 with n**, cold vs warm, hardware, network, and concurrency. Separate: deterministic local actions, straightforward natural-language commands, complex multi-step tasks, web-dependent tasks. **Reuse `bench/metrics.py`'s nearest-rank percentile** so new numbers are comparable with existing ones.

### Phase 3c — accuracy and safety

Keep every existing metric. Add: tool-selection accuracy, argument accuracy, **verified** action success (not reported success), recovery rate, clarification rate, unsafe-action rate. Track `false_trigger_adversarial` as a **release gate** — it is at 20.83% and must not get worse.

**Rules.** Establish baselines before setting targets. Never claim 100% — quantify residual error and rank by impact. Do not tune endpointing for latency without re-running accuracy: §12's rule binds the benchmark too.

---

## 16. Recommended architecture — the decision

**Hybrid: SARA's deterministic router as the fast path, `gemini-3.8-live` native audio as the conversational path, the existing local cascade retained as the offline path, all three behind one adapter, with every tool call routed through the existing security layer.**

```
                    ┌──────────── always local ────────────┐
   mic ─► wake word ─► AEC ─► cue-aware endpointer ─► [partial transcript]
                                                            │
                              ┌─────────────────────────────┤
                              ▼                             ▼
                   deterministic router              conversational engine
                   p95 0.997 ms  [MEAS]              ┌──────┴──────┐
                   100% hinglish [MEAS]              │             │
                              │                  Live S2S     local cascade
                              │                 (primary)      (offline)
                              └──────────┬──────────┴─────────────┘
                                         ▼
                        ══ sara/core/security/guard.py ══   ◄── MANDATORY
                           policy · tier · confirm · injection
                                         ▼
                                    tool dispatch
                                         ▼
                              outcome verification
                           done / unverified / failed
                                         ▼
                            speech out  ·  UI state
```

### The fifteen decisions your brief asked for

| # | Question | Decision | Basis |
| --- | --- | --- | --- |
| 1 | Primary voice architecture | **Hybrid**: local router + Live native audio | `[MEAS]` 0.997 ms router; `[REPO]` 0.3–0.6 s vs 1.5–4 s |
| 2 | faster-whisper? | **Retain** for the offline path. Do not replace | Already Hinglish-tuned `[REPO]`; no better code-switching option found |
| 3 | Kokoro ONNX? | **Retain.** Revisit only if §18 shows Hinglish failure | No measurement favours Piper for Hinglish |
| 4 | Wake word / audio components? | **Retain all.** AEC, endpointer, barge-in, chunker, prewarm | `[REPO]` — already built, and better than the reference's VAD |
| 5 | Reasoning / tool-use strategy | **Router first; Live for conversation.** No local LLM as primary | `[MEAS]` router strong where LLM is slow; `[REPO]` small models mis-pick tools |
| 6 | Native S2S as main conversational path? | **Yes — conditional on §18** | `[DOC]` auto code-switching, async tools, server barge-in |
| 7 | Fast local command path justified? | **Yes, emphatically.** The strongest finding here | `[MEAS]` 0.997 ms p95, 100% Hinglish |
| 8 | Conversation state / memory | Live session + **local** history; resumption from day one; keep budgeted memory | `[DOC]` 15-min cap; `[REPO]` memory core pattern |
| 9 | Hinglish evaluation | **Extend `bench/` with speech-in**; script-consistency + end-to-end intent accuracy | §15 |
| 10 | Local vs remote | Wake/VAD/AEC/routing/**policy** local; reasoning remote; cascade offline | §6.6 |
| 11 | Audio / transport / concurrency | WebSocket via `google-genai` (**already a dependency**); 16 kHz in / 24 kHz out; bounded queues; **every send schedules and returns** | `[DOC]`, `[REPO]` |
| 12 | Fallback without privacy drift | Capability dict drives UI; switches announced; local→cloud **opt-in only** | §13 |
| 13 | pywebview? | **Retain. No evidence to change it** | GUI is not on the voice latency path; 2,110 passing tests depend on it |
| 14 | Partial migration or redesign? | **Partial, behind an adapter.** Not a rewrite | §4.3; SARA's audio stack is an asset, not debt |
| 15 | Benefits / costs / risks / validation | §10, §13, §14, §15 | — |

**On pywebview specifically**, since the brief invites a change: the GUI is not on the voice latency path. SARA's bridge was audited and hardened in batches 1–6b3, and 2,110 tests pass against it. Replacing it would be a large, risky change justified by no measurement I have. **Revisit only if a measured GUI-side latency or rendering defect appears.**

### What must change

1. **Add the adapter** — four methods, five event fields, Live-shaped. Cascade implements the same contract.
2. **Add the `gemini-3.8-live` engine** — not the reference project's legacy `3.1-flash-live-preview`, which lacks async function calling `[DOC]`.
3. **Route every engine's tool calls through `guard.py`**, with a test that fails if any path bypasses it. *Non-negotiable.*
4. **Make `send_*` schedule-and-return** so a tool call cannot deadlock the receive loop.
5. **Act on partial transcripts** for tier-0 reversible intents only — the unimplemented half of brief objective A.
6. **Add session resumption and reconnection.**
7. **Structured tool outcomes** — `done`/`unverified`/`failed` with **facts, not sentences** (§4.5). Independently implemented.
8. **Extend `bench/` with speech-in** and the two new metrics.
9. **Fix D19** (§3.8) and attack `false_trigger_adversarial` (20.83%).
10. **A/B `STT_HARD_MUTE_DURING_TTS`** now that AEC exists.

### What stays

Audio capture, AEC, the cue-aware Hinglish endpointer, wake word, barge-in detection and classification, TTS chunking/player/prewarm/duck/cache, the deterministic intent router, **the entire security layer**, `bench/`, the memory system, pywebview and the hardened JS bridge, faster-whisper and Kokoro as the offline path.

### Alternatives rejected

| Rejected | Why |
| --- | --- |
| **Pure native S2S** | Discards a 0.997 ms / 100%-Hinglish router `[MEAS]`; no offline mode; all audio to the cloud unconditionally |
| **Keep cascade only** | Cannot reach the latency targets — 3 serial stages, 2 round trips per tool turn `[REPO]`; carries the single-label Hinglish weakness `[PUB]` |
| **IndicWhisper** | Best monolingual Hindi WER `[PUB]` but **one model per language** — structurally cannot code-switch |
| **`gpt-realtime-2`** | ~10× input / ~5× output cost `[DOC]`; no free tier; no Hinglish advantage found |
| **Piper over Kokoro** | Per-language voices are the §4.7 problem; Kokoro already integrated; no Hinglish measurement favours Piper |
| **Local LLM as primary reasoning** | Mis-picks tools at SARA's tool count `[REPO]`; router already covers the fast path better |
| **Replace pywebview** | No measured defect; large risk, no evidence |
| **Reusing Mark-LVI code** | **CC BY-NC 4.0 forbids commercial use** `[REPO]` |
| **WebRTC transport** | Solves NAT/P2P problems SARA does not have |
| **Neutral two-sided interface** | Would rewrite thousands of working lines for symmetry `[REPO]` |

---

## 17. Implementation plan

**Nothing starts until you approve, per §5 of your brief.** Phase 0 is research-only and can begin immediately; it changes no production code.

| Batch | Work | Exit criteria |
| --- | --- | --- |
| **0** | **§18 experiments.** Record the Hinglish set; measure STT candidates, the 12 latency stages, and network RTT/stability. No production change | Hinglish speech→intent accuracy known for every candidate; latency baseline recorded; **go/no-go on Live** |
| **1** | Fix **D19** (§3.8) and the `→chat` confusions. Red-green tests | Confusion cluster gone; accuracy ≥ 93.22% |
| **2** | Attack `false_trigger_adversarial` (20.83%) | Rate materially down; `false_trigger_negative` stays 0.00% |
| **3** | Extend `bench/` with speech-in + script-consistency + end-to-end intent accuracy; wire into CI | Hinglish regressions caught by CI |
| **4** | Define the adapter; wrap the **existing** cascade in it. No behaviour change | All 2,110+ tests pass; latency unchanged |
| **5** | `gemini-3.8-live` engine behind a flag, default **off**. Tool calls through `guard.py`; schedule-and-return sends | Live path works; **a test proves no tool call bypasses the policy** |
| **6** | Session resumption + reconnection | Survives a forced disconnect and the 15-min cap |
| **7** | Structured tool outcomes (`done`/`unverified`/`failed`, facts not sentences) | No tool reports unverified success; Hinglish replies improve |
| **8** | Partial-transcript early dispatch, **tier-0 reversible only** | Measured latency win; **zero escalated actions from partials** |
| **9** | A/B `STT_HARD_MUTE_DURING_TTS`; tune endpointing with hybrid VAD | Barge-in improves with no self-echo regression |
| **10** | Flip the Live default **only if** batch 0 and 5 both passed | Measured improvement on every §10 metric |

Each batch: evidence → expected measurable change → smallest effective change → tests alongside → targeted + regression run → latency/accuracy measurement → full diff review → ledger update. Feature-flagged, defaulting to current behaviour.

---

## 18. Open questions and the experiments that resolve them

Ordered by how much they change the decision.

| # | Question | Experiment | Cost | What it changes |
| --- | --- | --- | --- | --- |
| **1** | **Does Gemini Live handle *your* Hinglish?** `[GAP]` | Replay the recorded set; score WER, CER, **script-consistency**, **end-to-end intent accuracy** | ~3 h, mostly recording | **Decides batch 10.** If it fails, Live stays conversation-only and the cascade keeps all commands |
| **2** | **Does Whisper return the script the router expects?** `[GAP]` | Same recordings through faster-whisper `medium`; measure script-consistency | ~1 h once recorded | If Devanagari appears on code-switched input, the 100% router figure is on input it never receives — and this is a **bug to fix now**, independent of migration |
| **3** | **Is your network good enough for a persistent WebSocket?** `[GAP]` | RTT to the endpoint; 30-min session drop rate on your normal connection | ~1 h | If unstable, the hybrid tilts toward local-first |
| **4** | What is the real per-stage latency? `[GAP]` | Instrument §10's twelve stages | ~4 h | Turns every `[GAP]` in §10 into a number; validates or refutes my `[EST]`s |
| **5** | Is `large-v3`/`turbo` better than `medium` for Hinglish? `[GAP]` | A/B on the same recordings | ~1 h | Possible cheap offline-path win, no architecture change |
| **6** | Does Kokoro pronounce English terms inside Hindi acceptably? `[GAP]` | Synthesise 20 mixed sentences; listen | ~30 min | Decides whether TTS needs revisiting at all |
| **7** | Does `gemini-3.8-live` still refuse `TEXT` modality (error 1007)? `[GAP]` | One probe call | ~15 min | Affects how transcripts are obtained |
| **8** | What are the Live API rate limits? `[GAP]` | Check the rate-limits page / console | ~15 min | Capacity planning |
| **9** | Does `prewarm.py` warm **STT** or only TTS? `[GAP]` | Read it; time a cold first utterance | ~30 min | Mark-LVI's 8.5 s cold-start finding may apply directly |
| **10** | Is HiACC usable as an external Hinglish corpus? `[GAP]` | Read the paper; check licence | ~1 h | Would add speaker diversity your own voice cannot |

### Assumptions I am making explicit, so they can be challenged

1. Mark-LVI's 0.3–0.6 s vs 1.5–4 s is **directionally** right but is single-machine, no n, no percentiles `[REPO]`.
2. Gemini Live's documented automatic language switching is a **design property, not a measured accuracy** `[DOC]`. Question 1 exists because of this.
3. SARA's router keeps its 100% Hinglish accuracy **only if** ASR delivers the script it was measured on. Question 2 exists because of this.
4. `[EST]` figures in §10 are reasoned estimates, not measurements, and are labelled as such.
5. Vistaar is ~3 years old `[PUB]`.
6. I have **not** read every line of SARA's 2,154-line STT engine or 1,116-line dispatcher. My reading was targeted at the voice path, config, security and benchmark. A full trace is Phase 2 proper.

---

## 19. One-paragraph decision

Keep what measures well and add only what removes a stage. SARA's deterministic router already answers in **0.997 ms at p95 with 100% accuracy on 160 Hinglish utterances**, which is both the fast path your 50 ms aspiration was really asking for and proof that the aspiration is already met where it can be. Its audio stack — AEC, a Hinglish-cue-aware endpointer, barge-in, streaming TTS — is real engineering that a rewrite would throw away, and its security layer is better than the reference project's. What SARA lacks is not speed in routing but a conversational path that does not pay three serial stages and two round trips per tool call, and a speech layer that does not have to pick one language label for a sentence containing two. `gemini-3.8-live` removes exactly those, at roughly a tenth of the nearest competitor's price with a free tier, and its one real unknown — whether it actually handles your Hinglish — is cheap to measure and gates the decision rather than hiding inside it. So: hybrid, behind an adapter that adapts the new to the old, with every tool call still passing through the policy gate that already works, the offline cascade kept intact, and nothing switched on by default until the numbers say so.

---

*Research complete. No production code modified. Awaiting approval before Phase 5.*
