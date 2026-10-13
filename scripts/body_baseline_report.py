#!/usr/bin/env python3
"""Report measured native melee evidence from TestBench body_combat schema v1.

No external packages or game/model process are used. Windows are observed engine guard intervals,
not guessed attack phases; missing/censored evidence stays explicit.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "evals/npc-body/melee-v1.json"
BODY_PHASES = {"native_choice", "native_consume", "native_gate", "native_attempt", "phase_snapshot",
               "damage", "defence_result", "hit_attempt"}


def read_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events, issues = [], []
    try:
        trace_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], [f"missing trace file {path.name}: game did not reach the bench or collection is incomplete; infrastructure failure, not duel timeout"]
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = value
        return result
    for line_no, line in enumerate(trace_text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_object)
            if not isinstance(row, dict):
                raise ValueError("event is not an object")
        except (ValueError, TypeError) as exc:
            issues.append(f"line {line_no}: {exc}")
            continue
        if row.get("kind") == "body_duel_error":
            issues.append(f"engine setup failed: {row.get('reason', 'unknown')}")
        if row.get("kind") != "body_combat":
            continue
        required = {"schema_version", "seq", "t_game", "step", "phase", "actor_id", "actor",
                    "target_id", "target"}
        if required - row.keys() or row.get("schema_version") != 1:
            issues.append(f"line {line_no}: unsupported/incomplete body_combat envelope")
            continue
        if any(not isinstance(row[key], int) or isinstance(row[key], bool)
               for key in ("seq", "t_game", "step", "actor_id", "target_id")):
            issues.append(f"line {line_no}: non-integer trace IDs/time/step")
            continue
        if events and row["seq"] <= events[-1]["seq"]:
            issues.append(f"line {line_no}: trace sequence is not increasing")
        if events and (row["t_game"] < events[-1]["t_game"] or row["step"] < events[-1]["step"]):
            issues.append(f"line {line_no}: game time/step moved backwards")
        events.append(row)
    return events, issues


def distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {"n": len(values), "min": ordered[0], "median": statistics.median(ordered),
            "p95": ordered[min(len(ordered)-1, int((len(ordered)-1)*.95+.5))], "max": ordered[-1]}


def guard_windows(samples: list[dict[str, Any]], field: str, end_time: int) -> dict[str, Any]:
    intervals, opened, censored, present, left_censored, opening_censored = [], None, 0, 0, 0, False
    for sample in samples:
        if field not in sample:
            continue
        present += 1
        if sample[field] is True and opened is None:
            opened = sample["t_game"]
            opening_censored = present == 1
            left_censored += int(opening_censored)
        elif sample[field] is False and opened is not None:
            if not opening_censored:
                intervals.append(sample["t_game"]-opened)
            opened = None
    if opened is not None:
        censored = 1  # do not include a terminal-clipped window in duration quantiles
    return {"observed_samples": present, "complete_duration_ms": distribution(intervals),
            "left_censored": left_censored, "right_censored": censored,
            "censored_lower_bound_ms": end_time-opened if opened is not None else None}


def actor_report(actor_id: int, rows: list[dict[str, Any]], start_time: int, end_time: int,
                 config: dict[str, Any]) -> dict[str, Any]:
    own = [row for row in rows if row["actor_id"] == actor_id]
    samples = [row for row in own if row["phase"] == "phase_snapshot"]
    phases = Counter(row["phase"] for row in own)
    names = config.get("primitive_names", {})
    consumed = Counter(names.get(str(row.get("primitive")), str(row.get("primitive")))
                       for row in own if row["phase"] == "native_consume")
    damage = [row for row in own if row["phase"] == "damage"]  # actor is the victim, target the attacker
    measured_damage = [row for row in damage if all(key in row for key in ("net", "hp_before", "hp_after"))]
    attempts = [row for row in own if row["phase"] == "native_attempt"]
    choice_rows = [row for row in own if row["phase"] == "native_choice"]
    consume_rows = [row for row in own if row["phase"] == "native_consume"]
    choice_times = {row["choice_id"]: row["t_game"] for row in choice_rows if "choice_id" in row}
    def first_delays(phase_rows: list[dict[str, Any]]) -> list[int]:
        seen, delays = set(), []
        for row in phase_rows:
            choice_id = row.get("choice_id")
            if choice_id in choice_times and choice_id not in seen:
                seen.add(choice_id)
                delays.append(row["t_game"]-choice_times[choice_id])
        return delays
    choices = {(row["actor_id"], row["choice_id"]) for row in own
               if row["phase"] == "native_choice" and "choice_id" in row}
    consumed_choices = {(row["actor_id"], row["choice_id"]) for row in own
                        if row["phase"] == "native_consume" and "choice_id" in row}
    gaps = [b["t_game"]-a["t_game"] for a, b in zip(samples, samples[1:])]
    defence = [row for row in own if row["phase"] == "defence_result"]
    animation_layouts = {}
    for sample in samples:
        layers = sample.get("layers", [])
        if not isinstance(layers, list):
            continue  # summarize records malformed evidence; aggregation must still finish
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            name = layer.get("name")
            if isinstance(name, str):
                animation_layouts[name] = {key: layer.get(key) for key in
                                          ("combo_length", "fps", "duration_ms", "def_window_ms",
                                           "def_par_ms", "hit_end_ms", "optimal_frames")}
    return {"actor_id": actor_id, "actor": own[0]["actor"] if own else None,
            "event_counts": dict(sorted(phases.items())), "consumed_primitives": dict(sorted(consumed.items())),
            "unique_choices": len(choices), "choices_with_consume": len(choices & consumed_choices),
            "attempts_accepted": sum(row.get("accepted") is True for row in attempts),
            "attempts_rejected": sum(row.get("accepted") is False for row in attempts),
            "attempts_pose_changed": sum(row.get("pose_changed") is True for row in attempts),
            "choice_interval_ms": distribution([b["t_game"]-a["t_game"] for a, b in zip(choice_rows, choice_rows[1:])]),
            "consume_interval_ms": distribution([b["t_game"]-a["t_game"] for a, b in zip(consume_rows, consume_rows[1:])]),
            "attempt_interval_ms": distribution([b["t_game"]-a["t_game"] for a, b in zip(attempts, attempts[1:])]),
            "choice_to_first_attempt_ms": distribution(first_delays(attempts)),
            "choice_to_first_consume_ms": distribution(first_delays(consume_rows)),
            "snapshot_interval_ms": distribution(gaps),
            "snapshot_start_gap_ms": samples[0]["t_game"]-start_time if samples else None,
            "snapshot_end_gap_ms": end_time-samples[-1]["t_game"] if samples else None,
            "invalid_guard_snapshots": sum(any(not isinstance(sample.get(field), bool)
                                               for field in config["guard_fields"]) for sample in samples),
            "sample_phases": sorted({row.get("sample_phase", "missing") for row in samples}),
            "animation_layouts": animation_layouts,
            "windows": {field: guard_windows(samples, field, end_time) for field in config["guard_fields"]},
            "damage_received": {"events": len(damage), "fully_measured_events": len(measured_damage),
                                "net_sum": sum(row["net"] for row in measured_damage),
                                "hp_loss_sum": sum(row["hp_before"]-row["hp_after"] for row in measured_damage)},
            "defence_results": {"events": len(defence), "blocked": sum(row.get("blocked") is True for row in defence),
                                "jumped_back": sum(row.get("jumped_back") is True for row in defence)}}


def summarize(events: list[dict[str, Any]], config: dict[str, Any],
              input_issues: list[str] | None = None) -> dict[str, Any]:
    issues = list(input_issues or [])
    setups, episodes, active = {}, [], None
    for row in events:
        phase = row["phase"]
        for field in ("layers", "layers_after"):
            if field not in row:
                continue
            layers = row[field]
            if not isinstance(layers, list) or any(not isinstance(layer, dict) or
                                                   (layer.get("name") is not None and not isinstance(layer["name"], str))
                                                   for layer in layers):
                issues.append(f"seq {row['seq']} {phase}: invalid {field} animation metadata container")
        if phase == "actor_setup":
            setups[row["actor_id"]] = row
        elif phase == "episode_start":
            if active is not None:
                issues.append(f"seed {active['start'].get('seed')}: next episode before terminal")
                episodes.append(active)
            active = {"start": row, "rows": [], "end": None,
                      "setups": [setups.get(row["actor_id"]), setups.get(row["target_id"])]}
        elif phase == "episode_end":
            if active is None:
                issues.append("episode_end without episode_start")
            else:
                active["end"] = row
                episodes.append(active)
                active = None
        elif active is not None:
            active["rows"].append(row)
    if active is not None:
        episodes.append(active)
    reports, previous_participants = [], set()
    for episode in episodes:
        start, end, rows = episode["start"], episode["end"], episode["rows"]
        seed, actor_ids = start.get("seed"), {start["actor_id"], start["target_id"]}
        problems = []
        if start.get("source") != "native" or start.get("scenario") != config["scenario"]:
            problems.append("episode does not identify the expected native scenario")
        if start.get("timeout_ms") != config["timeout_ms"]:
            problems.append("episode timeout differs from versioned setup")
        if len(actor_ids) != 2 or 0 in actor_ids:
            problems.append("participants do not have two distinct lifetime IDs")
        if previous_participants & actor_ids:
            problems.append("participant lifetime reused across fresh-spawn baseline episodes")
        previous_participants.update(actor_ids)
        if end is None:
            problems.append("missing terminal event; infrastructure interruption is not a duel timeout")
        elif end.get("outcome") not in config["outcomes"]:
            problems.append("unknown terminal outcome")
        elif (end["actor_id"], end["target_id"]) != (start["actor_id"], start["target_id"]):
            problems.append("terminal participants differ from episode start")
        for side, setup in enumerate(episode["setups"]):
            if setup is None:
                problems.append("missing actor_setup")
                continue
            for key, expected in config["setup"].items():
                if setup.get(key) != expected:
                    problems.append(f"actor {setup['actor_id']}: setup {key}={setup.get(key)!r}, expected {expected!r}")
            if setup.get("seed") != seed:
                problems.append(f"actor {setup['actor_id']}: setup seed differs")
            if setup.get("actor") != config["actors"][side] or setup.get("guild") != config["guilds"][side]:
                problems.append(f"actor {setup['actor_id']}: source actor/guild differs")
        foreign = [row for row in rows if row["phase"] in BODY_PHASES and
                   (row["actor_id"] in actor_ids or row["target_id"] in actor_ids) and
                   (row["actor_id"] not in actor_ids or row["target_id"] not in actor_ids | {0})]
        if foreign:
            problems.append(f"{len(foreign)} combat events involve a third actor/target")
        non_melee = [row for row in rows if row["phase"] in {"damage", "defence_result"} and
                     (row["actor_id"] in actor_ids or row["target_id"] in actor_ids) and
                     row.get("damage_kind") != "melee"]
        for row in non_melee:
            problems.append(f"{row['phase']} seq {row['seq']}: damage_kind={row.get('damage_kind')!r}, expected 'melee'")
        stop_time = end["t_game"] if end else max([start["t_game"]]+[row["t_game"] for row in rows])
        stop_event = end or (rows[-1] if rows else start)
        reports.append({"seed": seed, "segment": start.get("seg"), "start_game_ms": start["t_game"],
                        "duration_ms": stop_time-start["t_game"], "step_span": stop_event["step"]-start["step"],
                        "outcome": end.get("outcome") if end else None,
                        "terminated": end is not None and end.get("outcome") != "timeout",
                        "truncated": end is not None and end.get("outcome") == "timeout",
                        "terminal": end, "setups": episode["setups"], "issues": problems,
                        "actors": [actor_report(actor_id, rows, start["t_game"], stop_time, config) for actor_id in sorted(actor_ids)]})
        issues.extend(f"seed {seed}: {problem}" for problem in problems)
    got_seeds = [episode["seed"] for episode in reports]
    if got_seeds != config["seeds"]:
        issues.append(f"episode seeds {got_seeds} differ from required {config['seeds']}")
    if not reports:
        issues.append("no native episodes recorded")
    evidence_gaps = []
    for episode in reports:
        if not any(actor["damage_received"]["fully_measured_events"] for actor in episode["actors"]):
            evidence_gaps.append(f"seed {episode['seed']}: no measured combat damage")
        for actor in episode["actors"]:
            missing = [phase for phase in ("native_choice", "native_consume", "native_attempt", "phase_snapshot")
                       if not actor["event_counts"].get(phase)]
            if missing:
                evidence_gaps.append(f"seed {episode['seed']} actor {actor['actor_id']}: missing {', '.join(missing)}")
            if actor["event_counts"].get("phase_snapshot", 0) < 2 or actor["invalid_guard_snapshots"]:
                evidence_gaps.append(f"seed {episode['seed']} actor {actor['actor_id']}: insufficient complete guard snapshots")
            if actor["sample_phases"] != [config["step"]["sample_phase"]]:
                evidence_gaps.append(f"seed {episode['seed']} actor {actor['actor_id']}: wrong or missing sample phase")
            limit = config["step"]["trace_sample_max_quantized_ms"]
            observed_gaps = [actor["snapshot_interval_ms"]["max"], actor["snapshot_start_gap_ms"], actor["snapshot_end_gap_ms"]]
            if any(gap is not None and gap > limit for gap in observed_gaps):
                evidence_gaps.append(f"seed {episode['seed']} actor {actor['actor_id']}: snapshot coverage exceeds {limit} ms")
    return {"schema_version": 1, "scenario": config["scenario"], "trace_schema_version": 1,
            "status": "complete" if not issues and not evidence_gaps else "incomplete",
            "issues": issues, "evidence_gaps": evidence_gaps, "episodes": reports,
            "interpretation": ["native_consume means queue consumption; accepted/pose_changed are reported separately and do not prove a hit",
                               "native_attempt counts animation hooks (set_anim/do_attack), including weapon draw, idle and hit reactions; it is not restricted to new fight decisions",
                               "choice_to_first_attempt_ms is elapsed game time to the first animation hook sharing the current/last materialized choice_id; this association is not policy latency or proof that the choice caused the animation",
                               "animation hooks may carry primitive 0 or a previous queue choice; raw hook, primitive and pose fields remain in the trace",
                               "guard durations come from engine booleans at the recorded sample phase; timestamps are quantized by simulation steps",
                               "left/right-censored guard intervals are excluded from duration quantiles",
                               "timeout is truncation, never a victory; missing terminal is infrastructure interruption",
                               "trace lifetime IDs, not script symbols, distinguish participants and episodes"]}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Native melee baseline", "", f"Status: **{report['status']}**. Scenario `{report['scenario']}`, trace schema v1.", "",
             "| Seed | Duration, ms | Steps | Outcome |", "|---|---:|---:|---|"]
    for episode in report["episodes"]:
        lines.append(f"| {episode['seed']} | {episode['duration_ms']} | {episode['step_span']} | {episode['outcome'] or 'missing terminal'} |")
    lines += ["", "| Seed / actor ID | Instruction queues / with consume | Animation hooks accepted / pose changed | HP loss | Blocks | Snapshot p95/max, ms |",
              "|---|---:|---:|---:|---:|---:|"]
    for episode in report["episodes"]:
        for actor in episode["actors"]:
            gaps = actor["snapshot_interval_ms"]
            lines.append(f"| {episode['seed']} / {actor['actor_id']} `{actor['actor']}` | {actor['unique_choices']} / {actor['choices_with_consume']} | "
                         f"{actor['attempts_accepted']} / {actor['attempts_pose_changed']} | {actor['damage_received']['hp_loss_sum']} | "
                         f"{actor['defence_results']['blocked']} | {gaps['p95']} / {gaps['max']} |")
    lines += ["", "| Seed / actor ID | New instruction queue interval p95, ms | Consume interval p95, ms | Animation hook interval p95, ms | Choice association to first hook / consume p95, ms |",
              "|---|---:|---:|---:|---:|"]
    for episode in report["episodes"]:
        for actor in episode["actors"]:
            lines.append(f"| {episode['seed']} / {actor['actor_id']} | {actor['choice_interval_ms']['p95']} | "
                         f"{actor['consume_interval_ms']['p95']} | {actor['attempt_interval_ms']['p95']} | "
                         f"{actor['choice_to_first_attempt_ms']['p95']} / {actor['choice_to_first_consume_ms']['p95']} |")
    lines += ["", "| Seed / actor ID / engine guard | Complete intervals | Min / median / p95 / max, ms | Censored left/right |", "|---|---:|---|---:|"]
    for episode in report["episodes"]:
        for actor in episode["actors"]:
            for field, window in actor["windows"].items():
                dist = window["complete_duration_ms"]
                lines.append(f"| {episode['seed']} / {actor['actor_id']} / `{field}` | {dist['n']} | "
                             f"{dist['min']} / {dist['median']} / {dist['p95']} / {dist['max']} | {window['left_censored']}/{window['right_censored']} |")
    lines += ["", "The JSON report preserves setup, terminal fields, event counts, consumed primitives and metadata.", ""]
    if report["issues"] or report["evidence_gaps"]:
        lines += ["Missing or invalid evidence:", ""] + [f"- {issue}" for issue in report["issues"]+report["evidence_gaps"]]+[""]
    lines += [f"- {item}" for item in report["interpretation"]]+[""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest", type=Path, help="measured build/assets/runtime metadata JSON; copied without invented values")
    parser.add_argument("--output-dir", "--output", type=Path, help="default: run_dir")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    events, issues = read_events(args.run_dir / "events.jsonl")
    report = summarize(events, config, issues)
    report["config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    trace_path = args.run_dir / "events.jsonl"
    report["events_sha256"] = hashlib.sha256(trace_path.read_bytes()).hexdigest() if trace_path.is_file() else None
    report["metadata"] = None
    if args.manifest:
        try:
            report["metadata"] = json.loads(args.manifest.read_text())
        except FileNotFoundError:
            report["issues"].append(f"missing measured manifest file {args.manifest.name}")
    if report["metadata"] is None:
        report["evidence_gaps"].append("missing measured build/assets/runtime manifest")
        report["status"] = "incomplete"
    out = args.output_dir or args.run_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "body-baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    (out / "body-baseline.md").write_text(markdown(report))
    print(f"body baseline {report['status']}: {out / 'body-baseline.md'}")
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
