#!/usr/bin/env python3
"""Export P30 GRU weights and replay Torch/NumPy decision parity offline.

Export/golden need Torch; check needs only NumPy and the standard library.
Recorded observations keep the reset metadata used by body_policy_net_check
beside each observations.jsonl. No game or running policy service is needed.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

import numpy as np

from body_policy_net_check import RuntimeReference, replay_requests
from body_trajectory_dataset import canonical_feature_names, strict_json


def _load_checkpoint(checkpoint: Path):
    package = Path(__file__).resolve().parents[1] / "body-service" / "src"
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
    from gothic_body.checkpoint import load_checkpoint
    from gothic_body.schema import ACTIONS, FEATURE_VERSION

    loaded = load_checkpoint(checkpoint, device="cpu")
    config = loaded["metadata"]["model_config"]
    names = canonical_feature_names(FEATURE_VERSION)
    if (config["architecture"] != "gru" or config["hidden_size"] != 256 or len(names) != 184
            or loaded["metadata"]["feature_names"] != names
            or loaded["metadata"]["actions"] != list(ACTIONS)):
        raise ValueError("bodynet v1 requires the P30 GRU-256 actor with 184 inputs and 12 actions")
    return loaded


def export_weights(checkpoint: Path, output: Path) -> dict:
    """Write a JSON header line, then contiguous little-endian float32 tensors.

    Offsets are bytes from the first byte after the header newline. Linear
    weights use PyTorch's output/input layout; GRUCell gates are reset/update/new.
    The checkpoint SHA is verified against its publication sidecar before export.
    """
    checkpoint, output = Path(checkpoint), Path(output)
    if output.resolve() in (checkpoint.resolve(), checkpoint.with_suffix(checkpoint.suffix + ".json").resolve()):
        raise ValueError("export output must differ from the checkpoint and its sidecar")
    loaded = _load_checkpoint(checkpoint)
    names = loaded["metadata"]["feature_names"]
    arrays = {name: tensor.detach().cpu().numpy() for name, tensor in loaded["model"].state_dict().items()}
    arrays.update({f"normalizer.{statistic}": [loaded["normalizer"][statistic][name] for name in names]
                   for statistic in ("mean", "std")})
    tensors, payload, offset = [], [], 0
    for name, values in arrays.items():
        array = np.asarray(values, dtype="<f4")
        if not np.isfinite(array).all() or name == "normalizer.std" and not (array > 0).all():
            raise ValueError(f"tensor {name} cannot be represented as finite float32 with positive scales")
        data = array.tobytes(order="C")
        tensors.append({"name": name, "shape": list(array.shape), "dtype": "float32",
                        "offset": offset, "nbytes": len(data)})
        payload.append(data)
        offset += len(data)
    header = {"format": "gothic-body-net", "version": 1, "byte_order": "little",
              "offset_origin": "first byte after JSON header newline", "data_nbytes": offset,
              "checkpoint_sha256": loaded["checkpoint_sha256"], "actions": loaded["metadata"]["actions"],
              "feature_names": names, "feature_version": loaded["normalizer"]["feature_version"],
              "tensors": tensors}
    with output.open("wb") as stream:
        stream.write(json.dumps(header, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")
        for data in payload:
            stream.write(data)
    return header


def torch_runtime(checkpoint: Path):
    """Use the real PolicyRuntime with a local CPU backend for the small actor.

    The service backend has a Linux-only startup gate. This offline adapter
    leaves that service and PolicyRuntime unchanged; checkpoint guards still
    apply (the Mac tests explicitly monkeypatch model_host_guard).
    """
    loaded = _load_checkpoint(Path(checkpoint))
    import torch
    from gothic_body.runtime import PolicyRuntime, configure_inference_cpu_threads

    if loaded["metadata"].get("training", {}).get("deployment_candidate") is False:
        raise ValueError("train-only diagnostic checkpoint cannot control a live body")
    configure_inference_cpu_threads(torch)

    class Backend:
        model, metadata, normalizer = loaded["model"], loaded["metadata"], loaded["normalizer"]

        def step(self, features, mask, state):
            with torch.inference_mode():
                logits, hidden = self.model.step(torch.tensor([features], dtype=torch.float32),
                                                 torch.tensor([mask], dtype=torch.bool), state=state)
            return logits[0].tolist(), hidden

    return PolicyRuntime(backend=Backend(), checkpoint_sha256=loaded["checkpoint_sha256"])


def step_record(runtime, request: dict, response: dict) -> dict:
    """Stable golden fields: absent inference is null, cleared memory is zeros."""
    row, processed = request["observation"], response["process"]
    identity = tuple(row[key] for key in ("session_epoch", "world_epoch", "entity_id", "control_generation"))
    state = runtime.states.get(identity)
    hidden = state.hidden if state is not None else None
    inferred = processed["model_processed"]
    return {"kind": "step", "observation": row, "stream_seq": processed["stream_seq"],
            "now_game_ms": request["now_game_ms"],
            **{key: response[key] for key in ("status", "action_id", "reason")},
            **{key: processed.get(key) for key in ("hidden_reset", "model_processed", "selected_action_id",
                                                  "sequence_dt_game_ms")},
            "reset_reason": (processed["reason"] if processed["status"] == "fallback" else "new_identity")
                            if processed["hidden_reset"] else None,
            "features": np.asarray(runtime.last_features, dtype=np.float32).tolist() if inferred else None,
            "logits": processed.get("logits") if inferred else None,
            "hidden_state": (np.zeros(256, dtype=np.float32) if hidden is None else
                             np.asarray(hidden, dtype=np.float32).reshape(256)).tolist()}


def golden(checkpoint: Path, observations: list[Path], output: Path) -> dict:
    output = Path(output)
    sources = {Path(checkpoint).resolve(), Path(checkpoint).with_suffix(Path(checkpoint).suffix + ".json").resolve()}
    for path in observations:
        sources.update((Path(path).parent / name).resolve() for name in
                       (Path(path).name, "arena-events.jsonl", "episode.json", "trajectory-manifest.json"))
    if output.resolve() in sources:
        raise ValueError("golden output must differ from checkpoint and episode inputs")
    runtime = torch_runtime(checkpoint)
    driver = RuntimeReference(runtime)
    summary = {"episodes": 0, "steps": 0, "decisions": 0, "fallbacks": 0}
    with gzip.open(output, "wt", encoding="utf-8") as stream:
        for path in observations:
            steps = 0
            for request in replay_requests(Path(path)):
                response = driver.handle(request)
                command = request["cmd"]
                if command == "step":
                    record = step_record(runtime, request, response)
                    steps += 1
                    summary["steps"] += 1
                    summary["decisions"] += response["status"] == "decision"
                    summary["fallbacks"] += response["status"] == "fallback"
                elif command in ("reset", "end_episode"):
                    record = {"kind": command, "session_epoch": request["session_epoch"],
                              "world_epoch": request["world_epoch"], "checkpoint_sha256": runtime.checkpoint_sha256}
                    if command == "reset":
                        record["initial_game_ms"] = request["initial_game_ms"]
                else:
                    continue
                stream.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
            if steps == 0:
                raise ValueError(f"{path}: journal contains no periodic primary observations")
            summary["episodes"] += 1
    return summary


def check(bodynet: Path, expected: Path) -> dict:
    from body_net_numpy import BodyNet, NumpyRuntime

    runtime = NumpyRuntime(BodyNet(bodynet))
    driver = RuntimeReference(runtime)
    summary = {"episodes": 0, "steps": 0, "mismatches": 0, "max_logits_error": 0.0, "max_hidden_error": 0.0}
    first_mismatch = None
    in_episode = False
    with gzip.open(expected, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            record = strict_json(line)
            kind = record.get("kind")
            if kind in ("reset", "end_episode"):
                if record.get("checkpoint_sha256") != runtime.checkpoint_sha256:
                    raise ValueError(f"golden line {number}: checkpoint SHA differs from bodynet")
                if kind == "reset" and in_episode or kind == "end_episode" and not in_episode:
                    raise ValueError(f"golden line {number}: invalid episode boundary")
                driver.handle({"id": number, "cmd": kind,
                               **{key: record[key] for key in ("session_epoch", "world_epoch")},
                               **({"initial_game_ms": record["initial_game_ms"]} if kind == "reset" else {})})
                in_episode = kind == "reset"
                summary["episodes"] += kind == "reset"
                continue
            if kind != "step" or not in_episode:
                raise ValueError(f"golden line {number}: step inside an episode required")
            request = {"id": number, "cmd": "step", "observation": record["observation"],
                       "now_game_ms": record["now_game_ms"]}
            actual = step_record(runtime, request, driver.handle(request))
            errors = []
            for key in ("stream_seq", "status", "action_id", "reason", "hidden_reset", "reset_reason", "model_processed",
                        "selected_action_id", "sequence_dt_game_ms"):
                if record[key] != actual[key]:
                    errors.append(key)
            for key, size in (("features", 184), ("logits", 12), ("hidden_state", 256)):
                left, right = record[key], actual[key]
                if left is None or right is None:
                    if left is not right:
                        errors.append(key)
                    continue
                left, right = np.asarray(left, dtype=np.float64), np.asarray(right, dtype=np.float64)
                if left.shape != (size,) or right.shape != (size,) or not np.isfinite(left).all():
                    raise ValueError(f"golden line {number}: invalid {key} vector")
                error = float(np.max(np.abs(left-right)))
                # Exported normalizer statistics are float32; the checkpoint's
                # JSON statistics are doubles. Allow their rounding in inputs.
                matches = np.allclose(left, right, atol=1e-5, rtol=1e-6 if key == "features" else 0)
                if not matches:
                    errors.append(key)
                if key in ("logits", "hidden_state"):
                    metric = "max_logits_error" if key == "logits" else "max_hidden_error"
                    summary[metric] = max(summary[metric], error)
            summary["steps"] += 1
            if errors:
                summary["mismatches"] += 1
                first_mismatch = first_mismatch or f"golden line {number}: mismatch in {', '.join(errors)}"
    if in_episode or not summary["steps"]:
        raise ValueError("golden stream must contain complete episodes and at least one step")
    if first_mismatch:
        summary["first_mismatch"] = first_mismatch
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export_parser = commands.add_parser("export", help="write a P30 actor as .bodynet")
    export_parser.add_argument("checkpoint", type=Path)
    export_parser.add_argument("output", type=Path)
    golden_parser = commands.add_parser("golden", help="replay recorded episodes with Torch")
    golden_parser.add_argument("checkpoint", type=Path)
    golden_parser.add_argument("observations", type=Path, nargs="+")
    golden_parser.add_argument("output", type=Path)
    check_parser = commands.add_parser("check", help="compare NumPy decisions with golden data")
    check_parser.add_argument("bodynet", type=Path)
    check_parser.add_argument("golden", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            header = export_weights(args.checkpoint, args.output)
            result = {"tensors": len(header["tensors"]), "data_nbytes": header["data_nbytes"],
                      "checkpoint_sha256": header["checkpoint_sha256"]}
        elif args.command == "golden":
            result = golden(args.checkpoint, args.observations, args.output)
        else:
            result = check(args.bodynet, args.golden)
        print(json.dumps(result, allow_nan=False))
        return int(result.get("mismatches", 0) != 0)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
