# Vendored from huskydj1/webpage-translator@ab85cadf (translators/structural/passes/join_labels.py); imports adjusted.
"""Join labels onto nameless controls (an index join: label[for] -> id, enclosing label, adjacent label text,
descendant text, alt/title/placeholder, humanized id/name attribute) and drop meaningless checkbox values."""
from dataclasses import replace
import re

from translators.t3.ir import IR, Node
from translators.t3.registry import register_pass
from translators.t3.common import actionable, clean_name

LABEL_ROLES = frozenset({"LabelText", "StaticText", "paragraph", "generic", "heading", "cell", "listitem"})
JUNK_VALUES = frozenset({"on", "off", "true", "false", "1", "0", "yes", "no"})
GENERIC_TOKENS = frozenset({"input", "field", "text", "txt", "id", "mat", "ui", "auto", "autogen", "autocomplete", "search",
                            "form", "select", "box", "btn", "button", "ctrl", "control", "el", "elem", "element", "item",
                            "edit", "wrapper", "container", "react", "ng", "js", "css", "checkbox", "radio"})
HEXLIKE = re.compile(r"^[0-9a-f]{16,}$|^[0-9A-F]{16,}$")


def humanize(identifier: str) -> str:
    """'emailConfirm' -> 'email Confirm'; returns '' when the identifier is generated or generic."""
    tokens = re.sub(r"([a-z])([A-Z])", r"\1 \2", identifier).replace("_", " ").replace("-", " ").replace(".", " ").split()
    words = [t for t in tokens if t.isalpha() and t.lower() not in GENERIC_TOKENS and not HEXLIKE.match(t)]
    if len(words) < 1 or sum(len(w) for w in words) < 5 or any(len(t) >= 16 for t in tokens):
        return ""
    return " ".join(words)


def wants_name(node: Node) -> bool:
    return node.backend_node_id is not None and not clean_name(node.normalized_name) and (
        actionable(node) or node.role == "image")


@register_pass
class JoinLabels:
    """Information fidelity: every control shows the label a sighted user reads for it, found by joining the
    label to the control through the structures that carry it (for/id, enclosure, adjacency, descent, attributes)."""

    name = "join_labels"
    principle = __doc__

    def __call__(self, ir: IR) -> IR:
        label_for = {}
        for node in ir.walk():
            target = node.attributes.get("for")
            if target and node.role in ("LabelText", "generic") and clean_name(node.normalized_name):
                label_for.setdefault(target, clean_name(node.normalized_name))
        return IR(roots=[self._process(root, None, label_for) for root in ir.roots], dom_nodes=ir.dom_nodes)

    def _process(self, node: Node, parent: Node | None, label_for: dict) -> Node:
        children = [self._process(child, node, label_for) for child in node.children]
        attributes = node.attributes
        name = node.name
        if wants_name(node):
            derived = self._derive(node, parent, label_for)
            if derived:
                name = derived
        if node.role in ("checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio") and "value" in attributes:
            attributes = {k: v for k, v in attributes.items() if k != "value"}
        if any(HEXLIKE.match(str(attributes.get(k, "")).strip()) for k in ("value", "placeholder")):
            attributes = {k: v for k, v in attributes.items() if not (k in ("value", "placeholder") and HEXLIKE.match(str(v).strip()))}
        if node.has_value and HEXLIKE.match(str(node.value).strip()):
            node = replace(node, value="(filled)")
        if name != node.name or attributes is not node.attributes or children != node.children:
            return replace(node, name=name, attributes=attributes, children=children)
        return node

    def _derive(self, node: Node, parent: Node | None, label_for: dict) -> str:
        a = node.attributes
        for key in ("aria-label", "aria_label"):
            if clean_name(a.get(key, "")):
                return clean_name(a[key])
        if a.get("id") and a["id"] in label_for:
            return label_for[a["id"]]
        if parent is not None and clean_name(parent.normalized_name):
            others = [c for c in parent.children if c is not node and (actionable(c) or c.role == "image")]
            if not others and parent.role in LABEL_ROLES | {"button", "link", "listitem", "cell", "gridcell", "generic", "LabelText"}:
                return clean_name(parent.normalized_name)
        if parent is not None:
            siblings = parent.children
            index = next((i for i, c in enumerate(siblings) if c is node), None)
            if index is not None:
                after_first = node.role in ("checkbox", "radio", "switch")
                for j in ((index + 1, index - 1) if after_first else (index - 1, index + 1)):
                    if 0 <= j < len(siblings):
                        sib = siblings[j]
                        if sib.role == "LabelText" and clean_name(sib.normalized_name) and not actionable(sib):
                            return clean_name(sib.normalized_name)
                for j in (index - 1, index + 1):
                    if 0 <= j < len(siblings):
                        sib = siblings[j]
                        if (sib.role in LABEL_ROLES and clean_name(sib.normalized_name) and not actionable(sib)
                                and not any(actionable(d) for d in self._descendants(sib))):
                            return clean_name(sib.normalized_name)[:80]
        texts = [] if node.role in ("combobox", "listbox") or node.tag == "select" else [
            clean_name(d.normalized_name) for d in self._descendants(node) if clean_name(d.normalized_name)]
        if texts:
            return " ".join(dict.fromkeys(texts))[:80]
        for key in ("title", "alt", "placeholder"):
            if clean_name(a.get(key, "")) and not HEXLIKE.match(a[key].strip()):
                return clean_name(a[key])
        if node.role in ("checkbox", "radio") and a.get("value") and a["value"].lower() not in JUNK_VALUES and a["value"].isalpha():
            return a["value"]
        for key in ("name", "id"):
            if a.get(key) and humanize(a[key]):
                return humanize(a[key])
        return ""

    @staticmethod
    def _descendants(node: Node):
        stack = list(node.children)
        while stack:
            current = stack.pop()
            yield current
            stack.extend(current.children)
