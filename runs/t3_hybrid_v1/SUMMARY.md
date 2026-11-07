# Online run: T3 hybrid (ported from webpage-translator) vs baseline and pruning candidates

40 WebArena-Verified tasks, gpt-4.1-mini, 8 parallel, max 30 steps, sites reset before each run.

| | baseline_v1 | prune_flat_v1 | prune_flat_v2 | t3_hybrid_v1 |
|---|---|---|---|---|
| success | 12/40 (30%) | 8/40 (20%) | 10/40 (25%) | 10/40 (25%) |
| shop / admin / reddit / gitlab / multi | 3 / 0 / 3 / 6 / 0 | 2 / 0 / 3 / 3 / 0 | 3 / 1 / 2 / 4 / 0 | 4 / 0 / 2 / 4 / 0 |
| steps (hit 30-step cap) | 516 (10) | 553 (11) | 558 (11) | 516 (9) |
| prompt tokens total | 3.56M | 1.60M | 2.18M | 3.35M (-6%) |
| prompt tokens/step mean / p90 / max | 6,903 / 12,185 / 40,978 | 2,894 / 5,471 / 19,002 | 3,901 / 6,681 / 24,506 | 6,497 / 15,307 / 41,077 |
| pages truncated at 40k | 24 | 0 | 0 | 11 |
| model latency/step mean / p90 | 1.08 / 1.50 s | 0.91 / 1.14 s | 0.98 / 1.35 s | 1.06 / 1.69 s |
| wall time/step | 2.65 s | 2.28 s | 2.77 s | 3.36 s |
| cost | $1.47 | $0.69 | $0.92 | $1.39 |

Static (all 516 baseline pages): T3 3,823 tokens/page mean (-45% vs 6,991), 284 IDs/page (vs 478), max 38,462,
translator 40 ms mean / 85 ms p90 (vs 4 ms), recall of baseline targets 93.3%.

Same-page check on the 516 pages the T3 run visited: baseline 10,920 / T3 5,637 (-48%) / prune_flat_v2 3,144
(-71%) tokens per page. The T3 agent spent 187 of 516 steps on shopping_admin pages (baseline rendering: 23k
tokens each), which is why its run total is close to the baseline's. Non-model time per step was higher
(2.29 s vs 1.56 s); the translator accounts for ~0.04 s of that.

Task changes vs baseline: lost 316, 349, 636, 750; gained 357, 572.
- 316: explore-page search loop, clicked the search box until the step cap.
- 349: listed the current user ("Byte Blaze") along with yjlou.
- 636: posted in LifeProTips instead of the DIY forum (forum choice).
- 750: created the project correctly, ran out of steps in the invite-member modal.

Port notes: translators/t3/ is vendored from huskydj1/webpage-translator@f2d083c8 (passes + HybridSerializer
unchanged); translators/t3/state_ir.py builds the IR from the live capture (AX tree + data-wid join to the captured
HTML) instead of re-rendering HTML offline with JS disabled. "clickable" is approximated by an onclick attribute
or interactive role attribute (upstream used Mind2Web's is_clickable). Icon-font glyphs show up as `text ""`
lines and link children repeat the link name (e.g. `[3] link " DASHBOARD"` then `text "DASHBOARD"`), since
AbsorbText only removes exact-match children.
