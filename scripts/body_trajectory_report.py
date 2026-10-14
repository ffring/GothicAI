#!/usr/bin/env python3
"""Independent stage 6 admission gates over closed native journals and a dataset."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
import tarfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from body_api_report import digest, evidence_path, native_parity, read_json, read_jsonl
from body_arena_report import clean_reset, linked_mechanics, semantic_initial, timed_pairs
import body_trajectory_dataset as dataset

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT/"evals/npc-body/body-trajectories-v1.json"
REPORTER_REGRADE_REASON = ("Regrade unchanged production service identity against its actual baseline; an already inactive "
                           "service may remain inactive. No collection, dataset, engine or mechanics criterion changed.")


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def reporter_binding(collected: str, actual: str, allow_regrade: bool) -> dict[str, Any]:
    require(collected == actual or allow_regrade, "reporter differs from collected code; explicit reporter regrade required")
    return {"collected_reporter_sha256": collected, "actual_reporter_sha256": actual,
            "reporter_regrade_reason": REPORTER_REGRADE_REASON if collected != actual else None}


def production_services(before: Any, after: Any) -> dict[str, Any]:
    """Compare real stable systemd identity, preserving an inactive baseline."""
    def parse(snapshot: Any) -> dict[str, dict[str, str]]:
        require(isinstance(snapshot, dict) and isinstance(snapshot.get("services"), dict), "production service snapshots missing")
        result = {}
        for name in ("gothic-voxtral", "gothic-voxcpm"):
            text = snapshot["services"].get(name)
            require(isinstance(text, str), f"production service snapshot missing: {name}")
            values = {}
            for line in text.splitlines():
                require("=" in line, f"malformed production service snapshot: {name}")
                key, value = line.split("=", 1)
                require(key and key not in values, f"duplicate/malformed production service field: {name}")
                values[key] = value
            require(all(key in values for key in ("ActiveState", "SubState", "MainPID")), f"production service identity missing: {name}")
            state = {key: values[key] for key in ("ActiveState", "SubState", "MainPID")}
            require(state["MainPID"].isascii() and state["MainPID"].isdigit(), f"malformed production MainPID: {name}")
            active = state["ActiveState"] == "active" and state["SubState"] == "running" and int(state["MainPID"]) > 0
            inactive = state["ActiveState"] == "inactive" and state["SubState"] == "dead" and state["MainPID"] == "0"
            require(active or inactive, f"production service identity is not a valid stable state: {name}")
            result[name] = state
        return result
    first, second = parse(before), parse(after)
    require(first == second, "production service identity changed")
    inactive = [name for name, state in first.items() if state["ActiveState"] == "inactive"]
    return {"unchanged": True, "services_all_active": not inactive, "identity": first,
            "baseline_inactive": inactive, "notes": [f"{name} was inactive/dead with MainPID=0 before collection and remained so afterward; availability was not established."
                                                       for name in inactive]}


def archive_provenance(root: Path, manifest: dict[str, Any], config: Path, *, allow_reporter_regrade: bool = False) -> dict[str, Any]:
    """Hash the frozen source archive in one streaming pass; never extract it."""
    hashes = {}
    with tarfile.open(root/"source.tar.gz", "r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            stream = archive.extractfile(member)
            sha = hashlib.sha256()
            for block in iter(lambda: stream.read(1024*1024), b""):
                sha.update(block)
            hashes[member.name] = sha.digest()
    engine = hashlib.sha256()
    engine_files = {name: sha for name, sha in hashes.items() if name.startswith("OpenGothic/")}
    for name in sorted(engine_files, key=Path):
        engine.update(name.encode()); engine.update(engine_files[name])
    require(engine.hexdigest() == manifest.get("source_sha256"), "frozen engine source SHA differs from runtime provenance")
    require(len(engine_files) == manifest.get("source_files_hashed"), "frozen engine source file count differs")
    require(digest(root/"reference/gothic-body-runner") == manifest.get("binary_sha256"), "frozen runner binary SHA differs")
    fields = {"scripts/body_trajectory_suite.py": "suite_sha256", "scripts/body_trajectory_recorder.py": "recorder_sha256",
              "scripts/body_trajectory_dataset.py": "dataset_validator_sha256",
              "scripts/body_trajectory_report.py": "reporter_sha256",
              "scripts/bench/body-arena.bench": "scenario_sha256", "evals/npc-body/body-trajectories-v1.json": "config_sha256"}
    for name, field in fields.items():
        require(name in hashes and hashes[name].hex() == manifest.get(field), f"frozen {field} differs from provenance")
    helpers = {}
    for name in ("body_trajectory_dataset.py", "body_arena_report.py", "body_api_report.py", "body_headless_parity.py", "body_baseline_report.py"):
        actual = digest(Path(__file__).resolve().parent/name)
        require(hashes.get("scripts/"+name, b"").hex() == actual, f"imported helper differs from frozen collection code: {name}")
        helpers[name] = actual
    require(digest(config) == manifest.get("config_sha256"), "report configuration differs from collected configuration")
    binding = reporter_binding(manifest["reporter_sha256"], digest(Path(__file__).resolve()), allow_reporter_regrade)
    return {"source_sha256": engine.hexdigest(), "engine_files": len(engine_files),
            "binary_sha256": manifest["binary_sha256"], "archive_sha256": digest(root/"source.tar.gz"),
            "frozen_python_sha256": {name: hashes[name].hex() for name in fields},
            "imported_helper_sha256": helpers,
            "reporter_sha256": binding["actual_reporter_sha256"], **binding}


def recorder_packets(path: Path, manifest: dict[str, Any], ipc: list[dict[str, Any]]) -> dict[str, Any]:
    packets = read_jsonl(evidence_path(path, manifest.get("capture_packets_file")))
    responses = [response for request, response, _, _ in timed_pairs(ipc) if request.get("cmd") == "body_trajectory_events"]
    require(len(packets) == len(responses), "recorded drain packets differ from raw engine RPC count")
    total = 0
    last_seq = None
    for index, (packet, response) in enumerate(zip(packets, responses), 1):
        require(packet.get("packet_seq") == index, "capture packet sequence gap")
        stats, events = packet.get("stats"), response.get("events")
        require(isinstance(stats, dict) and isinstance(events, list), "drain packet schema missing")
        require(stats == {key: value for key, value in response.items() if key != "events"}, "capture packet stats differ from native RPC")
        require(stats.get("capture_failed") is False and stats.get("capture_failures_total") == 0, "native capture failed")
        for key in ("dropped_total", "dropped_since_drain", "reset_discarded_total"):
            require(stats.get(key) == 0, f"native capture lost records: {key}")
        require(stats.get("capture_generation") == manifest["source_integrity"]["capture_generation"], "capture generation changed")
        require(packet.get("event_count") == len(events), "drain event count differs")
        require(packet.get("first_record_seq") == (events[0]["record_seq"] if events else None) and
                packet.get("last_record_seq") == (events[-1]["record_seq"] if events else None), "drain record range differs")
        if events:
            require(last_seq is None or events[0]["record_seq"] == last_seq+1, "gap across native drain packets")
            last_seq = events[-1]["record_seq"]
        total += len(events)
    require(bool(packets) and packets[-1]["stats"].get("queue_size") == 0, "native capture was not fully drained")
    require(last_seq == packets[-1]["stats"].get("record_seq_high_watermark"), "final native high watermark was not reached")
    require(total == manifest["source_integrity"]["capture_records"], "drained raw capture count differs")
    integrity = read_json(evidence_path(path, manifest.get("source_integrity_file")))
    pairs = timed_pairs(ipc)
    for field in ("runner_status", "control_events"):
        response = integrity.get(field)
        require(isinstance(response, dict), "source integrity lacks actual native counters")
        require(any(raw == response for _, raw, _, _ in pairs), "source-integrity response has no matching native RPC")
    for key in ("dropped_total", "event_dropped_total", "invalid_numeric_rows", "missed_period_buckets"):
        require(integrity["runner_status"].get("body_observation", {}).get(key) == 0, f"native observation loss: {key}")
    require(integrity["control_events"].get("dropped_total") == 0, "native control event loss")
    require(manifest.get("writer_closed") is True, "durable writer has not closed")
    return {"packets": len(packets), "records": total, "fully_drained": True, "writer_closed": True}


def arena_evidence(root: Path, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    paths = {key: evidence_path(root, record.get(key)) for key in ("events_file", "arena_events_file", "ipc_file")}
    native = read_jsonl(paths["arena_events_file"])
    reset = [row["arena"] for row in native if row.get("kind") == "reset_ready"]
    terminal = [row["arena"] for row in native if row.get("kind") == "terminal"]
    require(len(reset) == len(terminal) == 1, "one native arena reset and terminal snapshot required")
    initial, final = reset[0], terminal[0]
    require(record.get("initial") == initial and record.get("final") == final, "copied arena snapshots differ from native journal")
    require(not clean_reset(initial), "canonical native reset is dirty")
    for key in ("scenario", "seed", "side"):
        require(record.get(key) == initial.get(key), f"arena {key} differs from declaration")
    require(final.get("infrastructure") is False, "demonstration has an infrastructure outcome")
    if record.get("provider") == "native":
        require(final.get("outcome") == "death_or_down" and final.get("terminated") is True and final.get("truncated") is False,
                "native teacher demonstration is not a complete duel")
        require(any(actor.get("hp", 1) <= 0 or actor.get("down") for actor in final["actors"]), "native terminal has no down actor")
    else:
        require(final.get("outcome") == "task_success" and final.get("terminated") is True, "scripted diagnostic task did not succeed")
    mechanics = read_jsonl(paths["events_file"])
    counters = linked_mechanics(initial, final, [], mechanics)
    require(not counters["issues"], "native arena combat counters differ from actual mechanics")
    if record.get("provider") == "scripted" and initial.get("scenario") == "attack":
        require(counters["linked_attack_hits"] > 0 and counters["linked_opponent_hp_loss"] > 0, "scripted attack has no real hit/HP loss")
    if record.get("provider") == "scripted" and initial.get("scenario") == "block":
        require(counters["linked_blocked_hits"] > 0 and final["actors"][0]["hp"] == initial["actors"][0]["hp"], "scripted block has no actual defence without HP loss")
    if record.get("provider") == "scripted" and initial.get("scenario") == "distance":
        left, right = [actor["pos"] for actor in final["actors"]]
        require(math.hypot(left[0]-right[0], left[2]-right[2]) <= 220.001 and final.get("distance_held_ms", 0) >= 200,
                "distance diagnostic has no held actual range")
    require(not any(response.get("ok") is False for _, response, _, _ in timed_pairs(read_jsonl(paths["ipc_file"]))), "native RPC error in episode")
    return {"initial": initial, "final": final, "mechanics": {key: value for key, value in counters.items() if key != "issues"}}, {record[key]: digest(path) for key, path in paths.items()}


def normalized_mechanics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove runtime identities while retaining every ordered combat/physics value."""
    runtime = {}
    for row in rows:
        if row.get("kind") == "body_combat":
            runtime[row.get("actor_id")] = row.get("actor")
            if row.get("target_id"):
                runtime[row["target_id"]] = row.get("target")
    observer_ids = {}
    for row in rows:
        if row.get("phase") == "arena_actor_mapping":
            runtime[row["actor_id"]] = row["role"]
            observer_ids[row["observation_entity_id"]] = row["role"]
    result = []
    for row in rows:
        if row.get("kind") != "body_combat":
            continue
        value = dict(row)
        for key in ("actor_id", "target_id"):
            if key in value:
                value[key] = runtime.get(value[key], value[key])
        if "observation_entity_id" in value:
            value["observation_entity_id"] = observer_ids.get(value["observation_entity_id"], value["observation_entity_id"])
        for key in ("session_epoch", "world_epoch", "control_generation", "generation", "seq"):
            value.pop(key, None)
        result.append(value)
    require(bool(result), "native ordered mechanics trace is empty")
    return result


def recording_parity(root: Path, off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    require(off.get("capture") is False and on.get("capture") is True, "recording parity requires on and off modes")
    for key in ("seed", "side", "scenario", "provider"):
        require(off.get(key) == on.get(key), f"recording parity setup differs: {key}")
    require(semantic_initial(off["initial"]) == semantic_initial(on["initial"]), "recording changed initial semantic state")
    first = normalized_mechanics(read_jsonl(evidence_path(root, off.get("events_file"))))
    second = normalized_mechanics(read_jsonl(evidence_path(root, on.get("events_file"))))
    differences = []
    if len(first) != len(second):
        differences.append(f"ordered native event count {len(first)} != {len(second)}")
    for index, (a, b) in enumerate(zip(first, second)):
        if a != b:
            differences.append(f"ordered event {index}: keys differ: {sorted(key for key in set(a)|set(b) if a.get(key) != b.get(key))}")
            if len(differences) == 8:
                break
    require(not differences, "; ".join(differences))
    require(off["final"]["game_ms"] == on["final"]["game_ms"] and off["final"]["outcome"] == on["final"]["outcome"],
            "recording changed final timing/outcome")
    purity = []
    for record in (off, on):
        role_by_id = {actor["entity_id"]: actor["role"] for actor in record["final"]["actors"]}
        snapshots = [response for _, response, _, _ in timed_pairs(read_jsonl(evidence_path(root, record["ipc_file"])))
                     if response.get("arena", {}).get("game_ms") == record["final"]["game_ms"] and "body_control" in response]
        require(bool(snapshots), "recording parity lacks final native queue/RNG snapshots")
        actors = snapshots[-1]["body_control"].get("actors", [])
        states = {role_by_id[actor["entity_id"]]: actor.get("purity_state") for actor in actors if actor.get("entity_id") in role_by_id}
        require(set(states) == {"primary", "opponent"} and all(isinstance(state, dict) and
                isinstance(state.get("rng_state"), str) and state["rng_state"] and
                all(key in state for key in ("queue", "pose_identity", "loop_next_time", "fai_wait_until", "ani_wait_until"))
                for state in states.values()), "recording parity lacks actual native purity state")
        purity.append(states)
    require(purity[0] == purity[1], "recording changed final native queues, RNG, waits or pose identity")
    return {"status": "matched", "ordered_events": len(first), "physics_steps": sum(row.get("phase") == "physics_step" for row in first),
            "final_native_queue_rng_pose_waits_matched": True,
            "normalization": "Runtime actor/entity/epoch/event IDs only; no choice, queue, RNG-dependent event, time, pose, collision, damage or position difference allowed."}


def recording_warmup_evidence(root: Path, suite: dict[str, Any], config: dict[str, Any],
                              records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Bind the declared cold-cache warmup to its complete preserved native run."""
    warmups = suite.get("recording_warmup")
    require(config.get("parity_warmup_episodes") == 1 and isinstance(warmups, list) and len(warmups) == 1,
            "one declared recording-off warmup episode required")
    off, on = suite["recording_off"], suite["recording_on"]
    require(on in records, "recording-on parity is not the exact collected episode record")
    corpus_paths = {record["episode_dir"] for record in records}
    results, hashes = [], {}
    previous_epoch = None
    for record in [*warmups, off, on]:
        if record is not on:
            require(record.get("capture") is False and record.get("provider") == "native", "warmup/parity-off must be actual recording-off native episodes")
            require(record.get("episode_dir") not in corpus_paths, "recording-off diagnostic entered the training corpus")
        for key, expected in (("scenario", "native_duel"), ("seed", config["parity"]["seed"]), ("side", config["parity"]["side"])):
            require(record.get(key) == expected, f"recording warmup/parity configuration differs: {key}")
        initial = record["initial"]
        require(initial.get("distance_cm") == config["parity"]["distance_cm"] and initial.get("timeout_ms") == config["parity"]["timeout_ms"],
                "recording warmup/parity physical setup differs")
        require(semantic_initial(initial) == semantic_initial(off["initial"]), "recording warmup reset differs from measured parity setup")
        require(initial.get("session_epoch") == off["initial"].get("session_epoch"), "recording parity uses different runner sessions")
        epoch = initial.get("world_epoch")
        require(type(epoch) is int and (previous_epoch is None or epoch > previous_epoch), "recording warmup/off/on reset order differs")
        previous_epoch = epoch
    for record in warmups:
        arena, source_hashes = arena_evidence(root, record)
        hashes.update(source_hashes)
        mechanics = normalized_mechanics(read_jsonl(evidence_path(root, record["events_file"])))
        results.append({"name": record["name"], "capture": False, "outcome": arena["final"]["outcome"],
                        "ordered_events": len(mechanics), "excluded_from_dataset": True})
    return results, hashes


def declared_episode_set(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    native = config["native_duels"]
    result = {f"native-{seed}-{side}": {"provider": "native", "scenario": "native_duel", "seed": seed, "side": side,
              "ood": False, "distance_cm": native["distance_cm"], "timeout_ms": native["timeout_ms"]}
              for seed in native["seeds"] for side in native["sides"]}
    for kind, provider, ood in (("micro_cases", "scripted", False), ("ood_cases", "native", True)):
        for case in config[kind]:
            name = "scripted-"+case["name"] if provider == "scripted" else case["name"]
            result[name] = {"provider": provider, "scenario": case["task"], "ood": ood,
                            **{key: case[key] for key in ("seed", "side", "distance_cm", "timeout_ms")}}
    return result


def require_declared_episode(record: dict[str, Any], declared: dict[str, Any]) -> None:
    require(record.get("capture") is True, "declared corpus episode has recording disabled")
    for key in ("provider", "scenario", "seed", "side", "ood"):
        require(record.get(key) == declared[key], f"episode differs from collection configuration: {key}")
    for key in ("distance_cm", "timeout_ms"):
        require(record.get("initial", {}).get(key) == declared[key], f"episode physical setup differs from collection configuration: {key}")


def dataset_evidence(root: Path, config: dict[str, Any], episodes: list[Any]) -> dict[str, Any]:
    directory = root/"dataset"
    index = read_json(directory/"dataset.json")
    normalizer = read_json(directory/"normalization.json")
    assignment = dataset.split_groups(episodes, split_seed=config["splits"]["seed"])
    require(index.get("groups") == assignment, "dataset split groups differ from deterministic assignment")
    require(index.get("split_seed") == config["splits"]["seed"], "dataset split seed differs")
    expected = {str(episode.manifest["episode_id"]): episode for episode in episodes}
    entries = index.get("episodes")
    require(isinstance(entries, list) and len(entries) == len(expected), "dataset episode count differs")
    seen = set()
    for entry in entries:
        identity = str(entry.get("episode_id")); require(identity not in seen and identity in expected, "dataset has extra/duplicate episode")
        seen.add(identity); episode = expected[identity]
        require(Path(entry["path"]).resolve() == episode.path.resolve(), "dataset index references another corpus")
        require(entry["group"] == dataset.group_key(episode.manifest) and entry["split"] == assignment[entry["group"]], "group split leakage")
        require(entry.get("manifest_sha256") == digest(dataset.manifest_path(episode.path)) and entry.get("records_sha256") == digest(episode.path/"records.jsonl"), "dataset index SHA differs")
        require(entry.get("summary") == episode.summary, "dataset episode summary differs from independently validated records")
    require(index.get("normalization_sha256") == digest(directory/"normalization.json"), "normalizer SHA differs")
    # Recompute the train population independently of the persisted statistics.
    training = [episode for episode in episodes if assignment[dataset.group_key(episode.manifest)] == "train"]
    train_ids = [episode.manifest["episode_id"] for episode in training]
    require(normalizer.get("source_split") == "train" and normalizer.get("episode_ids") == train_ids, "normalizer leaks held-out episodes")
    names, count = None, 0
    sums, action_counts = defaultdict(float), Counter()
    for episode in training:
        for row in episode.observations.values():
            values = dataset.record_features(row)
            names = list(values) if names is None else names
            require(list(values) == names, "feature ordering changed")
            count += 1
            for name, value in values.items(): sums[name] += value
        for key, decision in episode.decisions.items():
            if episode.transitions[key].get("usable_for_bc"): action_counts[decision["action_id"]] += 1
    require(count > 0 and normalizer.get("observation_count") == count and normalizer.get("feature_names") == names, "normalizer training population differs")
    means = {name: sums[name]/count for name in names}; variance = defaultdict(float)
    for episode in training:
        for row in episode.observations.values():
            for name, value in dataset.record_features(row).items(): variance[name] += (value-means[name])**2
    for name in names:
        require(math.isclose(normalizer["mean"][name], means[name], rel_tol=1e-9, abs_tol=1e-8), f"train mean differs: {name}")
        population_variance = variance[name]/count
        require(math.isclose(normalizer.get("variance", {}).get(name, -1), population_variance, rel_tol=1e-8, abs_tol=1e-8), f"train variance differs: {name}")
        std = 1.0 if math.sqrt(population_variance) < 1e-6 else math.sqrt(population_variance)
        require(math.isclose(normalizer["std"][name], std, rel_tol=1e-8, abs_tol=1e-8), f"train std differs: {name}")
    require(normalizer.get("constant_features") == [name for name in names if math.sqrt(variance[name]/count) < 1e-6],
            "constant feature set differs from the train-only population")
    require(normalizer.get("class_counts") == [action_counts[i] for i in range(12)], "class weights use held-out or invalid labels")
    classes = sum(action_counts[i] > 0 for i in range(1, 12)); total = sum(action_counts.values())
    expected_weights = [0.] + [total/(classes*action_counts[i]) if action_counts[i] else 0. for i in range(1, 12)]
    require(normalizer.get("class_weights") == expected_weights, "class weights differ from train-only labels")
    coverage = {}
    for split in ("train", "eval", "test", "ood"):
        selected = [episode for episode in episodes if assignment[dataset.group_key(episode.manifest)] == split]
        coverage[split] = dataset._coverage(selected, min_labels=config["coverage"]["min_native_labels_per_class"],
                                            included_actions=config["coverage"]["included_actions"])
        require(index.get("coverage", {}).get(split) == coverage[split], f"coverage differs: {split}")
    require(index.get("diagnostic_bc_coverage_ready") == coverage["train"]["diagnostic_bc_coverage_ready"], "BC coverage gate differs")
    sequences = {}
    for split in ("train", "eval", "test", "ood"):
        labels = chunks = ticks = 0
        for chunk in dataset.OrderedSequenceLoader(directory, split=split, **{
                "chunk_length": config["sequence_loader"]["chunk_length"], "burn_in": config["sequence_loader"]["burn_in"]}):
            chunks += 1
            require(chunk.get("reset") is True and chunk["observations"][0].get("reset") is True, "loader misses recurrent reset")
            times = [row["t_game_ms"] for row in chunk["observations"]]
            require(times == sorted(times), "loader reorders observations")
            for row in chunk["observations"]:
                require(not row["burn_in"] or not row["loss_mask"], "loader applies BC loss to burn-in")
                require(all(math.isfinite(value) for value in row["features"]), "loader has nonfinite feature")
                labels += bool(row["loss_mask"]); ticks += not row["burn_in"]
        expected_labels = sum(episode.summary["usable_bc_labels"] for episode in episodes if assignment[dataset.group_key(episode.manifest)] == split)
        expected_ticks = sum(len(episode.observations) for episode in episodes if assignment[dataset.group_key(episode.manifest)] == split)
        require(labels == expected_labels and ticks == expected_ticks, "ordered loader dropped/duplicated labels or observation ticks")
        sequences[split] = {"chunks": chunks, "observation_ticks": ticks, "loss_labels": labels, "reset_and_burn_in_verified": True}
    return {"episodes": len(episodes), "groups": assignment, "train_observations": count, "train_labels": total,
            "normalization_sha256": digest(directory/"normalization.json"), "coverage": coverage, "sequences": sequences,
            "diagnostic_bc_coverage_ready": coverage["train"]["diagnostic_bc_coverage_ready"]}


def summarize(root: Path, config: dict[str, Any], native_reference: Path | None = None, *, config_path: Path | None = None,
              allow_reporter_regrade: bool = False) -> dict[str, Any]:
    issues, results, validated, hashes = [], [], [], {}
    parity = recorder_parity = statistics = provenance = warmups = services = None
    try:
        manifest, suite = read_json(root/"manifest.json"), read_json(root/"suite.json")
        require(manifest.get("suite_scope") == suite.get("suite_scope") == "full", "full collection required; smoke cannot establish stage 6 readiness")
        for key, value in (("headless", True), ("graphics_device_created", False), ("window_created", False), ("audio_device_created", False), ("model", None)):
            require(key in manifest and manifest[key] == value, f"headless provenance differs: {key}")
        require(config_path is not None, "hashed collection configuration required")
        provenance = archive_provenance(root, manifest, config_path, allow_reporter_regrade=allow_reporter_regrade)
        records = suite.get("episodes"); require(isinstance(records, list), "episode list missing")
        declaration = declared_episode_set(config)
        expected = set(declaration)
        require(len(records) == len(expected) and {record.get("name") for record in records} == expected, "declared 18 native/3 micro/2 OOD episode set differs")
        for record in records:
            try:
                require_declared_episode(record, declaration[record["name"]])
                path = evidence_path(root, record.get("manifest_file")).parent
                arena, raw_hashes = arena_evidence(root, record)
                hashes.update(raw_hashes)
                episode = dataset.validate_episode(path)
                require(episode.manifest.get("initial") == arena["initial"], "trajectory initial differs from native arena")
                require(episode.manifest.get("final", {}).get("t_game_ms") == arena["final"]["game_ms"], "trajectory terminal time differs from native arena")
                for key in ("source_sha256", "binary_sha256", "save_sha256", "config_sha256"):
                    require(episode.manifest.get(key) == manifest.get(key), f"episode provenance differs: {key}")
                require(episode.manifest.get("provider") == record["provider"] and episode.manifest.get("ood") == record["ood"], "episode provider/OOD differs")
                require(episode.manifest.get("group") == {"setup_family": config["native_duels"]["setup_family"], "seed": record["seed"]}, "related repeat group identity differs")
                if record["provider"] == "scripted": require(episode.summary["expert_labels"] == 0, "scripted micro invented native expert labels")
                packets = recorder_packets(path, episode.manifest, read_jsonl(evidence_path(root, record["ipc_file"])))
                for source in episode.manifest["source_files"]:
                    source_path = evidence_path(path, source["path"]); hashes[str(source_path.relative_to(root))] = digest(source_path)
                hashes[str(dataset.manifest_path(path).relative_to(root))] = digest(dataset.manifest_path(path))
                validated.append(episode)
                results.append({"name": record["name"], "status": "complete", "summary": episode.summary, "capture": packets,
                                "outcome": arena["final"]["outcome"], "ood": record["ood"]})
            except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
                issues.append(f"{record.get('name')}: {error}")
                results.append({"name": record.get("name"), "status": "incomplete", "issues": [str(error)]})
        try:
            off, on = suite["recording_off"], suite["recording_on"]
            warmups, warmup_hashes = recording_warmup_evidence(root, suite, config, records)
            hashes.update(warmup_hashes)
            _, off_hashes = arena_evidence(root, off)
            hashes.update(off_hashes)
            recorder_parity = recording_parity(root, off, on)
        except (OSError, ValueError, KeyError, TypeError) as error: issues.append(f"recording parity: {error}")
        if len(validated) == len(expected):
            try: statistics = dataset_evidence(root, config, validated)
            except (OSError, ValueError, KeyError, TypeError) as error: issues.append(f"dataset: {error}")
        require(native_reference is not None, "archived native reference required")
        parity = native_parity(native_reference, root/"native-off")
        require(parity.get("status") == "matched" and sorted(row.get("seed") for row in parity.get("episodes", [])) == [17,29,43], "legacy native mechanics parity failed")
        services = production_services(manifest.get("services_before"), manifest.get("services_after"))
        resources = suite.get("resources")
        require(isinstance(resources, list) and resources, "actual runner resource samples missing")
        for sample in resources:
            require(sample.get("pid") == suite.get("runner_pid") and type(sample.get("rss_kib")) is int and sample["rss_kib"] > 0, "resource sample does not describe actual runner")
            require(sample.get("forbidden_device_descriptors") == sample.get("display_socket_descriptors") == [], "runner opened GPU/audio/display device")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        issues.append(str(error))
    return {"schema_version": 1, "kind": "body_trajectories_stage6_report", "status": "complete" if not issues else "incomplete",
            "full_stage6_evidence": not issues, "issues": issues, "episodes": results, "provenance": provenance,
            "recording_warmup": warmups, "recording_parity": recorder_parity, "native_parity": parity, "dataset": statistics, "raw_sha256": hashes,
            "production_services": services, "services_all_active": bool(services and services["services_all_active"]),
            "diagnostic_bc_coverage_ready": bool(statistics and statistics["diagnostic_bc_coverage_ready"]),
            "limitations": ["This validates a diagnostic corpus and pipeline; no model, optimizer or on-policy rollout ran.",
                            "Environment/corpus readiness is separate from the 20-label-per-class and condition coverage gate.",
                            "OOD contains new separations in the same arena; geometry/opponent diversity remains future work."]}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Native body trajectories", "", f"Status: **{report['status']}**. BC coverage ready: **{report['diagnostic_bc_coverage_ready']}**.", "",
             "| Episode | Status | Expert labels | Usable BC labels | Outcome |", "|---|---|---:|---:|---|"]
    for row in report["episodes"]:
        summary = row.get("summary", {})
        lines.append(f"| {row['name']} | {row['status']} | {summary.get('expert_labels', 0)} | {summary.get('usable_bc_labels', 0)} | {row.get('outcome', '')} |")
    if report.get("dataset"):
        coverage = report["dataset"]["coverage"]["train"]
        lines += ["", "Insufficient train classes: " + (", ".join(row["action"] for row in coverage["insufficient_classes"]) or "none") + ".",
                  "Missing train conditions: " + (", ".join(coverage["missing_conditions"]) or "none") + "."]
    if report.get("production_services"):
        lines += ["", f"Production service identity unchanged: **{report['production_services']['unchanged']}**. All services active: **{report['services_all_active']}**.",
                  *report["production_services"]["notes"]]
    if (report.get("provenance") or {}).get("reporter_regrade_reason"):
        lines += ["", "Reporter regrade: " + report["provenance"]["reporter_regrade_reason"],
                  "Collected reporter SHA: `" + report["provenance"]["collected_reporter_sha256"] + "`.",
                  "Actual reporter SHA: `" + report["provenance"]["actual_reporter_sha256"] + "`."]
    lines += ["", *[f"- {issue}" for issue in report["issues"]], "", *[f"- {item}" for item in report["limitations"]], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path); parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--native-reference", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-reporter-regrade", action="store_true", help=REPORTER_REGRADE_REASON)
    args = parser.parse_args()
    if args.allow_reporter_regrade and args.output_dir.resolve().is_relative_to(args.run.resolve()):
        parser.error("reporter regrade output must be outside the immutable collected run")
    report = summarize(args.run.resolve(), read_json(args.config), args.native_reference.resolve(), config_path=args.config.resolve(),
                       allow_reporter_regrade=args.allow_reporter_regrade)
    report.update(config_sha256=digest(args.config), reporter_sha256=digest(Path(__file__).resolve()),
                  collected_reporter_sha256=(report.get("provenance") or {}).get("collected_reporter_sha256"),
                  reporter_regrade_reason=(report.get("provenance") or {}).get("reporter_regrade_reason"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/"body-trajectories.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir/"body-trajectories.md").write_text(markdown(report)+"\n")
    print(f"body trajectories {report['status']}: {args.output_dir/'body-trajectories.md'}")
    return 0 if report["full_stage6_evidence"] else 2


if __name__ == "__main__": raise SystemExit(main())
