#!/usr/bin/env python3
"""Validate stage 2 ragdoll frame coverage and gameplay controls, without accepting missing segments."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonfinite JSON number: " + value)


def finite_vector(value, count, label):
    if not isinstance(value, list) or len(value) != count or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in value
    ):
        raise ValueError("invalid finite " + label)


def distance(a, b):
    return math.sqrt(sum((x-y)**2 for x, y in zip(a, b)))


def read(run):
    with (run / "metrics.csv").open() as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("duplicate/missing CSV columns")
        rows = list(reader)
    if not rows:
        raise ValueError("empty metric table")
    required = {"seg", "frame", "ph_ms", "ph_bodies", "ph_rays", "rd_mode", "rd_weight", "rd_sleep"}
    if not required.issubset(rows[0]):
        raise ValueError("stage 2 CSV trace columns are missing")
    metrics = {}
    for row in rows:
        key = row["seg"], int(row["frame"])
        if key in metrics or None in row:
            raise ValueError("duplicate/malformed CSV row")
        for field in required - {"seg", "frame"}:
            value = float(row[field])
            if not math.isfinite(value) or value < 0:
                raise ValueError("invalid CSV physics metric: " + field)
            if field in {"ph_bodies", "ph_rays", "rd_mode", "rd_sleep"} and not value.is_integer():
                raise ValueError("noninteger CSV physics counter: " + field)
        metrics[key] = row
    events = []
    for line in (run / "events.jsonl").read_text().splitlines():
        events.append(json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant))
    if any(event.get("kind") == "ragdoll_error" for event in events):
        raise ValueError("scenario reported a ragdoll command error")
    frames = {}
    for event in events:
        if event.get("kind") != "ragdoll_frame":
            continue
        key = event["seg"], event["frame"]
        if key in frames or key not in metrics or event.get("schema_version") != 1:
            raise ValueError("duplicate or unmatched ragdoll frame")
        if any(type(event[field]) is not bool for field in ("dead", "unconscious", "sleeping")):
            raise ValueError("invalid gameplay state flag")
        if any(type(event[field]) is not int or event[field] < 0 for field in ("actor_id", "step", "part_count", "mode")):
            raise ValueError("invalid ragdoll counter")
        finite_vector(event["position_cm"], 3, "NPC position")
        if not event["world_bones"]:
            raise ValueError("world bone coverage is empty")
        names = []
        for bone in event["world_bones"]:
            names.append(bone["bone"])
            finite_vector(bone["position_cm"], 3, "world bone position")
        if len(names) != len(set(names)):
            raise ValueError("duplicate bone name")
        snapshot = event["snapshot"]
        parts = snapshot["parts"] if snapshot is not None else []
        if event["part_count"] != len(parts) or event["mode"] != (3 if parts else 0):
            raise ValueError("physical mode/part count differs from native snapshot")
        if bool(parts) != bool(event["weight"]) or event["weight"] not in (0, 1):
            raise ValueError("invalid limp-stage weight")
        if snapshot is not None:
            finite_vector(snapshot["root"], 16, "root matrix")
            if type(snapshot["version"]) is not int or snapshot["version"] not in (1, 2, 3) or not snapshot["skeleton"]:
                raise ValueError("invalid native snapshot identity")
            indices = []
            for part in parts:
                indices.append(part["bone"])
                finite_vector(part["transform"], 16, "part matrix")
                if snapshot["version"] >= 2:
                    finite_vector(part["reference_transform"], 16, "initial affine bone reference")
                if snapshot["version"] >= 3:
                    finite_vector(part["center_of_mass_meters"], 3, "exact native COM")
                    finite_vector(part["rotationxyzw"], 4, "exact native quaternion")
                    finite_vector(part["linear_velocity_meters"], 3, "exact native linear velocity")
                    if abs(sum(value*value for value in part["rotationxyzw"])-1) > .001:
                        raise ValueError("native quaternion is not a proper unit rotation")
                finite_vector(part["linear_velocity_cm_s"], 3, "linear velocity")
                finite_vector(part["angular_velocity_rad_s"], 3, "angular velocity")
                if type(part["sleeping"]) is not bool:
                    raise ValueError("invalid native sleep flag")
                index = part["bone"]
                if type(index) is not int or index < 0 or index >= len(event["world_bones"]):
                    raise ValueError("native part references an absent visual bone")
                if distance(part["transform"][12:15], event["world_bones"][index]["position_cm"]) > .1:
                    raise ValueError("final visual bone differs from its physical world frame")
            if len(indices) != len(set(indices)) or any(type(i) is not int or i < 0 for i in indices):
                raise ValueError("invalid native bone indices")
            if event["sleeping"] != all(part["sleeping"] for part in parts):
                raise ValueError("aggregate sleep differs from native part states")
            if distance(snapshot["root"][12:15], event["position_cm"]) > .1:
                raise ValueError("NPC loot/focus root does not follow the physical root")
            if snapshot["version"] >= 3:
                if type(snapshot["legacy_ancestors"]) is not bool or not isinstance(snapshot["ancestors"], list):
                    raise ValueError("invalid historic ancestor mode/coverage")
                ancestors = []
                for ancestor in snapshot["ancestors"]:
                    bone, anchor = ancestor["bone"], ancestor["anchor_bone"]
                    if type(bone) is not int or type(anchor) is not int or bone < 0 or bone >= len(event["world_bones"]) or bone in indices or anchor not in indices:
                        raise ValueError("invalid historic ancestor/native anchor mapping")
                    ancestors.append(bone)
                    finite_vector(ancestor["reference_transform"], 16, "historic ancestor frame")
                if len(ancestors) != len(set(ancestors)) or (snapshot["legacy_ancestors"] and ancestors):
                    raise ValueError("duplicate or inconsistent legacy ancestor frames")
        row = metrics[key]
        if (int(row["rd_mode"]), float(row["rd_weight"]), int(row["rd_sleep"])) != (
            event["mode"], event["weight"], int(event["sleeping"])
        ):
            raise ValueError("CSV and ragdoll event disagree")
        frames[key] = event
    if frames.keys() != metrics.keys():
        raise ValueError("ragdoll trace does not cover every recorded frame")
    for segment in {key[0] for key in frames}:
        actual = sorted(frame for seg, frame in frames if seg == segment)
        if actual != list(range(len(actual))):
            raise ValueError("noncontiguous segment: " + segment)
        sequence = [frames[segment, frame] for frame in actual]
        if len({(event["actor_id"], event["actor"]) for event in sequence}) != 1:
            raise ValueError("watched actor changes inside segment: " + segment)
        if any(b["step"] <= a["step"] or b["t_game_ms"] <= a["t_game_ms"] for a, b in zip(sequence, sequence[1:])):
            raise ValueError("simulation clock fails to advance inside segment: " + segment)
    if "done" not in (run / "bench.log").read_text().splitlines():
        raise ValueError("scenario did not finish")
    return metrics, frames, events


def bone_motion(frames):
    movement = acceleration = 0.0
    previous = older = None
    for frame in frames:
        current = {bone["bone"]: bone["position_cm"] for bone in frame["world_bones"]}
        if previous is not None:
            if current.keys() != previous.keys():
                raise ValueError("skeleton changes inside a recorded segment")
            movement = max(movement, max(distance(current[n], previous[n]) for n in current))
        if older is not None:
            acceleration = max(acceleration, max(math.sqrt(sum(
                (current[n][i]-2*previous[n][i]+older[n][i])**2 for i in range(3)
            )) for n in current))
        older, previous = previous, current
    return movement, acceleration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--expect", action="append", required=True,
                        help="segment:kind, where kind is dead, cliff, slope, sleep, ko or disabled")
    parser.add_argument("--blend-control", help="recorded ordinary AnimBlend control segment; required for ko")
    parser.add_argument("--archive", action="append", default=[],
                        help="required actual NPC archive restore label; append :old for stripped-entry fallback")
    parser.add_argument("--require-removal", action="store_true", help="require one successful actual World::removeNpc control")
    parser.add_argument("--require-loot", action="store_true", help="require normal exact focus and rendered Ransack of the moved dead_wake corpse")
    parser.add_argument("--toggle", action="append", default=[], help="required snapshot labels before_off:after_on, covering 600 ms off without a manual archive restore")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"schema_version": 1, "run": str(args.run.resolve()), "pass": False, "cases": {}}
    try:
        metrics, all_frames, events = read(args.run)
        if args.require_loot:
            focused = [event for event in events if event.get("kind") == "ragdoll_loot_focus"]
            opened = [event for event in events if event.get("kind") == "ragdoll_loot_open"]
            if len(focused) != 1 or len(opened) != 1:
                raise ValueError("actual rendered corpse focus/ransack coverage differs")
            focus, loot = focused[0], opened[0]
            if focus.get("matched") is not True or any(type(value) is not int or value < 0 for value in
                (focus["expected_actor_id"], focus["selected_actor_id"], loot["target_actor_id"], loot["player_actor_id"])):
                raise ValueError("actual corpse focus has invalid exact actor identity")
            if focus["expected_actor_id"] != focus["selected_actor_id"] or loot["target_actor_id"] != focus["expected_actor_id"] or loot["player_actor_id"] == loot["target_actor_id"] or loot["state"] != "Ransack":
                raise ValueError("normal inventory opened a different corpse or mode")
            finite_vector(focus["position_cm"], 3, "focused moved corpse position")
            gold = [item for item in focus["lootable_inventory"] if item["class_name"].upper() == "ITMI_GOLD"]
            if len(gold) != 1 or type(gold[0]["count"]) is not int or gold[0]["count"] < 7:
                raise ValueError("actual ransack lacks the declared lootable gold stack")
            initial = all_frames.get(("dead_wake", 0))
            if initial is None or initial["actor"] != focus["actor"] or distance(initial["position_cm"], focus["position_cm"]) < 20:
                raise ValueError("focus/ransack does not cover the physically moved wake corpse")
            result["loot_control"] = {"focus": focus, "opened": loot,
                                      "movement_cm": distance(initial["position_cm"], focus["position_cm"])}
        if args.toggle:
            from ragdoll_session_compare import snapshot, compare
            result["toggle_controls"] = {}
            for labels in args.toggle:
                before, after = labels.split(":")
                if not before or not after or any(not all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in label) for label in (before, after)):
                    raise ValueError("invalid toggle snapshot labels")
                expected = snapshot(args.run / ("session-"+before+".json"), "snapshot")
                actual = snapshot(args.run / ("session-"+after+".json"), "snapshot")
                for data in (expected, actual):
                    matching = [event for event in events if event.get("kind") == "ragdoll_session_archive"
                                and all(event.get(field) == value for field, value in data.items())]
                    if len(matching) != 1:
                        raise ValueError("toggle snapshot lacks exactly one matching game event")
                result["toggle_controls"][labels] = compare(expected, actual, False, minimum_advance_ms=600)
        archives = [event for event in events if event.get("kind") == "ragdoll_npc_archive"]
        if any(event.get("matched") is not True for event in archives if event.get("operation") == "restore"):
            raise ValueError("actual NPC archive restore failed")
        result["npc_archives"] = {}
        for requested in args.archive:
            label = requested.removesuffix(":old")
            old = requested.endswith(":old")
            saves = [event for event in archives if event.get("label") == label and event.get("operation") == "save"]
            restores = [event for event in archives if event.get("label") == label and event.get("operation") == "restore"
                        and bool(event.get("old_without_optional_entries")) == old]
            if len(saves) != 1 or len(restores) != 1:
                raise ValueError("required NPC archive coverage differs: " + requested)
            result["npc_archives"][requested] = restores[0]
        removals = [event for event in events if event.get("kind") == "ragdoll_remove"]
        if args.require_removal and (len(removals) != 1 or removals[0].get("matched") is not True):
            raise ValueError("actual NPC removal coverage differs")
        result["npc_removals"] = removals
        segments = {}
        for (segment, index), frame in sorted(all_frames.items()):
            segments.setdefault(segment, []).append(frame)
        control = bone_motion(segments[args.blend_control]) if args.blend_control else None
        expected = {}
        for value in args.expect:
            segment, kind = value.rsplit(":", 1)
            if segment in expected or kind not in {"dead", "cliff", "slope", "sleep", "ko", "disabled"}:
                raise ValueError("invalid expected segment: " + value)
            expected[segment] = kind
        for segment, kind in expected.items():
            frames = segments.get(segment, [])
            if len(frames) < 15:
                raise ValueError("expected segment has fewer than fifteen frames: " + segment)
            physical = [frame for frame in frames if frame["part_count"]]
            check = {"kind": kind, "frames": len(frames), "physical_frames": len(physical), "pass": True}
            if kind == "disabled":
                check["pass"] = not physical and all(frame["mode"] == 0 for frame in frames)
            elif kind == "ko":
                if control is None:
                    raise ValueError("KO requires a recorded ordinary AnimBlend control")
                knocked_out = [frame for frame in frames if frame["unconscious"] and frame["part_count"]]
                restored = [frame for frame in frames if not frame["unconscious"] and not frame["dead"] and not frame["part_count"]]
                handoffs = [i for i in range(1, len(frames)) if frames[i-1]["part_count"] and not frames[i]["part_count"]]
                if not knocked_out or not restored or len(handoffs) != 1:
                    check["pass"] = False
                    check["reason"] = "missing unconscious physical phase or a unique successful return"
                else:
                    handoff = handoffs[0]
                    motion = bone_motion(frames[max(0, handoff-2):handoff+20])
                    check["recovery_max_bone_frame_cm"], check["recovery_max_second_difference_cm"] = motion
                    check["control_max_bone_frame_cm"], check["control_max_second_difference_cm"] = control
                    check["pass"] = all(actual <= limit + .001 for actual, limit in zip(motion, control))
            else:
                check["pass"] = bool(physical) and all(frame["dead"] and frame["hp"] <= 0 for frame in frames)
                if physical:
                    start, end = physical[0]["position_cm"], physical[-1]["position_cm"]
                    check["npc_displacement_cm"] = distance(start, end)
                    check["vertical_drop_cm"] = start[1]-min(frame["position_cm"][1] for frame in physical)
                    check["horizontal_displacement_cm"] = math.hypot(end[0]-start[0], end[2]-start[2])
                    if kind == "cliff":
                        check["pass"] &= check["vertical_drop_cm"] >= 300
                    elif kind == "slope":
                        check["pass"] &= check["vertical_drop_cm"] >= 50 and check["horizontal_displacement_cm"] >= 50
                    elif kind == "sleep":
                        sleeping = [frame for frame in physical if frame["sleeping"]]
                        check["sleeping_frames"] = len(sleeping)
                        check["pass"] &= len(sleeping) >= 15
                        if sleeping:
                            pose = sleeping[0]["snapshot"]
                            check["pass"] &= all(frame["snapshot"] == pose for frame in sleeping)
            result["cases"][segment] = check
        result["pass"] = bool(result["cases"]) and all(case["pass"] for case in result["cases"].values())
        result["coverage"] = {"csv_frames": len(metrics), "trace_frames": len(all_frames)}
        result["input_sha256"] = {name: hashlib.sha256((args.run / name).read_bytes()).hexdigest()
                                  for name in ("metrics.csv", "events.jsonl", "bench.log")}
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as error:
        result["error"] = str(error)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("PASS" if result["pass"] else "FAIL")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
