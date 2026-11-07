"""T3 hybrid (webpage-translator): every choosable element keeps an [id] in the outline; subtrees with nothing
actionable fold to one `text "first 160 chars" (+N chars)` line; landmarks and open modals are labelled.
Same passes and serializer as upstream, fed from the live capture instead of an offline HTML re-render."""
from translators.t3.hybrid_serializer import HybridSerializer
from translators.t3.passes import AbsorbText, DropIgnored, DropNoise
from translators.t3.state_ir import build_ir_from_state

PIPELINE = [DropIgnored(), DropNoise(), AbsorbText()]
SERIALIZER = HybridSerializer()


def translate(state: dict) -> dict:
    ir = build_ir_from_state(state)
    for transform in PIPELINE:
        ir = transform(ir)
    text, id_map = SERIALIZER.serialize(ir)
    return {"text": text, "id_map": id_map}
