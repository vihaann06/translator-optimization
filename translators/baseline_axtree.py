"""Baseline: Chromium accessibility tree rendered as an indented outline.

Same rendering as webpage-translator/translators/baseline_axtree.py, but the input is a
live-captured page state (see src/browser.py) instead of Mind2Web HTML, so attributes,
form values and JS-rendered content come straight from the browser.

Contract: translate(state) -> {"text": str, "id_map": {short_id: wid}}
`state` keys: url, ax_nodes (CDP Accessibility.getFullAXTree nodes),
wid_by_backend ({backendDOMNodeId: wid}), html, viewport, bounds.
"""
import json

NOISE = {"generic", "none", "InlineTextBox"}
STATES = {"checked", "selected", "disabled", "expanded", "pressed", "required",
          "readonly", "multiselectable", "level", "valuemin", "valuemax", "valuetext",
          "focused"}


def render(nodes, elements):
    by_id = {node["nodeId"]: node for node in nodes}
    child_ids = {child for node in nodes for child in node.get("childIds", [])}
    roots = [node["nodeId"] for node in nodes if node["nodeId"] not in child_ids]
    lines, id_map, reverse = [], {}, {}
    stack = [(root, 0) for root in reversed(roots)]
    visited = set()
    while stack:
        node_id, depth = stack.pop()
        if node_id in visited or node_id not in by_id:
            continue
        visited.add(node_id)
        node = by_id[node_id]
        role = node.get("role", {}).get("value", "")
        name = " ".join(str(node.get("name", {}).get("value", "")).split())
        props = {p["name"]: p["value"].get("value") for p in node.get("properties", [])
                 if p["name"] in STATES}
        focusable = any(p["name"] == "focusable" and p["value"].get("value") is True
                        for p in node.get("properties", []))
        keep = not node.get("ignored", False) and bool(
            role not in NOISE or name or props or focusable)
        if role == "InlineTextBox":
            keep = False
        if keep:
            wid = elements.get(str(node.get("backendDOMNodeId")))
            prefix = ""
            if wid is not None:
                if wid not in reverse:
                    short = str(len(reverse) + 1)
                    reverse[wid] = short
                    id_map[short] = wid
                prefix = f"[{reverse[wid]}] "
            line = "  " * depth + prefix + role
            if name:
                line += " " + json.dumps(name, ensure_ascii=False)
            value = node.get("value", {}).get("value")
            if value not in (None, ""):
                line += " value=" + json.dumps(value, ensure_ascii=False)
            for key, val in props.items():
                line += f" {key}={json.dumps(val, ensure_ascii=False)}"
            lines.append(line)
        stack.extend((child, depth + int(keep)) for child in reversed(node.get("childIds", [])))
    return {"text": "\n".join(lines), "id_map": id_map}


def translate(state: dict) -> dict:
    return render(state["ax_nodes"], state["wid_by_backend"])
