import json

from src import agent
from src.run_offline import match
from src.tasks import resolve_url, select
from translators.baseline_axtree import translate

STATE = {
    "ax_nodes": [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Shop"}, "childIds": ["2", "3"],
         "backendDOMNodeId": 10},
        {"nodeId": "2", "role": {"value": "button"}, "name": {"value": "Search"}, "childIds": [],
         "backendDOMNodeId": 11},
        {"nodeId": "3", "role": {"value": "textbox"}, "name": {"value": "Query"}, "value": {"value": "shoes"},
         "childIds": [], "backendDOMNodeId": 12},
    ],
    "wid_by_backend": {"10": "1", "11": "5", "12": "7"},
}


def test_baseline_translate_ids_and_values():
    out = translate(STATE)
    assert out["text"].splitlines()[1].strip() == '[2] button "Search"'
    assert 'value="shoes"' in out["text"]
    assert out["id_map"] == {"1": "1", "2": "5", "3": "7"}


def test_parse_nested_and_flat_actions():
    _, a = agent.parse('{"thought": "x", "action": {"type": "click", "element": "[4]"}}')
    assert a == {"type": "click", "element": "4"}
    _, a = agent.parse('{"thought": "x", "action": "stop", "task_type": "RETRIEVE", "retrieved_data": [1]}')
    assert a["type"] == "stop" and a["retrieved_data"] == [1]


def test_match_lenient_ancestor_and_text():
    parents = {"9": "8"}
    assert match({"type": "click"}, "8", {"type": "click"}, "9", parents) == (True, True)
    assert match({"type": "type", "text": "Foo "}, "3", {"type": "type", "text": "foo"}, "3", {}) == (True, True)
    assert match({"type": "scroll", "direction": "up"}, None, {"type": "scroll"}, None, {}) == (None, False)


def test_resolve_url_prefers_longest_placeholder():
    cfg = {"shopping": {"url": "http://s"}, "shopping_admin": {"url": "http://a/admin"},
           "reddit": {"url": "http://r"}, "gitlab": {"url": "http://g"}}
    assert resolve_url("__SHOPPING_ADMIN__/sales", cfg) == "http://a/admin/sales"
    assert resolve_url("__SHOPPING__/x", cfg) == "http://s/x"


def test_select_respects_quota_and_unique_templates():
    tasks = json.load(open("data/hard.json"))
    subset = select(tasks, {"shopping": 5, "reddit": 3}, seed=1)
    assert len(subset) == 8
    assert len({t["intent_template_id"] for t in subset}) == 8


def _tiny_state():
    def n(i, role, name="", kids=(), backend=None, props=()):
        return {"nodeId": str(i), "role": {"value": role}, "name": {"value": name}, "childIds": [str(k) for k in kids],
                "backendDOMNodeId": backend, "properties": [{"name": k, "value": {"value": v}} for k, v in props]}
    nodes = [n(1, "RootWebArea", "Page", [2, 5]), n(2, "generic", "", [3, 4]),
             n(3, "link", "Home", [6], backend=10), n(4, "StaticText", "Hello world"),
             n(5, "table", "", [7]), n(6, "StaticText", "Home"), n(7, "row", "", [8, 9]),
             n(8, "cell", "Alice", [11]), n(9, "cell", "", [12]), n(11, "StaticText", "Alice"),
             n(12, "button", "Edit", backend=20, props=[("focusable", True)])]
    return {"ax_nodes": nodes, "wid_by_backend": {"10": "w10", "20": "w20"}, "bounds": {},
            "viewport": {"scrollY": 0, "height": 720}}


def test_pruned_variants_keep_interactive_ids_and_drop_noise():
    import importlib
    for name in ("prune_basic", "prune_labels", "prune_tables", "prune_sections", "prune_viewport", "prune_flat"):
        out = importlib.import_module(f"translators.{name}").translate(_tiny_state())
        assert set(out["id_map"].values()) == {"w10", "w20"}, name
        assert "generic" not in out["text"] and out["text"].count("Home") == 1, name
    tables = importlib.import_module("translators.prune_tables").translate(_tiny_state())["text"]
    assert 'row: Alice | [2] button "Edit"' in tables


def test_prune_flat_v2_keeps_blocks_dedups_values_and_groups_items():
    from translators.prune_flat_v2 import translate

    def n(i, role, name="", kids=(), backend=None, value=None, ignored=False):
        d = {"nodeId": str(i), "role": {"value": role}, "name": {"value": name}, "ignored": ignored,
             "childIds": [str(k) for k in kids], "backendDOMNodeId": backend, "properties": []}
        if value is not None:
            d["value"] = {"value": value}
        return d
    nodes = [n(1, "RootWebArea", "Page", [2, 6]),
             n(2, "searchbox", "Filter", [3], backend=10, value="react apps"), n(3, "generic", "", [4]),
             n(4, "StaticText", "react apps"), n(6, "list", "", [7, 8, 9]),
             *[x for k, title in ((7, "First"), (8, "Second"), (9, "Third")) for x in (
                 n(k, "listitem", "", [k * 10, k * 10 + 1]), n(k * 10, "generic", "", [k * 10 + 2]),
                 n(k * 10 + 2, "StaticText", title), n(k * 10 + 1, "generic", "", [k * 10 + 3]),
                 n(k * 10 + 3, "StaticText", f"{title} body \ue605"))]]
    out = translate({"ax_nodes": nodes, "wid_by_backend": {"10": "w10"}, "bounds": {},
                     "viewport": {"scrollY": 0, "height": 720}})["text"]
    assert out.count("react apps") == 1
    assert "- First\n  First body\n- Second" in out and "\ue605" not in out
