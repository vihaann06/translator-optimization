"""Offline replay: score a translator on page states captured during an online run.

For every recorded step of a reference run, the candidate translator re-renders the saved page
state, the agent sees the task + the recorded action history, and its next action is compared to
the reference action. No browser or live site is needed.

Splits:
  gold  - steps from reference tasks that the WebArena-Verified evaluator scored as success
  all   - every executed step (agreement with the reference agent, a weaker signal)
"""
import argparse
import asyncio
import gzip
import json
import re
import time
from pathlib import Path

from src import agent as agent_lib
from src.run_online import load_translator, line_for

ROOT = Path(__file__).resolve().parents[1]


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def ancestors(wid, parents, depth=3):
    out = []
    while wid in parents and len(out) < depth:
        wid = parents[wid]
        out.append(wid)
    return out


def match(pred, pred_wid, ref, ref_wid, parents):
    """Returns (element_ok, full_ok) for the predicted vs reference action."""
    kind = ref["type"]
    if kind in agent_lib.ELEMENT_ACTIONS:
        element_ok = pred_wid is not None and (
            pred_wid == ref_wid or pred_wid in ancestors(ref_wid, parents) or ref_wid in ancestors(pred_wid, parents))
        same_kind = pred["type"] == kind or {pred["type"], kind} <= {"click", "check", "uncheck"}
        if kind == "type":
            same_kind = same_kind and norm(pred.get("text")) == norm(ref.get("text"))
        if kind == "select":
            same_kind = same_kind and norm(pred.get("option")) == norm(ref.get("option"))
        return element_ok, element_ok and same_kind
    if pred["type"] != kind:
        return None, False
    if kind == "stop":
        ok = norm(pred.get("task_type")) == norm(ref.get("task_type")) and \
            sorted(map(norm, pred.get("retrieved_data") or [])) == sorted(map(norm, ref.get("retrieved_data") or []))
        return None, ok
    if kind == "goto":
        return None, norm(pred.get("url")).rstrip("/") == norm(ref.get("url")).rstrip("/")
    if kind == "scroll":
        return None, pred.get("direction", "down") == ref.get("direction", "down")
    if kind == "press":
        return None, norm(pred.get("key")) == norm(ref.get("key"))
    return None, True


def load_steps(ref_dir, split):
    items = []
    for task_dir in sorted((ref_dir / "tasks").iterdir(), key=lambda p: int(p.name)):
        eval_path = task_dir / "eval_result.json"
        score = json.loads(eval_path.read_text()).get("score", 0) if eval_path.exists() else 0
        if split == "gold" and not score:
            continue
        for line in (task_dir / "steps.jsonl").read_text().splitlines():
            rec = json.loads(line)
            action = rec.get("action")
            if not action or (action["type"] != "stop" and rec.get("outcome") != "ok"):
                continue
            items.append({"task_id": int(task_dir.name), "task_dir": task_dir, "rec": rec, "ref_score": score})
    return items


async def score_step(item, tasks, sites_cfg, translate, model, args):
    rec, task_dir = item["rec"], item["task_dir"]
    task = tasks[item["task_id"]]
    with gzip.open(task_dir / "snapshots" / f"step_{rec['step']:03d}.json.gz", "rt") as f:
        state = json.load(f)
    t0 = time.time()
    rendered = translate(state)
    translate_s = time.time() - t0
    text, id_map = rendered["text"], rendered["id_map"]
    start_urls = json.loads((task_dir.parents[1] / "run_config.json").read_text()).get("start_urls_override") or \
        [__import__("src.tasks", fromlist=["resolve_url"]).resolve_url(u, sites_cfg) for u in task["start_urls"]]
    tabs = [(u, u == state["url"]) for u in state.get("tabs", [state["url"]])]
    messages, n_tokens, truncated = agent_lib.build_messages(
        task, sites_cfg, start_urls, state["url"], tabs, rec["history"], text, args.max_page_tokens)
    reply, usage = await model(messages)
    out = {"task_id": item["task_id"], "step": rec["step"], "url": state["url"], "ref_score": item["ref_score"],
           "ref_action": rec["action"], "ref_wid": rec.get("wid"), "page_tokens": n_tokens,
           "truncated": truncated, "translate_s": round(translate_s, 3), "usage": usage, "reply": reply}
    try:
        thought, pred = agent_lib.parse(reply)
    except Exception as e:
        out.update(error=f"parse: {e}", element_ok=False if rec.get("wid") else None, correct=False)
        return out
    pred_wid = id_map.get(pred.get("element")) if pred["type"] in agent_lib.ELEMENT_ACTIONS else None
    element_ok, correct = match(pred, pred_wid, rec["action"], rec.get("wid"), state.get("wid_parent", {}))
    ref_short = next((k for k, v in id_map.items() if v == rec.get("wid")), None)
    out.update(thought=thought, pred_action=pred, pred_wid=pred_wid, element_ok=element_ok, correct=correct,
               ref_in_representation=ref_short is not None if rec.get("wid") else None,
               pred_line=line_for(text, pred.get("element")) if pred_wid else None,
               ref_line=line_for(text, ref_short) if ref_short else None)
    return out


async def main_async(args):
    sites_cfg = json.loads((ROOT / "config/sites.json").read_text())["sites"]
    ref_dir = Path(args.reference)
    from src.tasks import load_tasks
    tasks = {t["task_id"]: t for t in load_tasks(args.task_source)}
    translate = load_translator(args.translator)
    model = agent_lib.Model(args.model)
    items = load_steps(ref_dir, args.split)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)

    async def one(item):
        async with sem:
            try:
                return await score_step(item, tasks, sites_cfg, translate, model, args)
            except Exception as e:
                rec = item["rec"]
                return {"task_id": item["task_id"], "step": rec["step"], "ref_score": item["ref_score"],
                        "ref_action": rec["action"], "ref_wid": rec.get("wid"), "page_tokens": 0, "truncated": False,
                        "usage": {"cost_usd": 0.0}, "error": f"runner: {e!r}"[:300],
                        "element_ok": False if rec.get("wid") else None, "correct": False}

    results = await asyncio.gather(*(one(i) for i in items))
    await model.client.close()
    with (out_dir / "traces.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    elem = [r for r in results if r["element_ok"] is not None]
    by_task = {}
    for r in results:
        by_task.setdefault(r["task_id"], []).append(r["correct"])
    report = {"translator": args.translator, "reference": str(ref_dir), "split": args.split, "model": args.model,
              "n_steps": len(results), "n_tasks": len(by_task),
              "step_accuracy": sum(r["correct"] for r in results) / max(1, len(results)),
              "element_accuracy": sum(bool(r["element_ok"]) for r in elem) / max(1, len(elem)),
              "ref_element_recall": sum(bool(r.get("ref_in_representation")) for r in elem) / max(1, len(elem)),
              "task_macro_accuracy": sum(sum(v) / len(v) for v in by_task.values()) / max(1, len(by_task)),
              "parse_errors": sum(1 for r in results if r.get("error")),
              "mean_page_tokens": sum(r["page_tokens"] for r in results) / max(1, len(results)),
              "cost_usd": round(sum(r["usage"]["cost_usd"] for r in results), 4)}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reference", required=True, help="online run directory with snapshots")
    p.add_argument("--translator", default=str(ROOT / "translators/baseline_axtree.py"))
    p.add_argument("--out", required=True)
    p.add_argument("--split", choices=["gold", "all"], default="gold")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--task-source", default=str(ROOT / "data/hard.json"))
    p.add_argument("--max-page-tokens", type=int, default=40000)
    p.add_argument("--concurrency", type=int, default=8)
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
