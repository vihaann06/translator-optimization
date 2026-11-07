"""Autonomous translator search: diagnose offline failures -> propose translator -> offline eval -> accept.

Each iteration:
  1. Take the champion's offline traces and build a diagnosis bundle (failing steps with the task,
     reference vs predicted action, and the page text around both elements).
  2. Ask the optimizer model for a complete new translator module (translate(state) -> {text, id_map}).
  3. Smoke-test it on saved states, score it offline on the reference run, and promote it to champion
     if it beats the champion by --min-gain on the chosen split.
Online validation of a champion is a separate, explicit step (src.run_online --translator ...).
"""
import argparse
import asyncio
import gzip
import json
import re
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

from openai import OpenAI

from src import run_offline
from src.run_online import load_translator

ROOT = Path(__file__).resolve().parents[1]

OPTIMIZER_SYSTEM = """You improve a "translator": a Python module that turns a captured browser page state into
the text an LLM web agent reads. The agent acts on elements by the [id] shown in the text, so the
translator must return {"text": str, "id_map": {short_id: wid}} where wid values come from the state.

State keys available to translate(state):
  url, title, tabs (list of URLs), viewport {scrollX, scrollY, width, height, pageHeight}
  ax_nodes: Chromium CDP Accessibility.getFullAXTree nodes (nodeId, role, name, value, properties,
            childIds, parentId, ignored, backendDOMNodeId)
  wid_by_backend: {str(backendDOMNodeId): wid}  (wid = the element's data-wid attribute)
  wid_parent: {wid: parent wid}, bounds: {wid: [x, y, w, h]} in page coordinates
  html: page.content() with data-wid, data-live-value, data-live-checked, data-live-selected attributes
Only the Python standard library and lxml are available. translate must be deterministic, take under 2 s
per page and never raise. Every wid in id_map must come from the state.

You will see the current champion translator, the agent's offline failures on it and the history of past
attempts. Diagnose the representation bottleneck behind the failures, then write a complete replacement
module. Generalise: do not special-case particular sites, URLs, tasks or element names.
Reply with a short diagnosis, then the full module in one ```python block."""


def context_window(text, line, radius=12):
    if not line:
        return None
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip()[:160] == line:
            return "\n".join(lines[max(0, i - radius): i + radius + 1])
    return None


def diagnosis_bundle(trace_path, ref_dir, champion_translate, max_cases):
    traces = [json.loads(l) for l in Path(trace_path).read_text().splitlines()]
    fails = [t for t in traces if not t["correct"]]
    tasks = {t["task_id"]: t for t in json.loads((ROOT / "data/hard.json").read_text())}
    cases = []
    for t in fails[:max_cases]:
        state_path = Path(ref_dir) / "tasks" / str(t["task_id"]) / "snapshots" / f"step_{t['step']:03d}.json.gz"
        with gzip.open(state_path, "rt") as f:
            text = champion_translate(json.load(f))["text"]
        cases.append(
            f"### task {t['task_id']} step {t['step']}: {tasks[t['task_id']]['intent']}\nURL: {t['url']}\n"
            f"reference action: {json.dumps(t['ref_action'])}\nreference element line: {t.get('ref_line')}\n"
            f"predicted action: {json.dumps(t.get('pred_action'))}\npredicted element line: {t.get('pred_line')}\n"
            f"agent thought: {t.get('thought', '')[:400]}\nerror: {t.get('error')}\n"
            f"page text near reference element:\n{context_window(text, t.get('ref_line')) or '(not found)'}\n"
            f"page text near predicted element:\n{context_window(text, t.get('pred_line')) or '(n/a)'}")
    n = len(traces)
    stats = (f"{n} steps, {n - len(fails)} correct ({(n - len(fails)) / max(1, n):.1%}); "
             f"mean page tokens {sum(t['page_tokens'] for t in traces) / max(1, n):.0f}; "
             f"truncated pages {sum(t['truncated'] for t in traces)}")
    return stats, "\n\n".join(cases)


def smoke_test(path, ref_dir, n=6):
    translate = load_translator(path)
    states = sorted((Path(ref_dir) / "tasks").glob("*/snapshots/step_000.json.gz"))[:n]
    for p in states:
        with gzip.open(p, "rt") as f:
            state = json.load(f)
        t0 = time.time()
        out = translate(state)
        assert isinstance(out.get("text"), str) and isinstance(out.get("id_map"), dict), "bad return type"
        valid = set(state["wid_by_backend"].values())
        bad = [v for v in out["id_map"].values() if v not in valid]
        assert not bad, f"id_map has wids not in state: {bad[:5]}"
        assert len(out["text"]) >= 200 and out["id_map"], \
            f"near-empty output on {p.parent.parent.name}: {len(out['text'])} chars, {len(out['id_map'])} ids"
        assert time.time() - t0 < 10, "translate too slow"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--reference", required=True)
    p.add_argument("--champion", default=str(ROOT / "translators/baseline_axtree.py"))
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--split", default="gold")
    p.add_argument("--min-gain", type=float, default=0.05)
    p.add_argument("--agent-model", default="gpt-4.1-mini")
    p.add_argument("--optimizer-model", default="gpt-4.1")
    p.add_argument("--max-cases", type=int, default=25)
    p.add_argument("--max-cost", type=float, default=10.0, help="stop when total offline+optimizer USD exceeds this")
    a = p.parse_args()
    out = ROOT / "runs" / f"search_{a.name}"
    cand_dir = out / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    journal = out / "journal.jsonl"

    def offline(path, tag):
        args = SimpleNamespace(reference=a.reference, translator=str(path), out=str(out / "offline" / tag),
                               split=a.split, model=a.agent_model, task_source=str(ROOT / "data/hard.json"),
                               max_page_tokens=40000, concurrency=8)
        return asyncio.run(run_offline.main_async(args))

    champion, champ_tag = Path(a.champion), "champion0"
    champ_report = offline(champion, champ_tag)
    spent = champ_report["cost_usd"]
    history = []
    client = OpenAI()
    for it in range(1, a.iterations + 1):
        if spent > a.max_cost:
            print(f"budget reached (${spent:.2f})")
            break
        stats, cases = diagnosis_bundle(out / "offline" / champ_tag / "traces.jsonl", a.reference,
                                        load_translator(champion), a.max_cases)
        past = "\n".join(f"- iter {h['iter']}: {h['summary']} -> step_accuracy {h['step_accuracy']} ({h['decision']})"
                         for h in history) or "(none)"
        user = (f"## Champion translator\n```python\n{champion.read_text()}\n```\n\n## Champion offline result\n{stats}\n\n"
                f"## Past attempts\n{past}\n\n## Failing steps\n{cases}")
        resp = client.chat.completions.create(model=a.optimizer_model, temperature=0.7,
                                              messages=[{"role": "system", "content": OPTIMIZER_SYSTEM},
                                                        {"role": "user", "content": user}])
        u = resp.usage
        spent += (u.prompt_tokens * 2.0 + u.completion_tokens * 8.0) / 1e6
        reply = resp.choices[0].message.content
        code = re.search(r"```python\n(.*?)```", reply, re.S)
        path = cand_dir / f"iter{it:02d}.py"
        (cand_dir / f"iter{it:02d}.reply.md").write_text(reply)
        entry = {"iter": it, "candidate": str(path), "summary": reply.split("```")[0].strip()[:600]}
        try:
            assert code, "no python block"
            path.write_text(code.group(1))
            smoke_test(path, a.reference)
            report = offline(path, f"iter{it:02d}")
            spent += report["cost_usd"]
            entry.update(step_accuracy=round(report["step_accuracy"], 4), report=report)
            gain = report["step_accuracy"] - champ_report["step_accuracy"]
            if gain >= a.min_gain:
                entry["decision"] = f"accepted (+{gain:.3f})"
                champion, champ_tag, champ_report = path, f"iter{it:02d}", report
            else:
                entry["decision"] = f"rejected ({gain:+.3f})"
        except Exception as e:
            entry.update(step_accuracy=None, decision=f"invalid: {e!r}"[:300], traceback=traceback.format_exc()[-1500:])
        entry["spent_usd"] = round(spent, 4)
        history.append(entry)
        with journal.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        print(json.dumps({k: entry.get(k) for k in ("iter", "step_accuracy", "decision", "spent_usd")}), flush=True)
    (out / "champion.txt").write_text(str(champion) + "\n")
    print("champion:", champion, champ_report["step_accuracy"])


if __name__ == "__main__":
    main()
