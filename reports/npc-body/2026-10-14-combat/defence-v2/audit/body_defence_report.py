#!/usr/bin/env python3
"""Audit saved defence-child data, six CUDA candidates and CPU proofs.

No Torch/model imports, forwards, simulations or service queries. Canonical
validation and classification come from the child's hash-bound frozen code.
This is an offline integrity report; it gives no closed-loop combat credit.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shlex
import sys


FROZEN_DATASET_FUNCTION_SHA256 = "dee7a7c20ec28e959ee5dcacef966fe57458128c72b5ebede9ea5317b25b68e9"
UNUSED_PARENT_GROUPS = {'["arena-native-melee-v1",29]': "eval", '["arena-native-melee-v1",113]': "eval"}
REPORTER_REGRADE_REASON = "Empty parent eval metadata29/113 is pruned with all135 episodes immutable; suite-only exact3approval extensions are recomputed from canonical closed-source approval; actual systemd Running-as-unit header is independently bound and excluded from numerical command count. Physical data, models and acceptance criteria are unchanged."

PARENT_CHECKPOINTS = {
    "gru": "44727b7c99211c6a5e79b4f9aead852c33e277f6cee5dc4bbed95bd86af15c7c",
    "mlp": "21c81594817959490e2c4d8032d7a75296e878830ae34a89af6e56f0b084d5bf",
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def strict_read(path):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "Duplicate JSON key: "+key)
            result[key] = value
        return result
    value = json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON: "+value)))
    def finite(item):
        if isinstance(item, float):
            require(math.isfinite(item), "Nonfinite JSON number")
        elif isinstance(item, dict):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)
    finite(value)
    return value


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp has no UTC offset")
    return parsed


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)+"\n")


def load_frozen(root):
    environment = strict_read(root/"environment.json")
    files = environment["frozen_files"]
    require(files, "Frozen executable inventory missing")
    checked = {}
    for name, expected in files.items():
        path = (root/name).resolve()
        require(path.is_relative_to(root/"code"), "Frozen inventory escapes child code")
        if {"__pycache__", ".pytest_cache"}.intersection(path.parts):
            continue
        require(path.is_file() and digest(path) == expected, "Frozen file changed: "+name)
        checked[name] = expected
    require(digest(root/"source.tar.gz") == environment["source_archive_sha256"] and
            digest(root/"reference/gothic-body-runner") == environment["binary_sha256"], "Child source archive/binary changed")
    sys.path.insert(0, str(root/"code/scripts"))
    # A separate canonical import explicitly points at this child, avoiding
    # accidental use of the current checkout or immutable parent helper.
    for name in ("body_trajectory_dataset", "body_bc_report"):
        path = root/"code/scripts"/(name+".py")
        require(checked.get(str(path.relative_to(root))) == digest(path), "Required child helper missing from inventory")
        spec = importlib.util.spec_from_file_location(name, path)
        require(spec is not None and spec.loader is not None, "Frozen helper cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    canonical, bc = sys.modules["body_trajectory_dataset"], sys.modules["body_bc_report"]
    for name, module in list(sys.modules.items()):
        if name.startswith("body_") and name != "body_defence_report" and getattr(module, "__file__", None):
            path = Path(module.__file__).resolve()
            require(path.is_relative_to(root/"code/scripts") and checked.get(str(path.relative_to(root))) == digest(path),
                    "Loaded helper is not hash-bound child code: "+name)
    require("torch" not in sys.modules, "Saved-data audit imported Torch")
    return canonical, bc, {"environment_sha256": digest(root/"environment.json"), "files": len(checked),
                           "binary_sha256": environment["binary_sha256"], "source_archive_sha256": environment["source_archive_sha256"]}


def configuration(root, child):
    require(child["kind"] == "body_defence_child_bc" and child["declared_before_measurement"] is True,
            "Undeclared child profile")
    require(child["training"]["device"] == "cpu" and child["training"]["seeds"] == [11, 29, 43] and
            (child["training"]["epochs"], child["training"]["patience"], child["training"]["chunk_length"], child["training"]["burn_in"]) == (120, 25, 64, 32),
            "Preregistered child CPU defaults/sequence budget differ")
    cap = child["train_cuda"]
    require(cap["explicit_phase_only"] is True and cap["same_selection_algorithm"] is True and
            cap["verify_overfit_evaluate_device"] == "cpu" and cap["restart_or_service_changes"] is False and
            (cap["device"], cap["minimum_cuda_free_mib"], cap["cuda_memory_fraction"], cap["cuda_memory_limit_mib"]) == ("cuda", 3072, 0.04, 1024),
            "Explicit CUDA override differs")
    effective = copy.deepcopy(strict_read(root/"code/evals/npc-body/body-bc-v1.json"))
    effective["training"].update(child["training"])
    effective["training"].update({key: cap[key] for key in ("device", "minimum_cuda_free_mib", "cuda_memory_fraction", "cuda_memory_limit_mib")})
    effective["dataset"].update(child["dataset"])
    effective["dataset"]["minimum_true_expert_labels_per_included_class"] = child["dataset"]["minimum_train_labels_per_action"]
    effective["verification"].update(child["verification"])
    effective["overfit"].update(child["overfit"])
    return effective


def child_dataset_evidence(root, config, bc):
    # Identical to the frozen dataset audit except the documented empty-group
    # guard below. No index, source, normalization or model artifact is changed.
    origin = Path(bc.__file__).read_text()
    original = next(node for node in ast.parse(origin).body if isinstance(node, ast.FunctionDef) and node.name == "dataset_evidence")
    require(hashlib.sha256(ast.get_source_segment(origin, original).encode()).hexdigest() == FROZEN_DATASET_FUNCTION_SHA256,
            "Frozen dataset audit function differs from this narrow regrade")
    read_json, canonical, ACTIONS = bc.read_json, bc.canonical, bc.ACTIONS
    PopulationMoments, effective_mask, sum_episode_coverage = bc.PopulationMoments, bc.effective_mask, bc.sum_episode_coverage
    path = root/"dataset"
    index, normalizer = read_json(path/"dataset.json"), read_json(path/"normalization.json")
    cfg = config["dataset"]
    require(index.get("schema_version") == 1 and index.get("feature_version") == cfg["feature_version"] and
            index.get("action_version") == cfg["action_version"] and index.get("actions") == list(ACTIONS), "model dataset schema/action order differs")
    require(index.get("model_sampling") == {"kind": "periodic_only", "version": 1,
            "max_label_age_game_ms": cfg["max_label_age_game_ms"], "ambiguous": "drop_all", "recurrent_updates": "periodic_only"}, "model sampling profile differs")
    require(digest(path/"normalization.json") == index.get("normalization_sha256"), "model normalizer SHA differs")
    inherited = index.get("inherited_assignments", {})
    source = inherited.get("source", {})
    inherited_path = Path(source.get("path", ""))
    require(inherited_path.is_file() and digest(inherited_path) == source.get("sha256"), "immutable stage6 split source missing/changed")
    old = read_json(inherited_path)
    used_parent = {entry["group"]: entry["split"] for entry in old["episodes"]}
    unused_parent = {key: split for key, split in old["groups"].items() if key not in used_parent}
    require(unused_parent == UNUSED_PARENT_GROUPS and inherited.get("groups") == old["groups"],
            "historical group metadata changed or pruning is not exactly empty29/113")
    require(all(index["groups"].get(key) == split for key, split in used_parent.items()) and
            all(key not in index["groups"] for key in unused_parent), "actual parent group assignments changed/reopened")
    registry = index.get("teacher_registry")
    require(isinstance(registry, dict) and canonical.registry_sha256(registry) == index.get("teacher_registry_sha256"), "approved expert registry missing/changed")
    headers = [canonical.ValidatedEpisode(Path(entry["path"]),read_json(Path(entry["path"])/entry["manifest_file"]),[],{},{},{},{})
               for entry in index["episodes"]]
    require(len(headers) == index.get("episode_count"), "model dataset episode count differs")
    assignments = canonical.split_groups(headers, split_seed=cfg["split_seed"], existing_assignments=old["groups"], group_overrides=inherited.get("overrides"))
    require(assignments == index["groups"], "new split groups differ from declared inherited assignment")
    train_ids = [episode.manifest["episode_id"] for episode in headers if assignments[canonical.group_key(episode.manifest)] == "train"]
    require(normalizer.get("source_split") == "train" and normalizer.get("feature_version") == cfg["feature_version"] and
            normalizer.get("episode_ids") == train_ids, "normalizer leaks held-out data")
    names = normalizer["feature_names"]
    require(names == canonical.canonical_feature_names(cfg["feature_version"]), "model feature order differs")
    coverage = {split:canonical._coverage([],min_labels=cfg["minimum_true_expert_labels_per_included_class"],included_actions=cfg["included_actions"])
                for split in ("train","eval","test","ood")}
    labels, ids, epochs, hashes, moments, counts = {}, set(), set(), {}, PopulationMoments(), Counter()
    for entry in index["episodes"]:
        episode_path = Path(entry["path"])
        require(digest(episode_path/entry["manifest_file"]) == entry["manifest_sha256"] and digest(episode_path/entry["records_file"]) == entry["records_sha256"], "canonical episode bytes changed")
        episode = canonical.validate_episode(episode_path, teacher_registry=registry)
        identity = (episode.manifest["session_epoch"], episode.manifest["world_epoch"])
        require(episode.manifest["episode_id"] not in ids and identity not in epochs, "duplicated canonical episode/physical world")
        ids.add(episode.manifest["episode_id"]); epochs.add(identity)
        require(entry["group"] == canonical.group_key(episode.manifest) and entry["split"] == index["groups"][entry["group"]], "model dataset split/group differs")
        view = canonical.periodic_model_view(episode, max_label_age_game_ms=cfg["max_label_age_game_ms"])
        require(view.summary == entry["summary"], "persisted periodic projection differs from canonical source")
        require(all(row["observation"].get("periodic") is True and row["observation"]["phase"] == "post_simulation_post_animation" for row in view.observations.values()), "event-only/teacher snapshot entered recurrent memory")
        for key, transition in view.transitions.items():
            if transition["usable_for_bc"]:
                decision = view.decisions[key]
                row = view.observations[(key[0], decision["observation_seq"])]
                original = episode.decisions[key]
                require(original["expert"] and original["accepted"] and episode.transitions[key]["usable_for_bc"], "projection invented an expert label")
                age = original["t_game_ms"]-row["t_game_ms"]
                require(0 <= age <= cfg["max_label_age_game_ms"] and canonical.identity(row) == canonical.identity(original), "model label leaks future/generation")
                require(row["observation"]["native_decision_eligible"] is True and row["observation"]["availability_mask"][decision["action_id"]], "label outside causal physical opportunity")
                label_key = (entry["episode_id"], canonical.identity(row), row["observation_seq"])
                require(label_key not in labels, "multiple expert labels at one model input")
                labels[label_key] = {"split": entry["split"], "action_id": decision["action_id"], "action_mask": effective_mask(row["observation"]),
                                     "raw_mask": row["observation"].get("action_mask", row["observation"]["availability_mask"]),
                                     "t_game_ms": row["t_game_ms"], "decision_id": decision["decision_id"]}
        sum_episode_coverage(coverage[entry["split"]],canonical._coverage([view],min_labels=cfg["minimum_true_expert_labels_per_included_class"],included_actions=cfg["included_actions"]))
        if entry["split"] == "train":
            for record in view.observations.values():moments.add(canonical.record_features(record))
            counts.update(view.decisions[key]["action_id"] for key,transition in view.transitions.items() if transition["usable_for_bc"])
        for item in episode.manifest["source_files"]:
            p = episode_path/item["path"]; hashes[str(p)] = digest(p)
        del episode,view
    count = moments.count
    require(count > 0 and count == normalizer["observation_count"], "train population differs")
    for name in names:
        v = moments.variance(name); std = 1. if math.sqrt(v) < 1e-6 else math.sqrt(v)
        for field, actual in (("mean", moments.mean[name]), ("variance", v), ("std", std)):
            require(math.isclose(normalizer[field][name], actual, rel_tol=1e-8, abs_tol=1e-8), f"train-only {field} differs: {name}")
    require(normalizer["constant_features"] == [name for name in names if math.sqrt(moments.variance(name)) < 1e-6], "constant feature set differs")
    require(normalizer["class_counts"] == [counts[i] for i in range(12)], "class counts leak held-out/mixed-phase labels")
    classes, total = sum(counts[i] > 0 for i in range(1, 12)), sum(counts.values())
    weights = [0.] + [total/(classes*counts[i]) if counts[i] else 0. for i in range(1, 12)]
    require(normalizer["class_weights"] == weights, "class weights differ from effective train labels")
    sequences, train_chunks = {}, []
    for split in ("train", "eval", "test", "ood"):
        require(index["coverage"][split] == coverage[split], "coverage differs from effective periodic model view")
        observed = set(); chunks = 0
        for chunk in canonical.OrderedSequenceLoader(path, split=split, chunk_length=config["training"]["chunk_length"], burn_in=config["training"]["burn_in"]):
            chunks += 1
            require(chunk["reset"] is True and chunk["observations"][0]["reset"] is True, "recurrent boundary missing")
            for row in chunk["observations"]:
                require(row.get("periodic") is True and row["phase"] == "post_simulation_post_animation", "nonperiodic loader input")
                require(not row["burn_in"] or not row["loss_mask"], "burn-in enters supervised loss")
                if row["loss_mask"]:
                    key = (chunk["episode_id"], tuple(chunk["identity"]), row["observation_seq"])
                    require(key in labels and key not in observed and labels[key]["action_id"] == row["label"] and row["action_mask"] == labels[key]["action_mask"], "loader dropped/duplicated/masked a label")
                    observed.add(key)
            if split == "train":
                train_chunks.append({"episode_id":chunk["episode_id"],"identity":chunk["identity"],"split":split,
                    "first_observation_seq":chunk["observations"][0]["observation_seq"],
                    "last_observation_seq":chunk["observations"][-1]["observation_seq"],
                    "support_by_action":dict(Counter(row["label"] for row in chunk["observations"] if row["loss_mask"]))})
        expected = {key for key, value in labels.items() if value["split"] == split}
        require(observed == expected, "ordered loader loses effective expert labels")
        sequences[split] = {"chunks": chunks, "labels": len(observed)}
    return {"episodes": len(headers), "dataset_sha256": digest(path/"dataset.json"), "normalization_sha256": digest(path/"normalization.json"),
            "feature_count": len(names), "train_observations": count, "train_labels": total, "coverage": coverage,
            "sequences": sequences, "inherited_groups": old["groups"], "raw_sha256": hashes,
            "effective_coverage_ready": coverage["train"]["diagnostic_bc_coverage_ready"], "pruned_unused_parent_groups": unused_parent}, {"index": index, "normalizer": normalizer, "labels": labels, "train_chunks":train_chunks}


def lineage(root, child, canonical):
    parent = (root.parent/child["reference"]["run"]).resolve()
    require(parent.name == "stage8-combat-v6", "Historical selected parent differs")
    parent_index = parent/"dataset/dataset.json"
    old = strict_read(parent_index)
    require(digest(parent_index) == child["reference"]["dataset_sha256"] and old["episode_count"] == 135 and
            digest(parent/"dataset/normalization.json") == child["reference"]["normalizer_sha256"], "Immutable v6 corpus changed")
    parent_selection = strict_read(parent/"selection.json")
    for architecture, expected in PARENT_CHECKPOINTS.items():
        require(digest(parent/"models"/architecture/"best.pt") == parent_selection["models"][architecture]["selected"]["checkpoint_sha256"] == expected,
                "Fixed historical v6 checkpoint differs: "+architecture)
    index = strict_read(root/"dataset/dataset.json")
    profile = strict_read(root/"dataset/selection-profile.json")
    require(index["episode_count"] == len(index["episodes"]) == 207 and index["validated"] is True, "Combined207 corpus missing")
    entries = {entry["episode_id"]: entry for entry in index["episodes"]}
    require(len(entries) == 207 and all(entries.get(entry["episode_id"]) == entry for entry in old["episodes"]), "Inherited episode bytes/assignments changed")
    used_parent_groups = {entry["group"]: entry["split"] for entry in old["episodes"]}
    unused_parent_groups = {group: split for group, split in old["groups"].items() if group not in used_parent_groups}
    require(unused_parent_groups == UNUSED_PARENT_GROUPS, "Unused parent groups are not exactly episode-free29/113")
    expected_groups = dict(used_parent_groups)
    new_groups = strict_read(root/"code/evals/npc-body/body-defence-demonstrations-v1.json")["group_assignments"]
    require(all(group not in expected_groups or expected_groups[group] == split for group, split in new_groups.items()),
            "New declaration changes an actual inherited assignment")
    expected_groups.update(new_groups)
    require(index["groups"] == expected_groups, "Child groups are not exactly used parent plus declared new groups")
    bound = {"config_sha256": root/"code/evals/npc-body/body-defence-bc-v1.json", "dataset_sha256": root/"dataset/dataset.json",
             "normalization_sha256": root/"dataset/normalization.json", "dataset_code_sha256": root/"code/scripts/body_trajectory_dataset.py",
             "parent_dataset_sha256": parent_index}
    require(all(profile[key] == digest(path) for key, path in bound.items()) and Path(profile["parent_dataset_path"]).resolve() == parent_index,
            "Selection profile source bindings changed")
    eval_entries = [entry for entry in index["episodes"] if entry["split"] == "eval"]
    eval_ids = [entry["episode_id"] for entry in eval_entries]
    eval_groups = {entry["group"]: "eval" for entry in eval_entries}
    require({json.loads(group)[1] for group in eval_groups} == {223, 227} and profile["selection_evaluation_episode_ids"] == eval_ids and
            profile["selection_evaluation_groups"] == eval_groups and profile["selection_split"] == "eval" and
            profile["evaluation_mode"] == "continuous" and profile["selection_view"] == "full_episode" and
            profile["test_and_ood_used_for_selection"] is False and profile["inherited_assignment_changes"] == 0 and
            profile["excluded_v6_eval_reopened"] is False, "Selection reopened historical/held-out/screen groups")
    normalizer = strict_read(root/"dataset/normalization.json")
    train_ids = [entry["episode_id"] for entry in index["episodes"] if entry["split"] == "train"]
    require(normalizer["source_split"] == "train" and normalizer["episode_ids"] == train_ids and len(normalizer["feature_names"]) == 184,
            "Child normalizer does not use exactly combined train")
    collection = root/child["new_data"]["collection_dir"]
    cfg_path = root/"code/evals/npc-body/body-defence-demonstrations-v1.json"
    cfg, suite, manifest = (strict_read(path) for path in (cfg_path, collection/"suite.json", collection/"manifest.json"))
    require(suite["status"] == "collected" and suite["suite_scope"] == "full" and len(suite["episodes"]) == 72 and
            cfg["teacher_id"] == child["new_data"]["teacher_id"] and cfg["train_seeds"] == child["new_data"]["train_seeds"] and
            cfg["eval_seeds"] == child["new_data"]["eval_seeds"] and manifest["models_run"] is False,
            "New72 collection/teacher group declaration differs")
    require(profile["collection_path"] == str(collection) and profile["collection_config_sha256"] == digest(cfg_path) and
            profile["collection_manifest_sha256"] == digest(collection/"manifest.json") and profile["collection_suite_sha256"] == digest(collection/"suite.json") and
            profile["teacher_registry_sha256"] == digest(collection/"teacher-registry.json"), "Collection profile SHA binding differs")
    require(manifest["collector_sha256"] == digest(root/"code/scripts/body_defence_demonstrations.py") and
            manifest["teacher_code_sha256"] == digest(root/"code/scripts/body_defence_teacher.py"), "Actual teacher/collector source differs")
    expected_registry = canonical.merge_teacher_registries([old["teacher_registry"], strict_read(collection/"teacher-registry.json")])
    require(index["teacher_registry"] == expected_registry, "Composite registry altered original approval contexts")
    require(all(index["groups"].get(group) == split for group, split in cfg["group_assignments"].items()), "New declared group assignments differ")
    motor = profile["engine_binding"]
    proof = (root.parent/child["engine_proof_run_name"]).resolve()
    require(Path(motor["path"]).resolve() == proof and motor["run"] == proof.name and
            motor["binary_sha256"] == manifest["binary_sha256"] == digest(root/"reference/gothic-body-runner") == digest(proof/"reference/gothic-body-runner") and
            motor["source_sha256"] == manifest["source_sha256"] and motor["historical_owned_trajectories_asserted_equal"] is False,
            "Same-binary motor binding differs")
    for name, expected in motor["inventory"].items():
        require(digest(proof/name) == expected, "Motor proof bytes changed: "+name)
    parity, setup = strict_read(proof/"setup-probe/parity/headless-parity.json"), strict_read(proof/"setup-probe/manifest.json")
    require(setup["source_sha256"] == motor["source_sha256"] and setup["binary_sha256"] == motor["binary_sha256"] and
            parity["status"] == "matched" and parity["profile"] == "full" and len(parity["episodes"]) == 3 and
            {row["seed"] for row in parity["episodes"]} == {17, 29, 43} and
            all(row["status"] == "matched" and row["difference_count"] == 0 for row in parity["episodes"]), "Native full parity proof missing")
    old_ids = {entry["episode_id"] for entry in old["episodes"]}
    new = [entry for entry in index["episodes"] if entry["episode_id"] not in old_ids]
    expected_cases = {(split, seed, side, distance) for split, seeds in (("train", cfg["train_seeds"]), ("eval", cfg["eval_seeds"]),
                      ("test", cfg["reserved_test_seeds"]), ("ood", cfg["ood_seeds"])) for seed in seeds for side in cfg["sides"] for distance in cfg["distances_cm"]}
    actual_cases = {(row["split"], row["seed"], row["side"], row["distance_cm"]) for row in suite["episodes"]}
    require(len(new) == 72 and actual_cases == expected_cases and len(actual_cases) == 72 and
            profile["new_primary_cold_episode_ids"] == [entry["episode_id"] for entry in new], "Defence72 actual case matrix differs")
    return {"parent_dataset_sha256": digest(parent_index), "parent_selection_sha256": digest(parent/"selection.json"),
            "parent_checkpoint_sha256": PARENT_CHECKPOINTS, "pruned_unused_parent_groups": unused_parent_groups, "selection_profile_sha256": digest(root/"dataset/selection-profile.json"),
            "selection_evaluation_episode_ids": eval_ids, "selection_evaluation_groups": eval_groups,
            "split_episode_counts": dict(Counter(entry["split"] for entry in index["episodes"])), "engine_binding": motor}, new


def cold_scenes(root, child, canonical, new, dataset):
    counts, proofs = Counter(), []
    collection = root/child["new_data"]["collection_dir"]
    collected = {row["name"]: row for row in strict_read(collection/"suite.json")["episodes"]}
    require(set(collected) == {entry["episode_id"] for entry in new}, "Suite/canonical defence episode identities differ")
    for entry in new:
        episode = canonical.validate_episode(entry["path"], teacher_registry=dataset["index"]["teacher_registry"])
        start = canonical.validate_prepared_sequence_start(episode)
        view = canonical.periodic_model_view(episode, max_label_age_game_ms=67)
        rows = list(view.observations.values())
        require(rows and all(row["entity_id"] == start["entity_id"] and row["raw_capture_seq"] >= start["raw_capture_seq"] for row in rows) and
                rows[0]["raw_capture_seq"] == start["raw_capture_seq"] and rows[0]["sequence_dt_game_ms"] == 0,
                "Preparation/opponent history entered cold model memory")
        labels = [(key, row) for key, row in view.decisions.items() if view.transitions[key]["usable_for_bc"]]
        require(len(labels) == 1 and labels[0][1]["source"] == "scripted" and labels[0][1]["action_id"] == 9 and
                labels[0][0][0] == start["entity_id"], "New episode lacks one genuine primary completed-block label")
        require(episode.manifest["binary_sha256"] == digest(root/"reference/gothic-body-runner"), "New teacher capture uses another runtime binary")
        path = Path(entry["path"])
        require(path.resolve() == (collection/"episodes"/entry["episode_id"]).resolve() and
                episode.manifest["source_sha256"] == strict_read(collection/"manifest.json")["source_sha256"],
                "New capture source/native episode directory differs")
        sources = {item["path"]: item["sha256"] for item in episode.manifest["source_files"]}
        teacher = episode.manifest["teacher_provenance"]
        require(teacher["teacher_id"] == child["new_data"]["teacher_id"] and
                sources[teacher["code_file"]] == digest(root/"code/scripts/body_defence_teacher.py") and
                sources[teacher["config_file"]] == digest(root/"code/evals/npc-body/body-defence-demonstrations-v1.json") and
                digest(path/episode.manifest["preparation_normalizer_file"]) == child["reference"]["normalizer_sha256"],
                "New teacher/config/preparation normalizer differs from actual capture declaration")
        binding = strict_read(path/start["anchor_binding_file"])
        record = strict_read(path/"episode.json")
        approval = episode.manifest["scripted_expert_approval"]
        suite_record = {**record,
                       "approved_actions": {str(action): sum(row["action_id"] == action for row in approval["approved"]) for action in range(1, 12)},
                       "approval_diagnostics": approval["diagnostics"],
                       "approval_sha256": digest(path/"records-approved.jsonl")}
        require(suite_record == collected[entry["episode_id"]] and record["requested_primary_commands"] == record["primary_completed_commands"] == 1 and
                record["preparation_quality_credit"] is False and record["model_sequence_start"] == start and
                binding["first_sequence_dt_game_ms"] == 0 and binding["feature_count"] == 184 and
                binding["capture_float32_features_sha256"] == binding["wire_float32_features_sha256"], "Prepared scene proof differs")
        counts[entry["split"]] += 1
        proofs.append({"episode_id": entry["episode_id"], "split": entry["split"], "model_sequence_start": start,
                       "anchor_binding_sha256": digest(path/start["anchor_binding_file"]), "float32_features_sha256": binding["wire_float32_features_sha256"],
                       "records_sha256": entry["records_sha256"], "manifest_sha256": entry["manifest_sha256"]})
        del episode, view
    require(dict(counts) == child["new_data"]["required_usable_block_labels"] == {"train": 36, "eval": 12, "test": 12, "ood": 12},
            "Actual projected cold block support differs")
    require(strict_read(root/"dataset/selection-profile.json")["new_usable_block_labels"] == dict(counts), "Cold support profile claim differs")
    return {"usable_block_labels": dict(counts), "prepared_scene_proofs": proofs, "preparation_hidden_updates": 0,
            "raw_preparation_retained_and_validated": True, "mechanical_approval_recomputed": True}


def continuous_sources(root, canonical, dataset):
    result = {}
    for split in ("eval", "test", "ood"):
        seen, streams, observations = set(), set(), 0
        for stream in canonical.FullEpisodeLoader(root/"dataset", split=split):
            key = (stream["episode_id"], tuple(stream["identity"]))
            require(key not in streams, "Full evaluation repeats an actual stream")
            streams.add(key)
            previous = None
            for index, row in enumerate(stream["observations"]):
                require(row["reset"] is (index == 0) and row["burn_in"] is False and row["periodic"] is True,
                        "Full evaluation has artificial reset/burn-in/nonperiodic input")
                if previous is not None:
                    require(row["observation_seq"] > previous["observation_seq"] and row["dt_game_ms"] == row["t_game_ms"]-previous["t_game_ms"],
                            "Full actor stream order/dt differs")
                previous = row
                observations += 1
                if row["loss_mask"]:
                    loss_key = (stream["episode_id"], tuple(stream["identity"]), row["observation_seq"])
                    require(loss_key not in seen and dataset["labels"].get(loss_key, {}).get("action_id") == row["label"], "Full evaluation duplicates/invents a label")
                    seen.add(loss_key)
        require(seen == {key for key, row in dataset["labels"].items() if row["split"] == split}, "Full evaluation loses actual labels")
        result[split] = {"stream_count": len(streams), "actual_stream_reset_count": len(streams), "observation_count": observations,
                         "label_count": len(seen), "artificial_chunk_resets": 0, "duplicate_burnin_labels": 0, "evaluation_mode": "continuous"}
    return result


def gpu_execution(trained, sidecar, cap):
    execution = trained["execution"]
    require(execution == sidecar["metadata"]["training"]["execution"] and execution["device"] == "cuda" and
            execution["cuda_allocator_limit_applied"] is True and execution["visible_device_index"] == 0 and
            execution["cuda_visible_devices"] == "0", "Candidate did not use explicit bounded CUDA")
    budget = execution["cuda_memory_budget"]
    limit = min(int(budget["device_total_bytes"]*cap["cuda_memory_fraction"]), cap["cuda_memory_limit_mib"]*1024*1024)
    require(budget["requested_fraction"] == cap["cuda_memory_fraction"] and budget["requested_limit_mib"] == cap["cuda_memory_limit_mib"] and
            budget["allocator_limit_bytes"] == limit and budget["minimum_remaining_free_bytes"] == cap["minimum_cuda_free_mib"]*1024*1024 and
            budget["free_after_context_bytes"] >= limit+budget["minimum_remaining_free_bytes"] and
            math.isclose(budget["applied_fraction"], limit/budget["device_total_bytes"], rel_tol=1e-12) and
            budget["context_and_non_allocator_memory_capped"] is False, "CUDA allocator/headroom metadata differs")
    require(0 < trained["cuda_peak_allocated_bytes"] <= trained["cuda_peak_reserved_bytes"] <= limit,
            "Candidate CUDA peaks exceed the declared allocator budget")
    return execution


def models(root, child, effective, dataset, bc, contexts):
    results = {}
    selected = strict_read(root/"selection.json")
    profile = strict_read(root/"dataset/selection-profile.json")
    require(selected["config_sha256"] == digest(root/"code/evals/npc-body/body-defence-bc-v1.json") and
            selected["selection_profile_sha256"] == digest(root/"dataset/selection-profile.json") and
            selected["dataset_sha256"] == digest(root/"dataset/dataset.json") and selected["normalization_sha256"] == digest(root/"dataset/normalization.json") and
            selected["training_device"] == "cuda" and selected["test_and_ood_used_for_selection"] is False and
            selected["selection_split"] == "eval" and selected["selection_view"] == "full_episode" and selected["evaluation_mode"] == "continuous" and
            selected["criterion"] == child["training"]["selection_criterion"] == "macro_f1_then_unweighted_nll", "Fixed global selection binding differs")
    for key in ("selection_evaluation_episode_ids", "selection_evaluation_groups"):
        require(selected[key] == profile[key], "Selection read outside fresh223/227 eval")
    for architecture in ("gru", "mlp"):
        result = bc.model_evidence(root, architecture, effective, dataset, verification_device="cpu")
        selection = strict_read(root/"models"/architecture/"selection.json")
        require(selected["models"][architecture] == selection, "Global/model seed selection differs")
        require(selection["selection_evaluation_episode_ids"] == profile["selection_evaluation_episode_ids"] and
                selection["selection_evaluation_groups"] == profile["selection_evaluation_groups"], "Architecture selection group binding differs")
        executions = []
        for candidate in selection["candidates"]:
            path = bc.artifact(root, candidate["path"]+"/training.json").parent
            trained, sidecar = strict_read(path/"training.json"), strict_read(path/"best.pt.json")
            require(trained["config"]["evaluation_mode"] == trained["selection"]["evaluation_mode"] == "continuous" and
                    sidecar["metadata"]["training"]["selection_evaluation_mode"] == "continuous" and
                    sidecar["metadata"]["training"]["config"] == trained["config"] and trained["config"]["stack_size"] == 8,
                    "Candidate training/sidecar context differs")
            for key in ("minimum_cuda_free_mib", "cuda_memory_fraction", "cuda_memory_limit_mib"):
                require(trained["config"][key] == child["train_cuda"][key], "Candidate CUDA config override differs")
            for epoch in strict_read(path/"history.json"):
                require(all(epoch["eval"].get(key) == expected for key, expected in contexts["eval"].items()),
                        "Selected epoch did not evaluate exact full fresh actor histories")
            executions.append({"seed": candidate["seed"], "execution": gpu_execution(trained, sidecar, child["train_cuda"]),
                               "cuda_peak_allocated_bytes": trained["cuda_peak_allocated_bytes"], "cuda_peak_reserved_bytes": trained["cuda_peak_reserved_bytes"]})
        overfit = strict_read(root/"models"/architecture/"overfit/overfit.json")
        require(overfit["passed"] is True and overfit["config"]["device"] == "cpu" and overfit["config"]["threads"] == 2,
                "Tiny overfit did not run on CPU")
        for split in ("eval", "test", "ood"):
            metrics = strict_read(root/"models"/architecture/split/"metrics.json")
            require(all(metrics.get(key) == expected for key, expected in contexts[split].items()), "Saved predictions used other continuous source streams")
        result["cuda_candidate_execution"] = executions
        results[architecture] = result
    require(timestamp(selected["weights_fixed_at_utc"]) >= timestamp(profile["created_at_utc"]), "Weights fixed before declared selection profile")
    return {"selection_sha256": digest(root/"selection.json"), "weights_fixed_at_utc": selected["weights_fixed_at_utc"],
            "selection": selected, "models": results}


def resources(root, child, effective, bc):
    phases = {}
    for phase, command, count in (("defence-verify", "verify", 2), ("defence-overfit", "overfit", 2),
                                   ("defence-train-cuda", "train", 6), ("defence-evaluate", "evaluate", 6)):
        before, after = strict_read(root/f"services-{phase}-before.json"), strict_read(root/f"services-{phase}-after.json")
        protected = bc.all_services(before, after, effective["resources"]["required_services"])
        require(timestamp(before["recorded_at_utc"]) <= timestamp(after["recorded_at_utc"]), "Resource phase clocks reversed")
        text = (root/f"resources-{phase}.txt").read_text()
        require(re.search(r"Exit status:\s+0\s*$", text, re.M), "Resource phase failed: "+phase)
        peak = re.search(r"Maximum resident set size \(kbytes\):\s+(\d+)", text)
        require(peak and 0 < int(peak[1]) <= 6*1024*1024, "Phase peak RSS exceeded6GiB")
        running = [line for line in (root/f"launcher-{phase}.log").read_text().splitlines() if line.startswith("Running ")]
        headers = [line for line in running if line.startswith("Running as unit: ")]
        expected_header = re.escape(f"Running as unit: gothic-body-bc-{root.name}-{phase}.service; invocation ID: ") + r"[0-9a-f]{32}"
        require(len(headers) == 1 and re.fullmatch(expected_header, headers[0]), "Actual phase unit identity/header differs: "+phase)
        argv = [shlex.split(line[len("Running "):]) for line in running if line not in headers]
        require(len(argv) == count, "Actual numerical command inventory differs: "+phase)
        device = "cuda" if command == "train" else "cpu"
        require(all("gothic_body.cli" in row and row[row.index("gothic_body.cli")+1] == command and
                    row.count("--device") == 1 and row[row.index("--device")+1] == device and
                    row[row.index("--dataset-code")+1] == str(root/"code/scripts/body_trajectory_dataset.py") for row in argv),
                "Actual numerical CLI/device/packer differs: "+phase)
        phases[phase] = {"device": device, "command_count": len(argv), "peak_rss_kib": int(peak[1]),
                         "protected_services": protected, "before_sha256": digest(root/f"services-{phase}-before.json"),
                         "after_sha256": digest(root/f"services-{phase}-after.json"), "resources_sha256": digest(root/f"resources-{phase}.txt"),
                         "launcher_sha256": digest(root/f"launcher-{phase}.log")}
    phase = "defence-train-cuda"
    watch = strict_read(root/f"gpu-watch-{phase}.json")
    require(watch["status"] == "complete" and watch["exit_code"] == 0 and watch["stop_requested"] is False and
            watch["unit"] == f"gothic-body-bc-{root.name}-{phase}.service" and
            re.fullmatch(r"[0-9a-f]{32}", watch["invocation_id"]) and
            watch["sample_interval_seconds"] == 2 and watch["max_own_gpu_mib"] == 1536 and watch["min_gpu_free_mib"] == 2048,
            "Actual CUDA context monitor absent/failed/different")
    rows = list(bc.canonical.iter_jsonl(root/f"gpu-watch-{phase}.jsonl"))
    samples = [row for row in rows if row["kind"] == "sample"]
    require(samples and watch["samples"] == len(samples) and watch["peak_own_gpu_mib"] == max(row["own_gpu_mib"] for row in samples) and
            watch["minimum_gpu_free_mib"] == min(row["gpu_free_mib"] for row in samples) and
            any(row["own_gpu_process_count"] > 0 for row in samples) and
            all(row["own_gpu_mib"] <= 1536 and row["gpu_free_mib"] >= 2048 and row["gpu_index"] == 0 for row in samples),
            "Actual monitored GPU budget exceeded/unobserved")
    require(rows[0]["kind"] == "start" and rows[-1]["kind"] == "summary" and all(row["unit"] == watch["unit"] for row in rows) and
            all(rows[index]["elapsed_seconds"] <= rows[index+1]["elapsed_seconds"] for index in range(len(rows)-1)), "GPU watch stream identity/order differs")
    final = {key: value for key, value in rows[-1].items() if key not in ("kind", "recorded_at_utc", "elapsed_seconds")}
    require(final == watch, "GPU JSONL/final summary differ")
    require(not any(row["kind"] in ("budget_failure", "monitor_failure") for row in rows), "GPU monitor recorded a failure")
    before, after = strict_read(root/f"services-{phase}-before.json"), strict_read(root/f"services-{phase}-after.json")
    fixed = strict_read(root/"selection.json")["weights_fixed_at_utc"]
    require(timestamp(before["recorded_at_utc"]) <= timestamp(rows[0]["recorded_at_utc"]) <= timestamp(fixed) <=
            timestamp(rows[-1]["recorded_at_utc"]) <= timestamp(after["recorded_at_utc"]), "GPU monitor did not span fixed candidate selection")
    phases[phase]["gpu_watch"] = {"summary": watch, "json_sha256": digest(root/f"gpu-watch-{phase}.json"),
                                  "jsonl_sha256": digest(root/f"gpu-watch-{phase}.jsonl")}
    return phases


def file_inventory(root, output, report):
    """Bind every consumed immutable dependency for a later external reader."""
    files = {}
    def add(path):
        path = Path(path).resolve()
        require(path.is_file(), "Audited dependency missing: "+str(path))
        files[str(path)] = digest(path)
    for name in strict_read(root/"environment.json")["frozen_files"]:
        if not {"__pycache__", ".pytest_cache"}.intersection(Path(name).parts):
            add(root/name)
    for name in ("environment.json", "source.tar.gz", "reference/gothic-body-runner", "dataset/dataset.json", "dataset/normalization.json",
                 "dataset/selection-profile.json", "selection.json"):
        add(root/name)
    child = strict_read(root/"code/evals/npc-body/body-defence-bc-v1.json")
    collection = root/child["new_data"]["collection_dir"]
    for name in ("manifest.json", "suite.json", "teacher-registry.json"):
        add(collection/name)
    for entry in strict_read(root/"dataset/dataset.json")["episodes"]:
        path = Path(entry["path"])
        add(path/entry["manifest_file"])
        add(path/entry["records_file"])
        manifest = strict_read(path/entry["manifest_file"])
        for item in manifest["source_files"]:
            add(path/item["path"])
        if manifest.get("model_sequence_start"):
            add(path/"episode.json")
    parent = root.parent/child["reference"]["run"]
    for name in ("dataset/dataset.json", "dataset/normalization.json", "selection.json"):
        add(parent/name)
    for architecture in ("gru", "mlp"):
        add(parent/"models"/architecture/"best.pt")
        add(parent/"models"/architecture/"best.pt.json")
        directory = root/"models"/architecture
        for name in ("best.pt", "best.pt.json", "training.json", "history.json", "selection.json", "verify.json",
                     "overfit/overfit.json", "overfit/diagnostic.pt", "overfit/diagnostic.pt.json"):
            add(directory/name)
        for candidate in strict_read(directory/"selection.json")["candidates"]:
            for name in ("best.pt", "best.pt.json", "training.json", "history.json"):
                add(root/candidate["path"]/name)
        for split in ("eval", "test", "ood"):
            add(directory/split/"metrics.json")
            add(directory/split/strict_read(directory/split/"metrics.json")["predictions_file"])
    for phase in report["evidence"]["resources"]:
        for name in (f"services-{phase}-before.json", f"services-{phase}-after.json", f"resources-{phase}.txt", f"launcher-{phase}.log"):
            add(root/name)
    for suffix in ("json", "jsonl"):
        add(root/f"gpu-watch-defence-train-cuda.{suffix}")
    motor = report["evidence"]["lineage"]["engine_binding"]
    for name in motor["inventory"]:
        add(Path(motor["path"])/name)
    add(Path(motor["path"])/"reference/gothic-body-runner")
    add(output/"effective-profile.json")
    add(Path(__file__))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.run.resolve()
    output = (args.output_dir or root/"validation-defence").resolve()
    require(not output.is_relative_to(root/"code"), "Report cannot modify frozen source")
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "kind": "body_defence_child_saved_data_audit", "run": str(root),
              "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "reporter_path": str(Path(__file__).resolve()),
              "reporter_sha256": digest(Path(__file__)), "reporter_version": 3,
              "reporter_regrade_reason": REPORTER_REGRADE_REASON, "frozen_dataset_function_sha256": FROZEN_DATASET_FUNCTION_SHA256,
              "status": "failed", "passed": False, "scope": "Saved-data integrity only; no fresh screen, models or simulations read/run.",
              "closed_loop_quality_credit": False, "checks": [], "evidence": {}}
    try:
        canonical, bc, frozen = load_frozen(root)
        report["evidence"]["frozen"] = frozen
        config_path = root/"code/evals/npc-body/body-defence-bc-v1.json"
        child = strict_read(config_path)
        effective = configuration(root, child)
        write(output/"effective-profile.json", effective)
        report["evidence"]["configuration"] = {"original_config_path": str(config_path), "original_config_sha256": digest(config_path),
            "base_model_config_sha256": digest(root/"code/evals/npc-body/body-bc-v1.json"), "effective_config_sha256": digest(output/"effective-profile.json"),
            "effective_config_file": "effective-profile.json", "override": child["train_cuda"], "verification_device": "cpu"}
        lineage_evidence, new = lineage(root, child, canonical)
        report["evidence"]["lineage"] = lineage_evidence
        report["checks"].append("immutable parent, original/effective profile and fresh-only selection lineage")
        evidence, dataset = child_dataset_evidence(root, effective, bc)
        require(evidence["effective_coverage_ready"] is True, "Combined train lacks all11 true expert classes")
        report["evidence"]["dataset"] = evidence
        report["evidence"]["cold_scenes"] = cold_scenes(root, child, canonical, new, dataset)
        report["checks"].append("canonical207 raw approvals, causal periodic labels, train-only normalization and72 cold primary scenes")
        contexts = continuous_sources(root, canonical, dataset)
        report["evidence"]["continuous_sources"] = contexts
        report["evidence"]["training"] = models(root, child, effective, dataset, bc, contexts)
        report["checks"].append("all six bounded CUDA candidates, eval-only epoch/seed criterion and CPU saved held-out predictions")
        report["evidence"]["resources"] = resources(root, child, effective, bc)
        report["checks"].append("CPU verify/overfit/evaluate, external GPU context monitor and unchanged protected services")
        report["file_inventory"] = file_inventory(root, output, report)
        report["bindings"] = {"environment_sha256": digest(root/"environment.json"), "selection_sha256": digest(root/"selection.json"),
                              "dataset_sha256": digest(root/"dataset/dataset.json"), "normalization_sha256": digest(root/"dataset/normalization.json"),
                              "selection_profile_sha256": digest(root/"dataset/selection-profile.json"),
                              "original_config_sha256": digest(config_path), "effective_config_sha256": digest(output/"effective-profile.json")}
        require("torch" not in sys.modules, "Saved-data auditor imported Torch")
        report.update(status="passed", passed=True)
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    write(output/"report.json", report)
    text = ["# Defence child saved-data audit", "", f"Status: **{report['status']}**.", "", report["scope"], "",
            "This report gives no closed-loop combat quality credit.", "", f"Run: `{root}`", ""]
    if "error" in report:
        text += ["Failure: "+report["error"], ""]
    text += ["Passed checks:", "", *["- "+item for item in report["checks"]], "", "Exact hashes and evidence are in `report.json`; `effective-profile.json` binds the declared CUDA override.", ""]
    (output/"report.md").write_text("\n".join(text))
    print(json.dumps({"status": report["status"], "passed": report["passed"], "report": str(output/"report.json"),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
