"""
Improved translator: Accessibility tree to readable, context-rich transcript with logical groupings.

Contract: translate(state) -> {"text": str, "id_map": {short_id: wid}}
"""

import json
from collections import defaultdict

NOISE = {"generic", "none", "InlineTextBox"}
STATES = {"checked", "selected", "disabled", "expanded", "pressed", "required",
          "readonly", "multiselectable", "level", "valuemin", "valuemax", "valuetext",
          "focused"}

# For blockwise grouping of common patterns
GROUP_ROLES = {
    "list": {"listitem"},
    "table": {"row"},
    "group": {"group"},
    "form": {"form"},
    "comment": {"article", "group", "listitem"},  # comment threads often use these
    "section": {"section", "region", "complementary", "main"},
    "dialog": {"dialog", "alertdialog"}
}

INLINE_ROLES = {"StaticText", "text", "InlineTextBox", "LineBreak"}
CLICKABLE_ROLES = {"link", "button"}
FIELD_ROLES = {"textbox", "searchbox", "combobox", "checkbox", "radio", "spinbutton", "option", "menuitem"}
MEDIA_ROLES = {"image", "img"}

def get_role(node):
    return node.get("role", {}).get("value", "")

def get_name(node):
    return node.get("name", {}).get("value", "")

def get_value(node):
    return node.get("value", {}).get("value", "")

def get_states(node):
    """Extracts relevant property states as a dict."""
    props = {}
    for p in node.get("properties", []):
        if p["name"] in STATES:
            props[p["name"]] = p["value"].get("value")
    return props

def flatten_text(node, by_id):
    """Flatten all descendant StaticText/InlineTextBox into a single string."""
    role = get_role(node)
    text = []
    if role in INLINE_ROLES:
        v = get_name(node)
        if v:
            text.append(v)
        else:
            v = get_value(node)
            if v:
                text.append(str(v))
    for cid in node.get("childIds", []):
        child = by_id.get(cid)
        if child:
            text.append(flatten_text(child, by_id))
    return " ".join(s for s in text if s).strip()

def assign_short_id(wid, reverse, id_map):
    if wid not in reverse:
        short = str(len(reverse) + 1)
        reverse[wid] = short
        id_map[short] = wid
    return reverse[wid]

def first_wid(node, by_id, wid_by_backend):
    """Get the first wid in this node or its subtree."""
    wid = wid_by_backend.get(str(node.get("backendDOMNodeId")))
    if wid: return wid
    for cid in node.get("childIds", []):
        child = by_id.get(cid)
        if child:
            w = first_wid(child, by_id, wid_by_backend)
            if w: return w
    return None

def render_blocks(nodes, wid_by_backend):
    by_id = {n["nodeId"]: n for n in nodes}
    child_ids = {cid for n in nodes for cid in n.get("childIds", [])}
    roots = [n["nodeId"] for n in nodes if n["nodeId"] not in child_ids]

    id_map, reverse = {}, {}
    lines = []

    def block(node_id, depth=0, ctx=None):
        node = by_id[node_id]
        role = get_role(node)
        name = get_name(node)
        value = get_value(node)
        props = get_states(node)
        wid = wid_by_backend.get(str(node.get("backendDOMNodeId")))

        # Compose label for interactive elements
        prefix = ""
        if wid is not None:
            short = assign_short_id(wid, reverse, id_map)
            prefix = f"[{short}] "

        # Block grouping
        block_lines = []
        indent = "  " * depth

        # Special flatten for text-only subtrees (e.g. comment body)
        if role in {"paragraph", "article", "group"} and not CLICKABLE_ROLES.intersection(
                [get_role(by_id[cid]) for cid in node.get("childIds", [])]):
            flat = flatten_text(node, by_id)
            if flat:
                block_lines.append(f"{indent}{flat}")
            # But still traverse children for buttons, etc.
            for cid in node.get("childIds", []):
                child = by_id.get(cid)
                if child and get_role(child) in CLICKABLE_ROLES:
                    block_lines.extend(block(cid, depth+1, ctx))
            return block_lines

        # Comment thread grouping
        if "comment" in (name.lower() + " " + role.lower()) or role in GROUP_ROLES["comment"]:
            author, time, body = None, None, None
            for cid in node.get("childIds", []):
                cnode = by_id[cid]
                crole = get_role(cnode)
                cname = get_name(cnode)
                if "author" in (cname.lower() + " " + crole.lower()) or "user" in cname.lower():
                    author = flatten_text(cnode, by_id)
                elif crole in {"time", "labeltext"} or "ago" in cname.lower() or "date" in cname.lower():
                    time = flatten_text(cnode, by_id)
                elif crole in {"paragraph", "StaticText", "text"} or (not crole and cname):
                    if body is None:
                        body = flatten_text(cnode, by_id)
                    else:
                        body += " " + flatten_text(cnode, by_id)
            if author or body:
                label = f"{indent}Comment"
                if author:
                    label += f" by {author}"
                if time:
                    label += f" ({time})"
                if wid:
                    label = f"{indent}{prefix}{label}"
                block_lines.append(label)
                if body:
                    block_lines.append(f"{indent}  {body}")
            # Traverse children for replies/buttons
            for cid in node.get("childIds", []):
                child = by_id.get(cid)
                if child and get_role(child) in CLICKABLE_ROLES:
                    block_lines.extend(block(cid, depth+1, ctx))
            return block_lines

        # List grouping
        if role == "list":
            block_lines.append(f"{indent}List:")
            for cid in node.get("childIds", []):
                cnode = by_id[cid]
                if get_role(cnode) == "listitem":
                    items = block(cid, depth+1, ctx)
                    if items:
                        block_lines.extend(items)
            return block_lines

        # Table grouping
        if role in {"table", "grid"}:
            block_lines.append(f"{indent}Table:")
            for cid in node.get("childIds", []):
                cnode = by_id[cid]
                if get_role(cnode) == "row":
                    items = block(cid, depth+1, ctx)
                    if items:
                        block_lines.extend(items)
            return block_lines

        # Form grouping
        if role == "form":
            block_lines.append(f"{indent}Form:")
            for cid in node.get("childIds", []):
                block_lines.extend(block(cid, depth+1, ctx))
            return block_lines

        # Listitem (with children, e.g., menu, nav)
        if role == "listitem":
            block_lines.append(f"{indent}- {flatten_text(node, by_id)}")
            for cid in node.get("childIds", []):
                block_lines.extend(block(cid, depth+1, ctx))
            return block_lines

        # Interactive elements
        if role in CLICKABLE_ROLES:
            line = f"{indent}{prefix}{role}"
            if name:
                line += f" {json.dumps(name, ensure_ascii=False)}"
            if value and value != name:
                line += f" value={json.dumps(value, ensure_ascii=False)}"
            for key, val in props.items():
                line += f" {key}={json.dumps(val, ensure_ascii=False)}"
            block_lines.append(line)
            # Render text children (e.g. link text)
            for cid in node.get("childIds", []):
                cnode = by_id[cid]
                if get_role(cnode) in INLINE_ROLES:
                    flat = flatten_text(cnode, by_id)
                    if flat:
                        block_lines.append(f"{indent}  {flat}")
            return block_lines

        # Fields/inputs
        if role in FIELD_ROLES:
            line = f"{indent}{prefix}{role}"
            if name:
                line += f" {json.dumps(name, ensure_ascii=False)}"
            if value and value != name:
                line += f" value={json.dumps(value, ensure_ascii=False)}"
            for key, val in props.items():
                line += f" {key}={json.dumps(val, ensure_ascii=False)}"
            block_lines.append(line)
            # Show options for combobox/listbox
            if role in {"combobox", "listbox"}:
                for cid in node.get("childIds", []):
                    cnode = by_id[cid]
                    if get_role(cnode) == "option":
                        opt = flatten_text(cnode, by_id)
                        sel = get_states(cnode).get("selected")
                        short = None
                        wid_opt = wid_by_backend.get(str(cnode.get("backendDOMNodeId")))
                        if wid_opt:
                            short = assign_short_id(wid_opt, reverse, id_map)
                        opt_line = f"{indent}  "
                        if short:
                            opt_line += f"[{short}] "
                        opt_line += f"option {json.dumps(opt, ensure_ascii=False)}"
                        if sel:
                            opt_line += " selected=true"
                        block_lines.append(opt_line)
            return block_lines

        # Media
        if role in MEDIA_ROLES:
            desc = name if name else value
            line = f"{indent}{prefix}image"
            if desc:
                line += f" {json.dumps(desc, ensure_ascii=False)}"
            block_lines.append(line)
            return block_lines

        # Sectioning
        if role in GROUP_ROLES["section"]:
            if name:
                block_lines.append(f"{indent}Section: {name}")
            for cid in node.get("childIds", []):
                block_lines.extend(block(cid, depth+1, ctx))
            return block_lines

        # Fallback: non-noise, non-ignored, not just layout
        if role and role not in NOISE and not node.get("ignored", False):
            flat = flatten_text(node, by_id)
            if flat:
                block_lines.append(f"{indent}{role}: {flat}")
            else:
                block_lines.append(f"{indent}{role}")
            for cid in node.get("childIds", []):
                block_lines.extend(block(cid, depth+1, ctx))
            return block_lines

        # Otherwise, traverse children
        for cid in node.get("childIds", []):
            block_lines.extend(block(cid, depth, ctx))
        return block_lines

    for root in roots:
        lines.extend(block(root, 0, None))

    return {"text": "\n".join([l for l in lines if l.strip()]), "id_map": id_map}

def translate(state):
    return render_blocks(state["ax_nodes"], state["wid_by_backend"])

