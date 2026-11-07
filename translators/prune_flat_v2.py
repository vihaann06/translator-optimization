"""prune_flat plus fixes from the prune_flat_v1 online run: text blocks stay on separate lines, text repeating a
control's value is dropped, and multi-line items of repeated containers are bulleted (see translators/pruned.py)."""
from translators.pruned import render


def translate(state: dict) -> dict:
    return render(state, labels=True, tables=True, max_options=25, sections=True, indent="",
                  block_breaks=True, value_dedup=True, group_items=True, cleanup=True)
