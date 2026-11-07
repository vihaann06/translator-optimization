"""Compare online runs on a shared task set: success (overall, by site, hard/normal, task type), tokens, latency, cost.

    python -m scripts.compare_runs --tasks data/subset_v2.json baseline_s200 prune_flat_v2_s200
"""
import argparse
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def q(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else 0


def task_type(t):
    for e in t["eval"]:
        if e["evaluator"] == "AgentResponseEvaluator":
            return e.get("expected", {}).get("task_type", "?")
    return "?"


def load(run):
    d = ROOT / "runs" / run
    rep = json.loads((d / "report.json").read_text())
    summ = {r["task_id"]: r for r in map(json.loads, (d / "summary.jsonl").read_text().splitlines())}
    steps = [json.loads(l) for p in (d / "tasks").glob("*/steps.jsonl") for l in p.read_text().splitlines()]
    return {int(k): v for k, v in rep["scores"].items()}, summ, steps, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(ROOT / "data/subset_v2.json"))
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args()
    tasks = {t["task_id"]: t for t in json.loads(Path(a.tasks).read_text())}
    hard = {t["task_id"] for t in json.loads((ROOT / "data/hard.json").read_text())}
    groups = {}
    for tid, t in tasks.items():
        site = "multi" if len(t["sites"]) > 1 else t["sites"][0]
        groups[tid] = {"site": site, "difficulty": "hard" if tid in hard else "normal", "type": task_type(t)}
    data = {r: load(r) for r in a.runs}
    out = {}
    for run, (scores, summ, steps, rep) in data.items():
        usage = [s["usage"] for s in steps if s.get("usage")]
        pt = [u["prompt_tokens"] for u in usage]
        lat = [u["latency_s"] for u in usage]
        wall = sum(r["wall_s"] for r in summ.values())
        row = {"solved": int(sum(scores.values())), "n": len(scores), "success": round(sum(scores.values()) / len(scores), 3),
               "steps": len(steps), "hit_cap": sum(r.get("steps", 0) >= 30 for r in summ.values()),
               "prompt_tokens_total": sum(pt), "prompt_tokens_mean": round(st.mean(pt)), "prompt_tokens_median": q(pt, .5),
               "prompt_tokens_p90": q(pt, .9), "prompt_tokens_max": max(pt),
               "page_tokens_mean": round(st.mean(s["page_tokens"] for s in steps if "page_tokens" in s)),
               "truncated_pages": sum(bool(s.get("truncated")) for s in steps),
               "tokens_per_task": round(sum(pt) / len(scores)), "model_latency_mean": round(st.mean(lat), 3),
               "model_latency_p90": round(q(lat, .9), 3), "wall_per_step": round(wall / len(steps), 2),
               "wall_per_task": round(wall / len(summ), 1), "cost_usd": rep.get("cost_usd"), "runner_errors": rep.get("runner_errors")}
        for key in ("site", "difficulty", "type"):
            agg = defaultdict(lambda: [0, 0])
            for tid, sc in scores.items():
                agg[groups[tid][key]][0] += sc
                agg[groups[tid][key]][1] += 1
            row["by_" + key] = {k: f"{int(v[0])}/{v[1]}" for k, v in sorted(agg.items())}
        out[run] = row
    if len(a.runs) == 2:
        s1, s2 = data[a.runs[0]][0], data[a.runs[1]][0]
        common = sorted(set(s1) & set(s2))
        lost = [t for t in common if s1[t] > s2[t]]
        gained = [t for t in common if s2[t] > s1[t]]
        n = len(common)
        diff = (len(gained) - len(lost)) / n
        se = math.sqrt(((len(gained) + len(lost)) / n - diff ** 2) / n)
        # exact two-sided McNemar (binomial on discordant pairs)
        k, m = min(len(lost), len(gained)), len(lost) + len(gained)
        p = min(1.0, 2 * sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m) if m else 1.0
        out["paired"] = {"n": n, "lost": lost, "gained": gained, "diff_pts": round(100 * diff, 1),
                         "ci95_pts": [round(100 * (diff - 1.96 * se), 1), round(100 * (diff + 1.96 * se), 1)],
                         "mcnemar_p": round(p, 3)}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
