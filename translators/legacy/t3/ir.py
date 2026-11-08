# Vendored from huskydj1/webpage-translator@f2d083c8 (translators/final/pipeline, translators/structural); imports adjusted.
"""Lossless AX structure with a whitelisted DOM join, before optimization."""
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator

DOM_ATTRIBUTES = frozenset({"id", "class", "href", "type", "placeholder", "title", "alt", "name", "value",
                            "is_clickable", "tabindex", "role", "disabled", "readonly", "checked", "selected",
                            "multiple", "contenteditable", "for", "form", "target", "rel", "src"})


@dataclass(frozen=True)
class Bounds:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class DOMNode:
    dom_backend_id: int
    tag: str
    attributes: dict[str, str]
    backend_node_id: str | None = None
    bounds: Bounds | None = None


@dataclass
class Node:
    ax_id: str
    role: str = ""
    name: str = ""
    value: Any = None
    has_value: bool = False
    properties: dict[str, Any] = field(default_factory=dict)
    property_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    ignored: bool = False
    ignored_reasons: list[dict[str, Any]] = field(default_factory=list)
    dom_backend_id: int | None = None
    tag: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    bounds: Bounds | None = None
    backend_node_id: str | None = None
    focusable: bool = False
    clickable: bool = False
    children: list["Node"] = field(default_factory=list)

    @property
    def normalized_name(self) -> str:
        return " ".join(self.name.split())


@dataclass
class IR:
    roots: list[Node] = field(default_factory=list)
    dom_nodes: dict[int, DOMNode] = field(default_factory=dict)

    def walk(self) -> Iterator[Node]:
        stack = list(reversed(self.roots))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))


def retain(ir: IR, predicate: Callable[[Node], bool]) -> IR:
    """Persistent tree filtering: removed wrappers promote their children in order."""
    replacements = {}
    for node in reversed(list(ir.walk())):
        children = [child for original in node.children for child in replacements[id(original)]]
        replacements[id(node)] = [replace(node, children=children)] if predicate(node) else children
    return IR(roots=[node for root in ir.roots for node in replacements[id(root)]], dom_nodes=ir.dom_nodes)
