"""One model shared by env threads: exact steps equal single steps; batched steps keep each env's rows."""
from __future__ import annotations

from pathlib import Path
import sys
import threading

import pytest

torch = pytest.importorskip("torch")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"body-service/src"))
from body_rl_collect import SharedBackend  # noqa: E402
from gothic_body.models import ActorConfig, create_actor  # noqa: E402


class Base:
    """The TorchBackend surface SharedBackend uses, around a small random GRU."""
    def __init__(self):
        torch.manual_seed(5)
        self.model = create_actor(ActorConfig("gru", feature_count=6)).eval()
        self.torch, self.normalizer, self.metadata = torch, {}, {}
        self.execution_profile = {"inference_cpu_threads": 1}

    def step(self, features, mask, state):
        with torch.inference_mode():
            logits, state = self.model.step(torch.tensor([features]), torch.tensor([mask]), state=state)
            return logits[0].tolist(), state


def rollout(backend, seed, steps=12):
    generator = torch.Generator().manual_seed(seed)
    state, out = None, []
    for _ in range(steps):
        features = torch.randn(6, generator=generator).tolist()
        logits, state = backend.step(features, [True]*12, state)
        out.append(logits)
    return out


def test_exact_mode_equals_single_steps():
    base = Base()
    assert rollout(SharedBackend(base, batched=False), 1) == rollout(base, 1)


def test_batched_mode_returns_each_env_its_own_rows():
    base = Base()
    shared = SharedBackend(base, batched=True)
    results = {}
    def env(seed):
        results[seed] = rollout(shared, seed)
    threads = [threading.Thread(target=env, args=(seed,)) for seed in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for seed in range(6):
        expected = rollout(base, seed)
        assert len(results[seed]) == len(expected)
        for got, want in zip(results[seed], expected):
            assert max(abs(a-b) for a, b in zip(got, want)) < 1e-4
    assert sum(shared.batches.values()) <= 6*12
