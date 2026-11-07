# Vendored from huskydj1/webpage-translator@f2d083c8 (translators/final/pipeline, translators/structural); imports adjusted.
"""T3: every choosable control stays visible in the outline; non-interactive content subtrees fold to one line."""
from translators.t3.ir import IR, Node
from translators.t3.registry import register_serializer
from translators.t3.common import (Ids, LANDMARK_LABELS, descendants, element_line, is_landmark, is_modal, name_of)


@register_serializer
class HybridSerializer:
    """Rate allocation: full detail for actionable structure, lossy one-line summaries for passive content."""

    name = "hybrid_controls_folded_content"
    principle = __doc__

    def serialize(self, ir: IR) -> tuple[str, dict[str, str]]:
        ids = Ids(ir)
        lines = []
        for root in ir.roots:
            self.emit(root, 0, ids, lines)
        return "\n".join(lines), ids.map

    def emit(self, node: Node, depth: int, ids: Ids, lines: list):
        if node.role in ("RootWebArea", "none", "InlineTextBox", "ListMarker"):
            for c in node.children:
                self.emit(c, depth, ids, lines)
            return
        pad = " " * depth
        has_control = ids.choosable(node) or any(ids.choosable(d) for d in descendants(node))
        if not has_control:
            texts = [name_of(d) for d in [node, *descendants(node)] if name_of(d)]
            if texts:
                joined = " ".join(dict.fromkeys(texts))
                lines.append(pad + (f'heading "{joined[:160]}"' if node.role == "heading" else
                                    f'text "{joined[:160]}"' + (f" (+{len(joined) - 160} chars)" if len(joined) > 160 else "")))
            return
        if is_landmark(node) or is_modal(node):
            label = ("OPEN MODAL " if is_modal(node) else "") + LANDMARK_LABELS.get(node.role, node.role).upper()
            lines.append(pad + f"[{label}]" + (f' "{name_of(node)[:80]}"' if name_of(node) else ""))
            for c in node.children:
                self.emit(c, depth + 1, ids, lines)
            return
        if ids.choosable(node) or name_of(node) or node.role == "heading":
            lines.append(pad + element_line(node, ids))
            for c in node.children:
                self.emit(c, depth + 1, ids, lines)
        else:
            for c in node.children:
                self.emit(c, depth, ids, lines)
