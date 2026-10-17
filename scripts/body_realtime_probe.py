#!/usr/bin/env python3
"""Actual Linux realtime arena checks, without model/torch/physics substitutes.

One native runner process supplies every tick, actor, observation and result.
The caller must hold the shared simulation lock, as for body_setup_probe.py.
Generic native full parity is a separate root-owned phase.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time

from body_arena_env import ArenaRunner, LEASE_KEYS
from body_headless_suite import service_snapshot, sha256, write_json
from body_observe_suite import provenance


PROVIDER = "stage8-realtime-probe"
WATCHDOG_MS = 5000
FPS_BOUNDS = (27.0, 33.0)
OBSERVER_STABLE = ("generation", "session_epoch", "world_epoch", "sim_step", "watched_actors",
                   "emitted", "dropped_total", "event_dropped_total", "invalid_numeric_rows",
                   "missed_period_buckets", "entity_registry_size", "queue_size")


class ProbeRunner(ArenaRunner):
    def __init__(self, args):
        self.sent_ns = {}
        self.clock_spans = {}
        self.clock_brackets = 0
        super().__init__(args)

    def record(self, direction, value):
        now = time.monotonic_ns()
        self.ipc.write(json.dumps({"direction": direction, "wall_monotonic_ns": now,
                                  "clock_domain": "linux-clock-monotonic", "value": value},
                                 ensure_ascii=False, allow_nan=False, separators=(",", ":"))+"\n")
        self.ipc.flush()
        if direction == "request":
            self.sent_ns[value["id"]] = now
        else:
            sent = self.sent_ns.pop(value["id"])
            native = value.get("wall_monotonic_ns")
            if value.get("clock_domain") != "linux-clock-monotonic" or not isinstance(native, int):
                raise RuntimeError("native response did not attest Linux monotonic clock")
            if not sent <= native <= now:
                raise RuntimeError("native response clock is outside actual same-host IPC bounds")
            self.clock_spans[value["id"]] = {"request_ns": sent, "native_response_ns": native, "response_ns": now}
            self.clock_brackets += 1


def immutable(response):
    return {"game_ms": response["game_ms"], "sim_step": response["sim_step"],
            "native_choices": response["native_choices"], "arena": response.get("arena"),
            "body_control": response["body_control"],
            "observations": {key: response["body_observation"].get(key) for key in OBSERVER_STABLE}}


def require_hold(runner, initial, delay_s):
    time.sleep(delay_s)
    after = runner.call({"cmd": "status"})
    if immutable(after) != immutable(initial):
        raise RuntimeError(f"native world/control/observation state changed during {delay_s}s hold")
    return {"requested_wall_ms": round(delay_s*1000), "before": initial, "after": after}


def rate(before, after):
    wall_s = (after["wall_monotonic_ns"]-before["wall_monotonic_ns"])/1e9
    steps = after["sim_step"]-before["sim_step"]
    simulated_ms = after["game_ms"]-before["game_ms"]
    fps = steps/wall_s if wall_s else 0.0
    if not FPS_BOUNDS[0] <= fps <= FPS_BOUNDS[1]:
        raise RuntimeError(f"free native tick rate {fps:.3f} outside declared {FPS_BOUNDS}")
    if not 0.9 <= simulated_ms/(wall_s*1000) <= 1.1:
        raise RuntimeError("realtime native game clock did not advance near actual wall time")
    return {"wall_s": wall_s, "substeps": steps, "simulated_ms": simulated_ms,
            "substeps_per_wall_s": fps, "declared_fps_bounds": list(FPS_BOUNDS),
            "before": before, "after": after}


def pull(runner, event_history):
    packet = runner.call({"cmd": "body_observations", "max": 256})
    events = runner.call({"cmd": "body_events"})
    if not isinstance(packet.get("observations"), list) or not isinstance(events.get("events"), list):
        raise RuntimeError("native observation/event encoder returned no bounded list")
    event_history.extend(events["events"])
    return packet


def wall_probe(runner, args, result):
    config = json.loads(args.config.read_text())
    clearances = config.get("new_data", {}).get("micro_wall_clearances_cm")
    if (not isinstance(clearances, list) or len(clearances) != 6
            or any(type(value) not in (int, float) or not math.isfinite(value) or not 60 <= value <= 180 for value in clearances)
            or len(set(clearances)) != 6):
        raise RuntimeError("config.new_data.micro_wall_clearances_cm must declare six unique finite values in 60..180 cm")
    result["requested_wall_clearances_cm"] = clearances
    records = []
    result["wall_clearances"] = records
    for side in (0, 1):
        measurements = []
        for requested in clearances:
            output = args.output/f"wall-{side}-{requested}"
            output.mkdir()
            runner.episode_log(output)
            initial = runner.call({"cmd": "reset", "scenario": str(args.scenario), "mode": "realtime",
                                   "output_dir": str(output), "arena": {"scenario": "reset_stress", "seed": 17,
                                   "side": side, "distance_cm": 350, "timeout_ms": 1000,
                                   "geometry": "near_wall", "wall_clearance_cm": requested}})
            arena = initial["arena"]
            setup = arena["initial_state"]["setup"]
            proof = setup["geometry_proof"]
            if not arena["ready"] or arena["started"] or setup["wall_clearance_cm"] != requested:
                raise RuntimeError("clearance reset was not sealed before real simulation")
            for flag in ("wall_ray_hit", "capsule_move_blocked", "capsule_block_is_land", "initial_capsules_free",
                         "safe_outward_move", "grounded_placements", "target_visible", "melee_line_clear"):
                if proof.get(flag) is not True:
                    raise RuntimeError("clearance geometry lacks actual proof: "+flag)
            actual = proof["actual_wall_clearance_cm"]
            if proof["requested_wall_clearance_cm"] != requested or not 60 <= actual <= 180 or abs(actual-requested) > 20:
                raise RuntimeError("measured wall clearance violates the predeclared physical bounds")
            if abs(proof["wall_clearance_error_cm"]-(actual-requested)) > 0.001 or proof["wall_clearance_tolerance_cm"] != 20:
                raise RuntimeError("wall clearance provenance is inconsistent with actual ray")
            if proof.get("placement_basis") != "wall_normal_xz":
                raise RuntimeError("explicit wall clearance is not bound to a reported native normal")
            dot = lambda first, second: sum(a*b for a, b in zip(first, second))
            placement_normal = proof["placement_wall_normal_xz_world"]
            tangent = proof["placement_wall_tangent_world"]
            actual_normal = proof["actual_wall_normal_xz_world"]
            for vector in (placement_normal, tangent, actual_normal):
                if (len(vector) != 3 or any(not math.isfinite(value) for value in vector)
                        or abs(vector[1]) > 0.00001 or abs(dot(vector, vector)-1) > 0.0001):
                    raise RuntimeError("optional native wall placement basis is not unit horizontal geometry")
            if abs(dot(placement_normal, tangent)) > 0.0001 or dot(placement_normal, actual_normal) < 0.95:
                raise RuntimeError("native wall placement tangent or primary hit normal is inconsistent")
            if any(abs(a+b) > 0.0001 for a, b in zip(proof["wall_direction_world"], placement_normal)):
                raise RuntimeError("primary proof ray does not face the reported wall normal")
            raw_normal = proof["wall_normal"]
            raw_xz_length = math.hypot(raw_normal[0], raw_normal[2])
            raw_orientation = -1 if dot(raw_normal, proof["wall_direction_world"]) > 0 else 1
            if raw_xz_length < 0.000001 or any(abs(actual_normal[index]-raw_orientation*raw_normal[index]/raw_xz_length) > 0.0001
                                             for index in (0, 2)):
                raise RuntimeError("reported horizontal normal does not derive from the actual physics ray normal")
            center, point = proof["primary_collision_center_cm"], proof["wall_point_cm"]
            projection = dot([a-b for a, b in zip(center, point)], actual_normal)
            normal_actual = proof["actual_wall_normal_clearance_cm"]
            if (proof["requested_wall_normal_clearance_cm"] != requested or not 60 <= normal_actual <= 180
                    or abs(normal_actual-requested) > 20 or abs(projection-normal_actual) > 0.01
                    or abs(proof["wall_normal_clearance_error_cm"]-(normal_actual-requested)) > 0.001
                    or proof["wall_normal_clearance_tolerance_cm"] != 20):
                raise RuntimeError("measured native normal clearance does not satisfy its independent physical contract")
            if abs(proof["safe_height_delta_cm"]) > 50 or abs(proof["placement_height_delta_cm"]) > 50:
                raise RuntimeError("clearance variation failed actual placement/path height proof")
            packet = runner.call({"cmd": "body_observations", "max": 256})
            row = next((row for row in packet["observations"] if row["entity_id"] == arena["primary_entity_id"]), None)
            if row is None or row["world_epoch"] != arena["world_epoch"] or row["t_game_ms"] != arena["game_ms"]:
                raise RuntimeError("actual initial numeric wall observation is missing")
            direction = proof["wall_direction_world"]
            bearing = math.atan2(direction[2], direction[0])-row["self"]["yaw_rad"]
            error = lambda angle: abs((angle-bearing+math.pi) % (2*math.pi)-math.pi)
            ray = min(row["local_obstacles"]["rays"], key=lambda ray: error(ray["angle_rad"]))
            if error(ray["angle_rad"]) > 0.001 or not ray["hit"] or abs(ray["distance_cm"]-actual) > 2:
                raise RuntimeError("native wall proof does not match the actual numeric policy ray")
            world_angle = row["self"]["yaw_rad"]+ray["angle_rad"]
            numeric_normal = -ray["distance_cm"]*dot([math.cos(world_angle), 0, math.sin(world_angle)], actual_normal)
            if abs(numeric_normal-normal_actual) > 2:
                raise RuntimeError("actual numeric ray projection does not prove the separate normal clearance")
            record = {"side": side, "requested_cm": requested, "actual_cm": actual,
                      "normal_actual_cm": normal_actual, "normal_projection_cm": projection,
                      "numeric_ray_normal_projection_cm": numeric_normal,
                      "numeric_ray": ray, "numeric_ray_angle_error_rad": error(ray["angle_rad"]),
                      "numeric_observation": row, "initial": initial}
            records.append(record)
            # Compare after draining the reset rows; a read-only unstarted wait cannot refill them.
            record["pre_start_hold"] = require_hold(runner, runner.call({"cmd": "status"}), 0.5)
            if runner.call({"cmd": "body_observations", "max": 256})["observations"]:
                raise RuntimeError("unstarted wall reset emitted future numerical rows")
            measurements.append(ray["distance_cm"])
            write_json(args.output/"probe.json", result)
        if any(abs(a-b) < 2 for i, a in enumerate(measurements) for b in measurements[i+1:]):
            raise RuntimeError("declared wall variations produced indistinguishable actual numeric clearances")


def arena_probe(runner, args, result):
    output = args.output/"arena-realtime"
    output.mkdir()
    runner.episode_log(output)
    initial = runner.call({"cmd": "reset", "scenario": str(args.scenario), "mode": "realtime",
                           "output_dir": str(output), "arena": {"scenario": "native_duel", "seed": 17,
                           "side": 0, "distance_cm": 300, "timeout_ms": 1500, "load_fixture": True}})
    arena = initial["arena"]
    if initial["mode"] != "realtime" or not arena["ready"] or arena["started"]:
        raise RuntimeError("reset did not yield a ready, unstarted realtime arena")
    actors = arena["actors"]
    if len(actors) != 4 or len({actor["entity_id"] for actor in actors}) != 4:
        raise RuntimeError("load probe requires four distinct real native actors")
    pairs = arena.get("actor_pairs", [])
    if len(pairs) != 2 or {pair["primary_entity_id"] for pair in pairs} & {pair["opponent_entity_id"] for pair in pairs}:
        raise RuntimeError("actual same-world duel pair mapping is missing")
    primary = arena["primary_entity_id"]
    record = {"initial": initial, "scope": "scheduler/control proof; no neural or combat-quality credit"}
    result["arena"] = record
    write_json(args.output/"probe.json", result)
    runner.sample()
    rejected = runner.request({"cmd": "arena_start", "provider": "scripted"}, allow_error=True)
    if rejected.get("ok") is not False or rejected["arena"]["started"]:
        raise RuntimeError("unowned scripted start was not rejected without starting")
    record["invalid_unowned_start"] = rejected
    before = runner.call({"cmd": "status"})
    record["pre_start_holds"] = [require_hold(runner, before, delay) for delay in (0.5, 2.0)]
    leases = {}
    for actor in runner.call({"cmd": "body_control_status"})["actors"]:
        acquired = runner.call({"cmd": "body_acquire", **{key: actor[key] for key in LEASE_KEYS},
                                "target_entity_id": actor["target_entity_id"], "provider": PROVIDER,
                                "body_control": True, "mode": "realtime", "timeout_ms": WATCHDOG_MS})
        if acquired.get("accepted") is not True:
            raise RuntimeError("actual realtime lease acquisition was rejected")
        leases[actor["entity_id"]] = {key: acquired[key] for key in LEASE_KEYS}
    if len(leases) != 4:
        raise RuntimeError("could not acquire all four actual actors")
    runner.call({"cmd": "body_visibility_watch", "enabled": True})
    rejected = runner.request({"cmd": "arena_start", "provider": "native"}, allow_error=True)
    if rejected.get("ok") is not False or rejected["arena"]["started"]:
        raise RuntimeError("owned native start was not rejected without starting")
    record["invalid_owned_start"] = rejected
    owned_initial = runner.call({"cmd": "status"})
    record["owned_pre_start_hold"] = require_hold(runner, owned_initial, 0.5)
    events = []
    pull(runner, events)
    started = runner.call({"cmd": "arena_start", "provider": "scripted"})
    repeated = runner.request({"cmd": "arena_start", "provider": "scripted"}, allow_error=True)
    if repeated.get("ok") is not False or repeated["arena"]["start_game_ms"] != started["arena"]["start_game_ms"]:
        raise RuntimeError("repeated start changed the real arena start boundary")
    record["start"], record["repeated_start"] = started, repeated
    command_ids = {entity: 0 for entity in leases}
    blocks, latest = {}, {}
    block_deadline = time.monotonic()+0.4
    while len(blocks) < 4 and time.monotonic() < block_deadline:
        packet = pull(runner, events)
        latest.update({row["entity_id"]: row for row in packet["observations"]})
        for entity in leases:
            row = latest.get(entity)
            if entity in blocks or not row or not row["can_decide"] or not row["action_mask"][9]:
                continue
            command_ids[entity] += 1
            response = runner.call({"cmd": "body_action", **leases[entity], "provider": PROVIDER,
                                    "goal_version": row["goal"]["version"], "target_entity_id": row["target"]["entity_id"],
                                    "command_id": command_ids[entity], "action_id": 9,
                                    "observation_seq": row["observation_seq"], "issued_game_ms": row["t_game_ms"], "ttl_ms": 1000})
            if response.get("accepted"):
                blocks[entity] = response
            elif response.get("reason") not in ("stale_observation", "expired_wall"):
                raise RuntimeError(f"unexpected actual block refusal: {response.get('reason')}")
        if len(blocks) < 4:
            time.sleep(0.01)
    if len(blocks) != 4:
        raise RuntimeError("could not submit actual Block9 to each of the four actors")
    record["four_actor_blocks"] = blocks
    before = runner.call({"cmd": "status"})
    time.sleep(1.0)
    after = runner.call({"cmd": "status"})
    record["free_tick_rate"] = rate(before, after)
    if after["arena"]["elapsed_game_ms"] > (after["wall_monotonic_ns"]-started["wall_monotonic_ns"])/1e6+100:
        raise RuntimeError("arena start replayed pre-start wall delay as catch-up simulation debt")
    record["no_pre_start_catchup"] = True
    rejected = runner.request({"cmd": "arena_step", "substeps": 2}, allow_error=True)
    if rejected.get("ok") is not False or "lockstep" not in rejected.get("error", ""):
        raise RuntimeError("arena_step unexpectedly drove a realtime arena")
    record["realtime_step_rejected"] = rejected
    # Leave one actual finite native wait pending at the real timer boundary.
    # It must be censored by terminal, never promoted to fabricated completion.
    deadline = time.monotonic()+5
    latest, command = {}, command_ids[primary]
    while time.monotonic() < deadline:
        status = runner.call({"cmd": "arena_status"})
        packet = pull(runner, events)
        latest.update({row["entity_id"]: row for row in packet["observations"]})
        if status["done"]:
            break
        row = latest.get(primary)
        if status["arena"]["elapsed_game_ms"] >= 1250 and row and row["can_decide"] and row["action_mask"][11]:
            command += 1
            response = runner.call({"cmd": "body_action", **leases[primary], "provider": PROVIDER,
                                    "goal_version": row["goal"]["version"], "target_entity_id": row["target"]["entity_id"],
                                    "command_id": command, "action_id": 11, "observation_seq": row["observation_seq"],
                                    "issued_game_ms": row["t_game_ms"], "ttl_ms": 1000})
            if response.get("accepted"):
                record["pending_wait_request"] = response
                # Continue polling; the single accepted command cannot be replaced.
                latest.clear()
            elif response.get("reason") not in ("stale_observation", "expired_wall", "arena_terminal"):
                raise RuntimeError(f"unexpected real wait refusal: {response.get('reason')}")
        time.sleep(0.01)
    else:
        raise RuntimeError("realtime arena did not reach its real 1500ms timer")
    final_packet = pull(runner, events)
    final = runner.call({"cmd": "status"})
    if final["arena"]["outcome"] != "time_limit" or not final["arena"]["truncated"]:
        raise RuntimeError("idle four-actor probe did not end by actual game timer")
    if "pending_wait_request" not in record:
        raise RuntimeError("no real finite wait was accepted before terminal")
    accepted_id = record["pending_wait_request"]["command_id"]
    phases = [row["phase"] for row in events if row.get("entity_id") == primary and row.get("command_id") == accepted_id]
    if "started" not in phases or "completed" in phases or "cancelled" in phases:
        raise RuntimeError("terminal probe did not retain a genuinely started unfinished wait")
    record["final_packet"], record["final"], record["pending_wait_phases"] = final_packet, final, phases
    with (output/"events.jsonl").open() as stream:
        mechanics = [json.loads(line) for line in stream if line.strip()]
    mapping = {row["actor_id"]: row["observation_entity_id"] for row in mechanics
               if row.get("phase") == "arena_actor_mapping"}
    branch = [row for row in mechanics if row.get("phase") == "animation_branch_accept"]
    if {row.get("animation_actor_index") for row in branch} != {0, 1, 2, 3}:
        raise RuntimeError("four actual isolated animation streams were not physically executed")
    for row in branch:
        index = row["animation_actor_index"]
        if row.get("mode") != "isolated" or row.get("pair_index") != index//2 or mapping.get(row["actor_id"]) != actors[index]["entity_id"]:
            raise RuntimeError("isolated stream evidence does not bind to the actual actor/pair")
    record["four_actor_animation_streams"] = branch
    record["terminal_watchdog_hold"] = require_hold(runner, final, 6.0)
    if pull(runner, events)["observations"]:
        raise RuntimeError("new numerical rows appeared after the terminal hold")
    refused = {}
    for cmd in ("body_heartbeat", "body_acquire", "body_action", "body_goal"):
        response = runner.call({"cmd": cmd, **leases[primary], "provider": PROVIDER, "mode": "realtime",
                                "body_control": True, "target_entity_id": latest.get(primary, {}).get("target", {}).get("entity_id", arena["opponent_entity_id"]),
                                "goal_version": 2, "command_id": command+1, "action_id": 11})
        if response.get("accepted") is not False or response.get("reason") != "arena_terminal" or response["arena"] != final["arena"]:
            raise RuntimeError("late terminal mutation was not explicitly refused against the sealed arena")
        refused[cmd] = response
    record["late_refusals"] = refused
    if immutable(runner.call({"cmd": "status"})) != immutable(final):
        raise RuntimeError("terminal refusals changed native final state")
    record["cleanup_release"] = runner.call({"cmd": "body_release", **leases[primary], "provider": PROVIDER})
    other = next(entity for entity in leases if entity != primary)
    record["cleanup_disconnect"] = runner.call({"cmd": "body_disconnect", **leases[other], "provider": PROVIDER})
    record["events_after_cleanup"] = runner.call({"cmd": "body_events"})
    if record["cleanup_release"].get("accepted") is not True:
        raise RuntimeError("explicit release of the retained pending lease was rejected")
    disconnect = record["cleanup_disconnect"]
    if disconnect.get("accepted") is not True:
        # The first explicit cleanup deliberately resumes maintenance. After the
        # >5s hold it can already restore other safe actors; their old leases must
        # remain genuinely rejected, rather than fabricate a successful disconnect.
        actual = next((row for row in disconnect["body_control"]["actors"] if row["entity_id"] == other), None)
        identity = {key: leases[other][key] for key in LEASE_KEYS}
        matches = lambda row: all(row.get(key) == value for key, value in identity.items())
        cleanup = record["events_after_cleanup"]["events"]
        pending = next((row for row in cleanup if matches(row) and row.get("phase") == "ownership"
                        and row.get("reason") == "release_pending" and row.get("release_reason") == "watchdog_timeout"), None)
        restored = next((row for row in cleanup if matches(row) and row.get("phase") == "ownership"
                         and row.get("reason") == "native_restored" and row.get("release_reason") == "watchdog_timeout"
                         and row.get("owner") == "native"), None)
        span = runner.clock_spans[record["cleanup_release"]["id"]]
        if disconnect.get("reason") != "not_owned" or actual is None or actual["owner"] != "native" or actual["control_generation"] != 0:
            raise RuntimeError("cleanup refusal was not an actual already-restored native actor")
        if pending is None or restored is None or pending["event_seq"] >= restored["event_seq"]:
            raise RuntimeError("already-restored cleanup has no exact original-lease native event chain")
        if not span["request_ns"] <= pending["wall_monotonic_ns"] < restored["wall_monotonic_ns"] <= span["native_response_ns"]:
            raise RuntimeError("native restoration was not caused by the preceding explicit cleanup")
        if restored["t_game_ms"] != final["game_ms"] or restored["sim_step"] != final["sim_step"]:
            raise RuntimeError("explicit cleanup advanced native simulation after terminal")
        if any(disconnect.get(key) != leases[other][key] for key in ("entity_id", "session_epoch", "world_epoch")):
            raise RuntimeError("cleanup refusal belongs to another actor/world")
        record["cleanup_already_restored_proof"] = {"status": actual, "release_pending": pending,
                                                    "native_restored": restored, "preceding_release_clock_span": span,
                                                    "disconnect_accepted": False, "disconnect_reason": "not_owned"}
    if runner.call({"cmd": "status"})["arena"] != final["arena"]:
        raise RuntimeError("explicit cleanup rewrote the sealed arena final snapshot")
    runner.sample()
    record["status"] = "passed"
    write_json(args.output/"probe.json", result)


def generic_probe(runner, args, result):
    output = args.output/"generic-realtime"
    output.mkdir()
    runner.episode_log(output)
    source = args.scenario.read_text()
    before, marker, _ = source.partition("bodyarena 17")
    if not marker or source.count("bodyarena 17") != 1:
        raise RuntimeError("generic probe requires the declared body-arena bootstrap marker")
    scenario = args.output/"generic-realtime.bench"
    scenario.write_text(before+"bodyduel 17 2500\nwait 2600\n")
    initial = runner.call({"cmd": "reset", "scenario": str(scenario), "mode": "realtime", "output_dir": str(output)})
    if "arena" in initial or initial["mode"] != "realtime":
        raise RuntimeError("generic realtime reset retained an arena fixture")
    runner.call({"cmd": "body_watch", "enabled": True, "rate_hz": 20, "max_actors": 2})
    before = runner.call({"cmd": "status"})
    time.sleep(1.0)
    after = runner.call({"cmd": "status"})
    record = {"initial": initial, "scenario_sha256": sha256(scenario), "free_tick_rate": rate(before, after)}
    result["generic"] = record
    history, deadline = [], time.monotonic()+10
    while time.monotonic() < deadline:
        status = runner.call({"cmd": "status"})
        pull(runner, history)
        if status["done"]:
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("bounded generic realtime scenario did not finish")
    record["final"] = runner.call({"cmd": "status"})
    record["terminal_hold"] = require_hold(runner, record["final"], 0.5)
    runner.sample()
    record["status"] = "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-workers",dest="native_serial_workers",action="store_true")
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario",
                 "melee-scenario", "native-reference", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Native realtime probes run on dev-ubuntu, never on the Mac")
    for name, value in vars(args).items():
        if isinstance(value,Path):setattr(args, name, value.resolve())
    args.output.mkdir(exist_ok=False)
    result = {"schema_version": 1, "kind": "body_realtime_arena_probe", "status": "infrastructure_failure",
              "model": None, "quality_credit": False, "native_full_parity": "separate root-owned phase",
              "declared_fps_bounds": list(FPS_BOUNDS), "watchdog_ms": WATCHDOG_MS}
    runner = None
    try:
        manifest = {**provenance(args), "kind": result["kind"], "observer_ownership": "scripted API probe",
                    "probe_sha256": sha256(Path(__file__)), "model": None,
                    "helper_sha256": {name: sha256(Path(__file__).with_name(name)) for name in
                                      ("body_arena_env.py", "body_observe_suite.py", "body_headless_suite.py")}}
        write_json(args.output/"manifest.json", manifest)
        runner = ProbeRunner(args)
        result["runner_pid"] = runner.process.pid
        wall_probe(runner, args, result)
        arena_probe(runner, args, result)
        generic_probe(runner, args, result)
        result["final_close"] = runner.call({"cmd": "close"})
        if runner.process.wait(timeout=20) != 0:
            raise RuntimeError("native runner did not close cleanly")
        result["status"] = "passed"
    except Exception as error:
        result["error"] = str(error)
    finally:
        if runner is not None:
            result["clock_brackets"] = runner.clock_brackets
            result["resources"] = runner.samples
            runner.terminate()
        result["services_after"] = service_snapshot()
        result["imported_model_modules"] = [name for name in sys.modules if name.split(".")[0] in
                                            ("torch", "tensorflow", "jax", "onnxruntime")]
        if result["imported_model_modules"]:
            result["status"] = "infrastructure_failure"
            result["error"] = "model library unexpectedly imported"
        write_json(args.output/"probe.json", result)
    print(json.dumps({"status": result["status"], "error": result.get("error"), "model": None}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
