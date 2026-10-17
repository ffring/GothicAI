"""One-layer GRU-256 and history-stacked MLP with identical actor inputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .schema import ACTIONS, model_host_guard

model_host_guard()
import torch
from torch import Tensor, nn


@dataclass(frozen=True)
class ActorConfig:
    architecture: str
    feature_count: int
    action_count: int = len(ACTIONS)
    hidden_size: int = 256
    encoder_size: int = 256
    head_size: int = 128
    stack_size: int = 8

    def __post_init__(self):
        if self.architecture not in ("gru", "mlp"):
            raise ValueError("architecture must be gru or mlp")
        if self.action_count != len(ACTIONS) or self.feature_count <= 0:
            raise ValueError("Actor feature/action schema mismatch")
        if self.hidden_size != 256 or min(self.encoder_size, self.head_size) < 1:
            raise ValueError("The first actor uses GRU-256 and positive layer sizes")
        if self.stack_size not in (4, 8):
            raise ValueError("MLP history stack must contain 4 or 8 observations")

    def to_dict(self) -> dict:
        return asdict(self)


def masked_logits(logits: Tensor, mask: Tensor) -> Tensor:
    if mask.dtype != torch.bool or logits.shape != mask.shape:
        raise ValueError("Action mask must be a matching boolean tensor")
    if not bool(mask.any(dim=-1).all()):
        raise ValueError("Empty action mask must fall back before policy inference")
    return logits.masked_fill(~mask, torch.finfo(logits.dtype).min)


class BodyActor(nn.Module):
    def _check_step(self, features: Tensor, mask: Tensor, reset: Tensor | None):
        if features.ndim != 2 or features.shape[-1] != self.config.feature_count:
            raise ValueError("Expected features [batch, feature_count]")
        if not features.is_floating_point() or not bool(torch.isfinite(features).all()):
            raise ValueError("Features must be finite floating-point values")
        if mask.shape != (features.shape[0], self.config.action_count) or mask.dtype != torch.bool:
            raise ValueError("Invalid action mask shape/type")
        if reset is not None and (reset.shape != (features.shape[0],) or reset.dtype != torch.bool):
            raise ValueError("Reset must be a boolean vector")

    def forward(self, features: Tensor, mask: Tensor, reset: Tensor | None = None,
                state: Tensor | None = None) -> tuple[Tensor, Tensor]:
        if features.ndim != 3 or features.shape[1] == 0:
            raise ValueError("Expected a nonempty sequence [batch, time, feature_count]")
        if mask.shape != (*features.shape[:2], self.config.action_count):
            raise ValueError("Sequence mask shape mismatch")
        if reset is not None and reset.shape != features.shape[:2]:
            raise ValueError("Sequence reset shape mismatch")
        logits = []
        for index in range(features.shape[1]):
            result, state = self.step(features[:, index], mask[:, index], state,
                                      None if reset is None else reset[:, index])
            logits.append(result)
        return torch.stack(logits, dim=1), state


class GRUActor(BodyActor):
    def __init__(self, config: ActorConfig):
        super().__init__()
        self.config = config
        self.encoder = nn.Sequential(nn.Linear(config.feature_count, config.encoder_size), nn.ReLU())
        self.memory = nn.GRUCell(config.encoder_size, config.hidden_size)
        self.head = nn.Sequential(nn.Linear(config.hidden_size, config.head_size), nn.ReLU(),
                                  nn.Linear(config.head_size, config.action_count))

    def initial_state(self, features: Tensor) -> Tensor:
        return features.new_zeros((features.shape[0], self.config.hidden_size))

    def step(self, features: Tensor, mask: Tensor, state: Tensor | None = None,
             reset: Tensor | None = None) -> tuple[Tensor, Tensor]:
        self._check_step(features, mask, reset)
        state = self.initial_state(features) if state is None else state
        if state.shape != (features.shape[0], self.config.hidden_size):
            raise ValueError("GRU state shape mismatch")
        if state.dtype != features.dtype or state.device != features.device or not bool(torch.isfinite(state).all()):
            raise ValueError("GRU state must be finite with matching dtype/device")
        if reset is not None:
            state = state * (~reset).unsqueeze(-1)
        state = self.memory(self.encoder(features), state)
        return masked_logits(self.head(state), mask), state


class MLPActor(BodyActor):
    def __init__(self, config: ActorConfig):
        super().__init__()
        self.config = config
        self.network = nn.Sequential(nn.Linear(config.feature_count * config.stack_size, config.hidden_size),
                                     nn.ReLU(), nn.Linear(config.hidden_size, config.hidden_size), nn.ReLU(),
                                     nn.Linear(config.hidden_size, config.head_size), nn.ReLU(),
                                     nn.Linear(config.head_size, config.action_count))

    def initial_state(self, features: Tensor) -> Tensor:
        return features.new_zeros((features.shape[0], self.config.stack_size - 1, self.config.feature_count))

    def step(self, features: Tensor, mask: Tensor, state: Tensor | None = None,
             reset: Tensor | None = None) -> tuple[Tensor, Tensor]:
        self._check_step(features, mask, reset)
        state = self.initial_state(features) if state is None else state
        if state.shape != (features.shape[0], self.config.stack_size - 1, self.config.feature_count):
            raise ValueError("MLP history state shape mismatch")
        if state.dtype != features.dtype or state.device != features.device or not bool(torch.isfinite(state).all()):
            raise ValueError("MLP history must be finite with matching dtype/device")
        if reset is not None:
            state = state * (~reset)[:, None, None]
        history = torch.cat((state, features.unsqueeze(1)), dim=1)
        return masked_logits(self.network(history.flatten(1)), mask), history[:, 1:]


def create_actor(config: ActorConfig | dict) -> BodyActor:
    config = ActorConfig(**config) if isinstance(config, dict) else config
    return GRUActor(config) if config.architecture == "gru" else MLPActor(config)
