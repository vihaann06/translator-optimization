"""Pruned AX-tree translator variant; see translators/pruned.py for the options."""
from translators.pruned import render


def translate(state: dict) -> dict:
    return render(state, labels=True, tables=True, max_options=25, sections=True, viewport=1)
