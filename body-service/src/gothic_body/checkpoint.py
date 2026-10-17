"""Hash-bound actor checkpoints with immutable train normalization/schema."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .schema import (ACTIONS, CHECKPOINT_VERSION, FEATURE_VERSION, atomic_json, file_sha256,
                     json_sha256, model_host_guard, validate_normalizer)


def validate_metadata(metadata: dict, normalizer: dict) -> None:
    if metadata.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError("Unsupported checkpoint schema")
    if metadata.get("feature_version") != FEATURE_VERSION or metadata.get("action_version") != 1:
        raise ValueError("Unsupported actor feature/action version")
    if metadata.get("actions") != list(ACTIONS):
        raise ValueError("Checkpoint primitive order mismatch")
    names = metadata.get("feature_names")
    if not isinstance(names, list) or not names or metadata["model_config"]["feature_count"] != len(names):
        raise ValueError("Checkpoint feature count mismatch")
    validate_normalizer(normalizer, names)
    if json_sha256(normalizer) != metadata.get("embedded_normalizer_sha256"):
        raise ValueError("Embedded normalizer changed")
    if set(normalizer.get("episode_ids", [])) != set(metadata["dataset"]["train_episode_ids"]):
        raise ValueError("Normalizer train episode provenance mismatch")
    sampling = metadata["dataset"].get("model_sampling", {})
    if sampling.get("kind") != "periodic_only" or sampling.get("version") != 1:
        raise ValueError("Unsupported checkpoint sampling view")


def save_checkpoint(path: Path | str, model, *, normalizer: dict, provenance: dict, training: dict) -> dict:
    model_host_guard()
    import torch
    path = Path(path)
    metadata = {"checkpoint_version": CHECKPOINT_VERSION, "feature_version": FEATURE_VERSION,
                "action_version": 1, "actions": list(ACTIONS), "feature_names": normalizer["feature_names"],
                "model_config": model.config.to_dict(), "dataset": copy.deepcopy(provenance),
                "normalization_sha256": provenance["normalization_sha256"], "sampling_version": "periodic-v1",
                "embedded_normalizer_sha256": json_sha256(normalizer), "training": copy.deepcopy(training),
                "sampling_semantics": "Every periodic actor observation updates memory once; gaps reset/fallback.",
                "decision_semantics": "New primitive argmax excludes continue(0); locked ticks update memory with continue only."}
    validate_metadata(metadata, normalizer)
    state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    if any(not bool(torch.isfinite(value).all()) for value in state.values()):
        raise ValueError("Cannot save nonfinite policy weights")
    temporary = path.with_name(path.name + ".tmp")
    torch.save({"metadata": metadata, "normalizer": copy.deepcopy(normalizer), "state_dict": state}, temporary)
    temporary.replace(path)
    result = {"kind": "body-bc-checkpoint", "schema_version": 1,
              "checkpoint_sha256": file_sha256(path), "metadata": metadata}
    atomic_json(path.with_suffix(path.suffix + ".json"), result)
    return result


def load_checkpoint(path: Path | str, device="cpu") -> dict:
    model_host_guard()
    import torch
    from .models import create_actor
    path = Path(path)
    sidecar_path = path.with_suffix(path.suffix + ".json")
    sidecar = json.loads(sidecar_path.read_text())
    digest = file_sha256(path)
    if digest != sidecar.get("checkpoint_sha256"):
        raise ValueError("Checkpoint bytes changed after publication")
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if set(payload) != {"metadata", "normalizer", "state_dict"}:
        raise ValueError("Unknown checkpoint payload")
    validate_metadata(payload["metadata"], payload["normalizer"])
    if payload["metadata"] != sidecar.get("metadata"):
        raise ValueError("Checkpoint metadata sidecar mismatch")
    if any(not bool(torch.isfinite(value).all()) for value in payload["state_dict"].values()):
        raise ValueError("Nonfinite checkpoint weights")
    model = create_actor(payload["metadata"]["model_config"])
    model.load_state_dict(payload["state_dict"], strict=True)
    model.to(device).eval()
    return {"model": model, "metadata": payload["metadata"], "normalizer": payload["normalizer"],
            "checkpoint_sha256": digest}
