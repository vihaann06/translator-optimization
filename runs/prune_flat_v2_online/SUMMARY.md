# Online run: prune_flat_v2 vs baseline and prune_flat (40 tasks, gpt-4.1-mini, 8 parallel, max 30 steps)

| | baseline_v1 | prune_flat_v1 | prune_flat_v2 |
|---|---|---|---|
| success | 12/40 (30%) | 8/40 (20%) | 10/40 (25%) |
| shop / admin / reddit / gitlab / multi | 3 / 0 / 3 / 6 / 0 | 2 / 0 / 3 / 3 / 0 | 3 / 1 / 2 / 4 / 0 |
| steps (hit 30-step cap) | 516 (10) | 553 (11) | 558 (11) |
| prompt tokens total | 3.56M | 1.60M (-55%) | 2.18M (-39%) |
| prompt tokens/step mean / p90 / max | 6,903 / 12,185 / 40,978 | 2,894 / 5,471 / 19,002 | 3,901 / 6,681 / 24,506 |
| pages truncated at 40k | 24 | 0 | 0 |
| model latency/step mean / p90 | 1.08 / 1.50 s | 0.91 / 1.14 s | 0.98 / 1.35 s |
| wall time/step | 2.65 s | 2.28 s | 2.77 s |
| cost | $1.47 | $0.69 | $0.92 |

Same-page token check (all three translators re-rendered on the pages each run actually visited):
- pages from the v2 run: baseline 9,214 / v1 2,800 / v2 2,840 tokens per page (v2 -69% vs baseline)
- pages from the v1 run: baseline 5,545 / v1 1,857 / v2 1,904
So v2 costs ~1.5% more than v1 on identical pages; the higher run total comes from the v2 agent visiting bigger
pages (shopping_admin, long merge-request threads). Non-model time per step was also higher in this run
(median 1.22 s vs 0.96 s baseline) with the disk 97% full; the translator itself takes ~3 ms.

Task changes vs baseline: lost 316, 416, 636, 750; gained 357, 489. Vs v1: gained 165, 349, 489; lost 636.
- 165 (review titles) and 349 now pass; 750 now creates the project with the right template but runs out of
  steps before adding the member.
- 316: agent searched only its own projects, found nothing and stopped with NOT_FOUND (path variance).
- 416: same loop as v1: it keeps posting comments, so the "last comment" keeps changing (behavioural).
- 636 (reddit): forum picker is a JS combobox; the agent issued `select` 21 times, which Playwright rejects
  ("Element is not a <select> element"), instead of clicking the visible "DIY" option (harness/action issue).
