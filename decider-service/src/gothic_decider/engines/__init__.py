"""Decision engines: the model behind the scheduler. `strands` needs the GPU extras, `fake` needs nothing."""

from __future__ import annotations

from .base import Engine, EngineError
from .fake import FakeEngine

__all__ = ["Engine", "EngineError", "FakeEngine", "make_engine"]


def make_engine(kind: str, **kwargs) -> Engine:
    match kind:
        case "fake":
            return FakeEngine(**kwargs)
        case "strands":
            from .strands import StrandsEngine  # imports torch

            return StrandsEngine(**kwargs)
    raise ValueError(f"unknown engine {kind!r} (expected strands or fake)")
