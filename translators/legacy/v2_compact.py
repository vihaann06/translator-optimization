"""v2 compact translator, full-render mode (standard translate(state) contract; see translators/legacy/v2/translator.py)."""
from translators.legacy.v2.translator import render


def translate(state: dict) -> dict:
    out = render(state)
    return {"text": out["text"], "id_map": out["id_map"]}
