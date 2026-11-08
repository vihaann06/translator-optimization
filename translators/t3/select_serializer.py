# Vendored from huskydj1/webpage-translator@ab85cadf (translators/structural/select_serializer.py); imports adjusted.
"""T5: the hybrid outline, with each native <select> rendered as one line carrying its options inline."""
from translators.t3.ir import Node
from translators.t3.registry import register_serializer
from translators.t3.common import Ids, descendants, element_line, name_of
from translators.t3.hybrid_serializer import HybridSerializer

MAX_INLINE_OPTIONS = 12


def is_native_select(node: Node) -> bool:
    return node.tag == "select" and node.role in ("combobox", "listbox")


@register_serializer
class SelectFoldingSerializer(HybridSerializer):
    """Compaction of a known structure: a native select is one control whose value is chosen with SELECT, so its
    options are listed inline on its line instead of as hundreds of separately addressable lines."""

    name = "hybrid_select_folded"
    principle = __doc__

    def emit(self, node: Node, depth: int, ids: Ids, lines: list):
        if is_native_select(node) and ids.choosable(node):
            options = [name_of(d) for d in descendants(node) if d.role == "option" and name_of(d)]
            shown = options[:MAX_INLINE_OPTIONS]
            tail = f" (+{len(options) - len(shown)} more)" if len(options) > len(shown) else ""
            lines.append(" " * depth + element_line(node, ids) + (f" options: {' | '.join(shown)}{tail}" if shown else ""))
            return
        super().emit(node, depth, ids, lines)
