"""Bounded deterministic behavioral cloning; held-out test/OOD never select weights."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
import math
import os
from pathlib import Path
import random
import resource
import time

from .data import dataset_provenance, load_full_split, load_split, tensor_batch
from .evaluation import evaluate_chunks, evaluate_continuous
from .schema import ACTIONS, atomic_json, model_host_guard


@dataclass(frozen=True)
class TrainConfig:
    architecture: str = "gru"
    seed: int = 17
    epochs: int = 120
    batch_size: int = 8
    chunk_length: int = 32
    burn_in: int = 8
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    gradient_clip: float = 1.0
    patience: int = 25
    threads: int = 2
    device: str = "cpu"
    stack_size: int = 8
    max_wall_seconds: float = 1800
    minimum_cuda_free_mib: int = 1536
    cuda_memory_fraction: float = 0.04
    cuda_memory_limit_mib: int = 1024
    evaluation_mode: str = "chunked"

    def __post_init__(self):
        if self.architecture not in ("gru", "mlp") or self.device not in ("cpu", "cuda"):
            raise ValueError("Unsupported actor/device")
        if not 1 <= self.batch_size <= 64 or not 1 <= self.epochs <= 2000:
            raise ValueError("Training batch/epoch budget out of range")
        if not 8 <= self.chunk_length <= 64 or not 0 <= self.burn_in <= 32 or not 1 <= self.threads <= 4:
            raise ValueError("Sequence/thread budget out of range")
        if min(self.learning_rate, self.gradient_clip, self.max_wall_seconds) <= 0 or self.weight_decay < 0:
            raise ValueError("Invalid optimizer/wall budget")
        if self.stack_size not in (4, 8) or self.patience < 1:
            raise ValueError("Invalid MLP stack/early-stop patience")
        if self.evaluation_mode not in ("chunked", "continuous"):
            raise ValueError("Unknown held-out evaluation mode")
        if (type(self.cuda_memory_fraction) not in (int, float) or not math.isfinite(self.cuda_memory_fraction) or
                not 0 < self.cuda_memory_fraction <= 0.04 or type(self.cuda_memory_limit_mib) is not int or
                not 128 <= self.cuda_memory_limit_mib <= 1024 or type(self.minimum_cuda_free_mib) is not int or
                self.minimum_cuda_free_mib < 1024):
            raise ValueError("Invalid bounded CUDA allocation/headroom budget")


def cuda_training_budget(config: TrainConfig, *, free_bytes: int, total_bytes: int) -> dict:
    """Allocator limit and service headroom; CUDA context is outside this cap."""
    if any(type(value) is not int for value in (free_bytes, total_bytes)) or not 0 <= free_bytes <= total_bytes or total_bytes <= 0:
        raise ValueError("Invalid CUDA memory snapshot")
    cap = min(int(total_bytes * config.cuda_memory_fraction), config.cuda_memory_limit_mib * 1024 * 1024)
    headroom = config.minimum_cuda_free_mib * 1024 * 1024
    if cap < 1 or free_bytes < cap + headroom:
        raise RuntimeError("Insufficient free GPU memory for allocator cap plus service headroom; choose dev-ubuntu CPU")
    return {"requested_fraction": config.cuda_memory_fraction, "requested_limit_mib": config.cuda_memory_limit_mib,
            "allocator_limit_bytes": cap, "applied_fraction": cap / total_bytes,
            "free_after_context_bytes": free_bytes, "device_total_bytes": total_bytes,
            "minimum_remaining_free_bytes": headroom,
            "context_and_non_allocator_memory_capped": False,
            "context_monitoring": "External per-process nvidia-smi required; services remain untouched."}


def configure_torch(config: TrainConfig, *, execution_metadata: dict | None = None):
    model_host_guard()
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch
    random.seed(config.seed)
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        if torch.get_num_interop_threads() != 1:
            raise
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    execution = {"device": config.device, "cuda_allocator_limit_applied": False}
    if config.device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        # Apply the fractional ceiling before any model/data tensors. Driver
        # context creation is unavoidable and independently monitored; the
        # final absolute ceiling is installed after reading this device size.
        torch.cuda.set_per_process_memory_fraction(config.cuda_memory_fraction, device=0)
        free, total = torch.cuda.mem_get_info(0)
        budget = cuda_training_budget(config, free_bytes=free, total_bytes=total)
        torch.cuda.set_per_process_memory_fraction(budget["applied_fraction"], device=0)
        execution.update(cuda_allocator_limit_applied=True, visible_device_index=0,
                         cuda_visible_devices=os.environ.get("CUDA_VISIBLE_DEVICES"), cuda_memory_budget=budget)
        torch.cuda.manual_seed_all(config.seed)
        torch.cuda.reset_peak_memory_stats()
    if execution_metadata is not None:
        execution_metadata.update(execution)
    return torch


def _optimizer_step(model, optimizer, chunks, config, weights):
    import torch
    import torch.nn.functional as functional
    features, masks, resets, labels, loss_mask = tensor_batch(chunks, config.device)
    if not bool(loss_mask.any()):
        return 0.0, 0.0, 0
    optimizer.zero_grad(set_to_none=True)
    logits, _ = model(features, masks, resets)
    active_weights = weights[labels[loss_mask]]
    if not bool((active_weights > 0).all()):
        raise ValueError("Missing train class weight at an expert opportunity")
    losses = functional.cross_entropy(logits[loss_mask], labels[loss_mask], reduction="none")
    denominator = active_weights.sum()
    numerator = (losses * active_weights).sum()
    loss = numerator / denominator
    if not bool(torch.isfinite(loss)):
        raise ValueError("Nonfinite BC loss")
    loss.backward()
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip, error_if_nonfinite=True)
    optimizer.step()
    return float(numerator.detach()), float(denominator.detach()), labels[loss_mask].numel()


def train(dataset: Path | str, output: Path | str, config: TrainConfig, *, dataset_code=None) -> dict:
    execution = {}
    torch = configure_torch(config, execution_metadata=execution)
    from .checkpoint import save_checkpoint
    from .models import ActorConfig, create_actor
    started, cpu_started = time.monotonic(), time.process_time()
    train_loader, train_chunks = load_split(dataset, "train", chunk_length=config.chunk_length,
                                          burn_in=config.burn_in, dataset_code=dataset_code)
    if train_loader.report.get("diagnostic_bc_coverage_ready") is not True:
        raise ValueError("Periodic train coverage gate is incomplete; collect genuine missing demonstrations first")
    if config.evaluation_mode == "continuous":
        eval_loader, eval_chunks = load_full_split(dataset, "eval", dataset_code=dataset_code)
        evaluator = evaluate_continuous
    else:
        eval_loader, eval_chunks = load_split(dataset, "eval", chunk_length=config.chunk_length,
                                            burn_in=config.burn_in, dataset_code=dataset_code)
        evaluator = evaluate_chunks
    provenance = dataset_provenance(train_loader, dataset_code)
    if provenance != dataset_provenance(eval_loader, dataset_code):
        raise ValueError("Train/eval dataset provenance differs")
    # Chunks without a decision carry no optimization signal. Each retained
    # chunk still contains its locked observations and recorded burn-in prefix.
    train_chunks = [chunk for chunk in train_chunks if any(row["loss_mask"] for row in chunk["observations"])]
    if not train_chunks:
        raise ValueError("Train has no true expert supervision")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    model = create_actor(ActorConfig(config.architecture, len(train_loader.normalizer["feature_names"]),
                                    stack_size=config.stack_size)).to(config.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    weights = torch.tensor(train_loader.normalizer["class_weights"], dtype=torch.float32, device=config.device)
    shuffle = random.Random(config.seed)
    history, best_score, best_epoch, stopping = [], None, None, "epochs_completed"
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    for epoch in range(1, config.epochs + 1):
        if time.monotonic() - started >= config.max_wall_seconds:
            stopping = "wall_budget"
            break
        model.train()
        order = list(range(len(train_chunks)))
        shuffle.shuffle(order)
        numerator, denominator, labels_seen = 0.0, 0.0, 0
        epoch_start = time.monotonic()
        for offset in range(0, len(order), config.batch_size):
            if time.monotonic() - started >= config.max_wall_seconds:
                stopping = "wall_budget"
                break
            batch = [train_chunks[index] for index in order[offset:offset + config.batch_size]]
            values = _optimizer_step(model, optimizer, batch, config, weights)
            numerator += values[0]
            denominator += values[1]
            labels_seen += values[2]
        if stopping == "wall_budget":
            break
        metrics = evaluator(model, eval_chunks, device=config.device, batch_size=config.batch_size,
                            class_weights=train_loader.normalizer["class_weights"])
        metrics.pop("predictions")
        score = (metrics["macro_f1"], -metrics["nll"])
        improved = best_score is None or score > best_score
        row = {"epoch": epoch, "train_weighted_nll": numerator / denominator,
               "train_labels_seen": labels_seen, "eval": metrics, "selected": improved,
               "wall_seconds": time.monotonic() - epoch_start}
        history.append(row)
        if improved:
            best_score, best_epoch = score, epoch
            checkpoint = save_checkpoint(output / "best.pt", model, normalizer=train_loader.normalizer,
                                         provenance=provenance,
                                         training={"kind": "behavioral_cloning", "config": asdict(config),
                                                   "execution": execution,
                                                   "selected_epoch": epoch, "selection_split": "eval",
                                                   "selection_criterion": "macro_f1_then_unweighted_nll",
                                                   "selection_evaluation_mode": config.evaluation_mode,
                                                   "selected_eval": metrics})
        atomic_json(output / "history.json", history)
        print(f"epoch={epoch} train_nll={row['train_weighted_nll']:.5f} eval_f1={metrics['macro_f1']:.5f} best={best_epoch}",
              flush=True)
        if epoch - best_epoch >= config.patience:
            stopping = "eval_patience"
            break
    if best_epoch is None:
        raise RuntimeError("Training wall budget expired before a complete eval-selected epoch")
    result = {"kind": "body-bc-training", "schema_version": 1, "status": "complete",
              "config": asdict(config), "dataset": provenance, "parameter_count": parameter_count,
              "execution": execution,
              "optimizer": "AdamW; train-only inverse-frequency class-weighted CE; gradient clip",
              "selection": {"split": "eval", "criterion": "macro_f1_then_unweighted_nll",
                            "evaluation_mode": config.evaluation_mode,
                            "selected_epoch": best_epoch}, "completed_epochs": len(history), "stop_reason": stopping,
              "checkpoint": "best.pt", "checkpoint_sha256": checkpoint["checkpoint_sha256"],
              "history": "history.json", "test_and_ood_used_for_selection": False,
              "train_label_count": sum(row["loss_mask"] for chunk in train_chunks for row in chunk["observations"]),
              "wall_seconds": time.monotonic() - started, "process_cpu_seconds": time.process_time() - cpu_started,
              "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if config.device == "cuda" else 0,
              "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved() if config.device == "cuda" else 0,
              "torch_version": str(torch.__version__), "threads": torch.get_num_threads(),
              "interop_threads": torch.get_num_interop_threads(),
              "limitations": ["BC checkpoint quality is provisional until whole-episode closed-loop evaluation.",
                              "Macro F1 includes all 11 primitive classes; missing held-out support contributes zero."]}
    atomic_json(output / "training.json", result)
    return result


def select_overfit_chunks(chunks: list[dict], *, max_chunks: int, included_actions: list[int]) -> tuple[list[dict], dict[int, int]]:
    """Select train chunks and fail before model work if any head is untested."""
    if type(max_chunks) is not int or max_chunks < 1 or not included_actions or any(
            type(action) is not int or not 1 <= action < len(ACTIONS) for action in included_actions):
        raise ValueError("Invalid overfit chunk/action budget")
    candidates = [chunk for chunk in chunks if any(row["loss_mask"] for row in chunk["observations"])]
    # Deterministic greedy class coverage, solely from train. No held-out row is
    # inspected or used by this pipeline diagnostic.
    selected, seen = [], set()
    while candidates and len(selected) < max_chunks:
        best = max(range(len(candidates)), key=lambda index:
                   len({row["label"] for row in candidates[index]["observations"] if row["loss_mask"]} - seen))
        chunk = candidates.pop(best)
        selected.append(chunk)
        seen.update(row["label"] for row in chunk["observations"] if row["loss_mask"])
    counts = Counter(row["label"] for chunk in selected for row in chunk["observations"] if row["loss_mask"])
    missing = [ACTIONS[action] for action in included_actions if not counts[action]]
    if missing:
        raise ValueError(f"Overfit selected subset misses included actions within max_chunks={max_chunks}: "
                         f"{', '.join(missing)}. Increase the declared chunk budget before model training.")
    return selected, {action: counts[action] for action in included_actions}


def overfit(dataset: Path | str, output: Path | str, config: TrainConfig, *, dataset_code=None,
            steps=500, max_chunks=8, target_accuracy=0.98) -> dict:
    loader, chunks = load_split(dataset, "train", chunk_length=config.chunk_length,
                               burn_in=config.burn_in, dataset_code=dataset_code)
    included_actions = loader.report["coverage"]["train"]["included_actions"]
    selected, support = select_overfit_chunks(chunks, max_chunks=max_chunks, included_actions=included_actions)
    if steps < 1 or not 0 < target_accuracy <= 1:
        raise ValueError("Invalid overfit diagnostic budget")
    torch = configure_torch(config)
    from .checkpoint import save_checkpoint
    from .models import ActorConfig, create_actor
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    model = create_actor(ActorConfig(config.architecture, len(loader.normalizer["feature_names"]),
                                    stack_size=config.stack_size)).to(config.device)
    weights = torch.tensor(loader.normalizer["class_weights"], dtype=torch.float32, device=config.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=0)
    before = evaluate_chunks(model, selected, device=config.device, batch_size=config.batch_size,
                             class_weights=loader.normalizer["class_weights"])
    before.pop("predictions")
    started, after, actual_steps = time.monotonic(), before, 0
    history = []
    for step in range(1, steps + 1):
        if time.monotonic() - started > config.max_wall_seconds:
            break
        model.train()
        for offset in range(0, len(selected), config.batch_size):
            _optimizer_step(model, optimizer, selected[offset:offset + config.batch_size], config, weights)
        actual_steps = step
        if step % 10 == 0 or step == steps:
            after = evaluate_chunks(model, selected, device=config.device, batch_size=config.batch_size,
                                    class_weights=loader.normalizer["class_weights"])
            after.pop("predictions")
            history.append({"step": step, "accuracy": after["accuracy"], "nll": after["nll"]})
            if after["accuracy"] >= target_accuracy:
                break
    after = evaluate_chunks(model, selected, device=config.device, batch_size=config.batch_size,
                            class_weights=loader.normalizer["class_weights"])
    after.pop("predictions")
    checkpoint = save_checkpoint(output / "diagnostic.pt", model, normalizer=loader.normalizer,
                                 provenance=dataset_provenance(loader, dataset_code),
                                 training={"kind": "train_only_overfit_diagnostic", "config": asdict(config),
                                           "steps": actual_steps, "deployment_candidate": False})
    result = {"kind": "body-bc-overfit", "schema_version": 1,
              "passed": after["accuracy"] >= target_accuracy, "target_accuracy": target_accuracy,
              "architecture": config.architecture, "seed": config.seed, "source_split": "train",
              "selected_sequences": [{"episode_id": row["episode_id"], "identity": row["identity"],
                                      "first_observation_seq": row["observations"][0]["observation_seq"],
                                      "last_observation_seq": row["observations"][-1]["observation_seq"]} for row in selected],
              "steps": actual_steps, "before": before, "after": after, "history": history,
              "included_actions": included_actions,
              "selected_support_by_action": {ACTIONS[action]: count for action, count in support.items()},
              "selected_support_ready": all(support[action] > 0 for action in included_actions),
              "wall_seconds": time.monotonic() - started, "config": asdict(config),
              "checkpoint_sha256": checkpoint["checkpoint_sha256"], "deployment_candidate": False}
    atomic_json(output / "overfit.json", result)
    return result
