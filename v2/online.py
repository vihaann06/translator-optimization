"""Online run with the full v2 interface (separate from src/run_online.py, which it reuses).

Differences from src/run_online.run_task: change-only renders chained across steps (memo), the v2 legend in the
system prompt, a visited-pages line, local query/expand tools answered in-loop (extra LLM calls are counted),
and a multi-field fill action executed in the browser. Usage: same flags as src.run_online, e.g.
  python -m v2.online --run v2_online --tasks data/subset_v1.json --concurrency 8 --max-steps 30
"""
import asyncio
import json
import time

import src.run_online as ro
from src import agent as agent_lib
from src.browser import capture, execute
from src.run_online import line_for, write_gz
from src.tasks import resolve_url
from v2 import tools
from v2.offline import MAX_TOOL_CALLS, V2_LEGEND, parse_any
from v2.translator import render


async def run_fill(page, action, id_map, text):
    done = []
    for sid, value in (action.get("fields") or {}).items():
        sid = str(sid).strip("[] ")
        wid = id_map.get(sid)
        if wid is None:
            raise ValueError(f"element [{sid}] is not on the current page")
        line = line_for(text, sid) or ""
        kind = "select" if f"[{sid}] select" in line else "type"
        await asyncio.wait_for(execute(page, {"type": kind, "element": sid, "text": value, "option": value}, wid), 60)
        done.append(f"[{sid}]={json.dumps(value)}")
    submit = str(action.get("submit") or "").strip("[] ")
    if submit:
        wid = id_map.get(submit)
        if wid is None:
            raise ValueError(f"submit element [{submit}] is not on the current page")
        await asyncio.wait_for(execute(page, {"type": "click", "element": submit}, wid), 60)
    return done


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
    cdps, memo, visited = {}, None, []
    history, response, steps_log = [], None, task_dir / "steps.jsonl"
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "steps": 0, "llm_calls": 0}
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
        except Exception:
            await asyncio.sleep(2)
            cdps[page] = await context.new_cdp_session(page)
            state = await capture(page, cdps[page])
        state["tabs"] = [p.url for p in context.pages]
        t0 = time.perf_counter()
        rendered = render(state, memo)
        for _ in range(3):
            if len(rendered["text"]) >= 200:
                break
            await asyncio.sleep(2)
            state = await capture(page, cdps[page])
            state["tabs"] = [p.url for p in context.pages]
            t0 = time.perf_counter()
            rendered = render(state, memo)
        record["translate_s"] = round(time.perf_counter() - t0, 4)
        memo = rendered["memo"]
        text, id_map = rendered["text"], rendered["id_map"]
        write_gz(task_dir / "snapshots" / f"step_{step:03d}.json.gz", state)
        write_gz(task_dir / "translations" / f"step_{step:03d}.txt.gz", text)
        try:
            await page.screenshot(path=str(task_dir / "screenshots" / f"step_{step:03d}.jpg"),
                                  type="jpeg", quality=50, timeout=10000)
        except Exception:
            pass
        tabs = [(p.url, p == page) for p in context.pages]
        shown = ("(pages visited so far: " + ", ".join(visited[-12:]) + ")\n" + text) if visited else text
        if page.url not in visited:
            visited.append(page.url)
        messages, n_tokens, truncated = agent_lib.build_messages(
            task, sites_cfg, start_urls, page.url, tabs, history, shown, args.max_page_tokens)
        messages[0]["content"] = agent_lib.SYSTEM + V2_LEGEND
        record.update(url=page.url, page_tokens=n_tokens, truncated=truncated, history=list(history), calls=[])
        action = thought = None
        for i in range(MAX_TOOL_CALLS + 1):
            reply, usage = await model(messages)
            record["calls"].append(usage)
            totals["llm_calls"] += 1
            for key in ("prompt_tokens", "completion_tokens", "cost_usd"):
                totals[key] += usage[key]
            try:
                thought, action = parse_any(reply)
            except Exception as e:
                action = None
                record.update(error=f"parse: {e}", reply=reply)
                break
            if action["type"] not in ("query", "expand"):
                break
            fn = tools.query if action["type"] == "query" else tools.expand
            try:
                answer = fn(rendered, **{k: v for k, v in action.items() if k != "type"})
            except Exception as e:
                answer = f"{action['type']} error: {e}"
            record.setdefault("tools", []).append({"action": action, "answer": answer[:2000]})
            follow = "\nNow reply with your next action (JSON)." if i < MAX_TOOL_CALLS - 1 else \
                "\nTool limit reached: reply with a browser action or stop now (no more query/expand)."
            messages = messages + [{"role": "assistant", "content": reply},
                                   {"role": "user", "content": f"## Result of {action['type']}\n{answer}{follow}"}]
            action = None
        totals["steps"] = step + 1
        record["usage"] = {k: sum(c[k] for c in record["calls"]) for k in
                           ("prompt_tokens", "cached_tokens", "completion_tokens", "cost_usd", "latency_s")}
        if action is None:
            parse_failures += 1
            history.append(f"(invalid reply: {record.get('error', 'no browser action after query/expand')})")
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
        wid = label = None
        try:
            if action["type"] == "fill":
                done = await run_fill(page, action, id_map, text)
                submit = str(action.get("submit") or "").strip("[] ")
                desc = "fill " + ", ".join(done) + (f" then click {line_for(text, submit) or f'[{submit}]'}" if submit else "")
                outcome = "ok"
            else:
                if action["type"] in agent_lib.ELEMENT_ACTIONS:
                    label = line_for(text, action["element"])
                    wid = id_map.get(action["element"])
                    record["wid"] = wid
                    if wid is None:
                        raise ValueError(f"element [{action['element']}] is not on the current page")
                desc = agent_lib.describe(action, label)
                if action["type"] == "tab_focus":
                    active["page"] = context.pages[int(action["index"])]
                    await active["page"].bring_to_front()
                    outcome = "ok"
                else:
                    outcome = await asyncio.wait_for(execute(page, action, wid), timeout=60)
        except Exception as e:
            desc = desc if "desc" in locals() and action["type"] == "fill" else agent_lib.describe(action, label)
            outcome = f"error: {str(e).splitlines()[0][:200]}"
        record["outcome"] = outcome
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


ro.run_task = run_task

if __name__ == "__main__":
    ro.main()
