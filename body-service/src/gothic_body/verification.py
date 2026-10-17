"""Server-only numerical invariants and bounded single-actor latency probe."""
from __future__ import annotations

from pathlib import Path
import resource
import statistics
import time

from .data import dataset_provenance, load_split
from .schema import ACTIONS, atomic_json
from .training import TrainConfig, configure_torch


def verify(dataset: Path | str, output: Path | str, *, architecture="gru", device="cpu", seed=17,
           threads=2, stack_size=8, dataset_code=None, iterations=500, warmup=100) -> dict:
    torch = configure_torch(TrainConfig(architecture=architecture, device=device, seed=seed, threads=threads,
                                        stack_size=stack_size))
    from .models import ActorConfig, create_actor
    loader, chunks = load_split(dataset, "train", dataset_code=dataset_code)
    model = create_actor(ActorConfig(architecture, len(loader.normalizer["feature_names"]),
                                    stack_size=stack_size)).to(device).eval()
    features = torch.randn(2, 13, model.config.feature_count, device=device)
    masks = torch.rand(2, 13, len(ACTIONS), device=device) > 0.4
    masks[..., 0] = True
    resets = torch.zeros(2, 13, dtype=torch.bool, device=device)
    resets[:, 0] = True
    resets[0, 5], resets[1, 9] = True, True
    with torch.inference_mode():
        sequence, sequence_state = model(features, masks, resets)
        state, steps = None, []
        for index in range(features.shape[1]):
            logits, state = model.step(features[:, index], masks[:, index], state, resets[:, index])
            steps.append(logits)
        step_logits = torch.stack(steps, dim=1)
        forward_error = float((sequence[masks] - step_logits[masks]).abs().max())
        state_error = float((sequence_state - state).abs().max())
        fresh, fresh_state = model.step(features[:, 0], masks[:, 0])
        contaminated = model.initial_state(features[:, 0]) + 123
        reset_logits, reset_state = model.step(features[:, 0], masks[:, 0], contaminated,
                                              torch.ones(2, dtype=torch.bool, device=device))
        reset_error = max(float((fresh[masks[:, 0]] - reset_logits[masks[:, 0]]).abs().max()),
                          float((fresh_state - reset_state).abs().max()))
        probabilities = sequence.softmax(-1)
        invalid_probability = float(probabilities[~masks].abs().max())
        if not bool(torch.isfinite(sequence).all()):
            raise ValueError("Nonfinite masked categorical logits")
        # A batch row resetting cannot erase another NPC's state.
        second_logits, second_state = model.step(features[1:2, -1], masks[1:2, -1], state[1:2],
                                                torch.zeros(1, dtype=torch.bool, device=device))
        batch_logits, batch_state = model.step(features[:, -1], masks[:, -1], state,
                                              torch.tensor([True, False], dtype=torch.bool, device=device))
        isolation_error = max(float((second_logits[masks[1:2, -1]] - batch_logits[1:2][masks[1:2, -1]]).abs().max()),
                              float((second_state - batch_state[1:2]).abs().max()))
        row = chunks[0]["observations"][0]
        sample = torch.tensor([row["features"]], dtype=torch.float32, device=device)
        mask = torch.ones(1, len(ACTIONS), dtype=torch.bool, device=device)
        state, timings = None, []
        if not 1 <= iterations <= 5000 or not 0 <= warmup <= 1000:
            raise ValueError("Verification iteration budget out of range")
        for index in range(warmup + iterations):
            if device == "cuda":
                torch.cuda.synchronize()
            started = time.perf_counter_ns()
            _, state = model.step(sample, mask, state)
            if device == "cuda":
                torch.cuda.synchronize()
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            if index >= warmup:
                timings.append(elapsed)
    timings.sort()
    tolerance = 1e-5
    result = {"kind": "body-bc-verification", "schema_version": 1,
              "architecture": architecture, "seed": seed, "device": device,
              "model_config": model.config.to_dict(), "dataset": dataset_provenance(loader, dataset_code),
              "step_sequence_max_error": forward_error, "step_sequence_state_max_error": state_error,
              "reset_max_error": reset_error, "actor_isolation_max_error": isolation_error,
              "invalid_probability_max": invalid_probability, "tolerance": tolerance,
              "passed": max(forward_error, state_error, reset_error, isolation_error) <= tolerance and invalid_probability == 0,
              "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
              "step_latency_ms": {"p50": statistics.median(timings), "p95": timings[int(0.95 * (len(timings) - 1))],
                                  "max": max(timings), "warmup": warmup, "samples": iterations},
              "threads": torch.get_num_threads(), "interop_threads": torch.get_num_interop_threads(),
              "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else 0,
              "torch_version": str(torch.__version__),
              "latency_scope": "One actor float32 step including feature/shape validation; IPC and simulator excluded."}
    atomic_json(Path(output), result)
    return result
