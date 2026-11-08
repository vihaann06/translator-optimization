"""v2 compact translator, full-render mode (standard translate(state) contract; see v2/translator.py)."""
from v2.translator import render


def translate(state: dict) -> dict:
    out = render(state)
    return {"text": out["text"], "id_map": out["id_map"]}
