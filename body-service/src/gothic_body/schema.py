"""Shared, model-free policy schema and server-only model guard."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import platform

ACTIONS = ("continue", "approach", "turn_to_target", "strafe_left", "strafe_right", "jump_back",
           "attack_front", "attack_left", "attack_right", "block", "wait_200", "wait_300")
FEATURE_VERSION = "body-periodic-numeric-v1"
CHECKPOINT_VERSION = 1


def model_host_guard() -> None:
    if platform.system() != "Linux":
        raise RuntimeError("Body model imports and forwards run on dev-ubuntu only; use ssh dev-ubuntu.")


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def atomic_json(path: Path | str, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def validate_normalizer(normalizer: dict, feature_names: list[str]) -> None:
    if normalizer.get("source_split") != "train" or normalizer.get("feature_version") != FEATURE_VERSION:
        raise ValueError("Normalizer must be fitted on train with the supported feature schema")
    if normalizer.get("feature_names") != feature_names or len(feature_names) != len(set(feature_names)):
        raise ValueError("Normalizer feature order mismatch")
    for statistic in ("mean", "std"):
        values = normalizer.get(statistic, {})
        if set(values) != set(feature_names):
            raise ValueError("Normalizer feature keys mismatch")
        for name in feature_names:
            value = values[name]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Nonfinite normalizer")
            if statistic == "std" and value <= 0:
                raise ValueError("Normalizer standard deviation must be positive")
