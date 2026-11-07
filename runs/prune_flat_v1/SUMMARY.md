# Online run: prune_flat vs baseline (40 WebArena-Verified tasks, gpt-4.1-mini, 8 parallel, max 30 steps)

| | baseline_v1 | prune_flat_v1 | change |
|---|---|---|---|
| success | 12/40 (30%) | 8/40 (20%) | -4 tasks (lost 165, 316, 349, 416, 750; gained 357) |
| by site (shop / admin / reddit / gitlab / multi) | 3 / 0 / 3 / 6 / 0 | 2 / 0 / 3 / 3 / 0 | gitlab -3 |
| steps | 516 (12.9/task, 10 hit 30) | 553 (13.8/task, 11 hit 30) | +7% |
| prompt tokens total | 3.56M | 1.60M | -55% |
| prompt tokens/step mean / p90 / max | 6,903 / 12,185 / 40,978 | 2,894 / 5,471 / 19,002 | -58% / -55% / -54% |
| pages truncated at 40k | 24 | 0 | |
| model latency/step mean / p90 | 1.08 s / 1.50 s | 0.91 s / 1.14 s | -16% / -24% |
| wall time/step | 2.65 s | 2.28 s | -14% |
| task wall time (sum) | 1,365 s | 1,260 s | -8% |
| cost | $1.47 | $0.69 | -53% |

Lost-task diagnosis:
- 165 (shopping, review titles): merge_text joined review body, author line and next review title into one line; agent returned body text as a title.
- 316 (gitlab): searchbox value repeated as a bare text line looked like a search result; agent clicked the searchbox 25 times.
- 750 (gitlab): flat output removed per-template grouping; every radio is "Use template", agent picked Pages/Jekyll instead of Plain HTML and looped.
- 349 (gitlab): output clearly marked "It's you @byteblaze"; agent still included itself (model error).
- 416 (gitlab): agent kept re-posting comments and lost track of which comment was last (behavioural loop).
