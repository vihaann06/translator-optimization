# Offline comparison of task-agnostic pruning translators (compare_v1)

Reference: `runs/baseline_v1` (40 WebArena-Verified tasks, gpt-4.1-mini). Static metrics over all 516 saved
states; replay = gpt-4.1-mini predicting the next action on saved states, gold = 95 steps of the 12
successful tasks (3 repeats, mean), all = 489 executed steps (1 repeat).

| translator | page tokens mean / p90 / max | IDs/page | gold step acc | gold element acc | all step acc | prompt tokens/step (all) | model latency (all) |
|---|---|---|---|---|---|---|---|
| baseline_axtree | 6991 / 11243 / 71753 | 478 | 73.3% | 83.3% | 81.6% | 6959 | 0.93 s |
| prune_basic | 3335 / 7834 / 30724 | 158 | 65.6% | 79.4% | - | - | - |
| prune_labels | 3407 / 7842 / 31930 | 158 | 67.0% | 79.8% | - | - | - |
| prune_tables | 2351 / 4678 / 21729 | 120 | 68.4% | 79.0% | 66.9% | 3278 | 0.89 s |
| prune_sections | 2239 / 4443 / 21393 | 117 | 66.3% | 77.6% | - | - | - |
| prune_flat | 2228 / 4426 / 21387 | 117 | 75.4% | 80.3% | 65.4% | 3151 | 0.89 s |
| prune_viewport | 937 / 1739 / 2632 | 54 | 69.1% | 82.5% | 60.3% | 1840 | 0.89 s |

Replay SD across repeats is 0.5-2.3 points. Caveats: replay scores agreement with the baseline agent's own
actions, and the baseline translator reproduces the exact prompt that produced them (81.6% self-agreement
on `all`), which biases replay towards the baseline; many disagreements pick an element with the same name.
Baseline replays also hit OpenAI's prompt cache (repeated identical prompts), so replay cost/latency
understate the baseline's online cost.
