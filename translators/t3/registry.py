# Vendored from huskydj1/webpage-translator@f2d083c8 (translators/final/pipeline, translators/structural); imports adjusted.
"""Named, principled transformation and serialization components."""
from typing import Protocol

from translators.t3.ir import IR


class Pass(Protocol):
    name: str
    principle: str

    def __call__(self, ir: IR) -> IR: ...


class Serializer(Protocol):
    name: str
    principle: str

    def serialize(self, ir: IR) -> tuple[str, dict[str, str]]: ...


PASSES: dict[str, type[Pass]] = {}
SERIALIZERS: dict[str, type[Serializer]] = {}


def register_pass(component: type[Pass]) -> type[Pass]:
    if not component.name or not component.principle.strip() or not callable(getattr(component, "__call__", None)):
        raise ValueError("Passes need a name, principle, and callable implementation")
    if component.name in PASSES:
        raise ValueError(f"Duplicate pass: {component.name}")
    PASSES[component.name] = component
    return component


def register_serializer(component: type[Serializer]) -> type[Serializer]:
    if not component.name or not component.principle.strip() or not callable(getattr(component, "serialize", None)):
        raise ValueError("Serializers need a name, principle, and serialize implementation")
    if component.name in SERIALIZERS:
        raise ValueError(f"Duplicate serializer: {component.name}")
    SERIALIZERS[component.name] = component
    return component
