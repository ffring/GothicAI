#!/usr/bin/env python3
"""Recover closed, physically approved collection after its analysis timeout.

This offline command never creates an engine, changes source episodes or builds
a dataset. It admits only the specific frozen collector whose redundant inline
dataset phase timed out after the engine's successful close and all approvals.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import body_trajectory_dataset as canonical
from body_trajectory_recorder import TrajectoryRecorder

TIMEOUT_REASON = "unit_timeout_after_closed_collection_and_teacher_approvals"
EXIT_EVIDENCE = "captured_zero_returncode_guard_before_closed_binding_and_all_approvals"
SERVICES = ("gothic-voxtral", "gothic-voxcpm", "gothic-decider", "gothic-gemma")
CACHE_COMPONENTS = frozenset((".pytest_cache", "__pycache__"))


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def digest(path: Path) -> str:
    return canonical.sha256(path)


def bound(path: Path) -> dict[str, str]:
    require(path.is_file(), f"source evidence missing: {path}")
    return {"path": str(path.resolve()), "sha256": digest(path)}


def inventory(path: Path) -> dict[str, str]:
    require(path.is_dir(), f"source episode missing: {path}")
    files = {}
    for item in sorted(path.rglob("*")):
        if CACHE_COMPONENTS.intersection(item.relative_to(path).parts):
            continue
        require(not item.is_symlink(), f"source episode contains an unbound symlink: {item}")
        if item.is_file():
            files[str(item.relative_to(path))] = digest(item)
    require(bool(files), "empty episode inventory")
    return files


def verify_code_inventory(run: Path, captured_code: Path, files: dict[str, str]) -> list[dict[str, Any]]:
    """Only exact interpreter/test-cache components are mutable diagnostics."""
    ignored = []
    for name, sha in files.items():
        path = (run/name).resolve()
        require(path.is_relative_to(captured_code), f"captured executable escapes code: {name}")
        if CACHE_COMPONENTS.intersection(Path(name).parts):
            ignored.append({"path": str(path), "relative_path": name, "declared_sha256": sha,
                            "current_sha256": digest(path) if path.is_file() else None,
                            "reason": "mutable_python_or_pytest_cache"})
            continue
        require(path.is_file() and digest(path) == sha, f"captured executable changed: {name}")
    return ignored


def full_cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for split in ("train", "eval", "test"):
        for seed in config[split+"_seeds"]:
            for variant in config[split+"_variants"]:
                cases.append({"name": f"demo-{split}-{seed}-{variant}", "seed": seed, "split": split,
                              "variant_index": variant, "geometry": config["primary_geometry"], **config["variants"][variant]})
    for index, case in enumerate(config["near_wall_cases"]):
        group = json.dumps([config["setup_family"], case["seed"]], separators=(",", ":"))
        cases.append({**case, "variant_index": index, "geometry": "near_wall", "split": config["inherited_group_assignments"][group],
                      "center_offset_cm": [0, 0], "separation_yaw_deg": 0})
    for index, case in enumerate(config["ood_cases"]):
        cases.append({**case, "variant_index": index, "geometry": config["primary_geometry"], "split": "ood"})
    require(len(cases) == 37 and len({case["name"] for case in cases}) == 37, "recovery requires the declared full 37 cases")
    return cases


def collector_guard(path: Path) -> dict[str, Any]:
    """The frozen source establishes the exit check before existing approvals."""
    tree = ast.parse(path.read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    body = next(node.body for node in main.body if isinstance(node, ast.Try))
    statements = [ast.unparse(node) for node in body]
    snippets = ("runner.call({'cmd': 'close'})", "runner.process.wait(timeout=15)",
                "if runner.process.returncode != 0:\n    raise RecorderError('demonstration engine did not close cleanly')",
                "bind_closed_sources(args, suite['episodes'])")
    positions = []
    for snippet in snippets:
        require(statements.count(snippet) == 1, "captured collector does not have the audited close/zero-return guard")
        positions.append(statements.index(snippet))
    approvals = [index for index, node in enumerate(body) if isinstance(node, ast.For) and
                 "approve_scripted_demonstration(output, registry)" in statements[index]]
    analyses = [index for index, node in enumerate(body) if isinstance(node, ast.If) and
                "build_dataset(" in statements[index]]
    require(len(approvals) == len(analyses) == 1 and positions == sorted(positions) and
            positions[-1] < approvals[0] < analyses[0], "timeout was not after guarded close/binding/approval and before redundant analysis")
    return {"collected_driver": bound(path), "verified_control_flow": True, "backend_returncode": 0,
            "inferred": True, "guard_snippets": list(snippets),
            "guard_sha256": hashlib.sha256("\n".join(snippets).encode()).hexdigest()}


def close_evidence(path: Path, final: dict[str, Any]) -> dict[str, Any]:
    rows = canonical.read_jsonl(path)
    matches = [(index, row) for index, row in enumerate(rows) if row.get("direction") == "request" and row.get("value", {}).get("cmd") == "close"]
    require(len(matches) == 1, "source backend lacks a unique actual close request")
    index, request = matches[0]
    require(index+1 == len(rows)-1, "backend close is not the final completed IPC pair")
    response = rows[index+1]
    value = response.get("value", {})
    require(response.get("direction") == "response" and value.get("id") == request["value"].get("id") and
            value.get("ok") is True and value.get("done") is True and value.get("arena") == final and
            response["wall_monotonic"] >= request["wall_monotonic"], "backend close response is absent, mismatched or unsuccessful")
    capture = value.get("body_trajectory", {})
    require(capture.get("queue_size") == 0 and capture.get("dropped_total") == 0 and
            capture.get("reset_discarded_total") == 0 and capture.get("capture_failed") is False and
            capture.get("capture_failures_total") == 0 and capture.get("emitted") == capture.get("drained"),
            "backend close has unfinished/dropped/failed capture")
    return {"ipc_file": str(path.resolve()), "sha256": digest(path), "request_index": index,
            "response_index": index+1, "id": value["id"], "ok": True, "done": True,
            "request_wall_monotonic": request["wall_monotonic"], "response_wall_monotonic": response["wall_monotonic"],
            "capture": capture}


def verify_episode_metadata(directory: Path, record: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Bind the full arena receipt and the recorder's exact compact projection."""
    require(canonical.read_json(directory/"episode.json") == record, "source episode.json differs from persisted suite record")
    arena = canonical.read_jsonl(directory/"arena-events.jsonl")
    require([row["arena"] for row in arena if row.get("kind") == "reset_ready"] == [record["initial"]] and
            [row["arena"] for row in arena if row.get("kind") == "terminal"] == [record["final"]],
            "full episode receipt differs from actual native reset/terminal snapshots")
    original = canonical.read_json(directory/"trajectory-manifest-original.json")
    compact_final = TrajectoryRecorder._final_summary(record["final"])
    require(manifest.get("records_file") == "records-approved.jsonl" and original.get("records_file") == "records.jsonl" and
            manifest.get("initial") == original.get("initial") == record["initial"] and
            manifest.get("final") == original.get("final") == compact_final and
            manifest.get("counts") == original.get("counts") == record["counts"], "source copied episode evidence differs")
    # Approval adds exactly its separate records/proof and two file bindings;
    # the initial live manifest's metadata remains byte-bound and unchanged.
    expected = {**original, "records_file": "records-approved.jsonl",
                "scripted_expert_approval": manifest.get("scripted_expert_approval"),
                "source_files": original["source_files"]+[
                    {"path": "records-approved.jsonl", "sha256": digest(directory/"records-approved.jsonl")},
                    {"path": "trajectory-manifest-original.json", "sha256": digest(directory/"trajectory-manifest-original.json")} ]}
    require(expected == manifest, "approval altered original episode metadata or source bindings")


def source_services(run: Path) -> dict[str, Any]:
    paths = {key: run/f"services-demos-{key}.json" for key in ("before", "after")}
    snapshots = {key: canonical.read_json(path) for key, path in paths.items()}
    states = []
    for snapshot in snapshots.values():
        identities = {}
        for name in SERVICES:
            value = snapshot.get("services", {}).get(name)
            require(isinstance(value, str), f"source service evidence missing: {name}")
            fields = dict(line.split("=", 1) for line in value.splitlines())
            require(fields.get("ActiveState") == "active" and fields.get("SubState") == "running" and
                    int(fields.get("MainPID", 0)) > 0, f"source service unhealthy: {name}")
            identities[name] = {key: fields[key] for key in ("ActiveState", "SubState", "MainPID")}
        states.append(identities)
    require(states[0] == states[1], "source protected service identities changed")
    require(snapshots["before"]["recorded_at_utc"] <= snapshots["after"]["recorded_at_utc"], "source service times reversed")
    return {**{key: bound(path) for key, path in paths.items()}, "identity": states[0], "unchanged": True}


def recover(source: Path, output: Path, config_path: Path, captured_code: Path) -> dict[str, Any]:
    source, output, config_path, captured_code = (path.resolve() for path in (source, output, config_path, captured_code))
    require(not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output), "recovery destination must be new and separate")
    run = source.parent
    require(captured_code == run/"code", "captured code must belong to the original run")
    suite_path, manifest_path = source/"suite.json", source/"manifest.json"
    suite, manifest = canonical.read_json(suite_path), canonical.read_json(manifest_path)
    require(suite.get("kind") == "body_demonstrations_stage7_suite" and suite.get("suite_scope") == "full" and
            suite.get("status") == "infrastructure_failure" and not suite.get("error"), "source is not the eligible post-approval analysis timeout")
    require(manifest.get("kind") == "body_demonstrations_stage7" and manifest.get("suite_scope") == "full" and
            manifest.get("headless") is True and manifest.get("renderer") == "none" and manifest.get("model") is None,
            "source collection provenance is not the model-free headless demonstration suite")
    environment_path = run/"environment.json"
    environment = canonical.read_json(environment_path)
    files = environment.get("frozen_files", {})
    require(bool(files), "captured executable inventory missing")
    ignored_cache_files = verify_code_inventory(run, captured_code, files)
    source_archive = bound(run/"source.tar.gz")
    binary = bound(run/"reference/gothic-body-runner")
    require(source_archive["sha256"] == environment.get("source_archive_sha256") and
            binary["sha256"] == environment.get("binary_sha256") == manifest.get("binary_sha256"), "captured source archive/native binary changed")
    old_config = captured_code/"evals/npc-body/body-demonstrations-v1.json"
    require(digest(config_path) == digest(old_config) == manifest.get("config_sha256"), "recovery config differs from captured declaration")
    config = canonical.read_json(config_path)
    cases = full_cases(config)
    records = suite.get("episodes", [])
    require(len(records) == 37 and [row.get("name") for row in records] == [row["name"] for row in cases], "captured full cases/order incomplete")
    require({p.name for p in (source/"episodes").iterdir()} == {case["name"] for case in cases}, "source episode inventory differs from declared cases")
    existing_path = Path(suite.get("existing_dataset", ""))
    require(existing_path.is_file() and digest(existing_path) == suite.get("existing_dataset_sha256") and
            canonical.read_json(existing_path).get("groups") == config["inherited_group_assignments"], "immutable stage6 assignments changed")
    registry_path = source/"teacher-registry.json"
    registry = canonical.read_json(registry_path)
    entry = registry.get("teachers", {}).get(config["teacher_id"], {})
    teacher_path, driver_path = captured_code/"scripts/body_demonstration_teacher.py", captured_code/"scripts/body_demonstration_suite.py"
    require(entry.get("code_sha256") == digest(teacher_path) == manifest.get("teacher_code_sha256") and
            entry.get("config_sha256") == digest(config_path) and entry.get("provider") == config["provider"] and
            entry.get("allowed_actions") == config["coverage"]["actions"] and digest(driver_path) == manifest.get("collector_sha256"),
            "captured registry/teacher/collector source binding differs")
    guard = collector_guard(driver_path)
    log_path = run/"launcher-demos.log"
    log = log_path.read_text()
    require("Finished with result: timeout" in log and "code=killed, status=15/TERM" in log and
            all(f"[{case['name']}] declared real actions completed" in log for case in cases), "original unit timeout/closed collection progress is not evidenced")
    resources_path = run/"resources-demos.txt"
    source_resources = {**bound(resources_path), "empty": resources_path.stat().st_size == 0,
                        "measurement_available": resources_path.stat().st_size > 0}
    services = source_services(run)
    counts, borrowed, recovered_records, identities, wall_splits = {}, [], [], set(), set()
    for case, record in zip(cases, records):
        require(all(record.get(key) == value for key, value in case.items()), f"source case differs: {case['name']}")
        directory = source/"episodes"/case["name"]
        require(record.get("episode_dir") == f"episodes/{case['name']}" and
                record.get("manifest_file") == f"episodes/{case['name']}/trajectory-manifest.json", "source episode path differs")
        hashes = inventory(directory)
        episode = canonical.validate_episode(directory, teacher_registry=registry)
        m = episode.manifest
        require(m.get("episode_id") == case["name"] and m.get("declared_split") == case["split"] and
                canonical.group_key(m) == json.dumps([config["setup_family"], case["seed"]], separators=(",", ":")) and
                m.get("ood") is (case["split"] == "ood"), "captured episode group/split differs")
        require(m.get("config_sha256") == manifest["config_sha256"] and m.get("binary_sha256") == manifest["binary_sha256"] and
                m.get("source_sha256") == manifest["source_sha256"] and m.get("save_sha256") == manifest["save_sha256"], "episode engine/config source differs")
        verify_episode_metadata(directory, record, m)
        require(m["final"].get("outcome") == "time_limit" and m["final"].get("truncated") is True and
                m["final"].get("terminated") is False and m["final"].get("infrastructure") is False, "source demonstration is not a completed time-limit episode")
        initial = m["initial"]
        require(all(initial.get(key) == case[key] for key in ("seed", "side", "distance_cm")) and
                initial.get("timeout_ms") == config["budgets"]["episode_timeout_game_ms"] and
                all(initial.get("setup", {}).get(key) == case[key] for key in ("geometry", "yaw_offset_deg", "center_offset_cm", "separation_yaw_deg")), "source native setup differs")
        identity = (m["session_epoch"], m["world_epoch"])
        require(identity not in identities, "reused physical episode identity")
        identities.add(identity)
        view = canonical.periodic_model_view(episode, max_label_age_game_ms=67)
        actual = Counter(view.decisions[key]["action_id"] for key, transition in view.transitions.items() if transition["usable_for_bc"])
        require(all(actual[action] >= 1 for action in config["coverage"]["actions"]), f"source physical periodic approval incomplete: {case['name']}")
        counts.setdefault(case["split"], Counter()).update(actual)
        approval_counts = {str(action): actual[action] for action in range(1, 12)}
        require(approval_counts == {str(action): sum(row["action_id"] == action for row in m["scripted_expert_approval"]["approved"]) for action in range(1, 12)}, "periodic view drops an approved demonstration")
        if case["geometry"] == "near_wall":
            proof = initial["setup"].get("geometry_proof", {})
            require(proof.get("kind") == "existing_static_wall" and all(proof.get(key) is True for key in
                    ("capsule_move_blocked", "capsule_block_is_land", "initial_capsules_free", "safe_outward_move", "grounded_placements")), "wall episode lacks actual native geometry")
            wall_splits.add(case["split"])
        borrowed.append({"name": case["name"], "source_dir": str(directory.resolve()), "file_sha256": hashes,
                         "approved_actions": approval_counts, "model_usable_actions": approval_counts,
                         "ignored_cache_files": sorted(str(item.resolve()) for item in directory.rglob("*")
                                                       if item.is_file() and CACHE_COMPONENTS.intersection(item.relative_to(directory).parts))})
        recovered_records.append({**record, "approved_actions": approval_counts, "approval_sha256": hashes["records-approved.jsonl"]})
        print(f"body demonstration recovery [{case['name']}] immutable physical proof verified", flush=True)
    for split, field in (("train", "minimum_new_train_usable_per_action"), ("eval", "minimum_eval_usable_per_action"), ("test", "minimum_test_usable_per_action")):
        require(all(counts[split][action] >= config["coverage"][field] for action in config["coverage"]["actions"]), "recovered declared aggregate coverage is insufficient")
    require(set(config["coverage"]["wall_required_splits"]) <= wall_splits, "recovered wall split coverage incomplete")
    close = close_evidence(source/records[-1]["episode_dir"]/"ipc.jsonl", records[-1]["final"])
    close["episode"] = records[-1]["name"]
    recovery = {"schema_version": 1, "kind": "closed_demonstrations_offline_recovery", "source_directory": str(source),
                "source_run_directory": str(run), "source_suite": {**bound(suite_path), "status": suite["status"], "error": suite.get("error")},
                "source_manifest": bound(manifest_path), "captured_code_directory": str(captured_code),
                "captured_environment": bound(environment_path), "source_config": bound(old_config), "source_registry": bound(registry_path),
                "source_archive": source_archive, "source_binary": binary, "captured_code_sha256": files,
                "ignored_cache_files": ignored_cache_files,
                "inventory_cache_exclusions": sorted(CACHE_COMPONENTS),
                "collected_driver_sha256": digest(driver_path), "recovery_driver_sha256": digest(Path(__file__).resolve()),
                "validator_sha256": digest(Path(canonical.__file__).resolve()), "original_timeout": {"reason": TIMEOUT_REASON,
                    "evidence": bound(log_path), "backend_exit_evidence": EXIT_EVIDENCE, "guard_proof": guard},
                "engine_close": close, "borrowed_episodes": borrowed, "source_service_evidence": services,
                "source_resources": source_resources, "source_stage6_dataset": bound(existing_path),
                "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "source_mutation": False,
                "model_forward_performed": False, "dataset_built": False}
    # Rehash the complete borrowed inventory immediately before publication.
    require(digest(suite_path) == recovery["source_suite"]["sha256"] and digest(manifest_path) == recovery["source_manifest"]["sha256"] and
            all(inventory(Path(item["source_dir"])) == item["file_sha256"] for item in borrowed), "source changed during recovery")
    output.mkdir(parents=True)
    (output/"episodes").mkdir()
    for item in borrowed:
        (output/"episodes"/item["name"]).symlink_to(item["source_dir"], target_is_directory=True)
    (output/"teacher-registry.json").write_bytes(registry_path.read_bytes())
    result = {**suite, "status": "collected", "episodes": recovered_records, "recovery_provenance": recovery,
              "services_after": canonical.read_json(run/"services-demos-after.json"), "dataset_phase": "deferred_to_parent_canonical_dataset"}
    recovered_manifest = {**manifest, "recovery_provenance": recovery, "collected_driver_sha256": recovery["collected_driver_sha256"],
                          "recovery_driver_sha256": recovery["recovery_driver_sha256"], "completed_at_utc": recovery["completed_at_utc"]}
    for name, value in (("suite.json", result), ("manifest.json", recovered_manifest)):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output", "config", "captured-code"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    try:
        recover(args.source, args.output, args.config, args.captured_code)
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration, SyntaxError) as error:
        print(f"body demonstration recovery rejected: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
