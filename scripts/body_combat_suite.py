#!/usr/bin/env python3
"""Frozen stage 8 dataset and model phases with fresh-only continuous selection.

The full parent corpus retains the exposed stage 7 eval set for reporting. Its
selection child keeps exactly the same train/normalizer and excludes that eval
set before any candidate is trained. This module imports no numerical runtime.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"Object required: {path}")
    return value


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    temporary.replace(path)


def command(argv: list[Any]) -> None:
    print("Running "+" ".join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True)


def check_frozen_code(root: Path) -> dict[str, str]:
    files = read(root/"environment.json")["frozen_files"]
    checked = {}
    for name, sha in files.items():
        path = (root/name).resolve()
        require(path.is_relative_to((root/"code").resolve()), "Executable inventory escapes frozen code")
        require(digest(path) == sha, f"Frozen code changed: {name}")
        checked[name] = sha
    own_name = str(Path(__file__).resolve().relative_to(root))
    require(own_name in files, "Executing combat suite is outside the frozen inventory")
    return checked


def group_seed(entry: dict[str, Any]) -> int:
    _, seed = json.loads(entry["group"])
    require(type(seed) is int, "Invalid episode group seed")
    return seed


def verify_normal_clearance(record: dict[str, Any]) -> dict[str, Any]:
    """Check real wall-plane distance independently of the observer's ray length."""
    def number(value: Any, name: str) -> float:
        require(type(value) in (int, float) and math.isfinite(value), f"Non-finite wall proof: {name}")
        return float(value)

    def vector(value: Any, name: str) -> list[float]:
        require(isinstance(value, list) and len(value) == 3, f"Wall proof requires a 3-vector: {name}")
        return [number(part, name) for part in value]

    def dot(first: list[float], second: list[float]) -> float:
        return sum(a*b for a, b in zip(first, second))

    def horizontal_unit(value: Any, name: str) -> list[float]:
        result = vector(value, name)
        require(abs(result[1]) <= 0.001 and abs(dot(result, result)-1) <= 0.001,
                f"Wall proof requires a horizontal unit vector: {name}")
        return result

    requested = number(record["requested_cm"], "requested_cm")
    initial = record["initial"]["arena"]
    setup = initial["initial_state"]["setup"]
    proof = setup["geometry_proof"]
    require(initial["ready"] is True and initial["started"] is False and
            setup["wall_clearance_cm"] == requested and proof["placement_basis"] == "wall_normal_xz",
            "Wall-normal proof did not originate at the declared sealed reset")
    for flag in ("wall_ray_hit", "capsule_move_blocked", "capsule_block_is_land", "initial_capsules_free",
                 "safe_outward_move", "grounded_placements", "target_visible", "melee_line_clear"):
        require(proof.get(flag) is True, "Wall-normal proof lacks physical collision/placement evidence: "+flag)
    placement = horizontal_unit(proof["placement_wall_normal_xz_world"], "placement_wall_normal_xz_world")
    actual_basis = horizontal_unit(proof["actual_wall_normal_xz_world"], "actual_wall_normal_xz_world")
    tangent = horizontal_unit(proof["placement_wall_tangent_world"], "placement_wall_tangent_world")
    search = horizontal_unit(proof["search_ray_direction_world"], "search_ray_direction_world")
    toward = horizontal_unit(proof["wall_direction_world"], "wall_direction_world")
    vector(proof["placement_wall_anchor_cm"], "placement_wall_anchor_cm")
    center = vector(proof["primary_collision_center_cm"], "primary_collision_center_cm")
    point = vector(proof["wall_point_cm"], "wall_point_cm")
    require(abs(dot(placement, tangent)) <= 0.001 and dot(placement, search) < -0.000001 and
            all(abs(a+b) <= 0.001 for a, b in zip(placement, toward)),
            "Wall-normal placement basis is not outward/perpendicular to the real wall")
    agreement = dot(placement, actual_basis)
    require(agreement >= 0.95-0.000001, "Actual wall normal disagrees with placement wall normal")
    raw_normal = vector(proof["wall_normal"], "wall_normal")
    horizontal_length = math.hypot(raw_normal[0], raw_normal[2])
    require(horizontal_length > 0.000001 and abs(raw_normal[1]) <= 0.35,
            "Actual wall collision has no valid horizontal wall normal")
    projected_normal = [raw_normal[0]/horizontal_length, 0.0, raw_normal[2]/horizontal_length]
    if dot(projected_normal, toward) > 0:
        projected_normal = [-part for part in projected_normal]
    require(all(abs(a-b) <= 0.001 for a, b in zip(projected_normal, actual_basis)),
            "Recorded actual wall normal differs from the collision normal")
    projection = dot([a-b for a, b in zip(center, point)], actual_basis)
    normal = number(proof["actual_wall_normal_clearance_cm"], "actual_wall_normal_clearance_cm")
    ray = number(proof["actual_wall_clearance_cm"], "actual_wall_clearance_cm")
    require(proof["requested_wall_normal_clearance_cm"] == requested == proof["requested_wall_clearance_cm"] and
            60 <= requested <= 180 and 60 <= normal <= 180 and 60 <= ray <= 180 and
            abs(normal-requested) <= 20 and abs(ray-requested) <= 20 and abs(normal-projection) <= 0.01,
            "Measured wall normal/ray clearance violates the declared geometry bounds")
    for prefix, measured in (("wall_normal_clearance", normal), ("wall_clearance", ray)):
        require(number(proof[prefix+"_tolerance_cm"], prefix+"_tolerance_cm") == 20 and
                abs(number(proof[prefix+"_error_cm"], prefix+"_error_cm")-(measured-requested)) <= 0.001,
                "Wall normal/ray clearance error provenance differs")
    require(abs(number(proof["obstacle_distance_cm"], "obstacle_distance_cm")-ray) <= 0.001 and
            abs(number(record["actual_cm"], "actual_cm")-ray) <= 0.001 and
            all(abs(hit-(origin+direction*ray)) <= 0.05 for hit, origin, direction in zip(point, center, toward)),
            "Ray clearance differs from its real collision origin/hit")
    numeric_ray = record["numeric_ray"]
    observation = record["numeric_observation"]
    require(observation["entity_id"] == initial["primary_entity_id"] and
            observation["world_epoch"] == initial["world_epoch"] and observation["t_game_ms"] == initial["game_ms"] and
            numeric_ray in observation["local_obstacles"]["rays"], "Realtime clearance lost its actual initial numeric source")
    yaw = number(observation["self"]["yaw_rad"], "numeric_observation.self.yaw_rad")
    ray_angle = number(numeric_ray["angle_rad"], "numeric_ray.angle_rad")
    bearing = math.atan2(toward[2], toward[0])-yaw
    angle_error = abs((ray_angle-bearing+math.pi) % (2*math.pi)-math.pi)
    ray_distance = number(numeric_ray["distance_cm"], "numeric_ray.distance_cm")
    numeric_normal = -ray_distance*dot([math.cos(yaw+ray_angle), 0.0, math.sin(yaw+ray_angle)], actual_basis)
    require(numeric_ray["hit"] is True and abs(number(numeric_ray["distance_cm"], "numeric_ray.distance_cm")-ray) <= 2 and
            angle_error <= 0.001 and abs(number(record["numeric_ray_angle_error_rad"], "numeric_ray_angle_error_rad")-angle_error) <= 0.000001 and
            abs(number(record["normal_actual_cm"], "normal_actual_cm")-normal) <= 0.01 and
            abs(number(record["normal_projection_cm"], "normal_projection_cm")-projection) <= 0.01 and
            abs(number(record["numeric_ray_normal_projection_cm"], "numeric_ray_normal_projection_cm")-numeric_normal) <= 0.01 and
            abs(numeric_normal-normal) <= 2,
            "Realtime numeric ray does not match the measured collision ray")
    return {"side": record["side"], "requested_cm": requested, "normal_cm": normal,
            "recomputed_normal_cm": projection, "numeric_ray_normal_cm": numeric_normal,
            "ray_cm": ray, "placement_actual_normal_dot": agreement,
            "placement_basis": "wall_normal_xz"}


def assert_selection_profile(root: Path, config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    index = read(root/"dataset/dataset.json")
    profile = read(root/"dataset/selection-profile.json")
    require(profile["stage8_config_sha256"] == digest(config_path), "Selection declaration differs from frozen config")
    require(profile["dataset_parent_sha256"] == digest(root/"dataset-parent/dataset.json"), "Parent corpus changed")
    require(profile["dataset_sha256"] == digest(root/"dataset/dataset.json"), "Selection corpus changed")
    require(profile["normalization_sha256"] == digest(root/"dataset/normalization.json") ==
            digest(root/"dataset-parent/normalization.json"), "Selection normalizer differs from train-only parent")
    actual_eval = [entry for entry in index["episodes"] if entry["split"] == "eval"]
    require(actual_eval and {group_seed(entry) for entry in actual_eval} == set(config["new_data"]["eval_seeds"]),
            "Selection sees exposed eval, test/OOD or undeclared seeds")
    require(profile["selection_evaluation_episode_ids"] == [entry["episode_id"] for entry in actual_eval],
            "Selection episode list differs")
    require(profile["selection_evaluation_groups"] == {entry["group"]: "eval" for entry in actual_eval},
            "Selection group list differs")
    require(profile["selection_view"] == "full_episode" and profile["evaluation_mode"] == "continuous" and
            profile["test_and_ood_used_for_selection"] is False, "Selection mode weakened")
    require(profile["engine_compatibility_sha256"] == digest(root/"dataset/engine-compatibility.json") ==
            digest(root/"dataset-parent/engine-compatibility.json"), "Historical/current engine compatibility binding changed")
    compatibility = read(root/"dataset/engine-compatibility.json")
    environment = read(root/"environment.json")
    require(compatibility["runtime"]["binary_sha256"] == environment["binary_sha256"] ==
            digest(root/"reference/gothic-body-runner") and compatibility["runtime"]["source_archive_sha256"] ==
            environment["source_archive_sha256"] == digest(root/"source.tar.gz"), "Production motor identity changed before model phase")
    proof_root = root.parent/config["engine_compatibility"]["proof_run"]
    require(compatibility["proofs"]["path"] == str(proof_root) and
            compatibility["proofs"]["run"] == config["engine_compatibility"]["proof_run"], "Compatibility proof run changed")
    for name, sha in compatibility["proofs"]["inventory"].items():
        require(digest(proof_root/name) == sha, "Motor compatibility evidence changed before model phase")
    return profile


def engine_compatibility(root: Path, config: dict[str, Any], old_index_path: Path,
                         old: dict[str, Any], collected: Path, new_paths: list[Path], canonical) -> dict[str, Any]:
    """Bind a scoped motor proof, without equating old owned trajectories."""
    setting = config["engine_compatibility"]
    proof_root = root.parent/setting["proof_run"]
    require(proof_root.is_relative_to(root.parent) and proof_root.is_dir(), "Declared motor proof run missing")
    environment, proof_environment = canonical.read_json(root/"environment.json"), canonical.read_json(proof_root/"environment.json")
    runtime_binary = digest(root/"reference/gothic-body-runner")
    require(runtime_binary == environment["binary_sha256"] == proof_environment["binary_sha256"] ==
            digest(proof_root/"reference/gothic-body-runner"), "Motor proof used another runtime binary")
    require(digest(root/"source.tar.gz") == environment["source_archive_sha256"] and
            digest(proof_root/"source.tar.gz") == proof_environment["source_archive_sha256"], "Frozen source archive changed")
    setup = canonical.read_json(proof_root/"setup-probe/probe.json")
    parity = canonical.read_json(proof_root/"setup-probe/parity/headless-parity.json")
    realtime = canonical.read_json(proof_root/"realtime-probe/probe.json")
    setup_manifest = canonical.read_json(proof_root/"setup-probe/manifest.json")
    realtime_manifest = canonical.read_json(proof_root/"realtime-probe/manifest.json")
    collection_manifest = canonical.read_json(collected/"manifest.json")
    source_sha = collection_manifest["source_sha256"]
    require(collection_manifest["binary_sha256"] == runtime_binary and setup_manifest["binary_sha256"] == runtime_binary and
            realtime_manifest["binary_sha256"] == runtime_binary and
            setup_manifest["source_sha256"] == realtime_manifest["source_sha256"] == source_sha,
            "Motor proof/source and new collection identity differ")
    require(setup["status"] == "complete" and parity["status"] == "matched" and parity["profile"] == "full" and
            {row["seed"] for row in parity["episodes"]} == {17, 29, 43} and len(parity["episodes"]) == 3,
            "Full native17/29/43 parity proof missing")
    require(all(row["status"] == "matched" and row["difference_count"] == 0 for row in parity["episodes"]),
            "Native baseline parity diverged")
    require(realtime["status"] == "passed" and realtime.get("model") is None and not realtime.get("imported_model_modules"),
            "Realtime scheduler/terminal proof incomplete")
    clearances = realtime["wall_clearances"]
    requested_clearances = config["new_data"]["micro_wall_clearances_cm"]
    require(isinstance(requested_clearances, list) and len(requested_clearances) == len(set(requested_clearances)) == 6 and
            all(type(value) is int and 60 <= value <= 180 for value in requested_clearances),
            "Six unique physical clearance requests must be declared within60..180cm")
    expected_clearances = {(side, requested) for side in (0,1) for requested in requested_clearances}
    require(len(clearances) == 12 and {(row["side"], row["requested_cm"]) for row in clearances} == expected_clearances,
            "Six real clearance variants on both physical sides missing")
    normal_clearance_cases = [verify_normal_clearance(record) for record in clearances]
    arena, generic = realtime["arena"], realtime["generic"]
    require(arena["status"] == "passed" and generic["status"] == "passed" and arena["no_pre_start_catchup"] is True and
            arena["terminal_watchdog_hold"]["requested_wall_ms"] >= 6000 and len(arena["pre_start_holds"]) >= 2,
            "Ready-hold/free-tick/immutable-terminal proof incomplete")
    require(27 <= arena["free_tick_rate"]["substeps_per_wall_s"] <= 33 and
            27 <= generic["free_tick_rate"]["substeps_per_wall_s"] <= 33, "Actual realtime tick rate differs")
    historical, binaries, sources = [], set(), set()
    for entry in old["episodes"]:
        path = Path(entry["path"])/entry["manifest_file"]
        require(digest(path) == entry["manifest_sha256"], "Historical trajectory manifest changed")
        manifest = canonical.read_json(path)
        binary, source = manifest["binary_sha256"], manifest["source_sha256"]
        require(isinstance(binary,str) and len(binary) == 64 and isinstance(source,str) and len(source) == 64,
                "Historical trajectory lacks individual binary/source identity")
        binaries.add(binary); sources.add(source)
        historical.append({"episode_id": entry["episode_id"], "path": str(path), "manifest_sha256": entry["manifest_sha256"],
                           "binary_sha256": binary, "source_sha256": source})
    for path in new_paths:
        manifest = canonical.read_json(path)
        require(manifest["binary_sha256"] == runtime_binary and manifest["source_sha256"] == source_sha,
                "A new teacher trajectory used another production runtime")
    require(len(canonical.canonical_feature_names(config["dataset"]["feature_version"])) == 184,
            "Historical/current actor feature contract changed")
    proof_files = ("environment.json", "setup-probe/probe.json", "setup-probe/manifest.json",
                   "setup-probe/parity/headless-parity.json", "realtime-probe/probe.json", "realtime-probe/manifest.json")
    return {"schema_version": 1, "kind": "body_stage8_engine_compatibility",
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "historical": {"dataset_path": str(old_index_path), "dataset_sha256": digest(old_index_path),
                           "episode_count": len(historical), "episode_manifests": historical,
                           "binary_sha256_set": sorted(binaries), "source_sha256_set": sorted(sources)},
            "runtime": {"binary_sha256": runtime_binary, "source_sha256": source_sha,
                        "source_archive_sha256": environment["source_archive_sha256"],
                        "environment_sha256": digest(root/"environment.json")},
            "contracts": {"observation_schema_version": 1, "feature_version": config["dataset"]["feature_version"],
                          "feature_count": 184, "action_version": 1, "periodic_sample_ms": 50,
                          "max_label_age_game_ms": 67, "normalization_source": "train_only"},
            "proofs": {"run": setting["proof_run"], "path": str(proof_root), "binary_sha256": runtime_binary,
                       "source_sha256": source_sha, "source_archive_sha256": proof_environment["source_archive_sha256"],
                       "inventory": {name: digest(proof_root/name) for name in proof_files},
                       "native_full_parity": {"path": "setup-probe/parity/headless-parity.json", "profile": "full",
                                              "seeds": [17,29,43], "status": "matched"},
                       "setup": {"path": "setup-probe/probe.json", "manifest_path": "setup-probe/manifest.json", "status": "complete"},
                       "realtime": {"path": "realtime-probe/probe.json", "manifest_path": "realtime-probe/manifest.json",
                                    "status": "passed", "clearance_cases": 12, "requested_clearances_cm": requested_clearances,
                                    "normal_clearance_verified": True, "normal_clearance_cases": normal_clearance_cases,
                                    "numeric_ray_binding": "Realtime proof only; production yaw does not require observer-ray coincidence.",
                                    "sides": [0,1], "ready_hold": True, "terminal_sealed": True, "free_tick_fps_bounds": [27,33]}},
            "behavior_changes": ["Owned native waits and finite locomotion completion were intentionally corrected; historical owned trajectories are not asserted equal."],
            "historical_owned_trajectory_equivalence": False,
            "scope": "Historical demonstrations share primitive/feature contracts; native parity covers only the named baseline seeds and measured motor checks."}


def make_dataset(root: Path, config: dict[str, Any], config_path: Path, canonical) -> None:
    require(not (root/"selection.json").exists() and not any((root/"models").glob("*/seed-*/training.json")),
            "Dataset views must be declared before candidate training")
    reference = root.parent/config["reference"]["run"]
    old_index_path = reference/"dataset/dataset.json"
    old = canonical.read_json(old_index_path)
    require(old.get("validated") is True and old.get("episode_count") == 60, "Immutable stage 7 corpus differs")
    for architecture in ("gru", "mlp"):
        require(digest(reference/f"models/{architecture}/best.pt") ==
                config["reference"][architecture+"_checkpoint_sha256"], "Immutable reference checkpoint changed")
        sidecar = canonical.read_json(reference/f"models/{architecture}/best.pt.json")
        require(sidecar["checkpoint_sha256"] == config["reference"][architecture+"_checkpoint_sha256"] and
                sidecar["metadata"]["dataset"]["dataset_sha256"] == digest(old_index_path) and
                sidecar["metadata"]["normalization_sha256"] == digest(reference/"dataset/normalization.json"),
                "Immutable reference corpus/normalizer differs from its selected checkpoint")
    collected = root/"combat-demonstrations"
    suite = canonical.read_json(collected/"suite.json")
    require(suite.get("status") == "collected" and suite.get("suite_scope") == "full", "New combat collection is incomplete")
    require(suite.get("existing_dataset_sha256") == digest(old_index_path), "New collection used another parent corpus")
    new_paths = sorted((collected/"episodes").glob("*/trajectory-manifest.json"))
    require(len(new_paths) == len(suite["episodes"]) and len(new_paths) > 0, "New collection episode inventory differs")
    from body_combat_demonstrations import declared_cases
    collection_config = canonical.read_json(root/"code/evals/npc-body/body-combat-demonstrations-v1.json")
    cases = declared_cases(collection_config)
    actual_cases = {record["name"]: record for record in suite["episodes"]}
    require(len(actual_cases) == len(cases) and set(actual_cases) == {case["name"] for case in cases},
            "New bouts/microepisodes differ from the preregistered collection matrix")
    require({path.parent.name for path in new_paths} == set(actual_cases), "Episode paths differ from collected cases")
    for case in cases:
        require(all(actual_cases[case["name"]].get(key) == value for key, value in case.items()),
                "A collected episode changed its declared setup or split")
    compatibility = engine_compatibility(root, config, old_index_path, old, collected, new_paths, canonical)
    old_registry = old.get("teacher_registry")
    new_registry = canonical.read_json(collected/"teacher-registry.json")
    require(isinstance(old_registry, dict), "Original teacher registry missing")
    registry = canonical.merge_teacher_registries([old_registry, new_registry])
    parent_path, child_path = root/"dataset-parent", root/"dataset"
    all_paths = [Path(entry["path"]) for entry in old["episodes"]]+[path.parent for path in new_paths]
    report = canonical.build_dataset(all_paths, parent_path, sampling_view="periodic-v1",
                     max_label_age_game_ms=config["dataset"]["max_label_age_game_ms"],
                     min_labels=config["dataset"]["minimum_train_labels_per_action"],
                     included_actions=config["dataset"]["included_actions"], existing_assignments=old_index_path,
                     group_overrides=config["groups"], teacher_registry=registry)
    require(report["feature_version"] == config["dataset"]["feature_version"] and report["action_version"] == 1 and
            report["model_sampling"]["max_label_age_game_ms"] == 67, "Periodic model schema/alignment differs")
    require(report["diagnostic_bc_coverage_ready"] is True, "Combined periodic train coverage is incomplete")
    write(parent_path/"engine-compatibility.json", compatibility)
    old_ids = {entry["episode_id"] for entry in old["episodes"]}
    new_counts = Counter()
    fresh_eval_coverage = canonical._coverage([], min_labels=config["dataset"]["minimum_train_labels_per_action"],
                                             included_actions=config["dataset"]["included_actions"])
    for entry in report["episodes"]:
        if entry["episode_id"] in old_ids:
            continue
        episode = canonical.validate_episode(entry["path"], teacher_registry=registry)
        episode = canonical.periodic_model_view(episode, max_label_age_game_ms=67)
        if entry["split"] == "train":
            require(episode.manifest.get("teacher_provenance", {}).get("teacher_id") == config["new_data"]["teacher_id"],
                    "New train labels came from another teacher")
            primary = episode.manifest["initial"]["primary_entity_id"]
            for key, decision in episode.decisions.items():
                if key[0] == primary and decision["source"] == "scripted" and episode.transitions[key]["usable_for_bc"]:
                    new_counts[decision["action_id"]] += 1
        if entry["split"] == "eval":
            require(group_seed(entry) in config["new_data"]["eval_seeds"], "New eval group differs from declaration")
            canonical._merge_coverage(fresh_eval_coverage,
                     canonical._coverage([episode], min_labels=config["dataset"]["minimum_train_labels_per_action"],
                                         included_actions=config["dataset"]["included_actions"]),
                     min_labels=config["dataset"]["minimum_train_labels_per_action"])
        del episode
    required_new = config["dataset"]["minimum_new_teacher_train_labels_per_action"]
    for action, minimum in required_new.items():
        action = int(action)
        require(minimum >= 20 and new_counts[action] >= minimum,
                f"New genuinely completed teacher action {canonical.ACTIONS[action]} has {new_counts[action]}/{minimum} labels")
    child = dict(report)
    excluded = [entry for entry in report["episodes"] if entry["split"] == "eval" and group_seed(entry) not in config["new_data"]["eval_seeds"]]
    child["episodes"] = [entry for entry in report["episodes"] if entry not in excluded]
    child["episode_count"] = len(child["episodes"])
    # Assignments remain immutable even for the excluded legacy eval groups.
    # Only actual eval entries are traversed by FullEpisodeLoader/selection.
    child["groups"] = dict(report["groups"])
    child["coverage"] = {**report["coverage"], "eval": fresh_eval_coverage}
    child["model_alignment"] = {str(entry["episode_id"]): report["model_alignment"][str(entry["episode_id"])] for entry in child["episodes"]}
    eval_entries = [entry for entry in child["episodes"] if entry["split"] == "eval"]
    require({group_seed(entry) for entry in eval_entries} == set(config["new_data"]["eval_seeds"]), "Fresh selection eval missing")
    lineage = {"parent_dataset": str(parent_path/"dataset.json"), "parent_sha256": digest(parent_path/"dataset.json"),
               "immutable_stage7_dataset": str(old_index_path), "immutable_stage7_sha256": digest(old_index_path),
               "excluded_legacy_eval_episode_ids": [entry["episode_id"] for entry in excluded],
               "selection_evaluation_episode_ids": [entry["episode_id"] for entry in eval_entries],
               "selection_evaluation_groups": {entry["group"]: "eval" for entry in eval_entries},
               "engine_compatibility_sha256": digest(parent_path/"engine-compatibility.json"),
               "train_and_normalization_unchanged": True}
    child["selection_lineage"] = lineage
    child_path.mkdir()
    shutil.copyfile(parent_path/"normalization.json", child_path/"normalization.json")
    shutil.copyfile(parent_path/"engine-compatibility.json", child_path/"engine-compatibility.json")
    write(child_path/"dataset.json", child)
    (child_path/"coverage.md").write_text(canonical.coverage_markdown(child), encoding="utf-8")
    profile = {"schema_version": 1, "kind": "body_stage8_selection_view", "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "stage8_config_sha256": digest(config_path), "dataset_parent_sha256": digest(parent_path/"dataset.json"),
               "dataset_sha256": digest(child_path/"dataset.json"), "normalization_sha256": digest(child_path/"normalization.json"),
               "selection_split": "eval", "selection_view": "full_episode", "evaluation_mode": "continuous",
               "test_and_ood_used_for_selection": False, "new_teacher_train_labels_by_action": {str(action): new_counts[action] for action in range(1,12)},
               **lineage}
    write(child_path/"selection-profile.json", profile)
    assert_selection_profile(root, config, config_path)


def train_models(root: Path, config: dict[str, Any], config_path: Path, cli: list[Any], common: list[Any]) -> None:
    profile = assert_selection_profile(root, config, config_path)
    training, code = config["training"], root/"code"
    selection = {"schema_version": 1, "selection_split": "eval", "selection_view": "full_episode",
                 "evaluation_mode": "continuous", "criterion": training["selection_criterion"],
                 "config_sha256": digest(config_path), "selection_profile_sha256": digest(root/"dataset/selection-profile.json"),
                 "selection_evaluation_episode_ids": profile["selection_evaluation_episode_ids"],
                 "selection_evaluation_groups": profile["selection_evaluation_groups"],
                 "dataset_sha256": digest(root/"dataset/dataset.json"), "normalization_sha256": profile["normalization_sha256"],
                 "test_and_ood_used_for_selection": False, "models": {}}
    if (root/"selection.json").exists():
        existing_selection = read(root/"selection.json")
        require(existing_selection["dataset_sha256"] == selection["dataset_sha256"] and
                existing_selection["config_sha256"] == selection["config_sha256"] and
                existing_selection["selection_evaluation_episode_ids"] == selection["selection_evaluation_episode_ids"],
                "Existing model selection belongs to another declaration")
    else:
        existing_selection = None
    for architecture in ("gru", "mlp"):
        output = root/"models"/architecture
        output.mkdir(parents=True, exist_ok=True)
        candidates = []
        for seed in training["seeds"]:
            job = output/f"seed-{seed}"
            if not (job/"training.json").exists():
                require(not job.exists(), "Partial seed output exists; preserve it and use a fresh run")
                arguments = ["--architecture", architecture, "--seed", seed, "--stack-size", 8,
                             "--evaluation-mode", "continuous"]
                for key in ("epochs", "patience", "batch_size", "chunk_length", "burn_in", "learning_rate", "weight_decay",
                            "gradient_clip", "max_wall_seconds", "device", "threads"):
                    arguments.extend(["--"+key.replace("_", "-"), training[key]])
                command(cli+["train", *common, *arguments, "--output", job])
            result = read(job/"training.json")
            require(result["status"] == "complete" and result["test_and_ood_used_for_selection"] is False,
                    "Incomplete or held-out-selected training result")
            expected = {key: training[key] for key in ("epochs", "patience", "batch_size", "chunk_length", "burn_in",
                        "learning_rate", "weight_decay", "gradient_clip", "max_wall_seconds", "device", "threads")}
            expected.update(architecture=architecture, seed=seed, stack_size=8, evaluation_mode="continuous")
            require(all(result["config"].get(key) == value for key, value in expected.items()), "Training config changed")
            for key, file in (("dataset_sha256", root/"dataset/dataset.json"), ("normalization_sha256", root/"dataset/normalization.json"),
                              ("dataset_code_sha256", code/"scripts/body_trajectory_dataset.py")):
                require(result["dataset"].get(key) == digest(file), "Training dataset/code provenance differs")
            require(result["checkpoint_sha256"] == digest(job/"best.pt"), "Training checkpoint changed")
            history = json.loads((job/"history.json").read_text())
            best = max(history, key=lambda row: (row["eval"]["macro_f1"], -row["eval"]["nll"]))
            epoch = result["selection"]["selected_epoch"]
            require(epoch == best["epoch"] and result["selection"].get("evaluation_mode") == "continuous" and
                    result["selection"]["split"] == "eval" and result["selection"]["criterion"] == training["selection_criterion"],
                    "Training epoch selection is not declared full eval")
            for row in history:
                require(row["eval"].get("evaluation_mode") == "continuous" and row["eval"].get("artificial_chunk_resets") == 0 and
                        row["eval"].get("duplicate_burnin_labels") == 0, "An artificial chunk context selected weights")
            sidecar = read(job/"best.pt.json")
            require(sidecar["checkpoint_sha256"] == result["checkpoint_sha256"] and
                    sidecar["metadata"]["dataset"] == result["dataset"] and
                    sidecar["metadata"]["training"].get("selection_evaluation_mode") == "continuous" and
                    sidecar["metadata"]["training"]["selected_epoch"] == epoch and
                    sidecar["metadata"]["training"]["selected_eval"] == best["eval"], "Selected metadata differs")
            candidates.append({"seed": seed, "path": str(job.relative_to(root)), "epoch": epoch,
                               "eval_macro_f1": best["eval"]["macro_f1"], "eval_nll": best["eval"]["nll"],
                               "checkpoint_sha256": result["checkpoint_sha256"],
                               "training_sha256": digest(job/"training.json"), "history_sha256": digest(job/"history.json")})
        chosen = max(candidates, key=lambda row: (row["eval_macro_f1"], -row["eval_nll"]))
        source = root/chosen["path"]
        for name in ("best.pt", "best.pt.json", "training.json", "history.json"):
            if (output/name).exists():
                require(digest(output/name) == digest(source/name), "Selected artifact has changed bytes")
            else:
                shutil.copyfile(source/name, output/name)
        selected_model = {"candidates": candidates, "selected": chosen, "checkpoint": str((output/"best.pt").relative_to(root)),
                          "selection_split": "eval", "selection_view": "full_episode", "evaluation_mode": "continuous",
                          "selection_evaluation_episode_ids": profile["selection_evaluation_episode_ids"],
                          "selection_evaluation_groups": profile["selection_evaluation_groups"]}
        selection["models"][architecture] = selected_model
        write(output/"selection.json", selected_model)
    selection["weights_fixed_at_utc"] = (existing_selection["weights_fixed_at_utc"] if existing_selection is not None
                                          else dt.datetime.now(dt.timezone.utc).isoformat())
    write(root/"selection.json", selection)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("dataset", "verify", "overfit", "train", "evaluate"))
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    require(sys.platform == "linux", "Stage 8 model/data phases run on dev-ubuntu only")
    root = args.run.resolve()
    check_frozen_code(root)
    code = root/"code"
    sys.path.insert(0, str(code/"scripts"))
    import body_trajectory_dataset as canonical
    config_path = code/"evals/npc-body/body-stage8-v1.json"
    config = canonical.read_json(config_path)
    require(config["training"]["selection_view"] == "full_episode" and config["training"]["test_and_ood_selection"] is False,
            "Stage 8 requires full fresh eval-only selection")
    if args.phase == "dataset":
        make_dataset(root, config, config_path, canonical)
        return 0
    profile = assert_selection_profile(root, config, config_path)
    cli = [sys.executable, "-m", "gothic_body.cli"]
    common = ["--dataset", root/"dataset", "--dataset-code", code/"scripts/body_trajectory_dataset.py"]
    training = config["training"]
    execution = ["--device", training["device"], "--threads", training["threads"]]
    if args.phase in ("verify", "overfit"):
        settings = config["verification" if args.phase == "verify" else "overfit"]
        for architecture in ("gru", "mlp"):
            output = root/"models"/architecture
            output.mkdir(parents=True, exist_ok=True)
            options = ["--architecture", architecture, "--seed", settings["seed"], "--stack-size", 8, *execution]
            if args.phase == "verify":
                command(cli+["verify", *common, *options, "--output", output/"verify.json",
                             "--warmup", settings["warmup"], "--iterations", settings["iterations"]])
                result = read(output/"verify.json")
                require(result["passed"] is True and result["step_latency_ms"]["p95"] <= settings["model_step_p95_budget_ms"],
                        "Model forward or CPU latency gate failed")
            else:
                command(cli+["overfit", *common, *options, "--output", output/"overfit", "--steps", settings["steps"],
                             "--max-chunks", settings["max_chunks"], "--target-accuracy", settings["target_accuracy"],
                             "--learning-rate", settings["learning_rate"], "--weight-decay", settings["weight_decay"],
                             "--max-wall-seconds", settings["max_wall_seconds"], "--chunk-length", training["chunk_length"],
                             "--burn-in", training["burn_in"], "--evaluation-mode", "continuous"])
        return 0
    if args.phase == "train":
        train_models(root, config, config_path, cli, common)
        return 0
    selection = read(root/"selection.json")
    require(selection["selection_evaluation_episode_ids"] == profile["selection_evaluation_episode_ids"] and
            selection["test_and_ood_used_for_selection"] is False, "Evaluation weights were selected from another corpus")
    for architecture in ("gru", "mlp"):
        output = root/"models"/architecture
        checkpoint = output/"best.pt"
        require(digest(checkpoint) == selection["models"][architecture]["selected"]["checkpoint_sha256"], "Fixed weights changed")
        for split in ("eval", "test", "ood"):
            command(cli+["evaluate", *common, *execution, "--checkpoint", checkpoint, "--split", split,
                         "--output", output/split, "--evaluation-mode", "continuous", "--batch-size", training["batch_size"],
                         "--chunk-length", training["chunk_length"], "--burn-in", training["burn_in"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
