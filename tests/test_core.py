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
