#!/usr/bin/env python3
"""Preregister a separately frozen reaction diagnostic without changing v6.

This is a standard-library artifact tool. It never starts an engine, imports a
model, selects weights, or awards stage 8 quality credit to the known diagnostic.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import tarfile

KIND = "body_stage8_evaluation_protocol_repair"
EXPECTED = {
    "gru": "44727b7c99211c6a5e79b4f9aead852c33e277f6cee5dc4bbed95bd86af15c7c",
    "mlp": "21c81594817959490e2c4d8032d7a75296e878830ae34a89af6e56f0b084d5bf",
}
NORMALIZER = "76fc89990193b087085787d0aada1cb4683db934a81be17cbf0bc51d96f459b6"
CACHE = {"__pycache__", ".pytest_cache"}
MODULES = ("code/scripts/body_evaluation_repair.py", "code/scripts/body_block_reaction.py",
           "code/evals/npc-body/body-block-reaction-v1.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def relative_file(root, name):
    p = Path(name)
    require(not p.is_absolute() and ".." not in p.parts, "unsafe artifact path")
    result = root / p
    require(result.is_file() and result.resolve().is_relative_to(root.resolve()), "missing/escaped artifact: "+name)
    return result


def inventory(root, names):
    return {name: sha(relative_file(root, name)) for name in sorted(set(names))}


def verify_inventory(root, values):
    require(isinstance(values, dict) and values, "empty artifact inventory")
    for name, expected in values.items():
        require(sha(relative_file(root, name)) == expected, "artifact bytes changed: "+name)


def frozen_environment(root):
    environment = read(root / "environment.json")
    verify_inventory(root, {name: value for name, value in environment["frozen_files"].items()
                            if not CACHE.intersection(Path(name).parts)})
    require(sha(root / "reference/gothic-body-runner") == environment["binary_sha256"], "frozen binary changed")
    require(sha(root / "source.tar.gz") == environment["source_archive_sha256"], "frozen archive changed")
    return environment


def archive_source_identity(path):
    """Same path + per-file SHA concatenation as body_observe_suite.provenance."""
    files = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = Path(member.name)
            if name.parts and name.parts[0] == "OpenGothic" and member.isfile():
                require(".." not in name.parts and str(name) not in files, "ambiguous source archive")
                stream = archive.extractfile(member)
                require(stream is not None, "missing source archive member")
                h = hashlib.sha256()
                for block in iter(lambda: stream.read(1024*1024), b""):
                    h.update(block)
                files[str(name)] = h.hexdigest()
    require(files, "archive has no native source")
    h = hashlib.sha256()
    # pathlib sorts by path components, rather than the flattened path string.
    # This matches sorted(args.source.rglob('*')) in the frozen collector.
    for name in sorted(files, key=Path):
        value = files[name]
        h.update(name.encode()); h.update(bytes.fromhex(value))
    return {"source_sha256": h.hexdigest(), "source_files_hashed": len(files)}


def original_failures(reference):
    suite = read(reference / "realtime/suite.json")
    require(suite.get("status") == "collected" and not suite.get("error"), "original realtime evidence incomplete")
    blocks = [x for x in suite["cases"] if x["case"]["task"] == "block" and x["policy"] == "gru"]
    require(len(blocks) == 2 and {x["case"]["side"] for x in blocks} == {0, 1}, "missing original GRU block pair")
    names = ["realtime/suite.json", "realtime/manifest.json", "faults/suite.json", "launcher-stage8-faults.log"]
    findings = []
    for record in blocks:
        final = record["final"]
        primary = next(a for a in final["actors"] if a["entity_id"] == final["primary_entity_id"])
        require(primary["blocked_hits"] == 0 and final["outcome"] != "task_success", "original block failure changed")
        directory = reference / "realtime" / record["name"]
        require(read(directory / "episode.json") == record, "original episode differs from its suite")
        names.extend(str(p.relative_to(reference)) for p in directory.rglob("*") if p.is_file() and not CACHE.intersection(p.parts))
        findings.append({"name": record["name"], "outcome": final["outcome"], "reason": final["reason"],
                         "primary_hp": primary["hp"], "blocked_hits": 0, "block_success": False})
    failed = read(reference / "faults/suite.json")
    require(failed.get("status") == "infrastructure_failure" and not failed.get("cases"), "original missing-fixture failure changed")
    names.extend(str(p.relative_to(reference)) for p in (reference / "faults").rglob("*") if p.is_file())
    return {"files": inventory(reference, names), "original_gru_block": findings,
            "original_native_fault_phase_status": "infrastructure_failure", "legacy_result_replacement": False}


def fixed_artifacts(reference, config):
    selection = read(reference / "selection.json")
    require(selection.get("test_and_ood_used_for_selection") is False, "test/OOD selected original weights")
    require(config["reference"]["run_name"] == reference.name == "stage8-combat-v6", "wrong immutable reference")
    require(config["reference"]["normalizer_sha256"] == sha(reference / "dataset/normalization.json") == NORMALIZER,
            "original train normalization changed")
    names = ["selection.json", "dataset/dataset.json", "dataset/normalization.json"]
    for arch, expected in EXPECTED.items():
        checkpoint = "models/"+arch+"/best.pt"
        require(sha(reference / checkpoint) == config["reference"][arch+"_checkpoint_sha256"] == expected,
                "fixed selected checkpoint changed")
        require(selection["models"][arch]["selected"]["checkpoint_sha256"] == expected, "selection checkpoint differs")
        names.extend((checkpoint, checkpoint+".json", "models/"+arch+"/selection.json"))
    return {"files": inventory(reference, names), "checkpoint_sha256": EXPECTED,
            "normalizer_sha256": NORMALIZER, "weights_fixed_at_utc": selection["weights_fixed_at_utc"],
            "training_performed": False, "checkpoint_selection_performed": False}


def diagnostic_profile(config):
    require(config.get("schema_version") == 1 and config.get("scope") == "known_seed_diagnostic_only" and
            config.get("original_v6_credit") is False and config.get("policies") == ["native", "gru", "mlp"], "invalid diagnostic scope")
    require(config["cases"] == [{"name": f"block-reaction-223-{side}", "task": "block", "seed": 223,
            "side": side, "distance_cm": 150, "timeout_ms": 5000} for side in (0, 1)], "known diagnostic matrix changed")
    require(config["preparation"]["mode"] == config["rollout"]["mode"] == "lockstep" and
            config["runtime"]["first_sequence_dt_game_ms"] == config["runtime"]["preparation_hidden_updates"] == 0,
            "reaction sampling protocol changed")
    require(config["selection"] == {"retraining": False, "weight_selection": False,
            "known_cases_are_screening": False, "legacy_result_replacement": False}, "diagnostic used for selection/screening")
    require(config["mechanical_success"] == {"native_outcome": "task_success", "reason": "melee_hit_blocked",
            "primary_hp": 300, "blocked_hits_min": 1, "received_damage": 0}, "physical block gate weakened")


def declare(root, reference):
    root, reference = root.resolve(), reference.resolve()
    require(root != reference, "repair must have an independent frozen run")
    require(not (root / "evaluation-repair.json").exists() and not (root / "block-reaction").exists(),
            "declaration must precede diagnostic collection; preserve existing evidence")
    current, original = frozen_environment(root), frozen_environment(reference)
    config = read(relative_file(root, MODULES[2])); diagnostic_profile(config)
    require(Path(__file__).resolve() == relative_file(root, MODULES[0]).resolve(), "producer must execute its new frozen source")
    modules = inventory(root, MODULES)
    require(all(current["frozen_files"].get(k) == v for k, v in modules.items()), "new protocol source not in frozen environment")
    originals = inventory(reference, ["environment.json", "source.tar.gz", "reference/gothic-body-runner",
                "code/evals/npc-body/body-stage8-v1.json", "code/scripts/body_combat_report.py"])
    value = {"schema_version": 1, "kind": KIND, "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
             "original_run": str(reference), "new_run": str(root), "original_files": originals,
             "modules": modules, "fixed_artifacts": fixed_artifacts(reference, config),
             "original_failures": original_failures(reference), "allowed_repairs": ["reactive_block_start_protocol", "missing_native_fault_fixture"],
             "updated_runtime": {"binary_sha256": current["binary_sha256"], "source_archive_sha256": current["source_archive_sha256"],
                 "environment_sha256": sha(root / "environment.json"), **archive_source_identity(root / "source.tar.gz"),
                 "contracts": {"observation_schema": 1, "feature_count": 184, "action_version": 1,
                     "periodic_sample_ms": 50, "max_label_age_game_ms": 67}, "historical_owned_trajectory_equivalence": False},
             "reference_runtime": {"binary_sha256": original["binary_sha256"], "environment_sha256": sha(reference / "environment.json")},
             "scope": "known_seed_diagnostic_only", "proofs_pending_permitted_for_known_diagnostic_only": True,
             "output_roots": {"reactive": str(root / "block-reaction"), "native_fault_retry": str(reference / "faults-retry-v1/run")},
             "screening_preregistered": False, "screening_permitted": False, "stage8_quality_credit": False,
             "realtime_defense_gate_evaluated": False, "legacy_result_replacement": False}
    retry = reference / "faults-retry-v1/declaration.json"
    if retry.is_file():
        value["native_fault_retry_declaration"] = {"path": str(retry), "sha256": sha(retry)}
    (root / "evaluation-repair.json").write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
    return value


def validate_declaration(root):
    root = root.resolve(); value = read(root / "evaluation-repair.json")
    require(value.get("kind") == KIND and value.get("schema_version") == 1 and value.get("new_run") == str(root), "invalid repair declaration")
    reference = Path(value["original_run"])
    original = frozen_environment(reference); current = frozen_environment(root)
    for name in ("original_files",):
        verify_inventory(reference, value[name])
    verify_inventory(reference, value["fixed_artifacts"]["files"])
    verify_inventory(reference, value["original_failures"]["files"])
    verify_inventory(root, value["modules"])
    require(set(value["modules"]) == set(MODULES), "undeclared evaluation module")
    require(Path(__file__).resolve() == relative_file(root, MODULES[0]).resolve(), "auditor must execute its separately frozen source")
    require(value["fixed_artifacts"] == fixed_artifacts(reference, read(root / MODULES[2])), "fixed artifact declaration changed")
    require(value["updated_runtime"]["environment_sha256"] == sha(root / "environment.json") and
            value["updated_runtime"]["binary_sha256"] == current["binary_sha256"] and
            value["updated_runtime"]["source_archive_sha256"] == current["source_archive_sha256"], "updated runtime binding differs")
    require(value["reference_runtime"] == {"binary_sha256": original["binary_sha256"], "environment_sha256": sha(reference / "environment.json")}, "original runtime binding differs")
    diagnostic_profile(read(root / MODULES[2]))
    require(value.get("scope") == "known_seed_diagnostic_only" and all(value.get(k) is False for k in
        ("screening_preregistered", "screening_permitted", "stage8_quality_credit", "realtime_defense_gate_evaluated", "legacy_result_replacement")),
        "known diagnostic was promoted into quality/screening")
    require(value["updated_runtime"].get("historical_owned_trajectory_equivalence") is False, "historical owned equivalence fabricated")
    if "native_fault_retry_declaration" in value:
        entry = value["native_fault_retry_declaration"]
        require(host_path(entry["path"]) == reference / "faults-retry-v1/declaration.json" and sha(host_path(entry["path"])) == entry["sha256"], "fault retry binding changed")
    return value



def host_path(name):
    """A path recorded on another host names /home/<user>/...; here the same tree lives
    under this user's home. Only the location moves: callers still verify the hash."""
    path = Path(name)
    if path.exists() or len(path.parts) < 4 or path.parts[1] != "home":
        return path
    moved = Path.home().joinpath(*path.parts[3:])
    return moved if moved.exists() else path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_run", type=Path)
    parser.add_argument("--reference-run", type=Path)
    parser.add_argument("--audit", action="store_true", help="Read-only declaration recheck; no engine/model execution")
    args = parser.parse_args()
    try:
        if args.audit:
            value = validate_declaration(args.new_run)
            print(json.dumps({"kind": "body_evaluation_repair_binding_audit", "bindings_valid": True,
                              "scope": value["scope"], "stage8_quality_credit": False}))
        else:
            require(args.reference_run is not None, "--reference-run is required for preregistration")
            value = declare(args.new_run, args.reference_run)
            print(json.dumps({"declaration": str(args.new_run.resolve()/"evaluation-repair.json"),
                              "binary_sha256": value["updated_runtime"]["binary_sha256"], "stage8_quality_credit": False}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        parser.exit(2, "evaluation repair refused: "+str(error)+"\n")


if __name__ == "__main__":
    raise SystemExit(main())
