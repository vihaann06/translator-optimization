"""
Improved translator: Accessibility tree to a structured, segmented transcript.

Key improvements:
- Preserves logical groupings (lists, comment threads, nav, forms, cards, sections, etc.),
  so agents can reason about boundaries and relationships.
- Segments block-like regions as transcript sections, with clear context headers.
- Inline text is aggregated into coherent blocks, not split by every node.
- Labels are associated with their fields; options with their controls; authors with their comments.
- Structural context is made explicit at section/list/item/thread boundaries.
- Deterministic, performant, and general-purpose.

Contract: translate(state) -> {"text": str, "id_map": {short_id: wid}}
"""
from collections import defaultdict, deque

NOISE_ROLES = {"generic", "none", "InlineTextBox", "presentation"}
INLINE_ROLES = {"StaticText", "strong", "emphasis", "code", "mark"}
BLOCK_ROLES = {
    "paragraph", "heading", "sectionheader", "list", "listitem", "article",
    "group", "form", "main", "navigation", "complementary", "region",
    "textbox", "searchbox", "textarea", "combobox", "option", "radio",
    "checkbox", "button", "link", "img", "image", "menu", "menuitem", "tabpanel", "tab",
    "gridcell", "row", "table", "rowgroup", "rowheader", "columnheader", "cell", "figure",
    "blockquote", "separator", "summary", "details", "tabpanel", "tabpanel"
}
CONTAINER_ROLES = {
    "main", "navigation", "complementary", "region", "form", "group", "article", "list", "table",
    "tabpanel", "rowgroup", "menu", "row", "section", "tabpanel", "tabpanel", "dialog"
}
LIST_ROLES = {"list", "menu", "tablist", "tree", "listbox"}
LISTITEM_ROLES = {"listitem", "menuitem", "option", "treeitem", "tab"}
CONTROL_ROLES = {
    "textbox", "searchbox", "combobox", "radio", "checkbox", "button", "link", "option",
    "switch", "slider", "spinbutton", "listbox", "menuitem", "tab"
}
HEADING_ROLES = {"heading", "sectionheader"}
IMAGE_ROLES = {"image", "img", "figure"}
LABEL_ROLES = {"label", "LabelText"}
STATE_KEYS = {"checked", "selected", "disabled", "expanded", "pressed", "required", "readonly", "multiselectable", "level", "valuemin", "valuemax", "valuetext", "focused"}
VALUE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton"}

def translate(state: dict) -> dict:
    # --- Build node maps and relationships ---
    ax_nodes = state["ax_nodes"]
    wid_by_backend = state["wid_by_backend"]

    node_by_id = {node["nodeId"]: node for node in ax_nodes}
    children_by_id = defaultdict(list)
    parent_by_id = {}
    for node in ax_nodes:
        for cid in node.get("childIds", []):
            children_by_id[node["nodeId"]].append(cid)
            parent_by_id[cid] = node["nodeId"]

    # Find roots (should only be 1 but robust to multi-root)
    all_ids = set(node_by_id)
    all_children = set(cid for node in ax_nodes for cid in node.get("childIds", []))
    root_ids = list(all_ids - all_children)
    if not root_ids:
        root_ids = [ax_nodes[0]["nodeId"]]

    # --- Utility functions ---
    def node_role(node):
        return (node.get("role") or {}).get("value", "")

    def node_name(node):
        return (node.get("name") or {}).get("value", "")

    def node_value(node):
        return (node.get("value") or {}).get("value", "")

    def node_props(node):
        return {p["name"]: p["value"].get("value") for p in node.get("properties", [])}

    def get_wid(node):
        backend_id = node.get("backendDOMNodeId")
        if backend_id is not None:
            return wid_by_backend.get(str(backend_id))
        return None

    # For associating labels with fields, etc.
    def find_label_for(node_id):
        # Search parent/preceding siblings for label role or LabelText
        node = node_by_id[node_id]
        parent = parent_by_id.get(node_id)
        if parent:
            siblings = children_by_id[parent]
            idx = siblings.index(node_id)
            for sib_id in reversed(siblings[:idx]):
                sib = node_by_id[sib_id]
                if node_role(sib) in LABEL_ROLES or node_role(sib).lower() == "labeltext":
                    txt = node_name(sib).strip()
                    if not txt:
                        # Try aggregate StaticText under label
                        txt = collect_inline_text(sib_id)
                    if txt:
                        return txt
        return ""

    def collect_inline_text(node_id):
        # Collect all inline text under this node (in document order)
        node = node_by_id[node_id]
        role = node_role(node)
        name = node_name(node)
        out = []
        if role in INLINE_ROLES and name.strip():
            out.append(name.strip())
        for cid in children_by_id.get(node_id, []):
            out.append(collect_inline_text(cid))
        return " ".join(filter(None, out)).strip()

    # Map from wid -> short_id
    id_map = {}
    wid_to_short = {}
    next_short = [1]
    def short_id_for_wid(wid):
        if wid is None:
            return None
        if wid not in wid_to_short:
            sid = str(next_short[0])
            wid_to_short[wid] = sid
            id_map[sid] = wid
            next_short[0] += 1
        return wid_to_short[wid]

    # --- Main traversal/serialization ---
    lines = []

    def render(node_id, depth=0, context=None):
        node = node_by_id[node_id]
        role = node_role(node)
        name = node_name(node).strip()
        value = node_value(node)
        props = node_props(node)
        ignored = node.get("ignored", False)
        wid = get_wid(node)
        indent = "  " * depth
        label = ""

        # Always traverse children of ignored nodes, don't render them
        if ignored:
            for cid in children_by_id.get(node_id, []):
                render(cid, depth, context)
            return

        # --- Grouping and context headers ---
        # Section or container (main, nav, aside, region)
        if role in CONTAINER_ROLES and not (role in LIST_ROLES):
            header = []
            if wid:
                sid = short_id_for_wid(wid)
                header.append(f"{indent}[{sid}] {role}")
            else:
                header.append(f"{indent}{role}")
            if name:
                header.append(f" {name!r}")
            lines.append("".join(header).rstrip())
            for cid in children_by_id.get(node_id, []):
                render(cid, depth+1, context=role)
            return

        # List/menus: explicit boundaries
        if role in LIST_ROLES:
            header = []
            if wid:
                sid = short_id_for_wid(wid)
                header.append(f"{indent}[{sid}] {role}")
            else:
                header.append(f"{indent}{role}")
            if name:
                header.append(f" {name!r}")
            lines.append("".join(header).rstrip())
            for cid in children_by_id.get(node_id, []):
                render(cid, depth+1, context="list")
            return

        # List items: keep level, order, and option/selected state
        if role in LISTITEM_ROLES:
            desc = []
            if wid:
                sid = short_id_for_wid(wid)
                desc.append(f"{indent}[{sid}] {role}")
            else:
                desc.append(f"{indent}{role}")
            txt = name or collect_inline_text(node_id)
            if txt:
                desc.append(f" {txt!r}")
            if "level" in props:
                desc.append(f" level={props['level']}")
            for key in ("selected", "checked"):
                if key in props:
                    desc.append(f" {key}={props[key]}")
            if value not in (None, "", False):
                desc.append(f" value={value!r}")
            lines.append("".join(desc).rstrip())
            for cid in children_by_id.get(node_id, []):
                render(cid, depth+1, context="listitem")
            return

        # Headings
        if role in HEADING_ROLES:
            desc = []
            if wid:
                sid = short_id_for_wid(wid)
                desc.append(f"{indent}[{sid}] {role}")
            else:
                desc.append(f"{indent}{role}")
            if name:
                desc.append(f" {name!r}")
            lines.append("".join(desc).rstrip())
            return

        # Fields/controls: associate with label if available
        if role in CONTROL_ROLES:
            desc = []
            if wid:
                sid = short_id_for_wid(wid)
                desc.append(f"{indent}[{sid}] {role}")
            else:
                desc.append(f"{indent}{role}")
            txt = name
            # Try to find label for this control
            label = find_label_for(node_id)
            if label and label != txt:
                desc.append(f" label={label!r}")
            if txt:
                desc.append(f" {txt!r}")
            if value not in (None, "", False):
                desc.append(f" value={value!r}")
            # Show states
            for key in ("checked", "selected", "expanded", "pressed", "required", "readonly", "disabled", "focused"):
                if key in props:
                    desc.append(f" {key}={props[key]}")
            lines.append("".join(desc).rstrip())
            # Render children for e.g. combobox (options inside)
            for cid in children_by_id.get(node_id, []):
                render(cid, depth+1, context=role)
            return

        # Images
        if role in IMAGE_ROLES:
            desc = []
            if wid:
                sid = short_id_for_wid(wid)
                desc.append(f"{indent}[{sid}] {role}")
            else:
                desc.append(f"{indent}{role}")
            if name:
                desc.append(f" {name!r}")
            lines.append("".join(desc).rstrip())
            return

        # Blocks (paragraph, blockquote, etc): aggregate inline text
        if role in BLOCK_ROLES or context in ("listitem", "list", "main", "form", "group", "tabpanel"):
            txt = name or collect_inline_text(node_id)
            if txt:
                if wid:
                    sid = short_id_for_wid(wid)
                    lines.append(f"{indent}[{sid}] {role} {txt!r}")
                else:
                    lines.append(f"{indent}{role} {txt!r}")
            # Render children for nested structure
            for cid in children_by_id.get(node_id, []):
                render(cid, depth+1, context=role)
            return

        # Inline text, only if not already included
        if role in INLINE_ROLES:
            txt = name
            if txt:
                if wid:
                    sid = short_id_for_wid(wid)
                    lines.append(f"{indent}[{sid}] {role} {txt!r}")
                else:
                    lines.append(f"{indent}{role} {txt!r}")
            return

        # Fallback: render with role and name if not noise
        if role and role not in NOISE_ROLES:
            desc = []
            if wid:
                sid = short_id_for_wid(wid)
                desc.append(f"{indent}[{sid}] {role}")
            else:
                desc.append(f"{indent}{role}")
            if name:
                desc.append(f" {name!r}")
            lines.append("".join(desc).rstrip())
            for cid in children_by_id.get(node_id, []):
                render(cid, depth+1, context=role)
            return

        # Otherwise, just render children
        for cid in children_by_id.get(node_id, []):
            render(cid, depth, context=context)

    # Traverse all roots
    for rid in root_ids:
        render(rid, 0)

    text = "\n".join(lines)
    return {"text": text, "id_map": id_map}
