# SARA-Bench

Golden-set benchmark for the SARA intent router. Router mode classifies every labelled utterance with the real intent engine and never runs a handler.

## Run

```bash
python -m bench.run_bench --mode router --out bench/results/latest.json
python -m bench.run_bench --mode router --report-md bench/results/RESULTS.md
python -m bench.run_bench --mode router --compare bench/baseline.json --report-md bench/results/RESULTS.md
python -m bench.run_bench --mode router --accept-baseline
```

| Flag | Meaning |
| --- | --- |
| `--compare PATH` | Gate this run against a baseline results JSON. |
| `--accept-baseline` | Write this run as `bench/baseline.json` (or the `--compare` path). Refused when rows < `min_rows`. |
| `--report-md PATH` | Also write the Markdown report. |

`baseline.json` is never overwritten automatically; only `--accept-baseline` writes it.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Run OK (and gate passed when `--compare` is used). |
| 2 | Error, or a threshold in `bench/thresholds.json` failed (the message lists which). |
| 3 | Baseline missing or unreadable. |

## Metrics

All rates are fractions in [0, 1] and shown as percentages in reports.

- **Accuracy (top-1)** = rows with `pred == expected` / rows. Reported overall and per language, category, style and kind.
- **Precision / recall / F1 per intent** = `tp / (tp + fp)`, `tp / (tp + fn)`, `2PR / (P + R)`; a zero denominator gives 0.
- **Macro-F1** = mean F1 over intents with at least one expected row.
- **Top-10 confusions** = most frequent `(expected, pred)` pairs with `expected != pred`; ties are ordered alphabetically.
- **False-trigger rate** = rows routed to any tool / (rows of kind `negative` + rows of kind `adversarial` whose expected intent is `chat`). A prediction of `chat` or `error` is not a tool.
- **Gate false-open rate** = chat-expected rows with `gate_open == true` / chat-expected rows.
- **Group accuracy** = rows with `groups_ok == true` / rows that have expected groups (intent and groups both exact after lowercase/strip). Not defined when no such rows exist.
- **Latency p50 / p95 / p99** = nearest-rank percentile: sort the n latencies and take the value at rank `ceil(p * n / 100)`.
- **Latency score** = `clamp(1 - p95 / 200 ms, 0, 1)`.
- **Composite** = `0.5 * accuracy + 0.2 * (1 - false_trigger) + 0.2 * group_accuracy + 0.1 * latency_score`. When there are no group rows, the remaining weights are rescaled to sum to 1: `(0.5 * accuracy + 0.2 * (1 - false_trigger) + 0.1 * latency_score) / 0.8`.

## Regression gate

`bench/thresholds.json` defines the limits against the baseline:

- `max_accuracy_drop_pts`: baseline accuracy minus current accuracy, in percentage points.
- `max_false_trigger_increase_pts`: current minus baseline false-trigger rate, in percentage points.
- `max_p95_latency_increase_pct`: `(p95 - baseline p95) / baseline p95 * 100`. Ignored when the p95 rise is 0.5 ms or less (timer noise).
- `min_rows`: minimum number of rows in the run (and in any accepted baseline).

## First baseline

1. Run with `--report-md bench/results/RESULTS.md` and review the report.
2. Run `python -m bench.run_bench --mode router --accept-baseline`.
3. Commit `bench/baseline.json`.

## Memory benchmark

Compares Memory 2.0 (structured, temporal, correctable memory) with a simulation of the old vector-only RAG. In-process, offline, deterministic, temp databases only. Scenario data: `bench/memory/scenarios.jsonl` (gold facts and events, EN / Hinglish / Hindi).

```bash
python -m bench.run_bench --mode memory --embedder hash
python -m bench.run_bench --mode memory --embedder hash --gate
python -m bench.run_bench --mode memory --embedder gemini --out bench/results/memory_gemini.json
```

| Flag | Meaning |
| --- | --- |
| `--embedder hash` | Offline `hash_embed` (default). `gemini` uses `sara.core.rag.embed_text` (manual run, needs network). |
| `--extract gold` | Facts and events come from the scenario file. `llm` is not implemented (exit 2). |
| `--out PATH` | Raw results and metrics JSON (default `bench/results/memory.json`). |
| `--report-md PATH` | RESULTS.md to update (default `bench/results/RESULTS.md`); only its `## Memory` section is replaced. |
| `--gate` | Exit 2 on a regression (see below). |

Both systems see the same scenario: turns are replayed in order, `forget` ops run after the given turn (`after_turn` counts turns from 1; 0 means before the first turn), then every question is answered at its `at` time.

- **Memory 2.0**: gold facts / events go into a fresh `Memory2Store`; forget ops use `forget.find_matches` + `forget.retract_hits`; answers come from `retrieve()`.
- **Baseline**: raw user utterances embedded with the same embedder; answer = cosine top-5 with floor 0.30; a forget op deletes the single nearest utterance.

Pass rules (case-insensitive substrings): `expect.any` needs a match in the top 3 hits; `contradiction` also needs no `forbid` string in the top-1 hit; `abstain` needs zero hits (a hit is a false memory); `forget` needs no forbidden string in any hit.

Metrics, overall and per question type: recall@1/@3/@5 and MRR (types with `expect.any`), contradiction / temporal / abstention accuracy, false-memory rate, forgetting leak rate, retrieval latency p50 / p95 (ms, nearest rank, per `retrieve()` call).

| Code | Meaning |
| --- | --- |
| 0 | Run OK (and gate passed with `--gate`). |
| 2 | Error, `--extract llm`, or gate failed: Memory 2.0 forgetting leak > 0, overall recall@3 below baseline, abstention accuracy below baseline, or false-memory rate above baseline. |
| 4 | Memory 2.0 modules (`retrieve`, `forget`, `store`) or the embedder are missing. |