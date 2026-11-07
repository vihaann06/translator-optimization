# Online run: baseline vs v2 (40 tasks, data/subset_v1.json, gpt-4.1-mini, max 30 steps, concurrency 8)

Sites reset (`scripts/reset_sites.sh`) before each run. Baseline: `src.run_online` + `translators/baseline_axtree.py`
(run `baseline_v2_online`). v2: `v2.online` (change-only renders, visited pages, local query/expand, fill) (run `v2_online`).

| | Baseline | v2 |
|---|---|---|
| Task success | 10/40 (25.0%) | 11/40 (27.5%) |
| Steps per task | 12.6 | 14.8 |
| Tasks hitting 30-step cap | 10 | 13 |
| Prompt tokens per task | 90,972 | 42,200 (-54%) |
| Prompt tokens per step | 7,205 | 2,861 (-60%) |
| Page tokens per step | 7,449 | 902 (-88%) |
| LLM calls | 505 | 685 (95 query/expand, 37 fill) |
| LLM latency per call | 1.00s | 0.92s |
| LLM time per task | 12.6s | 15.7s |
| Wall time per task | 39.5s | 46.3s |
| Failed browser actions | 17 | 50 |
| Cost | $1.43 | $0.70 (-51%) |

Solved by both (8): 44 325 357 399 489 520 580 658. Baseline only: 25, 416. v2 only: 165, 349, 451.
On the 8 tasks both solved: steps 63 -> 50, prompt tokens 239k -> 110k, wall 180s -> 153s.
Earlier baseline_v1 run of the same tasks: 12/40, so run-to-run noise is about +/-2 tasks.

v2 failure modes seen: query on a collection that wasn't detected (task 25: reviews, 25 failed queries + scrolls);
fill keyed by field names instead of IDs, retried 23 times (615); repeated same click despite change-only output (587).
