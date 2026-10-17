#!/usr/bin/env python3
"""Replay saved accepted commands in the real engine; never infer or force a hit.

The caller holds the shared dev-ubuntu simulation lock. All actions use newly
observed native identities, masks and clocks. A failed physical replay is saved
as failure, with no substituted observations or simulated combat.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from body_arena_env import ArenaRunner, LEASE_KEYS
from body_headless_suite import sha256, write_json
from body_observe_suite import provenance

PROVIDER = "stage8-visibility-boundary-replay"
PHYSICAL_PHASES = {"phase_snapshot", "physics_step", "native_attempt", "damage",
                   "defence_result", "hit_attempt", "animation_branch_accept"}
POSITION_KEYS = {"x", "y", "z", "root_dx", "root_dy", "root_dz", "requested_dx",
                 "requested_dy", "requested_dz", "moved_dx", "moved_dy", "moved_dz"}
METADATA_KEYS = {"t", "seg", "frame", "seq", "step", "sim_step", "diagnostic_generation"}


def rows(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def accepted_schedule(events, start):
    accepted = [row for row in events if row.get("phase") == "accepted"]
    if not accepted or any(row.get("action_id") not in range(1, 12) for row in accepted):
        raise RuntimeError("reference must contain genuine accepted non-continue commands")
    if any(first["event_seq"] >= second["event_seq"] for first, second in zip(accepted, accepted[1:])):
        raise RuntimeError("reference command event order is not strictly increasing")
    result = [{"offset_ms": row["t_game_ms"]-start, "issued_offset_ms": row["issued_game_ms"]-start,
               "entity_id": row["entity_id"], "command_id": row["command_id"],
               "action_id": row["action_id"], "ttl_ms": row["ttl_ms"],
               "source_event_seq": row["event_seq"]} for row in accepted]
    if any(row["offset_ms"] < 0 or row["issued_offset_ms"] > row["offset_ms"] for row in result):
        raise RuntimeError("reference command clocks precede start or point into the future")
    return result


def physical_prefix(events, start, cutoff):
    mapping = {row["actor_id"]: row["observation_entity_id"] for row in events
               if row.get("phase") == "arena_actor_mapping"}
    result = []
    for original in events:
        if (original.get("actor_id") not in mapping or original.get("phase") not in PHYSICAL_PHASES or
                not start <= original.get("t_game", -1) <= start+cutoff):
            continue
        row = {key: value for key, value in original.items() if key not in METADATA_KEYS}
        row["actor_id"] = mapping[original["actor_id"]]
        if original.get("target_id") in mapping:
            row["target_id"] = mapping[original["target_id"]]
        row["t_game"] -= start
        # These native fields are actual absolute animation clocks, not transport
        # metadata. Normalize only their shared reset origin for comparison.
        for key in ("fai_wait_until", "ani_wait_until", "wait_until", "output_wait_until"):
            if row.get(key):
                row[key] -= start
        for key in ("layers", "layers_after"):
            if key in row:
                row[key] = [dict(layer) for layer in row[key]]
                for layer in row[key]:
                    for clock in ("start", "clock"):
                        if layer.get(clock):
                            layer[clock] -= start
        # Pose identities encode absolute layer starts; compare the native layer
        # descriptions above instead of accepting a different pose/clock origin.
        for key in ("pose_before", "pose_after"):
            if key in row:
                row[key] = normalize_pose_identity(row[key], start)
        result.append(row)
    return result


def normalize_pose_identity(value, start):
    parts = value.split("|")
    for index in range(1, len(parts)):
        fields = parts[index].split(":")
        if len(fields) != 5 or not fields[1].isdigit():
            raise RuntimeError("unrecognized native pose identity")
        fields[1] = str(int(fields[1])-start)
        parts[index] = ":".join(fields)
    return "|".join(parts)


def compare(actual, expected, path="", position_tolerance_cm=1.0):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise RuntimeError(f"physical prefix object fields differ at {path}")
        for key in expected:
            compare(actual[key], expected[key], f"{path}.{key}", position_tolerance_cm)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise RuntimeError(f"physical prefix list count differs at {path}")
        for index, (left, right) in enumerate(zip(actual, expected)):
            compare(left, right, f"{path}[{index}]", position_tolerance_cm)
    elif type(expected) in (int, float) and type(actual) in (int, float):
        tolerance = position_tolerance_cm if path.rsplit(".", 1)[-1] in POSITION_KEYS else 0.0001
        if not math.isfinite(actual) or not math.isfinite(expected) or abs(actual-expected) > tolerance:
            raise RuntimeError(f"physical prefix value differs at {path}: {actual} versus {expected}")
    elif type(actual) is not type(expected) or actual != expected:
        raise RuntimeError(f"physical prefix value differs at {path}: {actual!r} versus {expected!r}")


def boundary_evidence(events, observations, controls, start, cutoff, target_entity=3):
    mapping = {row["actor_id"]: row["observation_entity_id"] for row in events
               if row.get("phase") == "arena_actor_mapping"}
    target_trace = next(key for key, value in mapping.items() if value == target_entity)
    damage = [row for row in events if row.get("actor_id") == target_trace and row.get("phase") == "damage"
              and row.get("t_game") == start+cutoff and row.get("hp_before", 0)-row.get("hp_after", 0) == 70]
    stumble = [row for row in events if row.get("actor_id") == target_trace and row.get("phase") == "native_attempt"
               and row.get("t_game") == start+cutoff and row.get("animation") == "T_STUMBLEB"
               and row.get("accepted") is True]
    if len(damage) != 1 or len(stumble) != 1:
        raise RuntimeError("replay did not preserve the actual reference 70-damage forced stumble")
    layers = stumble[0].get("layers_after", [])
    if len(layers) != 1 or layers[0].get("duration_ms") != 360 or layers[0].get("name") != "T_STUMBLEB":
        raise RuntimeError("forced native stumble is not the actual 360ms reference clip")
    cancellation = [row for row in controls if row.get("entity_id") == target_entity and
                    row.get("t_game_ms") == start+cutoff and row.get("phase") == "cancelled" and
                    row.get("reason") == "hit_reaction" and row.get("command_id") == 14 and row.get("action_id") == 4]
    if len(cancellation) != 1:
        raise RuntimeError("actual accepted right strafe was not cancelled by the preserved hit")
    locked = [row for row in observations if row.get("entity_id") == target_entity and
              start+cutoff <= row["t_game_ms"] < start+cutoff+360 and
              row["self"].get("interruptible") is False]
    invisible = [row for row in locked if row["target"].get("visible") is False]
    if not invisible:
        raise RuntimeError("replay lacks a genuinely invisible, uninterruptible forced-animation observation")
    for row in invisible:
        if (row.get("can_decide") is not False or row.get("control_allowed") is not False or
                any(row.get("action_mask", []))):
            raise RuntimeError("locked lost-visibility observation enabled a policy decision")
    release = [row for row in controls if row.get("entity_id") == target_entity and
               row.get("phase") == "ownership" and row.get("reason") in ("release_pending", "native_restored") and
               start+cutoff <= row["t_game_ms"] < start+cutoff+360]
    if release:
        raise RuntimeError("authority was released during the actual native stumble lock")
    recovered = [row for row in observations if row.get("entity_id") == target_entity and
                 start+cutoff <= row["t_game_ms"] <= start+cutoff+1000 and
                 row["target"].get("visible") is True and row.get("control_generation", 0) > 0]
    if not recovered:
        raise RuntimeError("actual pose did not recover visibility while the same owner was retained")
    return {"target_entity_id": target_entity, "hit_offset_ms": cutoff, "damage_hp": 70,
            "stumble_duration_ms": 360, "damage_event_seq": damage[0]["seq"],
            "stumble_event_seq": stumble[0]["seq"], "cancel_event_seq": cancellation[0]["event_seq"],
            "invisible_locked_observation_sequences": [row["observation_seq"] for row in invisible],
            "recovered_offset_ms": min(row["t_game_ms"]-start for row in recovered),
            "release_during_forced_lock": False, "policy_decisions_disabled_during_loss": True}


def audit_saved_output(output):
    """Re-derive every proof claim from immutable native journals; no engine/model."""
    output = Path(output)
    manifest = json.loads((output/"manifest.json").read_text())
    reference_dir = Path(manifest["reference_episode"])
    for name, digest in manifest["reference_sha256"].items():
        if sha256(reference_dir/name) != digest:
            raise RuntimeError("immutable source replay evidence changed: "+name)
    saved = json.loads((output/"replay/replay-state.json").read_text())
    reference = json.loads((reference_dir/"episode.json").read_text())
    old_start, start = reference["final"]["start_game_ms"], saved["start"]["arena"]["start_game_ms"]
    cutoff = reference["final"]["elapsed_game_ms"]
    controls = rows(output/"replay/body-control-events.jsonl")
    original_schedule = accepted_schedule(rows(reference_dir/"body-control-events.jsonl"), old_start)
    actual_schedule = accepted_schedule(controls, start)
    for row in original_schedule+actual_schedule:
        row.pop("source_event_seq")
    compare(actual_schedule, original_schedule)
    mechanics = rows(output/"replay/events.jsonl")
    before = physical_prefix(rows(reference_dir/"events.jsonl"), old_start, cutoff)
    after = physical_prefix(mechanics, start, cutoff)
    compare(after, before)
    boundary = boundary_evidence(mechanics, rows(output/"replay/observations.jsonl"), controls, start, cutoff)
    actors = saved["final"]["arena"]["actors"]
    if ({actor["entity_id"] for actor in actors} != {1, 2, 3, 4} or len(actors) != 4 or
            any(actor["owner"] != "scripted" or actor["release_pending"] for actor in actors)):
        raise RuntimeError("four real replay actors did not retain healthy ownership after recovery")
    if saved["final"]["arena"]["elapsed_game_ms"] < cutoff+1000 or saved["final"]["arena"]["outcome"] != "running":
        raise RuntimeError("actual recovery proof did not advance through the declared post-hit interval")
    return {"physical_prefix": {"status": "matched", "ordered_rows": len(before),
             "through_offset_ms": cutoff, "position_tolerance_cm": 1,
             "clock_pose_damage_mask_tolerance": "exact except float roundoff <=0.0001"},
            "boundary": boundary, "four_actors_retained": True,
            "replayed_accepted_commands": len(actual_schedule),
            "checks": {name: True for name in ("accepted_schedule_replayed", "physical_prefix_matched",
            "actual_hit_70_preserved", "forced_stumble_360_preserved", "no_unsafe_visibility_release",
            "policy_decisions_disabled_during_loss", "actual_visibility_recovered", "four_owners_retained")}}


def replay(runner, args, result):
    reference = json.loads((args.reference_episode/"episode.json").read_text())
    original = reference["initial"]
    cutoff = reference["final"]["elapsed_game_ms"]
    if (reference["final"].get("reason") != "target_visibility_lost" or cutoff != 7567 or
            original.get("load_fixture") is not True):
        raise RuntimeError("reference is not the immutable four-actor 7567ms visibility failure")
    old_start = reference["final"]["start_game_ms"]
    old_controls = rows(args.reference_episode/"body-control-events.jsonl")
    schedule = accepted_schedule(old_controls, old_start)
    if len(schedule) != 64 or {row["entity_id"] for row in schedule} != {1, 2, 3, 4}:
        raise RuntimeError("reference does not contain all 64 accepted commands across four actual NPCs")
    output = args.output/"replay"
    output.mkdir()
    runner.episode_log(output)
    initial = runner.call({"cmd": "reset", "scenario": str(args.scenario), "mode": "lockstep",
                           "output_dir": str(output), "arena": {"scenario": "native_duel", "seed": original["seed"],
                           "side": original["side"], "distance_cm": original["distance_cm"],
                           "timeout_ms": original["timeout_ms"], "load_fixture": True}})
    ids = [row["entity_id"] for row in initial["arena"]["actors"]]
    if ids != [1, 2, 3, 4]:
        raise RuntimeError("fresh reset did not produce four distinct symbolic actor identities")
    for left, right in zip(initial["arena"]["actors"], original["actors"]):
        for key in ("pos", "yaw_deg", "hp", "hp_max", "pose", "weapon", "armor"):
            compare(left[key], right[key], "."+key)
    leases = {}
    for actor in initial["body_control"]["actors"]:
        reply = runner.call({"cmd": "body_acquire", **{key: actor[key] for key in LEASE_KEYS},
                             "target_entity_id": actor["target_entity_id"], "provider": PROVIDER,
                             "body_control": True, "mode": "lockstep", "timeout_ms": 5000})
        if reply.get("accepted") is not True:
            raise RuntimeError("actual replay ownership acquisition was refused")
        leases[actor["entity_id"]] = {key: reply[key] for key in LEASE_KEYS}
    runner.call({"cmd": "body_visibility_watch", "enabled": True})
    started = runner.call({"cmd": "arena_start", "provider": "scripted"})
    start = started["arena"]["start_game_ms"]
    result.update(initial=initial, start=started, source_accepted_commands=len(schedule),
                  declared_cutoff_ms=cutoff, source_schedule=schedule, replay_receipts=[])
    pending = list(schedule)
    sampled = {}
    final = started
    while final["arena"]["elapsed_game_ms"] < cutoff+1000:
        for lease in leases.values():
            reply = runner.call({"cmd": "body_heartbeat", **lease, "provider": PROVIDER})
            if reply.get("accepted") is not True:
                raise RuntimeError("replay lost an actual actor lease")
        final = runner.call({"cmd": "arena_step", "substeps": 1})
        if final.get("advanced_substeps") != 1 or final.get("done"):
            raise RuntimeError("replay terminated or stopped before the declared recovery boundary")
        packet = runner.call({"cmd": "body_observations", "max": 256})
        for observation in packet["observations"]:
            if observation.get("periodic"):
                sampled[(observation["entity_id"], observation["t_game_ms"]-start)] = observation
        elapsed = final["arena"]["elapsed_game_ms"]
        if pending and pending[0]["offset_ms"] < elapsed:
            raise RuntimeError("saved command schedule cannot be matched to the actual game clock")
        while pending and pending[0]["offset_ms"] == elapsed:
            source = pending.pop(0)
            observation = sampled.get((source["entity_id"], source["issued_offset_ms"]))
            action = source["action_id"]
            if (observation is None or observation.get("can_decide") is not True or
                    observation.get("action_mask_valid") is not True or observation["action_mask"][action] is not True):
                raise RuntimeError(f"actual saved-command mask/periodic anchor differs: {source}")
            request = {"cmd": "body_action", **leases[source["entity_id"]], "provider": PROVIDER,
                       "goal_version": observation["goal"]["version"],
                       "target_entity_id": observation["target"]["entity_id"],
                       "command_id": source["command_id"], "action_id": action,
                       "observation_seq": observation["observation_seq"],
                       "issued_game_ms": observation["t_game_ms"], "ttl_ms": source["ttl_ms"]}
            reply = runner.call(request)
            result["replay_receipts"].append({"source": source, "request": request, "response": reply})
            if reply.get("accepted") is not True:
                raise RuntimeError(f"actual saved-command replay was refused: {reply.get('reason')}")
        runner.call({"cmd": "body_events"})
    if pending:
        raise RuntimeError("saved command schedule was not completely replayed")
    result["final"] = runner.call({"cmd": "status"})
    write_json(output/"replay-state.json", {"initial": initial, "start": started, "final": result["final"]})
    runner.call({"cmd": "close"})
    if runner.process.wait(timeout=20) != 0:
        raise RuntimeError("native replay runner did not close cleanly")
    result.update(audit_saved_output(args.output))
    result["journal_sha256"] = {name: sha256(output/name) for name in
                                ("events.jsonl", "observations.jsonl", "body-control-events.jsonl", "runner-summary.json", "ipc.jsonl")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-workers",dest="native_serial_workers",action="store_true")
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario",
                 "melee-scenario", "native-reference", "reference-episode", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native replay runs only on dev-ubuntu")
    for name, value in vars(args).items():
        if isinstance(value,Path):setattr(args, name, value.resolve())
    args.output.mkdir(exist_ok=False)
    result = {"schema_version": 1, "kind": "body_visibility_boundary_probe", "status": "failed",
              "model": None, "quality_credit": False, "native_full_parity": "separate root-owned phase"}
    runner = None
    try:
        manifest = {**provenance(args), "kind": result["kind"], "probe_sha256": sha256(Path(__file__)),
                    "helper_sha256": {name: sha256(Path(__file__).with_name(name)) for name in
                                      ("body_arena_env.py", "body_observe_suite.py", "body_headless_suite.py")},
                    "reference_episode": str(args.reference_episode), "reference_sha256": {name: sha256(args.reference_episode/name)
                    for name in ("episode.json", "events.jsonl", "observations.jsonl", "body-control-events.jsonl")}}
        write_json(args.output/"manifest.json", manifest)
        runner = ArenaRunner(args)
        result["runner_pid"] = runner.process.pid
        replay(runner, args, result)
        result["status"] = "passed"
    except Exception as error:
        result["error"] = str(error)
    finally:
        if runner is not None:
            runner.terminate()
        result["imported_model_modules"] = [name for name in sys.modules if name.split(".")[0] in
                                            ("torch", "tensorflow", "jax", "onnxruntime")]
        if result["imported_model_modules"]:
            result.update(status="failed", error="model library unexpectedly imported")
        write_json(args.output/"probe.json", result)
    print(json.dumps({"status": result["status"], "error": result.get("error"), "model": None}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
