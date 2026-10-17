#!/usr/bin/env python3
"""Read-only comparison of actual perception traces before and after a motor fix.

This is an analysis aid, not a quality gate. Native identities are derived from
same-episode diagnostics, and every actor is cut off at its first native restore.
All excluded rows remain in the source journals and are counted here. No oracle
diagnostic is a policy input. Angles/travel are sampled evidence, not exact motion
integrals; the source row endpoints are included to make that limit explicit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def rows(path):
    if not path.is_file():
        return []
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def wrap(angle):
    return (angle + 180.0) % 360.0 - 180.0


def clock(row):
    return row.get("wall_monotonic_ns")


def game(row):
    return row.get("t_game_ms", row.get("t_game"))


def endpoint(row):
    if row is None:
        return None
    return {key: row.get(key) for key in (
        "seq", "diagnostic_seq", "sim_step", "t_game", "wall_monotonic_ns",
        "entity_id", "target_entity_id", "source_phase", "owner", "provider",
        "command_id", "execution_id", "animation", "body_yaw_deg", "pose_view_deg",
        "actual_can_see", "reason", "self_position_cm", "target_position_cm")}


def trace_summary(trace):
    if not trace:
        return {"sample_count": 0}
    yaw = [float(row["body_yaw_deg"]) for row in trace]
    view = [float(row["pose_view_deg"]) for row in trace]
    gap = [wrap(v-y) for y, v in zip(yaw, view)]
    lost = [row for row in trace if row.get("actual_can_see") is False]
    cone_clear = [row for row in lost if all(
        ray and ray.get("range_pass") is True and ray.get("ray_hit") is False
        for ray in (row.get("center_ray"), row.get("head_ray")))
        and row.get("reason") == "outside_pose_cone"]
    a, b = trace[0]["self_position_cm"], trace[-1]["self_position_cm"]
    first_loss = lost[0] if lost else None
    recovery = next((row for row in trace if first_loss
                     and row["seq"] > first_loss["seq"]
                     and row.get("actual_can_see") is True), None)
    return {
        "sample_count": len(trace), "actual_visible_samples": len(trace)-len(lost),
        "actual_lost_samples": len(lost), "lost_reasons": dict(Counter(r["reason"] for r in lost)),
        "clear_rays_pose_cone_lost_samples": len(cone_clear),
        "query_mismatch_samples": sum(r.get("query_matches_diagnostic") is not True for r in trace),
        "sampled_body_yaw_arc_deg": sum(wrap(y-x) for x, y in zip(yaw, yaw[1:])),
        "sampled_pose_view_arc_deg": sum(wrap(y-x) for x, y in zip(view, view[1:])),
        "max_abs_pose_body_gap_deg": max(map(abs, gap)),
        "sampled_displacement_cm": math.dist(a, b),
        "first": endpoint(trace[0]), "last": endpoint(trace[-1]),
        "first_loss": endpoint(first_loss), "first_recovery_after_loss": endpoint(recovery),
        "first_loss_to_recovery_game_ms": game(recovery)-game(first_loss) if recovery else None,
    }


def identity(row, initial):
    return all(row.get(key) == initial.get(key) for key in ("session_epoch", "world_epoch"))


def pair_targets(initial):
    pairs = initial.get("actor_pairs") or [{
        "primary_entity_id": initial["primary_entity_id"],
        "opponent_entity_id": initial["opponent_entity_id"]}]
    return {a: b for pair in pairs for a, b in (
        (pair["primary_entity_id"], pair["opponent_entity_id"]),
        (pair["opponent_entity_id"], pair["primary_entity_id"]))}


def after_restore(row, restored):
    if restored is None:
        return False
    # Same-process Linux monotonic clocks order the separate native journals.
    # Refuse to silently invent within-frame order when either timestamp is absent.
    if clock(row) is None or clock(restored) is None:
        raise ValueError("pre-handoff comparison requires native monotonic timestamps")
    return clock(row) >= clock(restored)


def eligible_endpoint(control, observations, start, finish):
    if finish is None or finish.get("phase") != "completed":
        return None
    following = next((r for r in control if identity(r, start) and r.get("entity_id") == start["entity_id"]
                      and r.get("control_generation") == start["control_generation"]
                      and r.get("phase") == "accepted" and clock(r) > clock(finish)), None)
    row = next((r for r in observations if identity(r, start) and r.get("entity_id") == start["entity_id"]
                and r.get("control_generation") == start["control_generation"]
                and r.get("periodic") is True and r.get("can_decide") is True
                and r.get("t_game_ms", -1) >= game(finish)
                and r.get("snapshot_wall_monotonic_ns", 0) > clock(finish)
                and (following is None or r["snapshot_wall_monotonic_ns"] < clock(following))), None)
    if row is None:
        return None
    return {"observation_seq": row["observation_seq"], "game_ms": row["t_game_ms"],
            "snapshot_wall_monotonic_ns": row["snapshot_wall_monotonic_ns"],
            "can_decide": row["can_decide"], "control_allowed": row["control_allowed"],
            "self": {key: row["self"].get(key) for key in
                     ("yaw_rad", "position_world_cm", "in_air", "swim", "dive", "body_state_base",
                      "interruptible", "gates")},
            "target": {key: row["target"].get(key) for key in
                       ("entity_id", "visible", "relative_position_cm", "yaw_error_rad",
                        "fight_distance_cm", "in_w_range", "focus_30", "focus_5", "melee_line_clear")},
            "before_next_command": {key: following.get(key) for key in
                                    ("command_id", "action_id", "t_game_ms", "wall_monotonic_ns")}
                                   if following else None}


def owned_strafes(control, observations, traces, initial):
    groups = defaultdict(list)
    for row in control:
        if identity(row, initial) and row.get("action_id") in (3, 4) and row.get("execution_id", 0):
            groups[(row["entity_id"], row["control_generation"], row["execution_id"])].append(row)
    result = []
    for (entity, generation, execution), events in groups.items():
        starts = [r for r in events if r.get("phase") == "started"]
        if not starts:
            continue
        start = starts[0]
        finish = next((r for r in events if r.get("phase") in ("completed", "cancelled")), None)
        actor_trace = traces.get(entity, [])
        during = [r for r in actor_trace if r.get("control_generation") == generation
                  and r.get("execution_id") == execution
                  and clock(r) <= (clock(finish) if finish else float("inf"))]
        # Preserve the causal previous sampled pose, before accepted movement starts.
        anchor = next((r for r in reversed(actor_trace) if clock(r) <= clock(start)), None)
        if anchor and (not during or anchor["seq"] != during[0]["seq"]):
            during.insert(0, anchor)
        result.append({
            "source": "owned_command", "entity_id": entity, "control_generation": generation,
            "execution_id": execution, "command_id": start["command_id"],
            "action_id": start["action_id"], "effective_primitive": start["effective_primitive"],
            "started_game_ms": game(start), "finished_game_ms": game(finish) if finish else None,
            "finished_phase": finish.get("phase") if finish else None,
            "finished_reason": finish.get("reason") if finish else None,
            "started_event_seq": start["event_seq"],
            "finished_event_seq": finish.get("event_seq") if finish else None,
            "first_eligible_periodic_before_next_command": eligible_endpoint(control, observations, start, finish),
            "trace": trace_summary(during),
        })
    return result


def native_strafes(mechanics, traces, native_ids):
    result = []
    for entity, native_actor in native_ids.items():
        actor_trace = traces[entity]
        consume = [r for r in mechanics if r.get("actor_id") == native_actor
                   and r.get("phase") == "native_consume"]
        for index, start in enumerate(consume):
            if start.get("primitive") not in (7, 8) or start["seq"] > actor_trace[-1]["seq"]:
                continue
            finish = next((r for r in consume[index+1:] if r.get("primitive") == 15), None)
            end_seq = finish["seq"] if finish else float("inf")
            during = [r for r in actor_trace if start["seq"] <= r["seq"] <= end_seq]
            anchor = next((r for r in reversed(actor_trace) if r["seq"] <= start["seq"]), None)
            if anchor:
                during.insert(0, anchor)
            if not during:
                continue
            result.append({
                "source": "native_queue", "entity_id": entity, "native_actor_id": native_actor,
                "choice_id": start.get("choice_id"), "effective_primitive": start["primitive"],
                "action_id": 3 if start["primitive"] == 7 else 4,
                "started_game_ms": game(start), "finished_game_ms": game(finish) if finish else None,
                "finished_phase": "native_strafe_end_consumed" if finish else None,
                "start_journal_seq": start["seq"], "finish_journal_seq": finish.get("seq") if finish else None,
                "trace": trace_summary(during),
            })
    return result


def housekeeping(mechanics, control, traces, native_ids):
    result, unpaired = [], []
    for entity, trace in traces.items():
        for before in trace:
            phase = before.get("source_phase", "")
            if not phase.startswith("owned_consumed_") or not phase.endswith("_before"):
                continue
            expected = phase[:-7]+"_after"
            after = next((r for r in trace if r["seq"] > before["seq"]
                          and r.get("source_phase") == expected
                          and all(r.get(k) == before.get(k) for k in
                                  ("sim_step", "control_generation", "command_id", "execution_id"))), None)
            if not after:
                unpaired.append(endpoint(before))
                continue
            between = [r for r in mechanics if before["seq"] < r.get("seq", -1) < after["seq"]
                       and r.get("actor_id") == native_ids[entity]]
            new_commands = [r["event_seq"] for r in control
                            if r.get("entity_id") == entity
                            and r.get("phase") in ("accepted", "started")
                            and clock(before) < clock(r) < clock(after)]
            result.append({
                "entity_id": entity, "case": phase[len("owned_consumed_"):-7],
                "sim_step": before["sim_step"], "execution_id": before["execution_id"],
                "before_journal_seq": before["seq"], "after_journal_seq": after["seq"],
                "body_yaw_delta_deg": wrap(after["body_yaw_deg"]-before["body_yaw_deg"]),
                "visible_before": before["actual_can_see"], "visible_after": after["actual_can_see"],
                "fai_wait_before": before.get("native_fai_wait"),
                "script_wait_before": before.get("native_script_wait"),
                "fai_wait_remaining_ms": before.get("native_fai_wait_remaining_ms"),
                "script_wait_remaining_ms": before.get("native_script_wait_remaining_ms"),
                "native_gate_reasons": [r.get("reason") for r in between if r.get("phase") == "native_gate"],
                "chooser_or_consume_between": [r["seq"] for r in between if r.get("phase") in
                                                ("native_choice", "native_consume")],
                "new_commands_between": new_commands,
            })
    return {"paired_spans": result, "unpaired_before": unpaired,
            "chooser_or_consume_violation_count": sum(bool(r["chooser_or_consume_between"]) for r in result),
            "new_commands_violation_count": sum(bool(r["new_commands_between"]) for r in result)}


def analyze(directory):
    episode = load(directory/"episode.json")
    initial = episode["initial"]
    target = pair_targets(initial)
    mechanics = rows(directory/"events.jsonl")
    control = rows(directory/"body-control-events.jsonl")
    observations = rows(directory/"observations.jsonl")
    restored = {entity: next((r for r in control if identity(r, initial)
                             and r.get("entity_id") == entity
                             and r.get("reason") == "native_restored"), None) for entity in target}
    excluded, traces, native_ids = Counter(), defaultdict(list), {}
    total = 0
    for row in mechanics:
        if row.get("phase") != "visibility_diagnostic":
            continue
        total += 1
        entity = row.get("entity_id")
        if not identity(row, initial):
            excluded["different_epoch"] += 1
        elif entity not in target:
            excluded["non_arena_actor"] += 1
        elif after_restore(row, restored[entity]):
            excluded["post_native_restored_paired" if row.get("target_entity_id") == target[entity]
                     else "post_native_restored_retarget"] += 1
        elif row.get("target_entity_id") != target[entity]:
            excluded["pre_handoff_non_pair_target"] += 1
        else:
            previous = native_ids.setdefault(entity, row["actor_id"])
            if previous != row["actor_id"]:
                raise ValueError(f"native actor identity changed within episode: {directory}")
            traces[entity].append(row)
    finals = [row["arena"] for row in rows(directory/"arena-events.jsonl")
              if row.get("kind") in ("terminal", "truncated", "infrastructure")
              and identity(row.get("arena", {}), initial)]
    final = finals[-1] if finals else episode.get("final", {})
    final_actor = {a["entity_id"]: a for a in final.get("actors", [])}
    terminal_restored = {entity for entity, event in restored.items() if event
                         and event.get("release_reason") == "actor_down"
                         and game(event) == final.get("game_ms")
                         and final_actor.get(entity, {}).get("hp", 1) <= 0}
    provenance = {name: digest(directory/name) for name in
                  ("episode.json", "events.jsonl", "body-control-events.jsonl", "arena-events.jsonl", "observations.jsonl")}
    summary = {
        "name": episode["name"], "directory": str(directory.resolve()),
        "case": episode["case"], "policy": episode["policy"], "mode": episode["mode"],
        "checkpoint_sha256": episode.get("checkpoint_sha256"),
        "normalizer_sha256": episode.get("normalizer_sha256"),
        "source_sha256": provenance, "diagnostic_rows_total": total,
        "excluded_rows": dict(excluded), "paired_pre_handoff_rows": sum(map(len, traces.values())),
        "outcome": final.get("outcome"), "reason": final.get("reason"),
        "elapsed_game_ms": final.get("elapsed_game_ms"),
        "final_from_native_arena_journal": bool(finals),
        "final_matches_episode": final == episode.get("final"),
        "final_hp": {str(entity): actor["hp"] for entity, actor in final_actor.items()},
        "primary_entity_id": initial["primary_entity_id"],
        "terminal_actor_down_restore_count": len(terminal_restored),
        "nonterminal_native_restore_count": sum(event is not None and entity not in terminal_restored
                                                for entity, event in restored.items()),
        "collector_fallback_events": episode.get("fallback_events"),
        "collector_native_fallback_boundary": episode.get("native_fallback_boundary"),
        "actors": {str(entity): trace_summary(trace) for entity, trace in traces.items()},
        "native_restored": {str(entity): {key: r.get(key) for key in
                             ("event_seq", "t_game_ms", "wall_monotonic_ns", "release_reason")}
                            for entity, r in restored.items() if r},
        "owned_strafes": owned_strafes(control, observations, traces, initial),
        "native_strafes": native_strafes(mechanics, traces, native_ids),
        "housekeeping": housekeeping(mechanics, control, traces, native_ids),
    }
    return summary


def collection(root):
    paths = [root/"episode.json"] if (root/"episode.json").is_file() else sorted(root.rglob("episode.json"))
    episodes = [analyze(path.parent) for path in paths]
    result = {}
    for episode in episodes:
        if episode["name"] in result:
            raise ValueError(f"duplicate comparison episode name: {episode['name']}")
        result[episode["name"]] = episode
    if not result:
        raise ValueError(f"no diagnostic episodes: {root}")
    return result


def compare(before, after):
    comparisons = []
    for name in sorted(before.keys() & after.keys()):
        a, b = before[name], after[name]
        same_case = a["case"] == b["case"] and a["mode"] == b["mode"] and a["policy"] == b["policy"]
        same_checkpoint = a["checkpoint_sha256"] == b["checkpoint_sha256"] and a["normalizer_sha256"] == b["normalizer_sha256"]
        comparisons.append({"name": name, "same_case_and_mode": same_case,
                            "same_checkpoint_and_normalizer": same_checkpoint,
                            "comparable": same_case and same_checkpoint,
                            "before_outcome": a["outcome"], "after_outcome": b["outcome"],
                            "before_native_restore_count": len(a["native_restored"]),
                            "after_native_restore_count": len(b["native_restored"]),
                            "before_nonterminal_native_restore_count": a["nonterminal_native_restore_count"],
                            "after_nonterminal_native_restore_count": b["nonterminal_native_restore_count"],
                            "before_final_hp": a["final_hp"], "after_final_hp": b["final_hp"],
                            "before_owned_strafe_completions": sum(r["finished_phase"] == "completed" for r in a["owned_strafes"]),
                            "after_owned_strafe_completions": sum(r["finished_phase"] == "completed" for r in b["owned_strafes"]),
                            "before_pre_handoff_lost_samples": sum(r["actual_lost_samples"] for r in a["actors"].values()),
                            "after_pre_handoff_lost_samples": sum(r["actual_lost_samples"] for r in b["actors"].values()),
                            "before_primary_pre_handoff_lost_samples": a["actors"].get(str(a["primary_entity_id"]), {}).get("actual_lost_samples"),
                            "after_primary_pre_handoff_lost_samples": b["actors"].get(str(b["primary_entity_id"]), {}).get("actual_lost_samples")})
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    before = collection(args.before)
    after = collection(args.after) if args.after else {}
    report = {"schema_version": 1, "purpose": "raw causal analysis; no model quality admission",
              "scope": "same-epoch initial actor pair, strictly before each actor's first native_restored",
              "limits": ["sampled angular arcs/displacement are diagnostic endpoints, not motion integrals",
                         "different native/scripted primitive schedules are described, not called paired equivalents",
                         "excluded raw rows remain unchanged in source journals"],
              "before": before, "after": after, "comparisons": compare(before, after),
              "before_only": sorted(before.keys()-after.keys()), "after_only": sorted(after.keys()-before.keys())}
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
