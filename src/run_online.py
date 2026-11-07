"""Online run: agent + translator against live WebArena-Verified sites.

Per task writes runs/<run>/tasks/<task_id>/:
  agent_response.json, network.har   (WebArena-Verified evaluation inputs)
  steps.jsonl                         (one record per step: action, reply, usage, outcome)
  snapshots/step_XXX.json.gz          (captured page state: AX tree, live HTML, wid maps, bounds)
  translations/step_XXX.txt.gz        (exact page text shown to the agent)
  screenshots/step_XXX.jpg
  eval_result.json                    (after evaluation)
"""
import argparse
import asyncio
import subprocess
import gzip
import importlib.util
import json
import time
import traceback
from pathlib import Path

from playwright.async_api import async_playwright

from src import agent as agent_lib
from src.browser import capture, execute
from src.tasks import load_tasks, resolve_url

ROOT = Path(__file__).resolve().parents[1]


def load_translator(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.translate


def line_for(text, short_id):
    tag = f"[{short_id}] "
    for line in text.splitlines():
        if tag in line:
            return line.strip()[:160]
    return None


def write_gz(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(data)


async def gitlab_login(browser, cfg, path):
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(cfg["url"] + "/users/sign_in")
    await page.get_by_label("Username or email").fill(cfg["user"])
    await page.get_by_label("Password").fill(cfg["password"])
    await page.get_by_role("button", name="Sign in").click()
    await page.wait_for_load_state("domcontentloaded")
    path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(path))
    await context.close()


async def run_task(browser, task, args, sites_cfg, translate, model, out_dir, storage):
    task_dir = out_dir / "tasks" / str(task["task_id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    headers = {cfg["header"]: f"{cfg['user']}:{cfg['password']}"
               for site, cfg in sites_cfg.items() if site in task["sites"] and cfg.get("header")}
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720}, extra_http_headers=headers,
        record_har_path=str(task_dir / "network.har"), record_har_content="embed",
        storage_state=str(storage) if storage and "gitlab" in task["sites"] else None)
    context.set_default_timeout(15000)
    start_urls = [resolve_url(u, sites_cfg) for u in task["start_urls"]]
    page = await context.new_page()
    for extra in start_urls[1:]:
        p = await context.new_page()
        await p.goto(extra, timeout=60000)
    await page.bring_to_front()
    await page.goto(start_urls[0], timeout=60000)
    active = {"page": page}
    context.on("page", lambda p: active.update(page=p))
    cdps = {}
    history, response, steps_log = [], None, task_dir / "steps.jsonl"
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "steps": 0}
    parse_failures = 0
    steps_log.write_text("")
    for step in range(args.max_steps):
        page = active["page"]
        if page.is_closed():
            page = active["page"] = context.pages[-1]
        if page not in cdps:
            cdps[page] = await context.new_cdp_session(page)
        record = {"step": step, "time": time.time()}
        try:
            await page.wait_for_load_state("load", timeout=10000)
        except Exception:
            pass
        try:
            state = await capture(page, cdps[page])
        except Exception as e:
            await asyncio.sleep(2)
            cdps[page] = await context.new_cdp_session(page)
            state = await capture(page, cdps[page])
        state["tabs"] = [p.url for p in context.pages]
        rendered = translate(state)
        for _ in range(3):
            if len(rendered["text"]) >= 200:
                break
            await asyncio.sleep(2)
            state = await capture(page, cdps[page])
            state["tabs"] = [p.url for p in context.pages]
            rendered = translate(state)
        text, id_map = rendered["text"], rendered["id_map"]
        write_gz(task_dir / "snapshots" / f"step_{step:03d}.json.gz", state)
        write_gz(task_dir / "translations" / f"step_{step:03d}.txt.gz", text)
        try:
            await page.screenshot(path=str(task_dir / "screenshots" / f"step_{step:03d}.jpg"),
                                  type="jpeg", quality=50, timeout=10000)
        except Exception:
            pass
        tabs = [(p.url, p == page) for p in context.pages]
        messages, n_tokens, truncated = agent_lib.build_messages(
            task, sites_cfg, start_urls, page.url, tabs, history, text, args.max_page_tokens)
        reply, usage = await model(messages)
        for key in ("prompt_tokens", "completion_tokens", "cost_usd"):
            totals[key] += usage[key]
        totals["steps"] = step + 1
        record.update(url=page.url, page_tokens=n_tokens, truncated=truncated, reply=reply, usage=usage,
                      history=list(history))
        try:
            thought, action = agent_lib.parse(reply)
        except Exception as e:
            parse_failures += 1
            record.update(error=f"parse: {e}")
            history.append(f"(invalid reply: {e})")
            with steps_log.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if parse_failures >= 3:
                break
            continue
        record.update(thought=thought, action=action)
        if action["type"] == "stop":
            response = {k: action.get(k) for k in ("task_type", "status", "retrieved_data", "error_details")}
            history.append(agent_lib.describe(action))
            with steps_log.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            break
        wid = None
        label = None
        if action["type"] in agent_lib.ELEMENT_ACTIONS:
            label = line_for(text, action["element"])
            wid = id_map.get(action["element"])
            record["wid"] = wid
        try:
            if action["type"] in agent_lib.ELEMENT_ACTIONS and wid is None:
                raise ValueError(f"element [{action['element']}] is not on the current page")
            if action["type"] == "tab_focus":
                active["page"] = context.pages[int(action["index"])]
                await active["page"].bring_to_front()
                outcome = "ok"
            else:
                outcome = await asyncio.wait_for(execute(page, action, wid), timeout=60)
        except Exception as e:
            outcome = f"error: {str(e).splitlines()[0][:200]}"
        record["outcome"] = outcome
        desc = agent_lib.describe(action, label)
        history.append(desc + ("" if outcome == "ok" else f"  -> {outcome}"))
        with steps_log.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if response is None:
        response = {"task_type": None, "status": "UNKNOWN_ERROR", "retrieved_data": None,
                    "error_details": "agent did not stop within the step budget"}
        totals["stopped"] = False
    else:
        totals["stopped"] = True
    (task_dir / "agent_response.json").write_text(json.dumps(response, indent=2, ensure_ascii=False))
    await context.close()
    return totals


def evaluate(out_dir, task_ids, eval_config):
    from webarena_verified.api.webarena_verified import WebArenaVerified
    wa = WebArenaVerified(config=Path(eval_config))
    results = {}
    for task_id in task_ids:
        task_dir = out_dir / "tasks" / str(task_id)
        try:
            response = json.loads((task_dir / "agent_response.json").read_text())
            result = wa.evaluate_task(task_id=task_id, agent_response=response,
                                      network_trace=task_dir / "network.har")
            data = json.loads(result.model_dump_json())
        except Exception as e:
            data = {"status": "error", "score": 0.0, "error_msg": repr(e)}
        (task_dir / "eval_result.json").write_text(json.dumps(data, indent=2, default=str))
        results[task_id] = float(data.get("score") or 0.0)
    return results


def is_mutating(task):
    return any(e.get("expected", {}).get("task_type") == "MUTATE"
               for e in task["eval"] if e["evaluator"] == "AgentResponseEvaluator")


async def main_async(args):
    sites_cfg = json.loads((ROOT / "config/sites.json").read_text())["sites"]
    tasks = load_tasks(args.tasks)
    if args.limit:
        tasks = tasks[:args.limit]
    out_dir = ROOT / "runs" / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_config.json").write_text(json.dumps(vars(args), indent=2))
    translate = load_translator(args.translator)
    model = agent_lib.Model(args.model)
    sem = asyncio.Semaphore(args.concurrency)
    site_locks = {site: asyncio.Lock() for site in sites_cfg}
    summary_path = out_dir / "summary.jsonl"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        storage = None
        if any("gitlab" in t["sites"] for t in tasks):
            storage = ROOT / "config/.auth/gitlab.json"
            await gitlab_login(browser, sites_cfg["gitlab"], storage)

        async def one(task):
            mutating = is_mutating(task)
            locks = [site_locks[s] for s in sorted(task["sites"])] if mutating and args.serialize_mutations else []
            for lock in locks:
                await lock.acquire()
            async with sem:
                started = time.time()
                try:
                    totals = await asyncio.wait_for(
                        run_task(browser, task, args, sites_cfg, translate, model, out_dir, storage),
                        timeout=args.task_timeout)
                    totals["error"] = None
                except Exception as e:
                    totals = {"error": repr(e), "traceback": traceback.format_exc()[-2000:]}
                    task_dir = out_dir / "tasks" / str(task["task_id"])
                    if not (task_dir / "agent_response.json").exists():
                        (task_dir / "agent_response.json").write_text(json.dumps(
                            {"task_type": None, "status": "UNKNOWN_ERROR", "retrieved_data": None,
                             "error_details": "runner error"}))
                totals.update(task_id=task["task_id"], sites=task["sites"], wall_s=round(time.time() - started, 1))
                with summary_path.open("a") as f:
                    f.write(json.dumps(totals) + "\n")
                print(json.dumps({k: totals.get(k) for k in ("task_id", "steps", "cost_usd", "wall_s", "stopped", "error")}),
                      flush=True)
            for lock in reversed(locks):
                lock.release()

        if args.read_only_first:
            await asyncio.gather(*(one(t) for t in tasks if not is_mutating(t)))
            await asyncio.gather(*(one(t) for t in tasks if is_mutating(t)))
        else:
            await asyncio.gather(*(one(t) for t in tasks))
        await browser.close()
    scores = evaluate(out_dir, [t["task_id"] for t in tasks], args.eval_config)
    rows = [json.loads(l) for l in summary_path.read_text().splitlines()]
    report = {"run": args.run, "translator": args.translator, "model": args.model, "n_tasks": len(tasks),
              "success_rate": sum(scores.values()) / max(1, len(scores)),
              "scores": scores,
              "cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 4),
              "mean_steps": sum(r.get("steps", 0) for r in rows) / max(1, len(rows)),
              "runner_errors": [r["task_id"] for r in rows if r.get("error")]}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    if args.gzip_har:
        for har in (out_dir / "tasks").glob("*/network.har"):
            subprocess.run(["gzip", "-1", "-f", str(har)], check=False)
    print(json.dumps({k: v for k, v in report.items() if k != "scores"}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--tasks", default=str(ROOT / "data/subset_v1.json"))
    parser.add_argument("--translator", default=str(ROOT / "translators/baseline_axtree.py"))
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-page-tokens", type=int, default=40000)
    parser.add_argument("--task-timeout", type=float, default=1200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--serialize-mutations", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--read-only-first", action=argparse.BooleanOptionalAction, default=False,
                        help="run all non-mutating tasks before any mutating task, so mutations cannot change their answers")
    parser.add_argument("--gzip-har", action=argparse.BooleanOptionalAction, default=True,
                        help="gzip network.har after evaluation (HARs are ~90%% of run size)")
    parser.add_argument("--eval-config", default=str(ROOT / "config/webarena_verified.json"))
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
