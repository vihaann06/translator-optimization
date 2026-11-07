"""
Improved structured accessibility-to-text translator.

- Preserves grouping: renders lists/threads/comments with sectioning, authorship, and timestamps.
- Distinguishes navigation/sidebar from main content.
- Associates labels with controls and options with their parent.
- Inline and block text are separated for clarity.
- Renders options, comments, and grouped content as lists/sections, not flat lines.
"""

import json
from lxml import html as lxml_html

NOISE_ROLES = {"generic", "none", "InlineTextBox"}
INLINE_ROLES = {
    "StaticText", "LabelText", "LineBreak", "paragraph", "strong", "emphasis", "code", "mark",
    "span", # some sites map to custom roles
}
BLOCK_ROLES = {
    "list", "listitem", "table", "row", "cell", "comment", "article", "section", "group", "main",
    "complementary", "navigation", "form", "menu", "tabpanel", "tabpanelgroup"
}
CONTROL_ROLES = {
    "textbox", "searchbox", "combobox", "checkbox", "radio", "option", "button", "link", "spinbutton",
    "slider", "menuitem", "tab", "switch", "togglebutton", "textarea"
}
# Properties to surface
STATES = {
    "checked", "selected", "disabled", "expanded", "pressed", "required", "readonly",
    "multiselectable", "level", "valuemin", "valuemax", "valuetext", "focused"
}

def _build_ax_maps(ax_nodes):
    """Build quick lookup tables for AX nodes."""
    by_id = {n["nodeId"]: n for n in ax_nodes}
    parent = {}
    for n in ax_nodes:
        for c in n.get("childIds", []):
            parent[c] = n["nodeId"]
    return by_id, parent

def _shorten_wid(wid, reverse, id_map):
    """Allocate a short ID for each wid, used in [id] markers."""
    if wid not in reverse:
        short = str(len(reverse) + 1)
        reverse[wid] = short
        id_map[short] = wid
    return reverse[wid]

def _get_node_wid(node, wid_by_backend):
    backend = node.get("backendDOMNodeId")
    if backend is not None:
        return wid_by_backend.get(str(backend))
    return None

def _render_props(node):
    """Render AX properties for stateful controls."""
    props = []
    for p in node.get("properties", []):
        name, val = p["name"], p["value"].get("value")
        if name in STATES and val not in (None, ""):
            props.append(f'{name}={json.dumps(val, ensure_ascii=False)}')
    return props

def _render_value(node):
    val = node.get("value", {})
    if val and val.get("value") not in (None, ""):
        return f'value={json.dumps(val["value"], ensure_ascii=False)}'
    return None

def _get_label(node, by_id):
    # Try aria-labelledby/labelledby, fallback to 'name'
    props = {p["name"]: p["value"].get("value") for p in node.get("properties", [])}
    label = None
    for attr in ("label", "labelledby", "aria-labelledby"):
        v = props.get(attr)
        if isinstance(v, str) and v in by_id:
            n = by_id[v]
            label = n.get("name", {}).get("value")
            if label: break
    if not label:
        label = node.get("name", {}).get("value")
    return label

def _is_inline(node):
    role = node.get("role", {}).get("value", "")
    return role in INLINE_ROLES

def _is_block(node):
    role = node.get("role", {}).get("value", "")
    return (role in BLOCK_ROLES) or (role in CONTROL_ROLES)

def _format_inline(nodes, by_id, wid_by_backend, reverse, id_map):
    """Join inline nodes and surface their text content."""
    parts = []
    for n in nodes:
        role = n.get("role", {}).get("value", "")
        name = n.get("name", {}).get("value", "")
        val = n.get("value", {}).get("value", "")
        wid = _get_node_wid(n, wid_by_backend)
        prefix = ""
        if wid:
            short = _shorten_wid(wid, reverse, id_map)
            prefix = f"[{short}] "
        if role == "LineBreak":
            parts.append("\n")
        elif role in ("StaticText", "LabelText"):
            if name:
                parts.append(prefix + name)
            elif val:
                parts.append(prefix + val)
        elif role == "strong":
            if name:
                parts.append("**" + name + "**")
        elif role == "emphasis":
            if name:
                parts.append("*" + name + "*")
        elif role == "code":
            if name:
                parts.append("`" + name + "`")
        elif role == "link":
            # Inline link (e.g. inside paragraph)
            if name:
                parts.append(f'{prefix}{role} "{name}"')
            elif val:
                parts.append(f'{prefix}{role} value={json.dumps(val, ensure_ascii=False)}')
        elif role == "image":
            # Inline image
            if name:
                parts.append(f'{prefix}image "{name}"')
            else:
                parts.append(f'{prefix}image')
        elif name:
            parts.append(prefix + name)
    return " ".join(parts).replace("  ", " ").replace("\n ", "\n")

def _render_options(node, by_id, wid_by_backend, reverse, id_map, depth):
    """Render options for select, combobox, menu, etc."""
    lines = []
    for child_id in node.get("childIds", []):
        child = by_id[child_id]
        role = child.get("role", {}).get("value", "")
        if role == "option":
            name = child.get("name", {}).get("value", "")
            val = child.get("value", {}).get("value", "")
            wid = _get_node_wid(child, wid_by_backend)
            prefix = ""
            if wid:
                short = _shorten_wid(wid, reverse, id_map)
                prefix = f"[{short}] "
            props = _render_props(child)
            line = "  " * (depth+1) + f"{prefix}option"
            if name:
                line += f" \"{name}\""
            if val:
                line += f" value={json.dumps(val, ensure_ascii=False)}"
            if props:
                line += " " + " ".join(props)
            lines.append(line)
    return lines

def _group_children(node, by_id):
    """Group children by their roles (for repeated structures: lists, tabpanels, menuitems)."""
    groups = []
    children = node.get("childIds", [])
    if not children:
        return []

    # Find contiguous runs of same role
    last_role = None
    cur_group = []
    for cid in children:
        child = by_id[cid]
        role = child.get("role", {}).get("value", "")
        if last_role is None or role == last_role:
            cur_group.append(cid)
        else:
            groups.append((last_role, list(cur_group)))
            cur_group = [cid]
        last_role = role
    if cur_group:
        groups.append((last_role, cur_group))
    return groups

def _render_node(node_id, by_id, wid_by_backend, reverse, id_map, depth=0, parent_role=None):
    node = by_id[node_id]
    role = node.get("role", {}).get("value", "")
    name = node.get("name", {}).get("value", "")
    val = node.get("value", {}).get("value", "")
    wid = _get_node_wid(node, wid_by_backend)
    props = _render_props(node)
    value_str = _render_value(node)
    indent = "  " * depth
    lines = []
    prefix = ""
    if wid:
        short = _shorten_wid(wid, reverse, id_map)
        prefix = f"[{short}] "
    # ----------- Render block/section/structural nodes -----------
    if role in ("main", "region", "article", "form", "complementary", "navigation", "tabpanel", "group", "section"):
        # Render as a section header
        if name:
            lines.append(f'{indent}{prefix}{role} "{name}"')
        else:
            lines.append(f'{indent}{prefix}{role}')
        # Render children (grouped)
        for child_id in node.get("childIds", []):
            lines.extend(_render_node(child_id, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    if role == "list":
        # Render lists with sections
        if name:
            lines.append(f'{indent}{prefix}list "{name}"')
        else:
            lines.append(f'{indent}{prefix}list')
        for child_id in node.get("childIds", []):
            lines.extend(_render_node(child_id, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    if role == "listitem":
        # Render list items as blocks (with their children inline if possible)
        li_line = indent + prefix + "listitem"
        if name:
            li_line += f' "{name}"'
        if props:
            li_line += " " + " ".join(props)
        lines.append(li_line)
        # Inline children if possible
        inline_children = []
        block_children = []
        for cid in node.get("childIds", []):
            child = by_id[cid]
            if _is_inline(child):
                inline_children.append(child)
            else:
                block_children.append(cid)
        if inline_children:
            lines[-1] += " " + _format_inline(inline_children, by_id, wid_by_backend, reverse, id_map)
        for cid in block_children:
            lines.extend(_render_node(cid, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    if role == "table":
        lines.append(f'{indent}{prefix}table')
        for child_id in node.get("childIds", []):
            lines.extend(_render_node(child_id, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    if role == "row":
        lines.append(f'{indent}{prefix}row')
        for child_id in node.get("childIds", []):
            lines.extend(_render_node(child_id, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    if role == "cell":
        cell_line = indent + prefix + "cell"
        if name:
            cell_line += f' "{name}"'
        if value_str:
            cell_line += f" {value_str}"
        if props:
            cell_line += " " + " ".join(props)
        lines.append(cell_line)
        # Inline or block children
        inline_children = []
        block_children = []
        for cid in node.get("childIds", []):
            child = by_id[cid]
            if _is_inline(child):
                inline_children.append(child)
            else:
                block_children.append(cid)
        if inline_children:
            lines[-1] += " " + _format_inline(inline_children, by_id, wid_by_backend, reverse, id_map)
        for cid in block_children:
            lines.extend(_render_node(cid, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    if role in CONTROL_ROLES:
        ctrl_line = indent + prefix + role
        label = _get_label(node, by_id)
        if label and label != name:
            ctrl_line += f' "{label}"'
        elif name:
            ctrl_line += f' "{name}"'
        if value_str:
            ctrl_line += f" {value_str}"
        if props:
            ctrl_line += " " + " ".join(props)
        lines.append(ctrl_line)
        # Render options for combobox, select, etc.
        if role in ("combobox", "menu", "listbox"):
            lines.extend(_render_options(node, by_id, wid_by_backend, reverse, id_map, depth))
        # Render children (e.g., textbox inside combobox, or menuitems)
        for child_id in node.get("childIds", []):
            child = by_id[child_id]
            if not _is_inline(child) and child.get("role", {}).get("value") != "option":
                lines.extend(_render_node(child_id, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
        return lines

    # Inline text container: render inline children
    if _is_inline(node):
        # This is only reached if parent is not a block (should be handled above in _format_inline)
        text = _format_inline([node], by_id, wid_by_backend, reverse, id_map)
        if text.strip():
            lines.append(indent + text)
        return lines

    # Fallback: generic node with children
    if name or val or props or value_str or wid:
        generic = indent + prefix + role
        if name:
            generic += f' "{name}"'
        if value_str:
            generic += f" {value_str}"
        if props:
            generic += " " + " ".join(props)
        lines.append(generic)
    for child_id in node.get("childIds", []):
        lines.extend(_render_node(child_id, by_id, wid_by_backend, reverse, id_map, depth + 1, role))
    return lines

def render_structured(ax_nodes, wid_by_backend):
    by_id, parent_map = _build_ax_maps(ax_nodes)
    # Find tree roots (not children of any node)
    all_ids = set(by_id)
    child_ids = set(cid for n in ax_nodes for cid in n.get("childIds", []))
    roots = [nid for nid in all_ids if nid not in child_ids]
    lines = []
    id_map = {}
    reverse = {}
    # Render navigation/sidebar separately if present
    navs, mains, others = [], [], []
    for rid in roots:
        node = by_id[rid]
        role = node.get("role", {}).get("value", "")
        if role in ("navigation", "complementary"):
            navs.append(rid)
        elif role in ("main", "region", "article"):
            mains.append(rid)
        else:
            others.append(rid)
    if navs:
        lines.append("Navigation:")
        for rid in navs:
            lines.extend(_render_node(rid, by_id, wid_by_backend, reverse, id_map, 1))
    if mains:
        lines.append("Main content:")
        for rid in mains:
            lines.extend(_render_node(rid, by_id, wid_by_backend, reverse, id_map, 1))
    if others:
        for rid in others:
            lines.extend(_render_node(rid, by_id, wid_by_backend, reverse, id_map, 0))
    return {"text": "\n".join(lines), "id_map": id_map}

def translate(state):
    return render_structured(state["ax_nodes"], state["wid_by_backend"])

