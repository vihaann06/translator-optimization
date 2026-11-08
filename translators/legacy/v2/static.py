"""Zero-LLM comparison: baseline vs v2 (full and change-only) page tokens, render time, reference-element recall."""
import gzip
import json
import statistics as st
import sys
import time
from pathlib import Path

from src.agent import ENC
from translators.baseline_axtree import translate as baseline
from translators.legacy.v2.translator import render


def tok(text):
    return len(ENC.encode(text, disallowed_special=()))


def main(ref="runs/baseline_v1", out="runs/v2_static/static.json"):
    rows = []
    for task_dir in sorted(Path(ref, "tasks").iterdir(), key=lambda p: int(p.name)):
        recs = {json.loads(l)["step"]: json.loads(l) for l in (task_dir / "steps.jsonl").read_text().splitlines()}
        memo = None
        for snap in sorted((task_dir / "snapshots").glob("step_*.json.gz")):
            step = int(snap.name[5:8])
            state = json.load(gzip.open(snap, "rt"))
            t0 = time.perf_counter(); b = baseline(state); tb = time.perf_counter() - t0
            t0 = time.perf_counter(); full = render(state); tf = time.perf_counter() - t0
            t0 = time.perf_counter(); diff = render(state, memo); td = time.perf_counter() - t0
            memo = diff["memo"]
            wid = recs.get(step, {}).get("wid")
            rows.append({"task": int(task_dir.name), "step": step, "base_tok": tok(b["text"]),
                         "full_tok": tok(full["text"]), "diff_tok": tok(diff["text"]),
                         "base_ms": tb * 1e3, "full_ms": tf * 1e3, "diff_ms": td * 1e3,
                         "nochange": "NO VISIBLE CHANGE" in diff["text"],
                         "ref_wid": wid, "recall": None if not wid else wid in full["id_map"].values(),
                         "visible_recall": None if not wid else any(
                             f"[{s}]" in full["text"] for s, w in full["id_map"].items() if w == wid)})
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rows))
    s = lambda k: sum(r[k] for r in rows)
    rec = [r for r in rows if r["recall"] is not None]
    print(f"snapshots={len(rows)}")
    for k in ("base_tok", "full_tok", "diff_tok"):
        print(f"{k}: total={s(k)} mean={s(k)/len(rows):.0f} median={st.median(r[k] for r in rows):.0f}")
    for k in ("base_ms", "full_ms", "diff_ms"):
        print(f"{k}: mean={s(k)/len(rows):.1f} p95={sorted(r[k] for r in rows)[int(.95*len(rows))]:.1f}")
    print(f"recall(id_map)={sum(r['recall'] for r in rec)/len(rec):.3f} visible_recall={sum(r['visible_recall'] for r in rec)/len(rec):.3f} n={len(rec)}")
    print(f"no-change steps={sum(r['nochange'] for r in rows)}")


if __name__ == "__main__":
    main(*sys.argv[1:])
