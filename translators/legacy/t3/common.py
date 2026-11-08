# Vendored from huskydj1/webpage-translator@f2d083c8 (translators/final/pipeline, translators/structural); imports adjusted.
"""Shared structural analysis: which nodes are actionable, what region/owner they belong to, repeated siblings."""
from collections import Counter
import json
import re

from translators.legacy.t3.ir import IR, Node

INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider", "spinbutton",
    "option", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "listbox", "textarea", "Iframe"})
INPUT_TAGS = frozenset({"a", "button", "input", "select", "textarea", "option", "summary"})
FIELD_ROLES = frozenset({"textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider", "spinbutton", "listbox", "textarea"})
LANDMARK_LABELS = {
    "banner": "header", "navigation": "navigation", "main": "main content", "complementary": "sidebar",
    "contentinfo": "footer", "search": "search", "form": "form", "region": "region", "dialog": "dialog",
    "alertdialog": "dialog", "menu": "menu", "menubar": "menu bar", "tablist": "tabs", "listbox": "list box",
    "MenuListPopup": "dropdown options", "table": "table", "grid": "grid", "tree": "tree"}
STATE_KEYS = ("checked", "selected", "expanded", "pressed", "disabled", "required", "invalid", "readonly", "multiselectable")
ROLE_TAG = {
    "button": "button", "link": "a", "textbox": "input", "searchbox": "input", "combobox": "select", "checkbox": "input",
    "radio": "input", "switch": "input", "option": "option", "menuitem": "li", "tab": "button", "heading": "h",
    "listitem": "li", "list": "ul", "image": "img", "dialog": "dialog", "alertdialog": "dialog", "navigation": "nav",
    "banner": "header", "contentinfo": "footer", "main": "main", "form": "form", "paragraph": "p", "table": "table",
    "row": "tr", "cell": "td", "columnheader": "th", "rowheader": "th", "LabelText": "label", "StaticText": "",
    "textarea": "textarea", "menu": "ul", "tablist": "div", "group": "div", "generic": "div", "region": "section",
    "complementary": "aside", "search": "form", "figure": "figure", "strong": "strong", "emphasis": "em"}
INPUT_TYPE = {"textbox": "text", "searchbox": "search", "checkbox": "checkbox", "radio": "radio", "switch": "checkbox"}


MARKER_ONLY = re.compile(r"^[\s•◦▪·\-–—*|>»·○●■□]+$")


def clean_name(name: str) -> str:
    """List markers and decorative punctuation are not names."""
    name = " ".join(str(name).split())
    return "" if MARKER_ONLY.match(name) else name


def q(text):
    return json.dumps(" ".join(str(text).split()), ensure_ascii=False)


def actionable(node: Node) -> bool:
    if node.role == "RootWebArea" or node.backend_node_id is None:
        return False
    return node.role in INTERACTIVE_ROLES or node.clickable or node.focusable or (node.tag in INPUT_TAGS)


def is_landmark(node: Node) -> bool:
    return node.role in LANDMARK_LABELS and node.role not in ("menu", "menubar", "tablist", "listbox", "MenuListPopup", "table", "grid", "tree")


def is_modal(node: Node) -> bool:
    return node.role in ("dialog", "alertdialog") and (node.properties.get("modal") is True or
                                                       node.attributes.get("aria-modal") == "true")


def states(node: Node) -> list[str]:
    out = []
    for key in STATE_KEYS:
        if key in node.properties:
            value = node.properties[key]
            if value in (True, "true"):
                out.append(key)
            elif value not in (False, "false", None, "") and key in ("checked", "expanded", "pressed", "selected"):
                out.append(f"{key}={value}")
    return out


def value_text(node: Node):
    if node.has_value and node.value not in (None, ""):
        return str(node.value)
    if node.attributes.get("value") and node.role in FIELD_ROLES:
        return node.attributes["value"]
    return None


def name_of(node: Node) -> str:
    return clean_name(node.normalized_name)


def first_text(node: Node, limit=80) -> str:
    """Title-like text of a subtree: the node's own name, else the first heading/name inside it."""
    if name_of(node):
        return name_of(node)[:limit]
    stack = list(reversed(node.children))
    best = ""
    while stack:
        current = stack.pop()
        if name_of(current):
            if current.role == "heading":
                return name_of(current)[:limit]
            if not best:
                best = name_of(current)[:limit]
        stack.extend(reversed(current.children))
    return best


def signature(node: Node, depth=2) -> str:
    """Structural shape of a subtree: roles of the node and its descendants to a small depth, order kept."""
    parts = [node.role]
    if depth > 0:
        parts.append("(" + ",".join(signature(c, depth - 1) for c in node.children[:8]) + ")")
    return "".join(parts)


def repeated_groups(children: list[Node], minimum=3):
    """Split a child list into runs; a run of >= minimum consecutive siblings with the same shape is a group."""
    groups, run = [], []
    for child in children:
        if run and signature(child) == signature(run[-1]) and child.role not in ("StaticText", "heading") and sum(1 for _ in descendants(child)) >= 3:
            run.append(child)
        else:
            if len(run) >= minimum:
                groups.append(("group", run))
            else:
                groups.extend(("single", [c]) for c in run)
            run = [child]
    if len(run) >= minimum:
        groups.append(("group", run))
    else:
        groups.extend(("single", [c]) for c in run)
    return groups


def descendants(node: Node):
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


LEAF_CONTENT_ROLES = frozenset({"listitem", "paragraph", "gridcell", "cell", "LabelText", "rowheader", "columnheader", "image"})


def choosable_set(ir: IR) -> set:
    """Backend IDs the reader may choose: actionable nodes, plus content leaves (image, named list item, cell,
    paragraph, label) that contain nothing actionable, since a click there has no more specific target."""
    chosen = set()

    def visit(node: Node) -> bool:  # returns whether the subtree contains anything actionable
        inside = False
        for child in node.children:
            inside = visit(child) or inside
        if actionable(node):
            chosen.add(node.backend_node_id)
            return True
        if (node.backend_node_id is not None and not inside and node.role in LEAF_CONTENT_ROLES
                and (node.role == "image" or clean_name(node.normalized_name))):
            chosen.add(node.backend_node_id)
        return inside

    for root in ir.roots:
        visit(root)
    return chosen


class Ids:
    """Short IDs for choosable nodes only, in document order; one ID per backend node."""

    def __init__(self, ir: IR):
        self.map, self.reverse = {}, {}
        self.allowed = choosable_set(ir)

    def choosable(self, node: Node) -> bool:
        return node.backend_node_id in self.allowed

    def get(self, node: Node):
        if not self.choosable(node):
            return None
        if node.backend_node_id not in self.reverse:
            short = str(len(self.reverse) + 1)
            self.reverse[node.backend_node_id] = short
            self.map[short] = node.backend_node_id
        return self.reverse[node.backend_node_id]


def role_word(node: Node) -> str:
    return node.role if node.role not in ("generic", "none") else ("clickable" if node.clickable or node.focusable else "generic")


def element_line(node: Node, ids: Ids, include_role=True) -> str:
    short = ids.get(node)
    parts = []
    if short:
        parts.append(f"[{short}]")
    if include_role:
        parts.append(role_word(node))
    if name_of(node):
        parts.append(q(name_of(node)))
    value = value_text(node)
    if value is not None:
        parts.append("value=" + q(value))
    elif node.role in FIELD_ROLES and node.role not in ("checkbox", "radio", "switch"):
        parts.append("(empty)")
    if node.attributes.get("placeholder") and not name_of(node):
        parts.append("placeholder=" + q(node.attributes["placeholder"]))
    parts.extend(states(node))
    if node.role == "heading" and node.properties.get("level"):
        parts.append(f"level={node.properties['level']}")
    return " ".join(parts)
