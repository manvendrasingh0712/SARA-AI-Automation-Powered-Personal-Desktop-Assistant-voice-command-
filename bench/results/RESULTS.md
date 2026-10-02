# SARA-Bench results

- Timestamp (UTC): 2026-10-02T08:38:43+00:00
- Git commit: 2417f4b
- Python: 3.11.9 | OS: Windows-10-10.0.26300-SP0 | CPU: AMD64 Family 25 Model 68 Stepping 1, AuthenticAMD
- Mode: router | Rows: 310
- Baseline: commit a6734bc, 2026-10-02T08:16:50+00:00

## Headline

| Metric | Value |
| --- | --- |
| Rows | 310 |
| Accuracy | 41.6% |
| Macro-F1 | 25.4% |
| False-trigger rate | 6.0% |
| Gate false-open rate | 3.6% |
| Group accuracy | n/a (0 rows) |
| Latency p95 | 1.0295 ms |
| Composite | 0.6196 |

## Per language

| Language | Rows | Accuracy | vs baseline |
| --- | --- | --- | --- |
| en | 57 | 80.7% | = +0.0 pts |
| hi | 93 | 22.6% | = +0.0 pts |
| hinglish | 160 | 38.8% | = +0.0 pts |

## Per category

| Category | Rows | Accuracy | vs baseline |
| --- | --- | --- | --- |
| apps | 8 | 25.0% | = +0.0 pts |
| chat | 84 | 94.0% | = +0.0 pts |
| files_folders | 15 | 0.0% | = +0.0 pts |
| keyboard_mouse | 32 | 21.9% | = +0.0 pts |
| media | 13 | 15.4% | = +0.0 pts |
| memory | 4 | 25.0% | = +0.0 pts |
| misc | 12 | 33.3% | = +0.0 pts |
| notes_todos | 12 | 8.3% | = +0.0 pts |
| power | 12 | 16.7% | = +0.0 pts |
| reminders_time | 26 | 19.2% | = +0.0 pts |
| settings_pages | 29 | 20.7% | = +0.0 pts |
| system_info | 13 | 69.2% | = +0.0 pts |
| volume_brightness | 22 | 18.2% | = +0.0 pts |
| web_info | 10 | 40.0% | = +0.0 pts |
| windows | 18 | 16.7% | = +0.0 pts |

## Top confusions

| Expected | Predicted | Count |
| --- | --- | --- |
| open_app | chat | 4 |
| play_pause_media | chat | 3 |
| screenshot_describe | chat | 3 |
| set_alarm | chat | 3 |
| set_volume | chat | 3 |
| wifi_off | chat | 3 |
| add_todo | chat | 2 |
| bluetooth_on | chat | 2 |
| clear_notes | chat | 2 |
| close_active_window | chat | 2 |

## Latency

| Percentile | Latency |
| --- | --- |
| p50 | 0.3397 ms |
| p95 | 1.0295 ms |
| p99 | 1.3251 ms |
| score | 0.9949 |

## Changes vs baseline

| Metric | Baseline | Current | Change |
| --- | --- | --- | --- |
| Accuracy | 41.6% | 41.6% | = +0.0 pts |
| Macro-F1 | 25.4% | 25.4% | = +0.0 pts |
| False-trigger rate | 6.0% | 6.0% | = +0.0 pts |
| Gate false-open rate | 3.6% | 3.6% | = +0.0 pts |
| Group accuracy | n/a | n/a | - |
| Latency p50 | 0.3474 ms | 0.3397 ms | ▼ -0.0077 ms (-2.2%) |
| Latency p95 | 1.0048 ms | 1.0295 ms | ▲ +0.0247 ms (+2.5%) |
| Latency p99 | 1.2767 ms | 1.3251 ms | ▲ +0.0484 ms (+3.8%) |
| Composite | 0.6196 | 0.6196 | ▼ -0.0000 |

▲ increased, ▼ decreased, = unchanged.
