# SARA-Bench results

- Timestamp (UTC): 2026-10-30T02:44:45+00:00
- Git commit: f566963
- Python: 3.11.0 | OS: Windows-10-10.0.26300-SP0 | CPU: AMD64 Family 25 Model 68 Stepping 1, AuthenticAMD
- Mode: router | Rows: 590
- Baseline: none

## Headline

| Metric | Value |
| --- | --- |
| Rows | 590 |
| Accuracy | 99.3% |
| Macro-F1 | 99.6% |
| False-trigger rate | 0.0% |
| Gate false-open rate | 3.6% |
| Group accuracy | 93.2% (74 rows) |
| Latency p95 | 1.2666 ms |
| Composite | 0.9825 |

## Per language

| Language | Rows | Accuracy | vs baseline |
| --- | --- | --- | --- |
| en | 337 | 99.1% | - |
| hi | 93 | 98.9% | - |
| hinglish | 160 | 100.0% | - |

## Per category

| Category | Rows | Accuracy | vs baseline |
| --- | --- | --- | --- |
| apps | 14 | 100.0% | - |
| chat | 84 | 100.0% | - |
| files_folders | 39 | 100.0% | - |
| keyboard_mouse | 74 | 98.6% | - |
| media | 25 | 100.0% | - |
| memory | 10 | 100.0% | - |
| misc | 28 | 96.4% | - |
| notes_todos | 26 | 100.0% | - |
| power | 26 | 100.0% | - |
| reminders_time | 50 | 98.0% | - |
| settings_pages | 71 | 100.0% | - |
| system_info | 33 | 100.0% | - |
| volume_brightness | 44 | 97.7% | - |
| web_info | 20 | 100.0% | - |
| windows | 46 | 100.0% | - |

## Top confusions

| Expected | Predicted | Count |
| --- | --- | --- |
| set_alarm | chat | 1 |
| set_volume | chat | 1 |
| typing_text | chat | 1 |
| why_proactive | chat | 1 |

## Latency

| Percentile | Latency |
| --- | --- |
| p50 | 0.1210 ms |
| p95 | 1.2666 ms |
| p99 | 2.1947 ms |
| score | 0.9937 |

## Changes vs baseline

No baseline to compare. Create it with `python -m bench.run_bench --mode router --accept-baseline`.

## Memory

- Embedder: hash | Extraction: gold | Scenarios: 40 | Questions: 130

| Metric | Baseline | Memory 2.0 | Change |
| --- | --- | --- | --- |
| Recall@1 | 45.7% | 96.2% | ▲ +50.5 pts |
| Recall@3 | 54.3% | 98.1% | ▲ +43.8 pts |
| Recall@5 | 54.3% | 98.1% | ▲ +43.8 pts |
| MRR | 0.5000 | 0.9714 | ▲ +0.4714 |
| Contradiction accuracy | 8.0% | 100.0% | ▲ +92.0 pts |
| Temporal accuracy | 21.1% | 89.5% | ▲ +68.4 pts |
| Abstention accuracy | 79.0% | 100.0% | ▲ +21.1 pts |
| False-memory rate | 21.1% | 0.0% | ▼ -21.1 pts |
| Forgetting leak rate | 16.7% | 0.0% | ▼ -16.7 pts |
| Overall pass rate | 53.1% | 98.5% | ▲ +45.4 pts |
| Retrieval latency p50 | 0.0194 ms | 0.8061 ms | ▲ +0.7867 ms (+4055.2%) |
| Retrieval latency p95 | 0.0404 ms | 1.6965 ms | ▲ +1.6561 ms (+4099.3%) |
| Type abstain: pass rate | 79.0% | 100.0% | ▲ +21.1 pts |
| Type contradiction: pass rate | 8.0% | 100.0% | ▲ +92.0 pts |
| Type contradiction: recall@3 | 40.0% | 100.0% | ▲ +60.0 pts |
| Type contradiction_history: pass rate | 100.0% | 100.0% | = +0.0 pts |
| Type contradiction_history: recall@3 | 100.0% | 100.0% | = +0.0 pts |
| Type forget: pass rate | 83.3% | 100.0% | ▲ +16.7 pts |
| Type multihop: pass rate | 36.4% | 100.0% | ▲ +63.6 pts |
| Type multihop: recall@3 | 36.4% | 100.0% | ▲ +63.6 pts |
| Type single: pass rate | 72.5% | 100.0% | ▲ +27.5 pts |
| Type single: recall@3 | 72.5% | 100.0% | ▲ +27.5 pts |
| Type temporal: pass rate | 21.1% | 89.5% | ▲ +68.4 pts |
| Type temporal: recall@3 | 21.1% | 89.5% | ▲ +68.4 pts |

▲ increased, ▼ decreased, = unchanged.

## Security

- Attack rows: 72 (covered 63, documented gaps 9) | Benign rows: 102

| Metric | Value | Target |
| --- | --- | --- |
| Recall (covered) | 100.0% | >= 90.0% |
| Attack success rate (covered) | 0.0% | 0.0% |
| Attack success rate (all) | 0.0% | - |
| False-positive rate (benign) | 0.0% | <= 2.0% |
| Policy invariant (tainted turn) | ok (310 checks) | ok |
| Detector latency p50 | 0.1136 ms | - |
| Detector latency p95 | 0.2107 ms | - |

### Recall per category (covered rows)

| Category | Rows | Recall |
| --- | --- | --- |
| exfil | 5 | 100.0% |
| fake_system | 5 | 100.0% |
| file_note | 5 | 100.0% |
| hinglish_hindi | 7 | 100.0% |
| path_sensitive | 7 | 100.0% |
| roleplay | 5 | 100.0% |
| split_chunks | 4 | 100.0% |
| tool_hijack | 8 | 100.0% |
| unicode | 5 | 100.0% |
| web_hidden | 6 | 100.0% |
| web_visible | 6 | 100.0% |

### False positives per category

| Category | Rows | FPR |
| --- | --- | --- |
| article | 14 | 0.0% |
| chat | 11 | 0.0% |
| code | 12 | 0.0% |
| docs | 11 | 0.0% |
| email | 8 | 0.0% |
| hindi_news | 12 | 0.0% |
| hinglish_text | 7 | 0.0% |
| recipe | 8 | 0.0% |
| trigger_words | 19 | 0.0% |

### Documented gaps (not in the target)

9 uncovered rows, 1 detected (11.1%).
