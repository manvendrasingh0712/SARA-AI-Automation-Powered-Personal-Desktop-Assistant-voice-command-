# Batch A — Pre-migration latency baseline

Captured 2026-10-28 from `telemetry.sqlite` on the target machine
(`C:\Users\…\AppData\Local\SARA-AI\data\`). Read directly; nothing estimated.

**The database held 4 KB in the main file and 218 KB in an un-checkpointed WAL,
so all three files (`.sqlite`, `-shm`, `-wal`) were read together.** Reading the
main file alone would have reported an empty table.

## The headline finding: there is no voice baseline

**All 16 recorded turns have `source='text'`. Zero voice turns.**

| Field | Value |
| --- | --- |
| Rows | 16 |
| Window | 2026-10-16 05:50 → 2026-10-27 17:57 UTC |
| `source` | `text` × 16 |
| `lang` | `NULL` × 16 |
| `outcome` | `ok` × 12, **`barge_in` × 4** |
| `route` | `llm` × 10, `regex` × 3, `tool_router` × 1, `NULL` × 2 |

The voice path **is** instrumented correctly — `_t_begin("voice", text=…, lang=…)`
at `core_wiring.py:929`, with `_t_pending("wake"/"speech_start"/"speech_end"/
"stt_done")` upstream. So the absence is data, not a missing hook: no voice turn
completed while telemetry was active. `stt_ms` is `NULL` in all 16 rows, which is
consistent.

**Consequence:** the voice figures this migration must be judged against do not
exist yet. They have to be captured by actually speaking to SARA. No voice
latency claim is made anywhere in this document.

## What the 16 text turns do show

Percentiles are nearest-rank, matching `telemetry._percentile`, which returns
`None` below 5 samples. **n is small — treat these as indicative, not settled.**

| Metric | n | p50 | p95 | min | max |
| --- | --- | --- | --- | --- | --- |
| `route_ms` | 14 | 922 | 2000 | 0 | 2000 |
| `llm_ttft_ms` | 10 | 2360 | 3110 | 1516 | 3110 |
| `llm_total_ms` | 10 | 2438 | 3172 | 1516 | 3172 |
| `tts_start_ms` | 5 | 1250 | 1453 | 0 | 1453 |
| **`ttfa_ms`** | **7** | **4156** | **5359** | 47 | 5359 |
| `total_ms` | 16 | 3110 | 8000 | 47 | 8000 |

### Three things worth stating plainly

**1. Time to first audio is ~4.2 s at p50, and that is without any audio input.**
These are typed turns: no wake word, no capture, no endpointing, no STT. The
voice path adds all of it on top, so **real voice `ttfa` will be worse than
4156 ms.** The decomposition accounts for it: `route_ms` 922 + `llm_ttft` 2360 +
`tts_start` 1250 ≈ 4.5 s.

**2. The deterministic path is genuinely fast in production**, confirming the
bench result from the other direction. `regex`-routed rows show `route_ms = 0`,
and one `paste_clipboard` turn reached first audio in **47 ms**. The slow rows
are all `route='llm'`. This is the measured production evidence for keeping the
local router as the fast path.

**3. Four of sixteen turns ended in `barge_in`** — the user interrupted a quarter
of them. With 4–5 s waits that is not surprising, and it is a useful signal in
its own right.

One outlier worth noting: a `play_youtube` turn spent **6047 ms inside the tool**
(`tool_ms`), not in the model. Tool time and model time are separate problems.

## Privacy

`TRACE_STORE_TEXT` is off (read via `getattr(Config, …, False)` and not defined
in `config.py`), so only `text_len` and a 12-character SHA-1 `text_hash` were
stored — **no transcripts were read or copied.** Only `telemetry.sqlite` was
accessed; `sara_data.db` in the same folder was not opened. `*.sqlite` is
gitignored and `telemetry.sqlite` is in `policy_rules._DB_NAMES`.

## How to capture the voice baseline (needs the target machine)

1. Use SARA by voice for 15–20 wake cycles, mixing English, Hindi and Hinglish,
   and including a few deliberate interruptions.
2. `python -m sara.core.telemetry.dump --last 100`
3. Expect `source='voice'` rows with `stt_ms` populated. `ttfa_p50`/`ttfa_p95`
   from `telemetry.get_summary()` are the numbers to compare against after
   batch B and, later, against any Live engine.

Capturing this **after** the batch-B wake-ack fix also measures that fix: the
interval it removes is exactly what `ttfa_ms` covers.
