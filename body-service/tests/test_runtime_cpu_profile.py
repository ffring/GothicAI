"""CPU allocation and clock receipts with fake Torch; no model or device work."""
from contextlib import nullcontext
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import gothic_body.runtime as runtime


class FakeTorch:
    float32, bool = "float32", "bool"
    def __init__(self):
        self.threads = 8
        self.configured = []
    def set_num_threads(self, threads):
        self.configured.append(threads)
        self.threads = threads
    def get_num_threads(self):
        return self.threads
    def get_num_interop_threads(self):
        return 4
    def inference_mode(self):
        return nullcontext()
    def tensor(self, values, **kwargs):
        return values


def test_historical_default_two_and_optin_one_apply_to_actual_pool_without_changing_interop():
    torch = FakeTorch()
    old = runtime.configure_inference_cpu_threads(torch)
    new = runtime.configure_inference_cpu_threads(torch, 1)
    assert torch.configured == [2, 1]
    assert old == {"inference_cpu_threads": 2, "torch_intraop_threads": 2, "torch_interop_threads": 4}
    assert new == {"inference_cpu_threads": 1, "torch_intraop_threads": 1, "torch_interop_threads": 4}


@pytest.mark.parametrize("threads", [True, 0, 3, "1"])
def test_invalid_inference_budget_fails_before_configuring_any_pool(threads):
    torch = FakeTorch()
    with pytest.raises(ValueError, match="1 or 2"):
        runtime.configure_inference_cpu_threads(torch, threads)
    assert not torch.configured


def test_backend_wall_and_cpu_clocks_are_separate_and_hidden_output_is_unchanged(monkeypatch):
    class Logits:
        def __getitem__(self, index):
            return self
        def detach(self):
            return self
        def cpu(self):
            return self
        def tolist(self):
            return [float(index) for index in range(12)]
    original, returned = object(), object()
    calls = []
    class Model:
        def step(self, values, masks, *, state):
            calls.append((values, masks, state))
            return Logits(), returned
    backend = runtime.TorchBackend.__new__(runtime.TorchBackend)
    backend.torch, backend.device, backend.model = FakeTorch(), "cpu", Model()
    backend.execution_profile = runtime.configure_inference_cpu_threads(backend.torch, 1)
    wall = iter((1_000_000, 11_000_000)); thread = iter((2_000_000, 2_500_000)); process = iter((3_000_000, 4_000_000))
    monkeypatch.setattr(runtime.time, "perf_counter_ns", lambda: next(wall))
    monkeypatch.setattr(runtime.time, "thread_time_ns", lambda: next(thread))
    monkeypatch.setattr(runtime.time, "process_time_ns", lambda: next(process))
    logits, hidden = backend.step([0.0]*184, [True]*12, original)
    assert calls[0][2] is original and hidden is returned and logits == list(map(float, range(12)))
    assert backend.last_step_timing == {"wall_ms": 10.0, "main_thread_cpu_ms": .5, "process_cpu_ms": 1.0}
    assert backend.execution_profile["torch_intraop_threads"] == 1


def test_backend_refuses_unexpected_global_pool_drift_before_any_forward():
    backend = runtime.TorchBackend.__new__(runtime.TorchBackend)
    backend.torch, backend.device = FakeTorch(), "cpu"
    backend.execution_profile = {"inference_cpu_threads": 1, "torch_intraop_threads": 1, "torch_interop_threads": 4}
    class NeverModel:
        def step(self, *args, **kwargs):
            raise AssertionError("drifted pool cannot forward")
    backend.model = NeverModel()
    with pytest.raises(RuntimeError, match="profile changed"):
        backend.step([0.0]*184, [True]*12, None)
    assert all(value >= 0 for value in backend.last_step_timing.values())
