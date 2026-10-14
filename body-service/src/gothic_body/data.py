"""Load hash-validated periodic trajectories without fitting new statistics."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from .schema import ACTIONS, file_sha256, validate_normalizer


def dataset_module(path: Path | str | None = None):
    path = Path(path) if path is not None else Path(__file__).resolve().parents[3] / "scripts/body_trajectory_dataset.py"
    path = path.resolve()
    if not path.is_file():
        raise ValueError("Provide --dataset-code pointing to the frozen body_trajectory_dataset.py")
    name = "gothic_body_trajectory_dataset_" + file_sha256(path)[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def load_split(dataset: Path | str, split: str, *, chunk_length: int = 32, burn_in: int = 8,
               dataset_code: Path | str | None = None, max_observations: int = 500_000):
    module = dataset_module(dataset_code)
    loader = module.OrderedSequenceLoader(dataset, split=split, chunk_length=chunk_length, burn_in=burn_in)
    return _checked_sequences(loader, max_observations=max_observations)


def load_full_split(dataset: Path | str, split: str, *, dataset_code: Path | str | None = None,
                    max_observations: int = 500_000):
    module = dataset_module(dataset_code)
    loader = module.FullEpisodeLoader(dataset, split=split)
    return _checked_sequences(loader, max_observations=max_observations)


def _checked_sequences(loader, *, max_observations: int):
    if tuple(loader.report.get("actions", ())) != ACTIONS:
        raise ValueError("Dataset action order mismatch")
    # Stage 6's mixed periodic/pre-native stream is audit evidence. It is not
    # the sampling view seen by the first live policy.
    sampling = loader.report.get("model_sampling", {})
    if sampling.get("kind") != "periodic_only" or sampling.get("version") != 1:
        raise ValueError("BC requires the declared periodic-only stage 7 dataset view")
    validate_normalizer(loader.normalizer, loader.normalizer["feature_names"])
    chunks, observation_count = [], 0
    for chunk in loader:
        observation_count += len(chunk["observations"])
        if observation_count > max_observations:
            raise ValueError("Dataset observation budget exceeded")
        for row in chunk["observations"]:
            if row.get("phase") != "post_simulation_post_animation" or row.get("periodic") is not True:
                raise ValueError("A teacher-only exact snapshot entered model memory")
            if row.get("loss_mask") and (row.get("label") is None or row["label"] == 0 or row.get("burn_in")):
                raise ValueError("Invalid supervised opportunity")
        chunks.append(chunk)
    if not chunks:
        raise ValueError(f"Dataset split {loader.split} has no observations")
    return loader, chunks


def tensor_batch(chunks: list[dict], device):
    from .schema import model_host_guard
    model_host_guard()
    import torch
    batch_size, length = len(chunks), max(len(chunk["observations"]) for chunk in chunks)
    feature_count = len(chunks[0]["observations"][0]["features"])
    features = torch.zeros(batch_size, length, feature_count, dtype=torch.float32, device=device)
    masks = torch.zeros(batch_size, length, len(ACTIONS), dtype=torch.bool, device=device)
    masks[..., 0] = True
    resets = torch.zeros(batch_size, length, dtype=torch.bool, device=device)
    labels = torch.zeros(batch_size, length, dtype=torch.long, device=device)
    loss_mask = torch.zeros(batch_size, length, dtype=torch.bool, device=device)
    for index, chunk in enumerate(chunks):
        rows = chunk["observations"]
        count = len(rows)
        features[index, :count] = torch.tensor([row["features"] for row in rows], dtype=torch.float32, device=device)
        row_masks = [row["action_mask"] for row in rows]
        if any(not isinstance(mask, list) or len(mask) != len(ACTIONS) or
               any(type(bit) is not bool for bit in mask) for mask in row_masks):
            raise ValueError("Sequence has an invalid primitive mask")
        if any(not any(mask) for mask in row_masks):
            raise ValueError("Sequence has an empty primitive mask")
        masks[index, :count] = torch.tensor(row_masks, dtype=torch.bool, device=device)
        resets[index, :count] = torch.tensor([row["reset"] for row in rows], dtype=torch.bool, device=device)
        labels[index, :count] = torch.tensor([row["label"] or 0 for row in rows], dtype=torch.long, device=device)
        loss_mask[index, :count] = torch.tensor([row["loss_mask"] for row in rows], dtype=torch.bool, device=device)
    if bool(masks[..., 0][loss_mask].any()):
        raise ValueError("Shared policy decision mask must exclude continue at expert opportunities")
    if bool(loss_mask.any()) and not bool(masks.gather(-1, labels.unsqueeze(-1)).squeeze(-1)[loss_mask].all()):
        raise ValueError("A supervised label is outside its causal primitive mask")
    return features, masks, resets, labels, loss_mask


def dataset_provenance(loader, dataset_code=None) -> dict:
    module = dataset_module(dataset_code)
    return {"dataset_sha256": file_sha256(loader.path / "dataset.json"),
            "normalization_sha256": file_sha256(loader.path / "normalization.json"),
            "dataset_code_sha256": file_sha256(module.__file__),
            "feature_version": loader.report["feature_version"], "action_version": loader.report["action_version"],
            "model_sampling": loader.report["model_sampling"],
            "groups": loader.report["groups"],
            "train_episode_ids": [row["episode_id"] for row in loader.report["episodes"] if row["split"] == "train"]}
