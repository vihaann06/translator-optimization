"""Compare translators offline: token cost, translator latency, element recall, replayed accuracy.

Static metrics (no model calls) cover every saved state of the reference run; replay metrics use the
chosen split, repeated --repeats times to average out sampling noise.
"""
import argparse
import asyncio
import gzip
import json
import statistics as st
import time
from pathlib import Path
from types import SimpleNamespace

from src import agent as agent_lib
from src import run_offline
from src.run_online import load_translator

ROOT = Path(__file__).resolve().parents[1]


def static_metrics(path, ref_dir):
    translate = load_translator(path)
    tokens, times, ids, recall, n_ref = [], [], [], 0, 0
    for steps in sorted((Path(ref_dir) / "tasks").glob("*/steps.jsonl")):
        task_dir = steps.parent
        recs = {r["step"]: r for r in map(json.loads, steps.read_text().splitlines())}
        for snap in sorted((task_dir / "snapshots").glob("step_*.json.gz")):
            with gzip.open(snap, "rt") as f:
                state = json.load(f)
            t0 = time.perf_counter()
            out = translate(state)
            times.append(time.perf_counter() - t0)
            tokens.append(len(agent_lib.ENC.encode(out["text"], disallowed_special=())))
            ids.append(len(out["id_map"]))
            rec = recs.get(int(snap.name[5:8]), {})
            if rec.get("wid") and rec.get("outcome") == "ok":
                n_ref += 1
                recall += rec["wid"] in set(out["id_map"].values())
    q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
    return {"pages": len(tokens), "mean_tokens": round(st.mean(tokens)), "median_tokens": q(tokens, 0.5),
            "p90_tokens": q(tokens, 0.9), "max_tokens": max(tokens), "over_40k": sum(t > 40000 for t in tokens),
            "mean_ids": round(st.mean(ids), 1), "translate_ms_mean": round(1000 * st.mean(times), 2),
            "translate_ms_p90": round(1000 * q(times, 0.9), 2),
            "ref_element_recall_all": round(recall / max(1, n_ref), 4)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("translators", nargs="+")
    p.add_argument("--reference", default=str(ROOT / "runs/baseline_v1"))
    p.add_argument("--out", default=str(ROOT / "runs/compare_v1"))
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--split", default="gold")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--static-only", action="store_true")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    for path in a.translators:
        name = Path(path).stem
        row = {"translator": path, **static_metrics(path, a.reference)}
        if not a.static_only:
            reps = []
            for r in range(a.repeats):
                args = SimpleNamespace(reference=a.reference, translator=path, out=str(out / name / f"{a.split}{r}"),
                                       split=a.split, model=a.model, task_source=str(ROOT / "data/hard.json"),
                                       max_page_tokens=40000, concurrency=8)
                rep = asyncio.run(run_offline.main_async(args))
                traces = [json.loads(l) for l in (Path(args.out) / "traces.jsonl").read_text().splitlines()]
                rep["prompt_tokens_mean"] = st.mean(t["usage"].get("prompt_tokens", 0) for t in traces)
                rep["latency_mean"] = st.mean(t["usage"].get("latency_s", 0) for t in traces)
                reps.append(rep)
            for key in ("step_accuracy", "element_accuracy", "ref_element_recall", "prompt_tokens_mean",
                        "latency_mean", "cost_usd"):
                vals = [r[key] for r in reps]
                row[f"{a.split}_{key}"] = round(st.mean(vals), 4)
                row[f"{a.split}_{key}_sd"] = round(st.pstdev(vals), 4)
        results.setdefault(name, {}).update(row)
        results_path.write_text(json.dumps(results, indent=2))
        print(json.dumps({"name": name, **{k: v for k, v in row.items() if not k.endswith("_sd") and k != "translator"}}),
              flush=True)


if __name__ == "__main__":
    main()
