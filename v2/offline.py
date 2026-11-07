"""Offline replay for the v2 interface (separate from src/run_offline.py).

Same scoring as src/run_offline.py (predicted next action vs the recorded baseline action, on archived
snapshots), plus what the v2 interface needs: change-only renders chained across each task's snapshots,
history re-labelled with v2 IDs, and local query/expand tools answered in-loop (their extra LLM calls are
counted). --mode baseline runs the baseline translator through the same loop for an apples-to-apples control.
"""
import argparse
import asyncio
import gzip
import json
import time
from pathlib import Path

from src import agent as agent_lib
from src.run_offline import load_steps, match
from src.run_online import line_for
from src.tasks import load_tasks, resolve_url
from translators.baseline_axtree import translate as baseline_translate
from v2 import tools
from v2.translator import render

ROOT = Path(__file__).resolve().parents[1]
MAX_TOOL_CALLS = 3

V2_LEGEND = """

Page format (compact): [N] marks an element you can act on. →/path is a link target on this site; "↗external
host" marks a link that leaves the task sites. Lines starting with §N name sections (navigation, main, forms...).
Tables and repeated items are shown as "collection C1 (K rows)" with one row per line, cells separated by " | ";
large collections show only their first rows. After an in-page action, sections that did not change are shown
as one line "§N ...: unchanged" (their elements still exist and keep their IDs); "NO VISIBLE CHANGE" means your
previous action did not change the page. NOTICE lines are alerts/status messages.
Extra actions (answered instantly, do not change the page; you then choose again):
- {"type": "query", "collection": "C1", "contains": "<text>" or null, "where": [["<column>", "=|!=|<|>|<=|>=|contains", "<value>"]],
   "count": false, "sum": "<column>" or null, "group_by": "<column>" or null, "offset": 0, "limit": 20}
   (searches/counts/sums the rows loaded on this page; use it instead of reading long tables)
- {"type": "expand", "block": "<N or C1>"}   (show a collapsed section or all rows of a collection)
- {"type": "fill", "fields": {"<id>": "<text or option>", ...}, "submit": "<id>" or null}   (fill several fields at once)"""


def pre_render(task_dir, mode, diff=True):
    renders, memo = {}, None
    for snap in sorted((task_dir / "snapshots").glob("step_*.json.gz")):
        state = json.load(gzip.open(snap, "rt"))
        t0 = time.perf_counter()
        if mode == "v2":
            out = render(state, memo if diff else None)
            memo = out["memo"]
        else:
            out = baseline_translate(state)
        out["translate_s"] = time.perf_counter() - t0
        renders[int(snap.name[5:8])] = (state, out)
    return renders


def v2_history(recs, renders, upto):
    hist = []
    for r in recs:
        if r["step"] >= upto:
            break
        action = r.get("action")
        if not action:
            hist.append(f"(invalid reply: {r.get('error', '')})")
            continue
        if action["type"] in agent_lib.ELEMENT_ACTIONS and r.get("wid") and r["step"] in renders:
            text, id_map = renders[r["step"]][1]["text"], renders[r["step"]][1]["id_map"]
            sid = next((k for k, v in id_map.items() if v == r["wid"]), None)
            desc = agent_lib.describe(dict(action, element=sid or "?"), line_for(text, sid) if sid else None)
        else:
            desc = agent_lib.describe(action)
        outcome = r.get("outcome")
        hist.append(desc + ("" if outcome in ("ok", None) else f"  -> {outcome}"))
    return hist


def parse_any(reply):
    try:
        obj = json.loads(reply)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("action"), dict) and obj["action"].get("type") in ("query", "expand", "fill"):
        return obj.get("thought", ""), obj["action"]
    return agent_lib.parse(reply)


def resolve_fill(pred, ref, id_map, parents):
    """Map a fill action onto the single recorded step it covers (for scoring)."""
    fields = {str(k).strip("[] "): v for k, v in (pred.get("fields") or {}).items()}
    for sid, value in fields.items():
        wid = id_map.get(sid)
        if wid and ref.get("type") in ("type", "select") and match({"type": ref["type"]}, wid, ref, ref.get("wid"), parents)[0]:
            return {"type": ref["type"], "element": sid, "text": value, "option": value}
    submit = str(pred.get("submit") or "").strip("[] ")
    if submit and ref.get("type") == "click":
        return {"type": "click", "element": submit}
    if fields:
        sid, value = next(iter(fields.items()))
        return {"type": "type", "element": sid, "text": value}
    return {"type": "noop"}


async def score_step(item, task, sites_cfg, renders, recs, model, args):
    rec, task_dir = item["rec"], item["task_dir"]
    state, out = renders[rec["step"]]
    text, id_map = out["text"], out["id_map"]
    start_urls = json.loads((task_dir.parents[1] / "run_config.json").read_text()).get("start_urls_override") or \
        [resolve_url(u, sites_cfg) for u in task["start_urls"]]
    tabs = [(u, u == state["url"]) for u in state.get("tabs", [state["url"]])]
    history = rec["history"] if args.mode == "baseline" else v2_history(recs, renders, rec["step"])
    page = text
    if args.mode == "v2" and not args.no_visited:
        visited = []
        for r in recs:
            if r["step"] >= rec["step"]:
                break
            if r.get("url") and r["url"] not in visited:
                visited.append(r["url"])
        if visited:
            page = "(pages visited so far: " + ", ".join(visited[-12:]) + ")\n" + text
    messages, n_tokens, truncated = agent_lib.build_messages(
        task, sites_cfg, start_urls, state["url"], tabs, history, page, args.max_page_tokens)
    if args.mode == "v2":
        legend = V2_LEGEND if not args.no_tools else V2_LEGEND.split("\nExtra actions")[0]
        messages[0]["content"] = agent_lib.SYSTEM + legend
    calls, tool_log = [], []
    result = {"task_id": item["task_id"], "step": rec["step"], "url": state["url"], "ref_score": item["ref_score"],
              "ref_action": rec["action"], "ref_wid": rec.get("wid"), "page_tokens": n_tokens, "truncated": truncated,
              "translate_s": round(out["translate_s"], 4), "nochange": "NO VISIBLE CHANGE" in text}
    pred = thought = None
    for i in range(MAX_TOOL_CALLS + 1):
        reply, usage = await model(messages)
        calls.append(usage)
        try:
            thought, action = parse_any(reply)
        except Exception as e:
            result.update(error=f"parse: {e}", reply=reply)
            break
        if action["type"] in ("query", "expand") and args.mode == "v2":
            fn = tools.query if action["type"] == "query" else tools.expand
            try:
                answer = fn(out, **{k: v for k, v in action.items() if k != "type"})
            except Exception as e:
                answer = f"{action['type']} error: {e}"
            tool_log.append({"action": action, "answer": answer[:2000]})
            follow = "\nNow reply with your next action (JSON)." if i < MAX_TOOL_CALLS - 1 else \
                "\nTool limit reached: reply with a browser action or stop now (no more query/expand)."
            messages = messages + [{"role": "assistant", "content": reply},
                                   {"role": "user", "content": f"## Result of {action['type']}\n{answer}{follow}"}]
            continue
        pred = action
        result["reply"] = reply
        break
    parents = state.get("wid_parent", {})
    result.update(calls=len(calls), tools=tool_log,
                  prompt_tokens=sum(c["prompt_tokens"] for c in calls),
                  cached_tokens=sum(c["cached_tokens"] for c in calls),
                  completion_tokens=sum(c["completion_tokens"] for c in calls),
                  latency_s=round(sum(c["latency_s"] for c in calls), 3),
                  cost_usd=sum(c["cost_usd"] for c in calls))
    ref_short = next((k for k, v in id_map.items() if v == rec.get("wid")), None)
    result["ref_in_representation"] = (ref_short is not None) if rec.get("wid") else None
    if pred is None or pred["type"] in ("query", "expand"):
        result.update(error=result.get("error") or "no browser action", element_ok=False if rec.get("wid") else None,
                      correct=False)
        return result
    if pred["type"] == "fill":
        result["fill"] = pred
        pred = resolve_fill(pred, dict(rec["action"], wid=rec.get("wid")), id_map, parents)
    pred_wid = id_map.get(str(pred.get("element"))) if pred["type"] in agent_lib.ELEMENT_ACTIONS else None
    element_ok, correct = match(pred, pred_wid, rec["action"], rec.get("wid"), parents)
    result.update(thought=thought, pred_action=pred, pred_wid=pred_wid, element_ok=element_ok, correct=correct,
                  pred_line=line_for(text, pred.get("element")) if pred_wid else None,
                  ref_line=line_for(text, ref_short) if ref_short else None)
    return result


def summarize(results, args):
    n = max(1, len(results))
    elem = [r for r in results if r.get("element_ok") is not None]
    gold = [r for r in results if r["ref_score"]]
    tot = lambda k, rs=results: sum(r.get(k, 0) or 0 for r in rs)
    lat = sorted(r.get("latency_s", 0) for r in results)
    return {"mode": args.mode, "no_diff": args.no_diff, "no_visited": args.no_visited, "no_tools": args.no_tools,
            "model": args.model, "n_steps": len(results),
            "n_tasks": len({r["task_id"] for r in results}),
            "step_accuracy": tot("correct") / n,
            "gold_step_accuracy": tot("correct", gold) / max(1, len(gold)), "n_gold_steps": len(gold),
            "element_accuracy": sum(bool(r["element_ok"]) for r in elem) / max(1, len(elem)),
            "ref_element_recall": sum(bool(r.get("ref_in_representation")) for r in elem) / max(1, len(elem)),
            "errors": sum(1 for r in results if r.get("error")),
            "mean_page_tokens": tot("page_tokens") / n, "total_prompt_tokens": tot("prompt_tokens"),
            "mean_prompt_tokens": tot("prompt_tokens") / n, "total_completion_tokens": tot("completion_tokens"),
            "cached_tokens": tot("cached_tokens"), "llm_calls": tot("calls"),
            "steps_with_tools": sum(1 for r in results if r.get("tools")),
            "mean_llm_latency_s": tot("latency_s") / n, "median_llm_latency_s": lat[len(lat) // 2] if lat else 0,
            "p90_llm_latency_s": lat[int(.9 * len(lat))] if lat else 0,
            "mean_translate_ms": 1000 * tot("translate_s") / n, "cost_usd": round(tot("cost_usd"), 4)}


async def main_async(args):
    sites_cfg = json.loads((ROOT / "config/sites.json").read_text())["sites"]
    tasks = {t["task_id"]: t for t in load_tasks(args.task_source)}
    items = load_steps(Path(args.reference), args.split)
    if args.tasks:
        keep = {int(x) for x in args.tasks.split(",")}
        items = [i for i in items if i["task_id"] in keep]
    renders, recs = {}, {}
    for tid in sorted({i["task_id"] for i in items}):
        task_dir = Path(args.reference) / "tasks" / str(tid)
        renders[tid] = pre_render(task_dir, args.mode, diff=not args.no_diff)
        recs[tid] = [json.loads(l) for l in (task_dir / "steps.jsonl").read_text().splitlines()]
    model = agent_lib.Model(args.model)
    sem = asyncio.Semaphore(args.concurrency)

    async def one(item):
        async with sem:
            try:
                return await score_step(item, tasks[item["task_id"]], sites_cfg, renders[item["task_id"]],
                                        recs[item["task_id"]], model, args)
            except Exception as e:
                rec = item["rec"]
                return {"task_id": item["task_id"], "step": rec["step"], "ref_score": item["ref_score"],
                        "ref_action": rec["action"], "ref_wid": rec.get("wid"), "page_tokens": 0,
                        "error": f"runner: {e!r}"[:300], "element_ok": False if rec.get("wid") else None,
                        "correct": False}

    results = await asyncio.gather(*(one(i) for i in items))
    await model.client.close()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "traces.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    report = summarize(results, args)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reference", default=str(ROOT / "runs/baseline_v1"))
    p.add_argument("--mode", choices=["v2", "baseline"], default="v2")
    p.add_argument("--out", required=True)
    p.add_argument("--split", choices=["gold", "all"], default="all")
    p.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--task-source", default=str(ROOT / "data/hard.json"))
    p.add_argument("--max-page-tokens", type=int, default=40000)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--no-diff", action="store_true", help="v2: always render the full page (no change-only)")
    p.add_argument("--no-visited", action="store_true", help="v2: omit the visited-pages line")
    p.add_argument("--no-tools", action="store_true", help="v2: do not offer query/expand/fill")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
