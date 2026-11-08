"""T5 (webpage-translator study-3 final pick): T3 hybrid + labels joined onto nameless controls (JoinLabels) + native
<select> options folded inline onto the select's line. Upstream passes/serializer, fed from the live capture."""
from translators.t3.hybrid_serializer import HybridSerializer  # noqa: F401  (registers base serializer)
from translators.t3.join_labels import JoinLabels
from translators.t3.passes import AbsorbText, DropIgnored, DropNoise
from translators.t3.select_serializer import SelectFoldingSerializer
from translators.t3.state_ir import build_ir_from_state

PIPELINE = [DropIgnored(), DropNoise(), AbsorbText(), JoinLabels()]
SERIALIZER = SelectFoldingSerializer()


def translate(state: dict) -> dict:
    ir = build_ir_from_state(state)
    for transform in PIPELINE:
        ir = transform(ir)
    text, id_map = SERIALIZER.serialize(ir)
    return {"text": text, "id_map": id_map}
