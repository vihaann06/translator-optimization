# Vendored from huskydj1/webpage-translator@f2d083c8 (translators/final/pipeline, translators/structural); imports adjusted.
"""Remove inaccessible AX nodes without losing accessible descendants."""
from translators.legacy.t3.ir import IR, retain
from translators.legacy.t3.registry import register_pass


@register_pass
class DropIgnored:
    """Dead-code elimination: remove ignored AX nodes and retain reachable descendants."""

    name = "drop_ignored"
    principle = __doc__

    def __call__(self, ir: IR) -> IR:
        return retain(ir, lambda node: not node.ignored)

"""Collapse inert wrappers and redundant inline text fragments."""
from translators.legacy.t3.ir import IR, Node, retain
from translators.legacy.t3.registry import register_pass

OUTLINE_STATES = frozenset({"checked", "selected", "disabled", "expanded", "pressed", "required",
                            "readonly", "multiselectable", "level", "valuemin", "valuemax", "valuetext"})
NOISE = frozenset({"generic", "none", "InlineTextBox"})


@register_pass
class DropNoise:
    """Wrapper collapsing / tree flattening: promote children of semantically inert wrappers."""

    name = "drop_noise"
    principle = __doc__

    @staticmethod
    def keep(node: Node) -> bool:
        return node.role != "InlineTextBox" and bool(
            node.role not in NOISE or node.normalized_name or OUTLINE_STATES.intersection(node.properties)
            or node.focusable or node.clickable)

    def __call__(self, ir: IR) -> IR:
        return retain(ir, self.keep)

"""Absorb StaticText children into parent elements to reduce redundancy."""
from dataclasses import replace
from translators.legacy.t3.ir import IR, Node
from translators.legacy.t3.registry import register_pass


@register_pass
class AbsorbText:
    """Deduplication: merge StaticText children into parent name and remove them."""

    name = "absorb_text"
    principle = __doc__

    def __call__(self, ir: IR) -> IR:
        return IR(roots=[self._process(root) for root in ir.roots], dom_nodes=ir.dom_nodes)

    def _process(self, node: Node) -> Node:
        # First, recursively process children
        processed_children = [self._process(child) for child in node.children]
        
        # Check if we should absorb StaticText children
        # Only absorb if parent has weak/empty name and has StaticText children
        if not node.normalized_name:
            # Collect text from StaticText children
            texts = []
            remaining_children = []
            for child in processed_children:
                if child.role == "StaticText" and child.normalized_name:
                    texts.append(child.normalized_name)
                else:
                    remaining_children.append(child)
            
            if texts:
                # Merge texts into parent name
                merged_name = " ".join(texts)
                return replace(node, name=merged_name, children=remaining_children)
        
        # If parent already has a name, check for duplicate StaticText children
        # Remove StaticText children that exactly match the parent's name
        if node.normalized_name:
            remaining_children = []
            for child in processed_children:
                # Keep child if it's not a StaticText or if its name differs from parent
                if child.role != "StaticText" or child.normalized_name != node.normalized_name:
                    remaining_children.append(child)
            
            if len(remaining_children) != len(processed_children):
                return replace(node, children=remaining_children)
        
        # No changes needed, but update children if they were processed
        if processed_children != node.children:
            return replace(node, children=processed_children)
        
        return node

