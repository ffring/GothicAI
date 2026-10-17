#!/usr/bin/env python3
"""Frozen child BC corpus/model phases; numerical jobs run only on dev-ubuntu.

The parent selected v6 dataset stays immutable. New primary-only reactive
defence scenes retain raw preparation but begin model memory at their declared
actual periodic anchor. Selection uses complete contiguous eval episodes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import shutil
import sys

from body_combat_suite import command, digest, group_seed, read, require, write


def frozen(root):
    files = read(root / "environment.json")["frozen_files"]
    for name, expected in files.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root / "code") and digest(path) == expected, "Frozen executable changed: "+name)
    for path in (Path(__file__).resolve(), root / "code/evals/npc-body/body-defence-bc-v1.json"):
        require(str(path.relative_to(root)) in files, "Child suite/declaration missing from frozen inventory")


def engine_binding(root, config, collection_manifest):
    name = config.get("engine_proof_run_name")
    require(isinstance(name, str) and name and Path(name).name == name, "Root must preregister the actual same-binary motor proof run")
    proof = root.parent / name
    environment = read(root / "environment.json")
    binary = digest(root / "reference/gothic-body-runner")
    require(binary == environment["binary_sha256"] == collection_manifest["binary_sha256"] ==
            digest(proof / "reference/gothic-body-runner"), "Collection and native motor proof binary differ")
    manifest = read(proof / "setup-probe/manifest.json")
    parity = read(proof / "setup-probe/parity/headless-parity.json")
    require(manifest["binary_sha256"] == binary and manifest["source_sha256"] == collection_manifest["source_sha256"],
            "Same-binary motor proof source differs")
    require(parity["status"] == "matched" and parity["profile"] == "full" and len(parity["episodes"]) == 3 and
            {x["seed"] for x in parity["episodes"]} == {17, 29, 43} and
            all(x["status"] == "matched" and x["difference_count"] == 0 for x in parity["episodes"]),
            "Full native17/29/43 parity proof missing")
    names = ("environment.json", "setup-probe/manifest.json", "setup-probe/parity/headless-parity.json")
    return {"run": name, "path": str(proof), "binary_sha256": binary,
            "source_sha256": collection_manifest["source_sha256"], "inventory": {name: digest(proof / name) for name in names},
            "historical_owned_trajectories_asserted_equal": False,
            "scope": "Inherited genuine v6 feature/action contract plus actual same-binary native parity; new prepared block uses its own physical receipts."}


def make_dataset(root, config, config_path, canonical):
    require(not (root / "dataset").exists() and not (root / "selection.json").exists() and
            not any((root / "models").glob("*/seed-*/training.json")), "Build child corpus before candidate training, never overwrite evidence")
    parent = root.parent / config["reference"]["run"]
    parent_index = parent / "dataset/dataset.json"
    old = canonical.read_json(parent_index)
    require(digest(parent_index) == config["reference"]["dataset_sha256"] and old["episode_count"] == 135 and old["validated"] is True,
            "Immutable selected v6 corpus differs")
    require(digest(parent / "dataset/normalization.json") == config["reference"]["normalizer_sha256"], "Parent train normalizer changed")
    folder = root / config["new_data"]["collection_dir"]
    suite, manifest = canonical.read_json(folder / "suite.json"), canonical.read_json(folder / "manifest.json")
    require(suite.get("status") == "collected" and suite.get("suite_scope") == "full", "Full72 real defence collection required")
    paths = sorted((folder / "episodes").glob("*/trajectory-manifest.json"))
    require(len(paths) == config["new_data"]["episode_count"] == 72 and len(suite["episodes"]) == 72,
            "Defence collection episode inventory differs")
    collection_config_path = root / "code/evals/npc-body/body-defence-demonstrations-v1.json"
    collection = canonical.read_json(collection_config_path)
    require(collection["teacher_id"] == config["new_data"]["teacher_id"] and collection["case_count"] == 72 and
            collection["train_seeds"] == config["new_data"]["train_seeds"] and collection["eval_seeds"] == config["new_data"]["eval_seeds"],
            "Defence teacher or declared groups differ")
    registry = canonical.merge_teacher_registries([old["teacher_registry"], canonical.read_json(folder / "teacher-registry.json")])
    motor = engine_binding(root, config, manifest)
    episodes = [Path(entry["path"]) for entry in old["episodes"]] + [path.parent for path in paths]
    report = canonical.build_dataset(episodes, root / "dataset", sampling_view="periodic-v1",
        max_label_age_game_ms=config["dataset"]["max_label_age_game_ms"],
        min_labels=config["dataset"]["minimum_train_labels_per_action"], included_actions=config["dataset"]["included_actions"],
        existing_assignments=parent_index, group_overrides=collection["group_assignments"], teacher_registry=registry)
    require(report["episode_count"] == 207 and report["feature_version"] == "body-periodic-numeric-v1" and
            report["action_version"] == 1 and report["diagnostic_bc_coverage_ready"] is True, "Combined periodic207 corpus/schema/coverage differs")
    old_entries = {entry["episode_id"]: entry for entry in old["episodes"]}
    for entry in report["episodes"]:
        if entry["episode_id"] in old_entries:
            require(entry["split"] == old_entries[entry["episode_id"]]["split"] and entry["group"] == old_entries[entry["episode_id"]]["group"],
                    "An inherited v6 assignment changed")
    new_labels = Counter()
    new_ids = []
    for entry in report["episodes"]:
        if entry["episode_id"] in old_entries:
            continue
        episode = canonical.validate_episode(entry["path"], teacher_registry=registry)
        require(episode.manifest.get("teacher_provenance", {}).get("teacher_id") == config["new_data"]["teacher_id"], "New labels came from another teacher")
        episode = canonical.periodic_model_view(episode, max_label_age_game_ms=67)
        primary = episode.manifest["initial"]["primary_entity_id"]
        usable = [decision for key, decision in episode.decisions.items() if episode.transitions[key]["usable_for_bc"]]
        require(len(usable) == 1 and usable[0]["action_id"] == 9 and usable[0]["source"] == "scripted" and
                all(key[0] == primary for key in episode.decisions if episode.transitions[key]["usable_for_bc"]),
                "Defence episode must contain one primary-only genuine completed block label")
        new_labels[entry["split"]] += 1
        new_ids.append(entry["episode_id"])
    require(dict(new_labels) == config["new_data"]["required_usable_block_labels"], "Primary-only cold scene block support differs")
    normalizer = canonical.read_json(root / "dataset/normalization.json")
    require(normalizer["source_split"] == "train" and len(normalizer["feature_names"]) == 184 and
            set(normalizer["episode_ids"]) == {entry["episode_id"] for entry in report["episodes"] if entry["split"] == "train"},
            "Normalizer was not recomputed on combined train only")
    eval_entries = [entry for entry in report["episodes"] if entry["split"] == "eval"]
    require({group_seed(entry) for entry in eval_entries} == {223, 227}, "Selection reopened excluded legacy eval or fresh screening")
    profile = {"schema_version": 1, "kind": "body_defence_child_selection_view", "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config_sha256": digest(config_path), "parent_dataset_path": str(parent_index), "parent_dataset_sha256": digest(parent_index),
        "dataset_sha256": digest(root / "dataset/dataset.json"), "normalization_sha256": digest(root / "dataset/normalization.json"),
        "dataset_code_sha256": digest(root / "code/scripts/body_trajectory_dataset.py"),
        "selection_split": "eval", "selection_view": "full_episode", "evaluation_mode": "continuous", "test_and_ood_used_for_selection": False,
        "selection_evaluation_episode_ids": [entry["episode_id"] for entry in eval_entries],
        "selection_evaluation_groups": {entry["group"]: "eval" for entry in eval_entries},
        "new_primary_cold_episode_ids": new_ids, "new_usable_block_labels": dict(new_labels),
        "collection_path": str(folder), "collection_manifest_sha256": digest(folder / "manifest.json"),
        "collection_suite_sha256": digest(folder / "suite.json"), "collection_config_sha256": digest(collection_config_path),
        "teacher_registry_sha256": digest(folder / "teacher-registry.json"), "engine_binding": motor,
        "inherited_assignment_changes": 0, "excluded_v6_eval_reopened": False, "recomputed_normalization_source": "combined_train_only"}
    write(root / "dataset/selection-profile.json", profile)


def selection_profile(root, config, config_path):
    profile = read(root / "dataset/selection-profile.json")
    dataset = read(root / "dataset/dataset.json")
    for key, path in (("config_sha256", config_path), ("dataset_sha256", root / "dataset/dataset.json"),
                      ("normalization_sha256", root / "dataset/normalization.json"), ("dataset_code_sha256", root / "code/scripts/body_trajectory_dataset.py"),
                      ("parent_dataset_sha256", Path(profile["parent_dataset_path"]))):
        require(profile[key] == digest(path), "Child selection data/code provenance changed: "+key)
    require(profile["parent_dataset_sha256"] == config["reference"]["dataset_sha256"] and
            profile["selection_view"] == "full_episode" and profile["evaluation_mode"] == "continuous" and
            profile["test_and_ood_used_for_selection"] is False, "Selection context or parent differs")
    entries = [entry for entry in dataset["episodes"] if entry["split"] == "eval"]
    require(profile["selection_evaluation_episode_ids"] == [entry["episode_id"] for entry in entries] and
            {group_seed(entry) for entry in entries} == {223, 227}, "Selection eval episode set changed")
    motor = profile["engine_binding"]
    require(motor["binary_sha256"] == digest(root / "reference/gothic-body-runner"), "Child runtime binary changed")
    for name, expected in motor["inventory"].items():
        require(digest(Path(motor["path"]) / name) == expected, "Motor proof inventory changed")
    return profile


def train(root, config, config_path, cli, common, *, cuda=False):
    profile = selection_profile(root, config, config_path)
    settings = config["training"]
    require(settings["device"] == "cpu" and settings["seeds"] == [11, 29, 43], "Default child training declaration differs")
    device = "cuda" if cuda else "cpu"
    cap = config["train_cuda"]
    require(not cuda or (cap["minimum_cuda_free_mib"], cap["cuda_memory_fraction"], cap["cuda_memory_limit_mib"]) == (3072, 0.04, 1024),
            "Explicit CUDA training budget changed")
    selection = {"schema_version": 1, "selection_split": "eval", "selection_view": "full_episode", "evaluation_mode": "continuous",
        "criterion": settings["selection_criterion"], "config_sha256": digest(config_path),
        "selection_profile_sha256": digest(root / "dataset/selection-profile.json"), "dataset_sha256": profile["dataset_sha256"],
        "normalization_sha256": profile["normalization_sha256"], "selection_evaluation_episode_ids": profile["selection_evaluation_episode_ids"],
        "selection_evaluation_groups": profile["selection_evaluation_groups"], "training_device": device,
        "test_and_ood_used_for_selection": False, "models": {}}
    old_selection = read(root / "selection.json") if (root / "selection.json").exists() else None
    if old_selection:
        require(all(old_selection[key] == selection[key] for key in ("config_sha256", "dataset_sha256", "training_device")),
                "Existing selection belongs to another frozen execution profile")
    keys = ("epochs", "patience", "batch_size", "chunk_length", "burn_in", "learning_rate", "weight_decay", "gradient_clip", "max_wall_seconds", "threads")
    for architecture in ("gru", "mlp"):
        output = root / "models" / architecture
        output.mkdir(parents=True, exist_ok=True)
        candidates = []
        for seed in settings["seeds"]:
            job = output / f"seed-{seed}"
            expected = {key: settings[key] for key in keys}
            expected.update(architecture=architecture, seed=seed, stack_size=8, evaluation_mode="continuous", device=device)
            if cuda:
                expected.update({key: cap[key] for key in ("minimum_cuda_free_mib", "cuda_memory_fraction", "cuda_memory_limit_mib")})
            if not (job / "training.json").exists():
                require(not job.exists(), "Partial seed artifacts exist; preserve evidence and use a fresh run")
                options = ["--architecture", architecture, "--seed", seed, "--stack-size", 8, "--evaluation-mode", "continuous", "--device", device]
                for key in keys:
                    options += ["--"+key.replace("_", "-"), settings[key]]
                if cuda:
                    for key in ("minimum_cuda_free_mib", "cuda_memory_fraction", "cuda_memory_limit_mib"):
                        options += ["--"+key.replace("_", "-"), cap[key]]
                command(cli + ["train", *common, *options, "--output", job])
            result = read(job / "training.json")
            require(result["status"] == "complete" and result["test_and_ood_used_for_selection"] is False and
                    all(result["config"].get(key) == value for key, value in expected.items()), "Completed training config or selection differs")
            for key, path in (("dataset_sha256", root / "dataset/dataset.json"), ("normalization_sha256", root / "dataset/normalization.json"),
                              ("dataset_code_sha256", root / "code/scripts/body_trajectory_dataset.py")):
                require(result["dataset"].get(key) == digest(path), "Training dataset/code binding differs")
            require(result["checkpoint_sha256"] == digest(job / "best.pt"), "Candidate checkpoint changed")
            history = json.loads((job / "history.json").read_text())
            best = max(history, key=lambda row: (row["eval"]["macro_f1"], -row["eval"]["nll"]))
            require(result["selection"]["selected_epoch"] == best["epoch"] and result["selection"]["split"] == "eval" and
                    result["selection"]["criterion"] == settings["selection_criterion"] and result["selection"].get("evaluation_mode") == "continuous",
                    "Selected epoch differs from full continuous eval history")
            require(all(row["eval"].get("evaluation_mode") == "continuous" and row["eval"].get("artificial_chunk_resets") == 0 and
                        row["eval"].get("duplicate_burnin_labels") == 0 for row in history), "Artificial eval chunks selected weights")
            sidecar = read(job / "best.pt.json")
            require(sidecar["checkpoint_sha256"] == result["checkpoint_sha256"] and sidecar["metadata"]["dataset"] == result["dataset"] and
                    sidecar["metadata"]["training"]["selected_epoch"] == best["epoch"] and
                    sidecar["metadata"]["training"]["selected_eval"] == best["eval"] and
                    sidecar["metadata"]["training"].get("selection_evaluation_mode") == "continuous", "Selected checkpoint sidecar differs")
            candidates.append({"seed": seed, "path": str(job.relative_to(root)), "epoch": best["epoch"],
                "eval_macro_f1": best["eval"]["macro_f1"], "eval_nll": best["eval"]["nll"], "checkpoint_sha256": result["checkpoint_sha256"],
                "training_sha256": digest(job / "training.json"), "history_sha256": digest(job / "history.json")})
        chosen = max(candidates, key=lambda row: (row["eval_macro_f1"], -row["eval_nll"]))
        source = root / chosen["path"]
        for name in ("best.pt", "best.pt.json", "training.json", "history.json"):
            if (output / name).exists():
                require(digest(output / name) == digest(source / name), "Selected artifact has different bytes")
            else:
                shutil.copyfile(source / name, output / name)
        selection["models"][architecture] = {"candidates": candidates, "selected": chosen, "checkpoint": str((output / "best.pt").relative_to(root)),
            "selection_split": "eval", "selection_view": "full_episode", "evaluation_mode": "continuous",
            "selection_evaluation_episode_ids": profile["selection_evaluation_episode_ids"], "selection_evaluation_groups": profile["selection_evaluation_groups"]}
        write(output / "selection.json", selection["models"][architecture])
    selection["weights_fixed_at_utc"] = old_selection["weights_fixed_at_utc"] if old_selection else dt.datetime.now(dt.timezone.utc).isoformat()
    write(root / "selection.json", selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("dataset", "verify", "overfit", "train", "train-cuda", "evaluate"))
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    require(sys.platform == "linux", "Child data/models run only on dev-ubuntu, never the Mac")
    root = args.run.resolve()
    frozen(root)
    code = root / "code"
    sys.path.insert(0, str(code / "scripts"))
    import body_trajectory_dataset as canonical
    config_path = code / "evals/npc-body/body-defence-bc-v1.json"
    config = read(config_path)
    if args.phase == "dataset":
        make_dataset(root, config, config_path, canonical)
        return 0
    profile = selection_profile(root, config, config_path)
    cli = [sys.executable, "-m", "gothic_body.cli"]
    common = ["--dataset", root / "dataset", "--dataset-code", code / "scripts/body_trajectory_dataset.py"]
    settings = config["training"]
    cpu = ["--device", "cpu", "--threads", 2]
    if args.phase in ("verify", "overfit"):
        options = config["verification" if args.phase == "verify" else "overfit"]
        for architecture in ("gru", "mlp"):
            output = root / "models" / architecture
            output.mkdir(parents=True, exist_ok=True)
            actor = ["--architecture", architecture, "--seed", options["seed"], "--stack-size", 8, *cpu]
            if args.phase == "verify":
                command(cli + ["verify", *common, *actor, "--output", output / "verify.json", "--warmup", options["warmup"], "--iterations", options["iterations"]])
                value = read(output / "verify.json")
                require(value["passed"] is True and value["step_latency_ms"]["p95"] <= options["model_step_p95_budget_ms"], "CPU numerical/latency verification failed")
            else:
                command(cli + ["overfit", *common, *actor, "--output", output / "overfit", "--steps", options["steps"],
                    "--max-chunks", options["max_chunks"], "--target-accuracy", options["target_accuracy"], "--learning-rate", options["learning_rate"],
                    "--weight-decay", options["weight_decay"], "--max-wall-seconds", options["max_wall_seconds"],
                    "--chunk-length", settings["chunk_length"], "--burn-in", settings["burn_in"], "--evaluation-mode", "continuous"])
        return 0
    if args.phase in ("train", "train-cuda"):
        train(root, config, config_path, cli, common, cuda=args.phase == "train-cuda")
        return 0
    selection = read(root / "selection.json")
    require(selection["test_and_ood_used_for_selection"] is False and selection["selection_evaluation_episode_ids"] == profile["selection_evaluation_episode_ids"],
            "Held-out evaluation requires fixed eval-selected weights")
    for architecture in ("gru", "mlp"):
        output = root / "models" / architecture
        checkpoint = output / "best.pt"
        require(digest(checkpoint) == selection["models"][architecture]["selected"]["checkpoint_sha256"], "Fixed selected weights changed")
        for split in ("eval", "test", "ood"):
            require(not (output / split).exists(), "Held-out metrics already exist; never overwrite evidence")
            command(cli + ["evaluate", *common, *cpu, "--checkpoint", checkpoint, "--split", split, "--output", output / split,
                "--evaluation-mode", "continuous", "--batch-size", settings["batch_size"], "--chunk-length", settings["chunk_length"], "--burn-in", settings["burn_in"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
