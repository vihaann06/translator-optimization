# v2 compact translator: offline replay vs baseline

Reference: `runs/baseline_v1` (40 tasks, 489 scored steps, 95 of them from the 12 successful tasks).
Model: gpt-4.1-mini, temperature 0. Both arms go through the same runner (`v2/offline.py`).
Scoring = does the agent pick the same next action the baseline agent recorded (biased toward the baseline).

| metric | baseline | v2 (full interface) | v2, no change-only | v2, representation only |
|---|---|---|---|---|
| step accuracy (all 489) | 80.2% | 53.2% | 54.6% | 52.8% |
| step accuracy (95 gold steps) | 75.8% | 66.3% | 68.4% | 64.2% |
| element accuracy | 82.9% | 59.1% | 63.0% | 58.3% |
| ref element present in representation | 100% | 91.3% | 91.3% | 91.3% |
| mean page tokens / step | 7,028 | 1,145 | 1,478 | 1,427 |
| mean prompt tokens / step (incl. tool calls) | 6,959 | 2,797 | 3,206 | 2,665 |
| total prompt tokens | 3.40M | 1.37M | 1.57M | 1.30M |
| LLM calls | 489 | 520 | 522 | 489 |
| mean / median / p90 LLM latency per step | 1.37 / 0.89 / 1.75 s | 0.99 / 0.84 / 1.37 s | 0.95 / 0.87 / 1.22 s | 0.86 / 0.83 / 1.06 s |
| translator time / step | 4 ms | 16 ms | 17 ms | 16 ms |
| cost | $1.12 | $0.58 | $0.64 | $0.56 |

Static (no LLM, 516 snapshots, `v2/static.py`): page tokens 3.61M baseline -> 0.72M v2 full (-80%) -> 0.55M change-only (-85%).

Reproduce: `python -m v2.static`; `python -m v2.offline --mode baseline|v2 [--no-diff --no-tools --no-visited] --out runs/v2_offline/<name>`.
