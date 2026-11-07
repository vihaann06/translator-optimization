"""Task loading, URL placeholders and the stratified long-horizon subset."""
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDERS = {"__SHOPPING__": "shopping", "__SHOPPING_ADMIN__": "shopping_admin",
                "__REDDIT__": "reddit", "__GITLAB__": "gitlab"}


def resolve_url(url, sites_cfg):
    for placeholder, site in sorted(PLACEHOLDERS.items(), key=lambda kv: -len(kv[0])):
        if placeholder in url:
            url = url.replace(placeholder, sites_cfg[site]["url"])
    return url


def load_tasks(path):
    data = json.loads(Path(path).read_text())
    return data["tasks"] if isinstance(data, dict) else data


def task_type(task):
    for e in task["eval"]:
        if e["evaluator"] == "AgentResponseEvaluator":
            return e["expected"]["task_type"]
    return "?"


def select(tasks, quota, seed=0, available=("shopping", "shopping_admin", "reddit", "gitlab")):
    """Stratified sample: per site group, round-robin over task types, one task per intent template."""
    rng = random.Random(seed)
    groups = defaultdict(lambda: defaultdict(list))
    for t in tasks:
        if not set(t["sites"]) <= set(available):
            continue
        key = "+".join(sorted(t["sites"]))
        key = key if len(t["sites"]) == 1 else "multi"
        groups[key][task_type(t)].append(t)
    chosen = []
    for key, n in quota.items():
        by_type = groups[key]
        for pool in by_type.values():
            rng.shuffle(pool)
        seen_templates, picked = set(), []
        types = sorted(by_type, key=lambda k: -len(by_type[k]))
        while len(picked) < n and any(by_type[k] for k in types):
            for k in types:
                while by_type[k]:
                    t = by_type[k].pop()
                    if t["intent_template_id"] not in seen_templates:
                        seen_templates.add(t["intent_template_id"])
                        picked.append(t)
                        break
                if len(picked) >= n:
                    break
        chosen += picked
    return sorted(chosen, key=lambda t: t["task_id"])


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=str(ROOT / "data/hard.json"))
    p.add_argument("--out", default=str(ROOT / "data/subset_v1.json"))
    p.add_argument("--quota", default="shopping=10,shopping_admin=10,reddit=8,gitlab=10,multi=2")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    quota = {k: int(v) for k, v in (x.split("=") for x in a.quota.split(","))}
    subset = select(load_tasks(a.source), quota, a.seed)
    Path(a.out).write_text(json.dumps(subset, indent=1))
    for t in subset:
        print(t["task_id"], "+".join(t["sites"]), task_type(t), t["intent"][:90])
    print(len(subset))
