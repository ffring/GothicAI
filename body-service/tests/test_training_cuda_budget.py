"""Resource policy checks use a fake CUDA API; no weights or forwards."""
from types import SimpleNamespace
import sys

import pytest

import gothic_body.training as training
from gothic_body.training import TrainConfig, cuda_training_budget


MIB = 1024 * 1024


def test_3090_budget_preserves_service_headroom_after_full_allocator_usage():
    budget = cuda_training_budget(TrainConfig(device="cuda", minimum_cuda_free_mib=3072),
                                  total_bytes=24576*MIB, free_bytes=5100*MIB)
    assert budget["allocator_limit_bytes"] == int(24576*MIB*0.04)
    assert budget["free_after_context_bytes"]-budget["allocator_limit_bytes"] >= 3072*MIB
    assert budget["context_and_non_allocator_memory_capped"] is False


def test_larger_gpu_still_cannot_raise_absolute_one_gib_ceiling():
    budget = cuda_training_budget(TrainConfig(device="cuda"), total_bytes=80*1024*MIB, free_bytes=8192*MIB)
    assert budget["allocator_limit_bytes"] == 1024*MIB
    assert budget["applied_fraction"]*budget["device_total_bytes"] == 1024*MIB


def test_cuda_refuses_spare_memory_that_cannot_cover_cap_and_service_headroom():
    with pytest.raises(RuntimeError, match="allocator cap plus service headroom"):
        cuda_training_budget(TrainConfig(device="cuda", minimum_cuda_free_mib=3072),
                             total_bytes=24576*MIB, free_bytes=3500*MIB)


@pytest.mark.parametrize("kwargs", [
    {"cuda_memory_fraction": 0}, {"cuda_memory_fraction": 0.05},
    {"cuda_memory_fraction": float("nan")}, {"cuda_memory_fraction": True},
    {"cuda_memory_limit_mib": 1025}, {"minimum_cuda_free_mib": 0},
])
def test_explicit_resource_overrides_cannot_bypass_hard_bounds(kwargs):
    with pytest.raises(ValueError, match="bounded CUDA"):
        TrainConfig(**kwargs)


class FakeCuda:
    def __init__(self):
        self.calls = []

    def is_available(self):
        self.calls.append("available")
        return True

    def set_per_process_memory_fraction(self, fraction, device):
        self.calls.append(("cap", fraction, device))

    def mem_get_info(self, device):
        self.calls.append(("memory", device))
        return 5100*MIB, 24576*MIB

    def manual_seed_all(self, seed):
        self.calls.append(("seed", seed))

    def reset_peak_memory_stats(self):
        self.calls.append("reset_peak")


def fake_torch(cuda):
    return SimpleNamespace(cuda=cuda, manual_seed=lambda seed: None,
                           set_num_threads=lambda threads: None, set_num_interop_threads=lambda threads: None,
                           use_deterministic_algorithms=lambda enabled: None,
                           backends=SimpleNamespace(cudnn=SimpleNamespace(benchmark=True, allow_tf32=True),
                                                    cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True))))


def test_cpu_default_never_calls_cuda_even_with_cuda_visible(monkeypatch):
    cuda = FakeCuda()
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(training, "model_host_guard", lambda: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch(cuda))
    metadata = {}
    training.configure_torch(TrainConfig(), execution_metadata=metadata)
    assert cuda.calls == []
    assert metadata == {"device": "cpu", "cuda_allocator_limit_applied": False}


def test_explicit_cuda_installs_both_bounds_and_records_actual_device_budget(monkeypatch):
    cuda = FakeCuda()
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(training, "model_host_guard", lambda: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch(cuda))
    metadata = {}
    training.configure_torch(TrainConfig(device="cuda", cuda_memory_limit_mib=512), execution_metadata=metadata)
    caps = [call for call in cuda.calls if isinstance(call, tuple) and call[0] == "cap"]
    assert caps[0] == ("cap", 0.04, 0)
    assert caps[1][1]*(24576*MIB) == 512*MIB
    assert metadata["cuda_memory_budget"]["allocator_limit_bytes"] == 512*MIB
    assert metadata["cuda_visible_devices"] == "0" and metadata["cuda_allocator_limit_applied"] is True
