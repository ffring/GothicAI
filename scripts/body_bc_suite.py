#!/usr/bin/env python3
"""Frozen, bounded stage-7 phase orchestration on the simulation/training host."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv):
    print("Running " + " ".join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("dataset", "verify", "overfit", "train", "evaluate"))
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Stage 7 model phases run on dev-ubuntu, never the development Mac")
    root = args.run.resolve()
    code = root / "code"
    config_path = code / "evals/npc-body/body-bc-v1.json"
    config = read(config_path)
    dataset_code = code / "scripts/body_trajectory_dataset.py"
    dataset = root / "dataset"
    cli = [sys.executable, "-m", "gothic_body.cli"]
    common = ["--dataset", dataset, "--dataset-code", dataset_code]
    training = config["training"]
    execution = ["--device", training["device"], "--threads", training["threads"]]

    if args.phase == "dataset":
        baseline = Path.home() / "gothic-ai/body/data/stage6-v1/dataset/dataset.json"
        original = read(baseline)
        episodes = [Path(row["path"]) for row in original["episodes"]]
        demos = root / "demonstrations"
        episodes.extend(sorted(path.parent for path in (demos / "episodes").glob("*/trajectory-manifest.json")))
        if len(episodes) == len(original["episodes"]):
            raise RuntimeError("Collect new approved demonstrations before building the BC dataset")
        command(cli + ["build-dataset", *episodes, "--output", dataset, "--dataset-code", dataset_code,
                       "--existing-assignments", baseline, "--teacher-registry", demos / "teacher-registry.json",
                       "--split-seed", config["dataset"]["split_seed"], "--max-label-age-game-ms",
                       config["dataset"]["max_label_age_game_ms"]])
        built = read(dataset / "dataset.json")
        if not built["diagnostic_bc_coverage_ready"]:
            raise RuntimeError("Effective periodic-view BC coverage is insufficient; collect missing demonstrations")
        return 0

    if args.phase in ("verify", "overfit"):
        settings = config["verification" if args.phase == "verify" else "overfit"]
        for architecture in ("gru", "mlp"):
            output = root / "models" / architecture
            output.mkdir(parents=True, exist_ok=True)
            options = ["--architecture", architecture, "--seed", settings["seed"], *execution]
            if args.phase == "verify":
                command(cli + ["verify", *common, *options, "--output", output / "verify.json",
                               "--warmup", settings["warmup"], "--iterations", settings["iterations"]])
                result = read(output / "verify.json")
                if result["step_latency_ms"]["p95"] > settings["model_step_p95_budget_ms"]:
                    raise RuntimeError("CPU latency budget failed; measure an alternate server execution profile")
            else:
                command(cli + ["overfit", *common, *options, "--output", output / "overfit",
                               "--steps", settings["steps"], "--max-chunks", settings["max_chunks"],
                               "--target-accuracy", settings["target_accuracy"], "--learning-rate", settings["learning_rate"],
                               "--weight-decay", settings["weight_decay"], "--max-wall-seconds", settings["max_wall_seconds"]])
        return 0

    if args.phase == "train":
        selection = {"schema_version": 1, "selection_split": "eval", "criterion": training["selection_criterion"],
                     "config_sha256": digest(config_path), "test_and_ood_used_for_selection": False, "models": {}}
        for architecture in ("gru", "mlp"):
            output = root / "models" / architecture
            output.mkdir(parents=True, exist_ok=True)
            candidates = []
            for seed in training["seeds"]:
                job = output / f"seed-{seed}"
                # A finished seed is immutable and may be reused after a later
                # seed fails; no partial checkpoint or test score is reused.
                if not (job / "training.json").exists():
                    command(cli + ["train", *common, *execution, "--architecture", architecture, "--seed", seed,
                                   "--output", job, "--epochs", training["epochs"], "--patience", training["patience"],
                                   "--batch-size", training["batch_size"], "--chunk-length", training["chunk_length"],
                                   "--burn-in", training["burn_in"], "--learning-rate", training["learning_rate"],
                                   "--weight-decay", training["weight_decay"], "--gradient-clip", training["gradient_clip"],
                                   "--max-wall-seconds", training["max_wall_seconds"]])
                result = read(job / "training.json")
                if result["status"] != "complete" or result["test_and_ood_used_for_selection"]:
                    raise RuntimeError("An incomplete or held-out-selected training result cannot be deployed")
                expected_config = {key: training[key] for key in ("epochs", "patience", "batch_size", "chunk_length",
                                   "burn_in", "learning_rate", "weight_decay", "gradient_clip", "max_wall_seconds",
                                   "device", "threads")}
                expected_config.update(architecture=architecture, seed=seed, stack_size=config["models"]["mlp"]["stack_size"])
                if any(result["config"].get(key) != value for key, value in expected_config.items()):
                    raise RuntimeError("Training configuration differs from the frozen run")
                for key, path in (("dataset_sha256", dataset / "dataset.json"),
                                  ("normalization_sha256", dataset / "normalization.json"),
                                  ("dataset_code_sha256", dataset_code)):
                    if result["dataset"].get(key) != digest(path):
                        raise RuntimeError("Training dataset/normalizer/validator bytes changed")
                if result["checkpoint_sha256"] != digest(job / "best.pt"):
                    raise RuntimeError("Training checkpoint bytes changed")
                history = read(job / "history.json")
                epoch = result["selection"]["selected_epoch"]
                if result["selection"]["split"] != "eval" or result["selection"]["criterion"] != training["selection_criterion"]:
                    raise RuntimeError("Training used an undeclared selection criterion")
                best = max(history, key=lambda row: (row["eval"]["macro_f1"], -row["eval"]["nll"]))
                if epoch != best["epoch"]:
                    raise RuntimeError("Selected epoch is not the best eval score in its complete history")
                metrics = next(row["eval"] for row in history if row["epoch"] == epoch)
                sidecar = read(job / "best.pt.json")
                if (sidecar["checkpoint_sha256"] != result["checkpoint_sha256"] or
                    sidecar["metadata"]["dataset"] != result["dataset"] or
                    sidecar["metadata"]["training"]["selected_epoch"] != epoch or
                    sidecar["metadata"]["training"]["selected_eval"] != metrics):
                    raise RuntimeError("Checkpoint sidecar disagrees with the selected training result")
                candidates.append({"seed": seed, "path": str(job.relative_to(root)), "epoch": epoch,
                                   "eval_macro_f1": metrics["macro_f1"], "eval_nll": metrics["nll"],
                                   "checkpoint_sha256": result["checkpoint_sha256"],
                                   "training_sha256": digest(job / "training.json"), "history_sha256": digest(job / "history.json")})
            chosen = max(candidates, key=lambda row: (row["eval_macro_f1"], -row["eval_nll"]))
            source = root / chosen["path"]
            for name in ("best.pt", "best.pt.json", "training.json", "history.json"):
                if (output / name).exists():
                    if digest(output / name) != digest(source / name):
                        raise RuntimeError("Selected artifact already has different bytes; use a fresh run")
                else:
                    shutil.copyfile(source / name, output / name)
            selection["models"][architecture] = {"candidates": candidates, "selected": chosen,
                                                  "checkpoint": str((output / "best.pt").relative_to(root))}
            write(output / "selection.json", selection["models"][architecture])
        write(root / "selection.json", selection)
        return 0

    selection = read(root / "selection.json")
    if selection["selection_split"] != "eval" or selection["test_and_ood_used_for_selection"]:
        raise RuntimeError("Held-out evaluation requires an eval-selected immutable checkpoint")
    for architecture in ("gru", "mlp"):
        output = root / "models" / architecture
        checkpoint = output / "best.pt"
        if digest(checkpoint) != selection["models"][architecture]["selected"]["checkpoint_sha256"]:
            raise RuntimeError("Selected checkpoint changed before held-out evaluation")
        for split in ("eval", "test", "ood"):
            command(cli + ["evaluate", *common, *execution, "--checkpoint", checkpoint, "--split", split,
                           "--output", output / split, "--batch-size", training["batch_size"],
                           "--chunk-length", training["chunk_length"], "--burn-in", training["burn_in"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
