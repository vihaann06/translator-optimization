# translator-optimization

Long-horizon follow-up to [webpage-translator](https://github.com/huskydj1/webpage-translator).
Same question (which page representation helps a web agent most?), but agents now solve whole
multi-step [WebArena-Verified](https://github.com/ServiceNow/webarena-verified) tasks on live,
self-hosted sites instead of predicting one Mind2Web step.

```
online baseline (live sites, slow)  ──►  per-step page states saved to disk
                                              │
            translator search loop  ◄─────────┘  offline replay (no browser, cheap)
                     │
                     └─► champion ──► online validation (live sites, task success)
```

## Setup

```bash
pip install uv && uv venv -p 3.12 && uv pip install -r requirements.txt
.venv/bin/playwright install chromium
export OPENAI_API_KEY=...
# sites (WebArena-Verified optimized images, ~91 GB of images total)
docker run -d --name webarena-verified-shopping       -p 7770:80   -p 7771:8877 am1n3e/webarena-verified-shopping:0.1.0
docker run -d --name webarena-verified-shopping_admin -p 7780:80   -p 7781:8877 am1n3e/webarena-verified-shopping_admin:0.1.0
docker run -d --name webarena-verified-reddit         -p 9999:80   -p 9998:8877 am1n3e/webarena-verified-reddit:0.1.0
docker run -d --name webarena-verified-gitlab         -p 8023:8023 -p 8024:8877 am1n3e/webarena-verified-gitlab:0.1.0
```
Reset state between online runs by recreating the containers (`docker rm -f ... && docker run ...`).
Map and Wikipedia are not hosted; tasks needing them are excluded.

## Layout

| path | what |
|---|---|
| `translators/baseline_axtree.py` | baseline: Chromium AX tree outline (same renderer as webpage-translator) |
| `src/browser.py` | page-state capture (tags every element with `data-wid`) and action execution |
| `src/agent.py` | fixed, representation-agnostic agent prompt, OpenAI call, action grammar |
| `src/run_online.py` | parallel live runs + WebArena-Verified evaluation |
| `src/run_offline.py` | offline replay of saved states against reference actions |
| `src/search.py` | autonomous loop: diagnose offline failures → propose translator → score → accept |
| `src/tasks.py` | stratified long-horizon subset (`data/subset_v1.json`, 40 tasks) |
| `data/hard.json` | WebArena-Verified hard subset (258 tasks) |

Translator contract: `translate(state) -> {"text": str, "id_map": {short_id: wid}}`.
`state` holds the URL, tabs, viewport, CDP accessibility tree, `wid_by_backend`, `wid_parent`, element
bounds and live HTML (`page.content()` plus `data-live-value/checked/selected`), so a translator can
work from the AX tree, the HTML, or both.

## Commands

```bash
# 1. online baseline (writes runs/baseline_v1/: logs, HAR, snapshots, eval results, report.json)
.venv/bin/python -m src.run_online --run baseline_v1 --tasks data/subset_v1.json --concurrency 8 --max-steps 30
# 2. offline replay of any translator against the saved baseline states
.venv/bin/python -m src.run_offline --reference runs/baseline_v1 --translator translators/baseline_axtree.py \
    --out runs/offline_baseline --split gold     # or --split all
# 3. autonomous search (gpt-4.1 proposes, gpt-4.1-mini reads), budget-capped
.venv/bin/python -m src.search --name s1 --reference runs/baseline_v1 --iterations 5 --max-cost 10
# 4. online validation of a champion
.venv/bin/python -m src.run_online --run champ_v1 --translator runs/search_s1/candidates/iterNN.py
```

### Per-task outputs (`runs/<run>/tasks/<task_id>/`)
`agent_response.json`, `network.har` (WebArena-Verified inputs), `eval_result.json`, `steps.jsonl`
(reply, parsed action, target `wid`, outcome, token usage, history shown to the agent),
`snapshots/step_XXX.json.gz` (full page state), `translations/step_XXX.txt.gz` (text the agent saw),
`screenshots/step_XXX.jpg`. Large artifacts are git-ignored; ship them as a release tarball.

## Design choices

- **Tasks:** 40 tasks from the hard subset: shopping 10, shopping_admin 10, reddit 8, gitlab 10,
  multi-site 2; round-robin over RETRIEVE/MUTATE/NAVIGATE, one task per intent template
  (`python -m src.tasks` to regenerate). With 8 tasks in parallel, one online iteration is roughly
  15–25 minutes; offline replay of the same states takes a few minutes.
- **Parallelism vs. shared state:** tasks that mutate a site take a per-site lock, so at most one
  mutating task runs on each site at a time; read-only tasks run freely (`--no-serialize-mutations`
  to disable).
- **Offline scoring:** `gold` = steps from baseline tasks the evaluator scored as success;
  `all` = agreement with every executed baseline step. Element matches are lenient
  (same `wid`, or ancestor/descendant within 3 levels). The history shown at each step is the
  reference run's history, rendered with the baseline translator's labels.
- **Limits of offline replay:** it only covers states the reference agent visited and cannot score
  recovery from a different path, so champions must be confirmed online.
