#!/usr/bin/env python3
"""Compare ordered melee mechanics, never just aggregates, across rendered/headless runs."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import body_baseline_report as baseline

DEFAULT_PARITY_CONFIG = baseline.ROOT / "evals/npc-body/headless-parity-v1.json"
SEMANTIC_FIELDS = {
    "native_choice": ("choice_id", "fai_instruction", "primitive", "queue"),
    "native_consume": ("choice_id", "primitive", "next_primitive"),
    "native_gate": ("reason", "choice_id", "primitive"),
    "native_queue_clear": ("reason", "primitive_before"),
    "native_attempt": ("hook", "choice_id", "primitive", "requested_anim", "accepted", "pose_changed"),
    "hit_attempt": ("result", "animation"),
    "defence_result": ("damage_kind", "blocked", "jumped_back", "animation"),
    "damage": ("damage_kind", "hp_before", "hp_after", "net", "has_hit", "down", "dead"),
    "termination": ("reason", "hp", "down", "dead"),
}
LAYER_FIELDS = ("name", "combination", "body_state", "combo_length", "speed", "fps", "duration_ms",
                "def_window_ms", "def_par_ms", "hit_end_ms", "optimal_frames")
SNAPSHOT_STATE = ("animation", "hp", "body_state", "weapon_state", "down", "dead", "primitive", "choice_id")


def episodes(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Input completeness is validated by baseline.summarize before this extraction."""
    result, setups, active = {}, {}, None
    for row in events:
        if row["phase"] == "actor_setup":
            setups[row["actor_id"]] = row
        elif row["phase"] == "episode_start":
            roles = {row["actor_id"]: "actor", row["target_id"]: "target"}
            active = {"start": row, "end": None, "roles": roles,
                      "setup": {role: setups.get(actor) for actor, role in roles.items()}, "rows": []}
            result[row["seed"]] = active
        elif active is not None:
            if row["phase"] == "episode_end":
                active["end"] = row
                active = None
            elif row["actor_id"] in active["roles"]:
                active["rows"].append(row)
    return result


def rel_time(row: dict[str, Any], episode: dict[str, Any]) -> int:
    return row["t_game"] - episode["start"]["t_game"]


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def layer_signature(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{field: layer.get(field) for field in LAYER_FIELDS} for layer in layers]


def values_equal(left: Any, right: Any, tolerance: float) -> bool:
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if finite_number(left) and finite_number(right):
        return abs(left-right) <= tolerance
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(values_equal(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(values_equal(left[k], right[k], tolerance) for k in left)
    return type(left) is type(right) and left == right


def trace_evidence(episode: dict[str, Any], config: dict[str, Any], profile: str,
                   label: str) -> list[str]:
    issues = []
    for role, setup in episode["setup"].items():
        for key in ("weapon_damage", "weapon_length", "fight_tactic", "x", "y", "z"):
            if key not in setup:
                issues.append(f"{label} {role}: missing actor_setup {key}")
    for row in episode["rows"]:
        phase = row["phase"]
        fields = SEMANTIC_FIELDS.get(phase, ())
        if phase == "phase_snapshot":
            fields = (*SNAPSHOT_STATE, "x", "y", "z", "yaw", "layers")
        if phase == "physics_step" and config["profiles"][profile]["requires_physics_step"]:
            fields = (*config["physics_step"]["required_fields"], "sample_phase")
        missing = [field for field in fields if field not in row]
        if missing:
            issues.append(f"{label} seq {row['seq']} {phase}: missing {', '.join(missing)}")
        for field in ("x", "y", "z", "yaw"):
            if field in fields and not finite_number(row.get(field)):
                issues.append(f"{label} seq {row['seq']}: {field} is not a finite number")
        if phase == "native_choice" and (not isinstance(row.get("queue"), list) or len(row["queue"]) != 6):
            issues.append(f"{label} seq {row['seq']}: native queue is not six slots")
        for field in ("layers", "layers_after"):
            if phase == "native_attempt" and field == "layers" or phase == "phase_snapshot" and field == "layers_after":
                continue
            if phase in {"native_attempt", "phase_snapshot"}:
                layers = row.get(field)
                if not isinstance(layers, list):
                    issues.append(f"{label} seq {row['seq']}: missing {field}")
                elif any(not isinstance(layer, dict) or any(key not in layer for key in (*LAYER_FIELDS, "start", "clock")) for layer in layers):
                    issues.append(f"{label} seq {row['seq']}: incomplete animation metadata")
        if phase == "physics_step" and config["profiles"][profile]["requires_physics_step"]:
            if row.get("sample_phase") != config["physics_step"]["sample_phase"]:
                issues.append(f"{label} seq {row['seq']}: wrong physics sample phase")
            if row.get("dt_ms") not in (33, 34) or not all(isinstance(row.get(k), bool) for k in config["physics_step"]["boolean_fields"]):
                issues.append(f"{label} seq {row['seq']}: incomplete physics timestep/flags")
            for key in config["physics_step"]["required_fields"]:
                if key not in (*config["physics_step"]["boolean_fields"], "collision_scope", "skip_reason") and not finite_number(row.get(key)):
                    issues.append(f"{label} seq {row['seq']}: invalid physics {key}")
            if row.get("movealgo_tick") is True:
                if row.get("collision_scope") != "movealgo_trymove" or row.get("skip_reason") != "":
                    issues.append(f"{label} seq {row['seq']}: wrong collision evidence scope")
            elif row.get("movealgo_tick") is False:
                zeros = ("root_dx", "root_dy", "root_dz", "requested_dx", "requested_dy", "requested_dz",
                         "collision_attempts", "collision_failed_count")
                flags = ("collision_land", "collision_npc", "collision_vob", "collision_prefall")
                if (row.get("collision_scope") != "none" or row.get("skip_reason") != "npc_tick_without_movealgo" or
                        any(row.get(k) != 0 for k in zeros) or any(row.get(k) is not False for k in flags)):
                    issues.append(f"{label} seq {row['seq']}: invalid skipped movement evidence")
    for role in ("actor", "target"):
        rows = [row for row in episode["rows"] if episode["roles"][row["actor_id"]] == role]
        if config["profiles"][profile]["requires_physics_step"]:
            physics = [row for row in rows if row["phase"] == "physics_step"]
            if not physics:
                issues.append(f"{label} {role}: missing per-tick root-motion/collision evidence")
            else:
                # Every fixed step between the first and last measurement must exist.
                steps = [row["step"] for row in physics]
                if any(b != a+1 for a, b in zip(steps, steps[1:])):
                    issues.append(f"{label} {role}: missing/duplicate physics steps")
                if physics[0]["step"] > episode["start"]["step"]+1 or physics[-1]["step"] < episode["end"]["step"]-1:
                    issues.append(f"{label} {role}: physics evidence does not cover episode boundaries")
    return issues


def compare_episode(reference: dict[str, Any], candidate: dict[str, Any],
                    config: dict[str, Any], melee_config: dict[str, Any], profile: str) -> dict[str, Any]:
    seed = reference["start"]["seed"]
    differences, evidence_gaps, checks = [], [], Counter()
    tolerance = config["tolerances"]
    for label, episode in (("reference", reference), ("candidate", candidate)):
        evidence_gaps += trace_evidence(episode, config, profile, label)
    if evidence_gaps:
        return {"seed": seed, "status": "incomplete", "checks": {}, "difference_count": 0,
                "first_differences": [], "evidence_gaps": evidence_gaps,
                "unexercised_guards": [], "reference_duration_ms": rel_time(reference["end"], reference),
                "candidate_duration_ms": rel_time(candidate["end"], candidate)}
    def difference(area: str, detail: str, left: Any = None, right: Any = None,
                   ref_row: dict[str, Any] | None = None, cand_row: dict[str, Any] | None = None) -> None:
        differences.append({"area": area, "detail": detail, "reference": left, "candidate": right,
                            "reference_seq": ref_row.get("seq") if ref_row else None,
                            "candidate_seq": cand_row.get("seq") if cand_row else None})
    def timed(area: str, left: dict[str, Any], right: dict[str, Any]) -> None:
        a, b = rel_time(left, reference), rel_time(right, candidate)
        checks["timing"] += 1
        if abs(a-b) > tolerance["event_time_ms"]:
            difference(area, "relative event time exceeds fixed-step tolerance", a, b, left, right)
    def layer_times(left: dict[str, Any], right: dict[str, Any], field: str, role: str) -> None:
        for a, b in zip(left.get(field, []), right.get(field, [])):
            for key in ("start", "clock"):
                if not finite_number(a.get(key)) or not finite_number(b.get(key)):
                    evidence_gaps.append(f"{role}: missing finite animation {key}")
                    continue
                x, y = a[key]-reference["start"]["t_game"], b[key]-candidate["start"]["t_game"]
                if abs(x-y) > tolerance["event_time_ms"]:
                    difference("animation_clock", f"{role}.{a.get('name')}.{key}", x, y, left, right)
    global_a = [row for row in reference["rows"] if row["phase"] in SEMANTIC_FIELDS]
    global_b = [row for row in candidate["rows"] if row["phase"] in SEMANTIC_FIELDS]
    for index, (left, right) in enumerate(zip(global_a, global_b)):
        signature_a = (left["phase"], reference["roles"].get(left["actor_id"]), reference["roles"].get(left["target_id"], "none"))
        signature_b = (right["phase"], candidate["roles"].get(right["actor_id"]), candidate["roles"].get(right["target_id"], "none"))
        checks["global_event_order"] += 1
        if signature_a != signature_b:
            difference("global_event_order", f"event {index}: phase/participant order differs", signature_a, signature_b, left, right)
    for role in ("actor", "target"):
        a_setup, b_setup = reference["setup"][role], candidate["setup"][role]
        for key in (*melee_config["setup"], "guild", "actor", "weapon_damage", "weapon_length", "fight_tactic"):
            if a_setup.get(key) != b_setup.get(key):
                difference("setup", f"{role}.{key}", a_setup.get(key), b_setup.get(key), a_setup, b_setup)
        for key in ("x", "y", "z"):
            if not finite_number(a_setup.get(key)) or not finite_number(b_setup.get(key)):
                evidence_gaps.append(f"{role}: missing setup position {key}")
            elif abs(a_setup[key]-b_setup[key]) > tolerance["position_cm"]:
                difference("setup", f"{role}.{key} exceeds position tolerance", a_setup[key], b_setup[key])
        a_rows = [row for row in reference["rows"] if reference["roles"][row["actor_id"]] == role]
        b_rows = [row for row in candidate["rows"] if candidate["roles"][row["actor_id"]] == role]
        # Preserve order across semantic phases. Comparing phase histograms could hide reordered attacks.
        a_sem = [row for row in a_rows if row["phase"] in SEMANTIC_FIELDS]
        b_sem = [row for row in b_rows if row["phase"] in SEMANTIC_FIELDS]
        if len(a_sem) != len(b_sem):
            difference("ordered_mechanics", f"{role}: event count differs", len(a_sem), len(b_sem))
        for index, (left, right) in enumerate(zip(a_sem, b_sem)):
            checks["ordered_mechanics"] += 1
            phase = left["phase"]
            if phase != right["phase"]:
                difference("ordered_mechanics", f"{role} event {index}: phase differs", phase, right["phase"], left, right)
                continue
            timed(phase, left, right)
            for key in SEMANTIC_FIELDS[phase]:
                if key in left and key in right and left[key] != right[key]:
                    difference(phase, f"{role} event {index}: {key}", left[key], right[key], left, right)
            if phase == "native_attempt" and isinstance(left.get("layers_after"), list) and isinstance(right.get("layers_after"), list):
                layer_times(left, right, "layers_after", role)
                if not values_equal(layer_signature(left["layers_after"]), layer_signature(right["layers_after"]), tolerance["animation_scalar"]):
                    difference("animation_metadata", f"{role} attempt {index}: layer layout differs",
                               layer_signature(left["layers_after"]), layer_signature(right["layers_after"]), left, right)
        a_samples = [row for row in a_rows if row["phase"] == "phase_snapshot"]
        b_samples = [row for row in b_rows if row["phase"] == "phase_snapshot"]
        for field in melee_config["guard_fields"]:
            def transitions(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [row for i, row in enumerate(samples) if i == 0 or row[field] != samples[i-1][field]]
            a_edges, b_edges = transitions(a_samples), transitions(b_samples)
            if len(a_edges) != len(b_edges):
                difference("guard", f"{role}.{field}: transition count differs", len(a_edges), len(b_edges))
            for left, right in zip(a_edges, b_edges):
                checks["guard_transitions"] += 1
                if left[field] != right[field]:
                    difference("guard", f"{role}.{field}: value differs", left[field], right[field], left, right)
                timed(f"guard.{field}", left, right)
            a_window = baseline.guard_windows(a_samples, field, reference["end"]["t_game"])
            b_window = baseline.guard_windows(b_samples, field, candidate["end"]["t_game"])
            for key in ("left_censored", "right_censored"):
                if a_window[key] != b_window[key]:
                    difference("guard_censoring", f"{role}.{field}.{key}", a_window[key], b_window[key])
        # Match every sample in both directions: no arbitrary subsampling hides a trajectory excursion.
        # Samples may use different absolute 50ms buckets; nearest relative time must be within one step.
        max_spatial, max_yaw, max_dt = 0.0, 0.0, 0
        for source, dest, source_episode, dest_episode, reverse in (
            (a_samples, b_samples, reference, candidate, False),
            (b_samples, a_samples, candidate, reference, True)):
            for row in source:
                if not dest:
                    evidence_gaps.append(f"{role}: missing trajectory samples")
                    break
                match = min(dest, key=lambda item: abs(rel_time(row, source_episode)-rel_time(item, dest_episode)))
                dt = abs(rel_time(row, source_episode)-rel_time(match, dest_episode))
                max_dt = max(max_dt, dt)
                checks["trajectory_samples"] += 1
                left, right = (match, row) if reverse else (row, match)
                if dt > tolerance["event_time_ms"]:
                    difference("trajectory", f"{role}: sample has no nearby counterpart", rel_time(left, reference), rel_time(right, candidate), left, right)
                    continue
                if not all(finite_number(item.get(k)) for item in (left, right) for k in ("x", "y", "z", "yaw")):
                    continue
                distance = math.sqrt(sum((left[k]-right[k])**2 for k in ("x", "y", "z")))
                yaw = abs((left["yaw"]-right["yaw"]+180) % 360-180)
                max_spatial, max_yaw = max(max_spatial, distance), max(max_yaw, yaw)
                if distance > tolerance["position_cm"]:
                    difference("trajectory", f"{role}: 3D position exceeds tolerance", [left[k] for k in ("x", "y", "z")], [right[k] for k in ("x", "y", "z")], left, right)
                if yaw > tolerance["yaw_deg"]:
                    difference("trajectory", f"{role}: yaw exceeds tolerance", left["yaw"], right["yaw"], left, right)
                for key in SNAPSHOT_STATE:
                    if left.get(key) != right.get(key):
                        difference("snapshot_state", f"{role}.{key} differs at matched trajectory sample", left.get(key), right.get(key), left, right)
                if not values_equal(layer_signature(left.get("layers", [])), layer_signature(right.get("layers", [])), tolerance["animation_scalar"]):
                    difference("animation_metadata", f"{role}: snapshot layer layout differs", layer_signature(left.get("layers", [])), layer_signature(right.get("layers", [])), left, right)
                layer_times(left, right, "layers", role)
        checks[f"{role}_max_position_cm"] = max_spatial
        checks[f"{role}_max_yaw_deg"] = max_yaw
        checks[f"{role}_max_sample_offset_ms"] = max_dt
        if config["profiles"][profile]["requires_physics_step"]:
            a_physics = [row for row in a_rows if row["phase"] == "physics_step"]
            b_physics = [row for row in b_rows if row["phase"] == "physics_step"]
            if len(a_physics) != len(b_physics):
                difference("physics", f"{role}: per-tick event count differs", len(a_physics), len(b_physics))
            for left, right in zip(a_physics, b_physics):
                checks["physics_steps"] += 1
                timed("physics", left, right)
                for prefix in ("root_", "requested_", "moved_"):
                    keys = [prefix+axis for axis in ("dx", "dy", "dz")]
                    distance = math.sqrt(sum((left[k]-right[k])**2 for k in keys))
                    if distance > tolerance["position_cm"]:
                        difference("physics", f"{role}.{prefix}3D delta exceeds tolerance", [left[k] for k in keys], [right[k] for k in keys], left, right)
                for key in config["physics_step"]["required_fields"]:
                    if key not in left or key not in right:
                        continue
                    limit = tolerance["yaw_deg"] if key == "yaw" else tolerance["position_cm"]
                    if key == "yaw":
                        equal = abs((left[key]-right[key]+180) % 360-180) <= limit
                    else:
                        equal = left[key] == right[key] if key in config["physics_step"]["exact_fields"] else values_equal(left[key], right[key], limit)
                    if not equal:
                        difference("physics", f"{role}.{key}", left[key], right[key], left, right)
    timed("terminal", reference["end"], candidate["end"])
    for key in ("outcome", "actor_hp", "target_hp", "actor_dead", "target_dead"):
        if key not in reference["end"] or key not in candidate["end"]:
            evidence_gaps.append(f"terminal: missing {key}")
        elif reference["end"][key] != candidate["end"][key]:
            difference("terminal", key, reference["end"][key], candidate["end"][key])
    return {"seed": seed, "status": "incomplete" if evidence_gaps else "diverged" if differences else "matched",
            "checks": dict(checks), "difference_count": len(differences), "first_differences": differences[:25],
            "evidence_gaps": evidence_gaps,
            "unexercised_guards": [field for field in melee_config["guard_fields"]
                                   if not any(row.get(field) is True for row in reference["rows"] if row["phase"] == "phase_snapshot")],
            "reference_duration_ms": rel_time(reference["end"], reference),
            "candidate_duration_ms": rel_time(candidate["end"], candidate)}


def compare(reference_events: list[dict[str, Any]], candidate_events: list[dict[str, Any]],
            reference_manifest: dict[str, Any] | None, candidate_manifest: dict[str, Any] | None,
            melee_config: dict[str, Any], parity_config: dict[str, Any], profile: str = "archived_reference",
            reference_issues: list[str] | None = None, candidate_issues: list[str] | None = None) -> dict[str, Any]:
    reports = [baseline.summarize(events, melee_config, issues) for events, issues in
               ((reference_events, reference_issues), (candidate_events, candidate_issues))]
    issues = []
    for label, report in zip(("reference", "candidate"), reports):
        issues += [f"{label}: {issue}" for issue in report["issues"]+report["evidence_gaps"]]
    provenance_differences = []
    if reference_manifest is None or candidate_manifest is None:
        issues.append("missing measured reference/candidate manifest")
    else:
        for key in parity_config["provenance_equal"]:
            if key not in reference_manifest or key not in candidate_manifest:
                issues.append(f"missing provenance {key}")
            elif reference_manifest[key] != candidate_manifest[key]:
                provenance_differences.append(f"reference/candidate provenance differs: {key}")
        for key, expected in parity_config["headless_manifest"].items():
            if key not in candidate_manifest:
                issues.append(f"candidate: missing measured headless evidence {key}")
            elif candidate_manifest[key] != expected:
                provenance_differences.append(f"candidate: {key}={candidate_manifest[key]!r}, expected {expected!r}")
    episode_reports = []
    if all(report["status"] == "complete" for report in reports):
        ref_episodes, cand_episodes = episodes(reference_events), episodes(candidate_events)
        for seed in melee_config["seeds"]:
            episode_reports.append(compare_episode(ref_episodes[seed], cand_episodes[seed], parity_config, melee_config, profile))
    incomplete = bool(issues) or any(ep["status"] == "incomplete" for ep in episode_reports)
    diverged = bool(provenance_differences) or any(ep["status"] == "diverged" for ep in episode_reports)
    comparison_kind = "unknown"
    if reference_manifest is not None:
        if reference_manifest.get("headless") is False:
            comparison_kind = "rendered_vs_headless"
        elif reference_manifest.get("headless") is True:
            comparison_kind = "headless_schedule_invariance"
    return {"schema_version": 1, "scenario": melee_config["scenario"], "profile": profile,
            "status": "incomplete" if incomplete else "diverged" if diverged else "matched",
            "comparison_kind": comparison_kind,
            "full_stage2_evidence": profile == "full" and comparison_kind == "rendered_vs_headless" and not incomplete and not diverged,
            "scope": parity_config["profiles"][profile]["scope"], "tolerances": parity_config["tolerances"],
            "issues": issues, "provenance_differences": provenance_differences, "episodes": episode_reports,
            "interpretation": parity_config["interpretation"],
            "reference_metadata": reference_manifest, "candidate_metadata": candidate_manifest}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Headless mechanics parity", "", f"Status: **{report['status']}**; profile `{report['profile']}`, comparison `{report['comparison_kind']}`.", "",
             report["scope"], "", "| Seed | Status | Reference / headless duration, ms | Ordered checks | Differences |",
             "|---|---|---:|---:|---:|"]
    for ep in report["episodes"]:
        lines.append(f"| {ep['seed']} | {ep['status']} | {ep['reference_duration_ms']} / {ep['candidate_duration_ms']} | {ep['checks'].get('ordered_mechanics', 0)} | {ep['difference_count']} |")
    lines += ["", "Predeclared tolerances: " + ", ".join(f"`{k}` = {v}" for k, v in report["tolerances"].items()) + ".", ""]
    for issue in report["issues"]+report["provenance_differences"]:
        lines.append(f"- {issue}")
    for ep in report["episodes"]:
        for issue in ep["evidence_gaps"]:
            lines.append(f"- Seed {ep['seed']}: {issue}")
        for diff in ep["first_differences"][:5]:
            lines.append(f"- Seed {ep['seed']}, `{diff['area']}`, seq {diff['reference_seq']} / {diff['candidate_seq']}: {diff['detail']}; `{diff['reference']}` → `{diff['candidate']}`.")
        if ep["unexercised_guards"]:
            lines.append(f"- Seed {ep['seed']}: unexercised reference guards: {', '.join(ep['unexercised_guards'])}.")
    lines += ["", "The JSON preserves the first 25 differences per seed and the total count. Raw traces remain the source of evidence.", ""]
    if report["comparison_kind"] == "headless_schedule_invariance":
        lines += ["This comparison checks headless wall-time invariance. It does not establish rendered/headless collision parity or alone close stage 2.", ""]
    lines += [f"- {item}" for item in report["interpretation"]]
    return "\n".join(lines)+"\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_PARITY_CONFIG)
    parser.add_argument("--melee-config", type=Path, default=baseline.DEFAULT_CONFIG)
    parser.add_argument("--profile", choices=("archived_reference", "full"), default="archived_reference")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    parity_config, melee_config = json.loads(args.config.read_text()), json.loads(args.melee_config.read_text())
    ref_events, ref_issues = baseline.read_events(args.reference / "events.jsonl")
    cand_events, cand_issues = baseline.read_events(args.candidate / "events.jsonl")
    def manifest(directory: Path, issues: list[str]) -> dict[str, Any] | None:
        try:
            value = json.loads((directory / "manifest.json").read_text())
            if not isinstance(value, dict):
                raise ValueError("manifest is not an object")
            return value
        except (OSError, ValueError) as exc:
            issues.append(f"manifest: {exc}")
            return None
    report = compare(ref_events, cand_events, manifest(args.reference, ref_issues), manifest(args.candidate, cand_issues),
                     melee_config, parity_config, args.profile, ref_issues, cand_issues)
    report["config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    report["inputs"] = {"reference": str(args.reference), "candidate": str(args.candidate)}
    report["trace_sha256"] = {label: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                              for label, path in (("reference", args.reference / "events.jsonl"), ("candidate", args.candidate / "events.jsonl"))}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "headless-parity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir / "headless-parity.md").write_text(markdown(report))
    print(f"headless parity {report['status']} ({args.profile}): {args.output_dir / 'headless-parity.md'}")
    return 0 if report["status"] == "matched" else 2


if __name__ == "__main__":
    raise SystemExit(main())
