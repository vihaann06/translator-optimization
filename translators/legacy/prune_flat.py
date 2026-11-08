"""Pruned AX-tree translator variant; see translators/final_translator/pruned.py for the options."""
from translators.final_translator.pruned import render


def translate(state: dict) -> dict:
    return render(state, labels=True, tables=True, max_options=25, sections=True, indent='')
