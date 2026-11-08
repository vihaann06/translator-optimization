"""Build the vendored IR from a live capture (src/browser.capture) instead of re-rendering HTML offline.

Element identity is the `data-wid` attribute: IR `backend_node_id` holds the wid, so the serializer's id_map is
short id -> wid as the agent expects. Tag and attributes come from the captured HTML (joined on data-wid), live
form values/checked state from the data-live-* attributes, bounds from the capture. "clickable" (Mind2Web's
is_clickable upstream) is approximated by an onclick attribute or an interactive ARIA role attribute.
"""
import lxml.html

from translators.legacy.t3.common import INTERACTIVE_ROLES
from translators.legacy.t3.ir import DOM_ATTRIBUTES, IR, Bounds, Node


def _elements(html):
    out = {}
    try:
        root = lxml.html.fromstring(html)
    except Exception:
        return out
    for el in root.iter():
        wid = el.get("data-wid") if isinstance(el.tag, str) else None
        if wid is None:
            continue
        attrs = dict(el.attrib)
        if "data-live-value" in attrs:
            attrs["value"] = attrs["data-live-value"]
        if attrs.get("data-live-checked") == "true":
            attrs["checked"] = "checked"
        if attrs.get("data-live-selected") == "true":
            attrs["selected"] = "selected"
        clickable = "onclick" in attrs or attrs.get("role") in INTERACTIVE_ROLES
        out[wid] = (el.tag.lower(), {k: v for k, v in attrs.items() if k in DOM_ATTRIBUTES or k.startswith("aria-")},
                    clickable)
    return out


def build_ir_from_state(state: dict) -> IR:
    ax_nodes, wids, bounds = state["ax_nodes"], state["wid_by_backend"], state.get("bounds", {})
    elements = _elements(state.get("html") or "")
    by_id = {node["nodeId"]: node for node in ax_nodes}
    child_ids = {child for node in ax_nodes for child in node.get("childIds", [])}
    ir = IR()
    stack = [(nid, ir.roots) for nid in reversed([n["nodeId"] for n in ax_nodes if n["nodeId"] not in child_ids])]
    visited = set()
    while stack:
        node_id, siblings = stack.pop()
        if node_id in visited or node_id not in by_id:
            continue
        visited.add(node_id)
        raw = by_id[node_id]
        wid = wids.get(str(raw.get("backendDOMNodeId")))
        tag, attrs, clickable = elements.get(wid, (None, {}, False))
        props = raw.get("properties", [])
        node = Node(ax_id=node_id, role=raw.get("role", {}).get("value", ""),
                    name=str(raw.get("name", {}).get("value", "")),
                    value=raw.get("value", {}).get("value"), has_value="value" in raw,
                    properties={p["name"]: p["value"].get("value") for p in props},
                    property_details={p["name"]: p["value"] for p in props},
                    ignored=raw.get("ignored", False), ignored_reasons=raw.get("ignoredReasons", []),
                    dom_backend_id=raw.get("backendDOMNodeId"), tag=tag, attributes=attrs,
                    bounds=Bounds(*bounds[wid]) if wid in bounds else None, backend_node_id=wid,
                    focusable=any(p["name"] == "focusable" and p["value"].get("value") is True for p in props),
                    clickable=clickable)
        siblings.append(node)
        stack.extend((child, node.children) for child in reversed(raw.get("childIds", [])))
    return ir
