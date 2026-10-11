from __future__ import annotations

from typing import Protocol

from ..protocol import DecideRequest


class EngineError(RuntimeError):
    pass


class Engine(Protocol):
    name: str

    def cost(self, req: DecideRequest) -> int:
        """Tokens one forward pass spends on this request (sum over its question rows)."""
        ...

    def run(self, reqs: list[DecideRequest]) -> list[dict[str, dict]]:
        """Answer a batch in one go: answers per request, in order. Blocking; called off the event loop."""
        ...

    def warmup(self) -> None:
        """Compile kernels for the shapes the scheduler will send, so the first requests are not slow."""
        ...
