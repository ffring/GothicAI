#!/usr/bin/env python3
"""Reproduce navigation keys checks with the CPU body runner on Ryzen.

One process and one shared simulation-lock acquisition per case and controller
mode. The script never builds, downloads assets, runs models, or modifies saves.
All scenarios, runner journals and the machine-readable summary stay in OUTPUT.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


CASES = ("parity", "jump", "climb", "ttl", "ttl-compare", "head", "stress", "void", "void-jump", "void-replay")
POSITION_CHANNELS = ("physical_root_cm", "npc_root_cm", "visual_root_cm")
SEGMENTS = (
    ("forward", 3000, 1, 0, 0, "walk"),
    ("side", 2000, 0, 1, 0, "walk"),
    ("turn", 1000, 0, 0, 1, "walk"),
    ("back", 2000, -1, 0, 0, "walk"),
    ("run", 3000, 1, 0, 0, "run"),
)


class EvidenceError(RuntimeError):
    """Missing or invalid fixture evidence; distinct from a failed mechanic."""


class CheckFailed(RuntimeError):
    """A correctly prepared mechanic exceeded its requested limits."""


class Limitation(RuntimeError):
    """Complete evidence of an unresolved controller/world limitation."""

    def __init__(self, classification, message):
        super().__init__(message)
        self.classification = classification


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(path):
    if not path.is_file():
        raise EvidenceError(f"Required journal missing: {path}")
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                result.append(json.loads(line))
            except ValueError as exc:
                raise EvidenceError(f"Invalid JSON at {path}:{number}: {exc}") from exc
    return result


def vector(row, key="root_cm"):
    value = row.get(key)
    if isinstance(value, dict):
        value = [value.get(axis) for axis in "xyz"]
    if (not isinstance(value, (list, tuple)) or len(value) != 3 or
            any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise EvidenceError(f"Invalid {key}: {value}")
    return tuple(value)


def horizontal(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def angle_delta(end, start):
    return (end - start + 180) % 360 - 180


def configure_ini(source, target, mode):
    text = source.read_text(encoding="utf-8", errors="replace") if source else ""
    for setting in ("character_virtual", "npc_character_virtual"):
        text = re.sub(rf"(?im)^\s*{setting}\s*=.*$", "", text)
    flags = f"character_virtual={mode}\nnpc_character_virtual={mode}\n"
    if re.search(r"(?im)^\[GAME\]\s*$", text):
        text = re.sub(r"(?im)^(\[GAME\])\s*$", lambda match: match[1] + "\n" + flags,
                      text, count=1)
    else:
        text += "\n[GAME]\n" + flags
    target.write_text(text, encoding="utf-8")


class SimulationLock:
    def __init__(self, path, timeout):
        self.path, self.timeout, self.stream = path, timeout, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise EvidenceError(f"Ryzen busy: timed out waiting for {self.path}")
                time.sleep(1)

    def __exit__(self, *_):
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def keys(actor, duration=0, forward=0, side=0, turn=0, gait="walk", jump=0,
         climb=0, yaw=0, pitch=0, ttl=None):
    ttl = duration + 100 if ttl is None else ttl
    command = f"keys {actor} {forward} {side} {turn} {gait} {jump} {climb} {yaw} {pitch} {ttl}"
    return [command, f"wait {duration}"] if duration else [command]


def mark(name):
    return [f"keys sample {name}"]


def recorded_stress_tail():
    """NPC 6, decisions 767..820 of the archived seed-73013 market run."""
    state = 73013
    result = []
    for decision in range(821):
        for actor in range(10):
            values = []
            for bound in (3, 3, 3, 2, 121, 41, 40, 50):
                state = (state * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
                values.append((state >> 32) % bound)
            if actor == 6 and decision >= 767:
                result.append(dict(forward=values[0]-1, side=values[1]-1, turn=values[2]-1,
                    gait="walk" if values[3] else "run", yaw=values[4]-60, pitch=values[5]-20,
                    jump=int(values[6] == 0), climb=int(values[7] == 0)))
    return result


def scenario(case, seed):
    """Only bench commands; hero input travels through the bench PlayerControl adapter."""
    lines = [f"seed {seed}", "cheat god", "set time 10 0", "release all"]
    if case == "parity":
        lines += ["keys setup flat 1"]
        for actor in ("hero", "0"):
            lines += [f"keys place {actor}", "wait 1000"]
            lines += mark(f"{actor}-start")
            for name, duration, forward, side, turn, gait in SEGMENTS:
                lines += [f"keys trace {actor}-{name} on"]
                lines += keys(actor, duration, forward, side, turn, gait)
                lines += mark(f"{actor}-{name}")
            lines += keys(actor, ttl=100) + ["wait 1000"] + mark(f"{actor}-settled")
    elif case in ("jump", "climb"):
        lines += [f"keys setup {case} 1"]
        for actor in ("hero", "0"):
            lines += [f"keys place {actor}", "wait 1000"]
            lines += mark(f"{actor}-start")
            lines += [f"keys trace {actor}-{case} on"]
            if case == "jump":
                # A running jump selects S_JUMP. Jumping from standing at the
                # low obstruction can select JumpUpLow and test a climb instead.
                lines += keys(actor, 700, forward=1, gait="run")
                lines += keys(actor, 300, forward=1, gait="run", jump=1, ttl=100)
                lines += keys(actor, 2500, forward=1, gait="run")
            else:
                lines += keys(actor, 4000, forward=1, gait="run", climb=1)
            lines += keys(actor, ttl=100) + ["wait 1000"]
            lines += mark(f"{actor}-end")
    elif case == "ttl":
        lines += ["keys setup flat 1", "keys place 0", "keys 0 on", "keys trace ttl on", "wait 1000"]
        lines += mark("before") + keys("0", forward=1, gait="run", ttl=100)
        lines += ["wait 1200"] + mark("expired-a") + ["wait 1000"] + mark("expired-b")
        lines += ["keys routine 0", "keys 0 off"] + mark("routine-start")
        lines += ["wait 6000"] + mark("routine-end")
    elif case == "ttl-compare":
        lines += ["keys setup flat 1"]
        for actor in ("hero", "0"):
            lines += [f"keys place {actor}", "wait 1000", f"keys trace {actor}-ttl-compare on"]
            lines += mark(f"{actor}-before") + keys(actor, forward=1, gait="run", ttl=100)
            lines += ["wait 1200"] + mark(f"{actor}-1200")
            lines += ["wait 1000"] + mark(f"{actor}-2200")
            lines += ["wait 2000"] + mark(f"{actor}-4200")
            lines += ["keys trace setup off", f"keys place {actor}"]
            lines += keys(actor, gait="run", ttl=100) + ["wait 1000", f"keys trace {actor}-idle-compare on"]
            lines += mark(f"{actor}-idle-0")
            for second in (1, 2, 3):
                lines += ["wait 1000"] + mark(f"{actor}-idle-{second}")
    elif case == "head":
        lines += ["keys setup flat 1"]
        for yaw in (0, -60, 60):
            lines += ["keys place 0", "keys 0 on", "wait 1000"]
            lines += keys("0", 1000, yaw=yaw, ttl=5000)
            lines += [f"keys eye 0 head-{yaw}"] + mark(f"head-{yaw}-start")
            lines += [f"keys trace head-{yaw} on"]
            lines += keys("0", 3000, forward=1, gait="walk", yaw=yaw)
            lines += mark(f"head-{yaw}-end") + keys("0", ttl=100) + ["wait 1000"]
    elif case == "stress":
        lines += ["keys setup market 10", "keys trace stress on", "wait 1000"] + mark("stress-start")
        lines += [f"keys stress begin {seed}", "wait 60000", "keys stress end"]
        lines += mark("stress-end") + ["wait 1000"] + mark("stress-expired")
    elif case == "void":
        lines += ["keys setup void 1"]
        for actor in ("hero", "0"):
            lines += [f"keys place {actor}", "wait 1000", f"keys trace {actor}-void on"]
            lines += mark(f"{actor}-start") + keys(actor, 12000, forward=1, gait="walk")
            lines += keys(actor, ttl=100) + ["wait 2200"] + mark(f"{actor}-end")
    elif case == "void-jump":
        lines += ["keys setup void 1"]
        for actor in ("hero", "0"):
            # About 180 cm behind the recorded north-side takeoff. Rotate
            # through real input to the stress trajectory's near -90° heading.
            lines += [f"keys place {actor} -102.316 30.967"]
            lines += keys(actor, 100, turn=1, gait="run")
            lines += keys(actor, gait="run", ttl=100) + ["wait 1000"]
            lines += [f"keys trace {actor}-void-jump on"] + mark(f"{actor}-start")
            lines += keys(actor, 700, forward=1, gait="run")
            lines += keys(actor, 300, forward=1, gait="run", jump=1, ttl=100)
            lines += keys(actor, gait="run", ttl=100) + ["wait 4000"] + mark(f"{actor}-end")
    elif case == "void-replay":
        # Reproduce the supported idle pose just before the original takeoff,
        # including its sidesteps and turns, through ordinary held keys.
        lines += ["keys setup void-replay 1", "wait 66"]
        for actor in ("hero", "0"):
            lines += [f"keys place {actor}", "wait 1000", f"keys trace {actor}-void-replay on"]
            lines += mark(f"{actor}-start")
            for command in recorded_stress_tail():
                lines += keys(actor, 66, ttl=100, **command)
            lines += keys(actor, gait="run", ttl=100) + ["wait 1200"] + mark(f"{actor}-end")
    lines += ["keys trace done off", "release all", "label done"]
    return "\n".join(lines) + "\n"


def run_case(args, mode, case):
    output = args.output / f"mode-{mode}" / case
    if args.analyze_only:
        return analyze(case, output, mode, allow_related=True)
    output.mkdir(parents=True, exist_ok=True)
    if not args.generate_only and (output / "keys-events.jsonl").exists():
        raise EvidenceError(f"Raw journals already exist in {output}; use a fresh --output or --analyze-only")
    bench = output / "scenario.bench"
    bench.write_text(scenario(case, args.seed), encoding="utf-8")
    work = output / "work"
    work.mkdir(exist_ok=True)
    configure_ini(args.ini, work / "Gothic.ini", mode)
    if args.generate_only:
        return {"status": "generated", "output": str(output)}
    if not args.analyze_only:
        env = dict(os.environ, GOTHIC_AI="0", OPENGOTHIC_BODY_TRACE="0",
                   OPENGOTHIC_BODY_ANIMATION_RNG="isolated",
                   VK_DRIVER_FILES="/nonexistent", VK_ICD_FILENAMES="/nonexistent")
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        command = [str(args.runner), "-g", str(args.assets), "-g2", "-devmode", "--save", str(args.save),
                   "--scenario", str(bench), "--output", str(output), "--mode", "lockstep"]
        print(f"mode={mode} case={case}: waiting for simulation lock", flush=True)
        with SimulationLock(args.lock, args.lock_timeout):
            available = shutil.disk_usage(output).free
            if available < 1024 ** 3:
                raise EvidenceError(f"Less than 1 GiB free in {output}: {available} bytes")
            with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
                started = time.monotonic()
                try:
                    process = subprocess.run(command, cwd=work, env=env, stdout=stdout, stderr=stderr,
                                             timeout=args.timeout, check=False)
                except subprocess.TimeoutExpired as exc:
                    raise EvidenceError(f"Runner watchdog after {args.timeout}s; journals retained in {output}") from exc
                if process.returncode:
                    raise EvidenceError(f"Runner exited {process.returncode}; journals retained in {output}")
        print(f"mode={mode} case={case}: runner completed in {time.monotonic() - started:.2f}s", flush=True)
    return analyze(case, output, mode)


class Evidence:
    def __init__(self, output):
        self.output = output
        self.rows = jsonl(output / "keys-frames.jsonl")
        self.events = jsonl(output / "keys-events.jsonl")
        self.samples = {row["tag"]: row for row in self.rows if row.get("kind") == "keys_sample"}
        setups = [row for row in self.events if row.get("kind") == "keys_setup"]
        if len(setups) != 1:
            raise EvidenceError(f"Expected one prepared fixture in {output}, found {len(setups)}")
        self.setup = setups[0]
        self.metrics = {}

    def sample(self, tag, actor="0"):
        if tag not in self.samples:
            raise EvidenceError(f"Missing sample {tag} in {self.output}")
        return self.actor(self.samples[tag], actor)

    @staticmethod
    def actor(row, actor):
        matches = [item for item in row["actors"] if item["actor"] == actor]
        if len(matches) != 1:
            raise EvidenceError(f"Missing/duplicate actor {actor} at {row.get('tag')}")
        value = dict(matches[0], game_ms=row["game_ms"])
        vector(value)
        return value

    def frames(self, tag, actor="0"):
        result = [self.actor(row, actor) for row in self.rows
                  if row.get("kind") == "keys_frame" and row.get("tag") == tag]
        if not result:
            raise EvidenceError(f"Missing frames for {tag}/{actor} in {self.output}")
        return result


def displacement(start, end):
    a, b = vector(start), vector(end)
    return (b[0] - a[0], 0, b[2] - a[2])


def animation_counts(frames):
    counter = Counter()
    for frame in frames:
        counter.update(set(re.findall(r"\b[ST]_[A-Z0-9_]+\b", frame["animations"])))
    return dict(counter.most_common())


def dominant_animation(frames):
    counts = animation_counts(frames)
    if not counts:
        raise EvidenceError("Animation trace has no native sequence names")
    return next(iter(counts))


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def distance3d(a, b):
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def difference(end, start):
    return tuple(end[index] - start[index] for index in range(3))


def idle_controller_evidence(frames):
    """Require actual per-frame zero requests before calling drift passive."""
    request_fields = ("cv_requested_cm", "cv_clipped_request_cm", "cv_native_requested_cm", "anim_root_local_cm")
    maxima, missing = {}, {}
    for name in request_fields:
        values = []
        absent = 0
        for frame in frames:
            if frame.get(name) is None:
                absent += 1
            else:
                values.append(distance3d(vector(frame, name), (0, 0, 0)))
        maxima[name] = max(values, default=None)
        missing[name] = absent
    all_active = bool(frames) and all(frame.get("cv_active", False) for frame in frames)
    all_valid = bool(frames) and all(frame.get("cv_diagnostics_valid", False) for frame in frames)
    zero_requests = all(missing[name] == 0 and maxima[name] is not None and maxima[name] <= 1.e-5
                        for name in request_fields)
    return dict(samples=len(frames), cv_active_all_frames=all_active, cv_diagnostics_valid_all_frames=all_valid,
                maximum_request_length_cm=maxima, missing_request_samples=missing,
                all_cv_requests_and_animation_root_zero=zero_requests)


def related_ttl_comparison(evidence, a, b):
    """Offline-only corroboration; the physical-stop criterion remains unmet."""
    path = evidence.output.parent / "ttl-compare" / "result.json"
    proof = dict(path=str(path), accepted=False, late_vector_tolerance_cm=.01)
    if not getattr(evidence, "allow_related", False):
        proof["reason"] = "Related comparison is read only with --analyze-only"
        return proof
    if not path.is_file():
        proof["reason"] = "No neighboring ttl-compare/result.json"
        return proof
    try:
        paired = json.loads(path.read_text(encoding="utf-8"))
        if (paired.get("status") != "passed" or
                paired.get("classification") != "shared_character_virtual_passive_drift"):
            proof["reason"] = "Neighbor has not established shared passive CV drift"
            return proof
        if paired.get("mode") != 1 or paired["fixture"].get("anchor") != evidence.setup.get("anchor"):
            proof["reason"] = "Comparison mode or authored fixture differs"
            return proof
        root_error = distance3d(vector(paired["fixture"]), vector(evidence.setup))
        proof["fixture_root_error_cm"] = root_error
        if root_error > 1:
            proof["reason"] = "Comparison starts from another authored physical location"
            return proof
        measured = difference(vector(b, "physical_root_cm"), vector(a, "physical_root_cm"))
        proof["ttl_1200_2200_vector_cm"] = measured
        proof["ttl_1200_2200_horizontal_cm"] = horizontal(measured, (0, 0, 0))
        proof["paired_late_vectors_cm"] = {}
        proof["paired_late_vector_error_cm"] = {}
        proof["late_window_duration_ms"] = b["game_ms"] - a["game_ms"]
        for actor in ("hero", "0"):
            samples = paired["actors"][actor]["samples"]
            start, end = samples["1200"], samples["2200"]
            paired_vector = difference(vector(end, "physical_root_cm"), vector(start, "physical_root_cm"))
            proof["paired_late_vectors_cm"][actor] = paired_vector
            proof["paired_late_vector_error_cm"][actor] = distance3d(measured, paired_vector)
            if end["game_ms"] - start["game_ms"] != proof["late_window_duration_ms"]:
                proof["reason"] = "TTL and paired late windows have different durations"
                return proof
        if max(proof["paired_late_vector_error_cm"].values()) > proof["late_vector_tolerance_cm"]:
            proof["reason"] = "Measured TTL late vector does not match both paired controllers"
            return proof
        proof.update(accepted=True, reason="Same fixture and late physical vectors corroborate validated idle-only comparison")
        proof["controller_limitation"] = paired["controller_limitation"]
    except (ValueError, KeyError, TypeError, EvidenceError) as exc:
        proof["reason"] = f"Comparison evidence is incomplete: {exc}"
    return proof


def position_metrics(frames):
    """Preserve native physics, public NPC root and rendered root independently."""
    if not frames:
        raise EvidenceError("No position samples")
    if any(not frame.get("physical_root_valid", False) for frame in frames):
        raise EvidenceError("Missing or invalid native physical-root evidence")
    result = {}
    for channel in POSITION_CHANNELS:
        if any(channel not in frame for frame in frames):
            raise EvidenceError(f"Missing position channel {channel}")
        positions = [vector(frame, channel) for frame in frames]
        origin = positions[0]
        horizontal_steps = [horizontal(a, b) for a, b in zip(positions, positions[1:])]
        speeds = [travel * 1000 / (b["game_ms"] - a["game_ms"])
                  for travel, a, b in zip(horizontal_steps, frames, frames[1:]) if b["game_ms"] > a["game_ms"]]
        result[channel] = dict(first_cm=origin, last_cm=positions[-1],
            minimum_cm=[min(position[index] for position in positions) for index in range(3)],
            maximum_cm=[max(position[index] for position in positions) for index in range(3)],
            max_horizontal_from_first_cm=max(horizontal(origin, position) for position in positions),
            max_3d_from_first_cm=max(distance3d(origin, position) for position in positions),
            net_horizontal_cm=horizontal(origin, positions[-1]),
            horizontal_path_cm=sum(horizontal_steps),
            max_derived_horizontal_speed_cm_s=max(speeds, default=0))
    result["samples"] = len(frames)
    result["start_game_ms"] = frames[0]["game_ms"]
    result["end_game_ms"] = frames[-1]["game_ms"]
    result["maximum_visual_to_physical_cm"] = max(
        distance3d(vector(frame, "visual_root_cm"), vector(frame, "physical_root_cm")) for frame in frames)
    result["maximum_npc_to_physical_cm"] = max(
        distance3d(vector(frame, "npc_root_cm"), vector(frame, "physical_root_cm")) for frame in frames)
    return result


def diagnostic_snapshot(frame):
    names = (*POSITION_CHANNELS, "root_cm", "physical_root_source", "physical_root_valid",
             "cv_root_cm", "cv_position_cm", "cv_native_position_cm", "cv_accepted_cm",
             "cv_diagnostics_valid", "cv_ground_state", "cv_ground_normal", "cv_velocity_cm_s",
             "cv_active", "support_physical", "support_npc", "air", "falling", "jumping",
             "keys_enabled", "has_command", "remaining_ms", "animations", "game_ms")
    result = {name: frame.get(name) for name in names}
    result.update({name: value for name, value in frame.items()
                   if name.startswith(("cv_", "support_probe_")) or name in ("frame_dt_ms", "anim_root_local_cm")})
    return result


def support_metrics(frames):
    """Detect missing terrain beneath the native feet without hiding it as travel."""
    if any("support_physical" not in frame or "support_npc" not in frame for frame in frames):
        raise EvidenceError("Missing static support-ray evidence")
    result = {"positions": position_metrics(frames), "missing_physical_support_frames": 0,
              "missing_npc_support_frames": 0, "below_last_support_frames": 0,
              "maximum_below_last_support_cm": 0, "support_loss_episodes": [],
              "root_below_probe_surface_frames": 0, "maximum_root_below_probe_surface_cm": 0,
              "first_support_issue": None, "physical_support_drop_range_cm": None}
    episodes, episode, last_supported = result["support_loss_episodes"], None, None
    drops, issues = [], []
    for frame in frames:
        physical = vector(frame, "physical_root_cm")
        support = frame["support_physical"]
        result["missing_npc_support_frames"] += frame["support_npc"] is None
        if support is not None:
            drop = float(support["drop_cm"])
            if not math.isfinite(drop):
                raise EvidenceError("Nonfinite physical support ray")
            drops.append(drop)
            grounded_pose = not (frame["air"] or frame["falling"] or frame.get("jumping", False))
            if abs(drop) <= 30 and grounded_pose:
                last_supported = dict(game_ms=frame["game_ms"], root_cm=physical, point_cm=vector(support, "point_cm"))
            if episode is not None:
                episodes.append(episode)
                episode = None
            if drop < -5:
                # The authored jump root can dip below terrain; a ray starting
                # above it can also hit a tabletop. Neither proves loss of floor.
                # Keep the measurement, but require missing support for a fall.
                result["root_below_probe_surface_frames"] += 1
                result["maximum_root_below_probe_surface_cm"] = max(
                    result["maximum_root_below_probe_surface_cm"], -drop)
            elif drop > 100 and not (frame["air"] or frame["falling"] or frame.get("jumping", False)):
                issues.append(("grounded_state_over_100cm_above_support", frame))
            continue
        result["missing_physical_support_frames"] += 1
        below = max(0, last_supported["point_cm"][1] - physical[1]) if last_supported else 0
        result["maximum_below_last_support_cm"] = max(result["maximum_below_last_support_cm"], below)
        if below > 100:
            result["below_last_support_frames"] += 1
            issues.append(("missing_support_below_last_surface", frame))
        if episode is None:
            episode = dict(start_game_ms=frame["game_ms"], end_game_ms=frame["game_ms"], frames=0,
                           first=diagnostic_snapshot(frame), last_supported=last_supported,
                           minimum_y_cm=physical[1], maximum_y_cm=physical[1])
        episode["frames"] += 1
        episode["end_game_ms"] = frame["game_ms"]
        episode["minimum_y_cm"] = min(episode["minimum_y_cm"], physical[1])
        episode["maximum_y_cm"] = max(episode["maximum_y_cm"], physical[1])
    if episode is not None:
        episodes.append(episode)
    persistent = [episode for episode in episodes if episode["frames"] >= 3]
    # Missing the central ray while jumping across a gap is not a fall. Require
    # a physical descent below a previously confirmed ground pose as well.
    falling_episodes = [episode for episode in persistent if episode["last_supported"] and
                        episode["minimum_y_cm"] < episode["last_supported"]["point_cm"][1] - 100]
    issues += [("persistent_missing_support_and_physical_fall", episode["first"])
               for episode in falling_episodes]
    if issues:
        reason, first = min(issues, key=lambda item: item[1]["game_ms"])
        result["first_support_issue"] = dict(reason=reason, sample=diagnostic_snapshot(first))
    if drops:
        result["physical_support_drop_range_cm"] = [min(drops), max(drops)]
    result["persistent_support_loss_episodes"] = len(persistent)
    result["physical_fall_episodes"] = len(falling_episodes)
    result["support_issue_detected"] = bool(issues)
    return result


def parity(evidence):
    result = evidence.metrics
    result["segments"] = []
    starts = {actor: evidence.sample(f"{actor}-start", actor) for actor in ("hero", "0")}
    initial_gap = horizontal(vector(starts["hero"]), vector(starts["0"]))
    if initial_gap > 5 or abs(angle_delta(starts["hero"]["body_yaw_deg"], starts["0"]["body_yaw_deg"])) > 1:
        raise EvidenceError(f"Parity actors did not start on the same lane: root gap {initial_gap:.3f} cm")
    previous = dict(starts)
    for name, duration, *_ in SEGMENTS:
        ends = {actor: evidence.sample(f"{actor}-{name}", actor) for actor in ("hero", "0")}
        moves = {actor: displacement(previous[actor], ends[actor]) for actor in ("hero", "0")}
        distances = {actor: horizontal(moves[actor], (0, 0, 0)) for actor in ("hero", "0")}
        yaws = {actor: angle_delta(ends[actor]["body_yaw_deg"], previous[actor]["body_yaw_deg"])
                for actor in ("hero", "0")}
        error = horizontal(moves["hero"], moves["0"])
        animations = {actor: dominant_animation(evidence.frames(f"{actor}-{name}", actor))
                      for actor in ("hero", "0")}
        row = dict(segment=name, requested_ms=duration, hero_cm=distances["hero"], npc_cm=distances["0"],
                   vector_error_cm=error, hero_yaw_deg=yaws["hero"], npc_yaw_deg=yaws["0"], animations=animations)
        result["segments"].append(row)
        if name != "turn":
            row["relative_error"] = error / max(distances["hero"], 1)
            require(min(distances.values()) > 50, f"{name}: no real motion: {row}")
            require(row["relative_error"] <= .1, f"{name}: displacement differs by >10%: {row}")
        else:
            require(81 <= abs(yaws["hero"]) <= 99, f"Hero did not perform 90-degree turn: {row}")
            require(abs(angle_delta(yaws["0"], yaws["hero"])) <= 9, f"NPC turn differs by >10%: {row}")
        require(animations["hero"] == animations["0"], f"{name}: different dominant native animations: {row}")
        previous = ends
    held_moves = {actor: displacement(starts[actor], previous[actor]) for actor in starts}
    result["held_sequence_vector_error_cm"] = horizontal(held_moves["hero"], held_moves["0"])
    settled = {actor: evidence.sample(f"{actor}-settled", actor) for actor in starts}
    final_moves = {actor: displacement(starts[actor], settled[actor]) for actor in starts}
    final_distance = horizontal(final_moves["hero"], (0, 0, 0))
    result["total_vector_error_cm"] = horizontal(final_moves["hero"], final_moves["0"])
    result["total_relative_error"] = result["total_vector_error_cm"] / max(final_distance, 1)
    result["final_yaw_error_deg"] = abs(angle_delta(settled["0"]["body_yaw_deg"], settled["hero"]["body_yaw_deg"]))
    require(final_distance > 100 and result["total_relative_error"] <= .1, f"Final displacement differs by >10%: {result}")
    require(result["final_yaw_error_deg"] <= 9, f"Final yaw differs by >10%: {result}")
    return result


def obstacle(evidence, case):
    start, goal = vector(evidence.setup), vector(evidence.setup, "goal_cm")
    forward = vector(evidence.setup, "forward")
    distance = horizontal(start, goal)
    require(distance > 50, f"Degenerate {case} fixture: {evidence.setup}")
    result = evidence.metrics
    result["actors"] = {}
    starts = {actor: evidence.sample(f"{actor}-start", actor) for actor in ("hero", "0")}
    start_gap = distance3d(vector(starts["hero"], "physical_root_cm"), vector(starts["0"], "physical_root_cm"))
    start_yaw_gap = abs(angle_delta(starts["hero"]["body_yaw_deg"], starts["0"]["body_yaw_deg"]))
    start_shifts = {actor: distance3d(vector(initial, "physical_root_cm"), start) for actor, initial in starts.items()}
    result.update(actual_start_gap_cm=start_gap, actual_start_yaw_gap_deg=start_yaw_gap,
                  actual_start_shift_cm=start_shifts)
    if start_gap > 1 or start_yaw_gap > 1:
        raise EvidenceError(f"{case}: settled actor starts differ: physical gap {start_gap} cm, yaw gap {start_yaw_gap} degrees")
    if max(start_shifts.values()) > 30:
        raise EvidenceError(f"{case}: initial capsule correction exceeds 30 cm: {start_shifts}")
    for actor in ("hero", "0"):
        frames = evidence.frames(f"{actor}-{case}", actor)
        initial = starts[actor]
        final = evidence.sample(f"{actor}-end", actor)
        crossed = []
        for frame in frames:
            root = vector(frame)
            delta = [root[axis] - goal[axis] for axis in range(3)]
            along = delta[0] * forward[0] + delta[2] * forward[2]
            lateral = abs(-delta[0] * forward[2] + delta[2] * forward[0])
            if along >= 0 and lateral <= 100 and abs(root[1] - goal[1]) <= 65:
                crossed.append(frame)
        animations = animation_counts(frames)
        jump_names = [name for name in animations if "JUMP" in name]
        forward_jump_names = [name for name in jump_names if "JUMPUP" not in name and "JUMP_UP" not in name]
        climb_names = [name for name in animations if "JUMPUP" in name or "CLIMB" in name]
        peak = max(vector(frame)[1] for frame in frames) - vector(initial)[1]
        result["actors"][actor] = dict(crossed=bool(crossed),
            elapsed_to_cross_ms=crossed[0]["game_ms"] - initial["game_ms"] if crossed else None,
            start_cm=vector(initial), end_cm=vector(final), maximum_rise_cm=peak,
            animations=animations, saw_air=any(frame["air"] for frame in frames))
        if case == "jump" and actor == "0":
            commands = [event for event in evidence.events if event.get("kind") == "keys_command"
                        and event.get("actor") == actor and event.get("jump")]
            if len(commands) != 1 or commands[0].get("ttl_ms") != 100:
                raise EvidenceError("Airborne TTL fixture needs one explicit 100-ms jump command")
            deadline = commands[0]["game_ms"] + 100
            expired_airborne = [frame for frame in frames
                                if deadline <= frame["game_ms"] < commands[0]["game_ms"] + 300
                                and frame["keys_enabled"] and not frame["has_command"]
                                and (frame.get("jumping", False) or frame["air"])
                                and "JUMP" in frame["animations"] and "JUMPUP" not in frame["animations"]]
            result["actors"][actor]["airborne_ttl_100_survival_frames"] = len(expired_airborne)
            require(expired_airborne, f"{case}/{actor}: 100-ms TTL interrupted airborne jump")
        require(peak > 25 and jump_names, f"{case}/{actor}: no native jump observed: {result['actors'][actor]}")
        if case == "jump":
            require(forward_jump_names, f"{case}/{actor}: used climb instead of native forward jump: {result['actors'][actor]}")
        if case == "climb":
            require(climb_names, f"{case}/{actor}: no native climb observed: {result['actors'][actor]}")
        require(crossed, f"{case}/{actor}: did not cross real fixture goal: {result['actors'][actor]}")
    return result


def ttl(evidence):
    initial = evidence.sample("before")
    a, b = evidence.sample("expired-a"), evidence.sample("expired-b")
    travel = horizontal(vector(initial), vector(a))
    drift = horizontal(vector(a), vector(b))
    result = evidence.metrics
    result.update(input_travel_cm=travel, post_ttl_drift_cm=drift)
    routine_start, routine_end = evidence.sample("routine-start"), evidence.sample("routine-end")
    deadline = initial["game_ms"] + 100
    expired_frames = [frame for frame in evidence.frames("ttl")
                      if deadline <= frame["game_ms"] < routine_start["game_ms"]
                      and frame["keys_enabled"] and not frame["has_command"]]
    if not expired_frames:
        raise EvidenceError("No expired-command frames before native routine resumes")
    first_expired = expired_frames[0]
    channel_motion = position_metrics(expired_frames)
    maximum_expired_travel = channel_motion["physical_root_cm"]["max_horizontal_from_first_cm"]
    maximum_model_motion = channel_motion["visual_root_cm"]["max_3d_from_first_cm"]
    release_latency = first_expired["game_ms"] - deadline
    result.update(max_post_expiry_travel_cm=maximum_expired_travel,
                  max_post_expiry_visual_travel_cm=maximum_model_motion,
                  post_expiry_channels=channel_motion,
                  expiry_release_game_ms=first_expired["game_ms"], expiry_stop_latency_ms=release_latency,
                  first_expired=diagnostic_snapshot(first_expired),
                  expired_a=diagnostic_snapshot(a), expired_b=diagnostic_snapshot(b))
    require(travel > 1, f"TTL input produced no motion: {travel:.3f} cm")
    require(release_latency <= 34, f"TTL release exceeded one world tick: {release_latency} ms")
    for sample in (a, b, *expired_frames):
        require(sample["keys_enabled"] and not sample["has_command"] and sample["remaining_ms"] == 0,
                f"TTL did not release held command: {sample}")
        require(not any(sample[name] for name in ("forward", "strafe", "turn", "head_yaw_deg", "head_pitch_deg")),
                f"TTL left nonzero keys: {sample}")
    goals = [row for row in evidence.events if row.get("kind") == "keys_native_goal"]
    if len(goals) != 1:
        raise EvidenceError("Missing retained native routine goal")
    goal = vector(goals[0], "goal_cm")
    start_distance = horizontal(vector(routine_start), goal)
    end_distance = horizontal(vector(routine_end), goal)
    result.update(native_goal_cm=goal, native_start_distance_cm=start_distance,
                  native_end_distance_cm=end_distance, native_progress_cm=start_distance - end_distance,
                  native_end_animations=routine_end["animations"])
    require(not routine_start["keys_enabled"] and not routine_end["keys_enabled"], "keys off did not detach stand override")
    require(start_distance - end_distance > 100, f"Native goal did not resume after off: {start_distance:.2f} -> {end_distance:.2f} cm")
    motion_exceeds_limit = maximum_expired_travel > 1 or maximum_model_motion > 10
    result["physical_stop_within_1cm"] = maximum_expired_travel <= 1
    result["visual_catchup_within_10cm"] = maximum_model_motion <= 10
    if motion_exceeds_limit:
        message = (f"Post-expiry physical motion {maximum_expired_travel:.3f} cm (limit 1), "
                   f"visual motion {maximum_model_motion:.3f} cm (limit 10); "
                   "held keys and native resumption passed; inspect paired ttl-compare")
        if any(frame["cv_active"] for frame in expired_frames):
            proof = related_ttl_comparison(evidence, a, b)
            result["related_comparison_validation"] = proof
            result["keys_check_passed"] = True if proof["accepted"] else None
            classification = "shared_character_virtual_passive_drift" if proof["accepted"] else "unclassified_cv_motion"
            if proof["accepted"]:
                result["controller_limitation"] = proof["controller_limitation"]
            raise Limitation(classification, message)
        raise CheckFailed(message)
    result["keys_check_passed"] = True
    return result


def ttl_compare(evidence):
    result = evidence.metrics
    result.update(status="observed", measurement_only=True, actors={}, comparison={})
    initial = {actor: evidence.sample(f"{actor}-before", actor) for actor in ("hero", "0")}
    if distance3d(vector(initial["hero"], "physical_root_cm"), vector(initial["0"], "physical_root_cm")) > 5:
        raise EvidenceError("TTL comparison actors did not start at the same physical root")
    for actor in ("hero", "0"):
        before = initial[actor]
        samples = {str(elapsed): evidence.sample(f"{actor}-{elapsed}", actor) for elapsed in (1200, 2200, 4200)}
        frames = [frame for frame in evidence.frames(f"{actor}-ttl-compare", actor)
                  if before["game_ms"] <= frame["game_ms"] <= samples["4200"]["game_ms"]]
        after_deadline = [frame for frame in frames if frame["game_ms"] >= before["game_ms"] + 100]
        if not after_deadline:
            raise EvidenceError(f"No post-release frames for {actor}")
        actor_result = dict(before=diagnostic_snapshot(before),
                            samples={elapsed: diagnostic_snapshot(sample) for elapsed, sample in samples.items()},
                            full_trajectory=position_metrics(frames), post_release=position_metrics(after_deadline),
                            late_windows={})
        result["actors"][actor] = actor_result
        require(horizontal(vector(before, "physical_root_cm"), vector(samples["1200"], "physical_root_cm")) > 1,
                f"TTL comparison {actor} never moved")
        for start, end in ((1200, 2200), (2200, 4200)):
            window = [frame for frame in frames
                      if samples[str(start)]["game_ms"] <= frame["game_ms"] <= samples[str(end)]["game_ms"]]
            actor_result["late_windows"][f"{start}-{end}"] = position_metrics(window)
        idle_samples = {str(second): evidence.sample(f"{actor}-idle-{second}", actor) for second in (0, 1, 2, 3)}
        idle_frames = [frame for frame in evidence.frames(f"{actor}-idle-compare", actor)
                       if idle_samples["0"]["game_ms"] <= frame["game_ms"] <= idle_samples["3"]["game_ms"]]
        actor_result["idle_only"] = dict(
            samples={second: diagnostic_snapshot(sample) for second, sample in idle_samples.items()},
            trajectory=position_metrics(idle_frames),
            support=support_metrics(idle_frames),
            controller=idle_controller_evidence(idle_frames))
        if actor == "0":
            for frame in after_deadline:
                require(frame["keys_enabled"] and not frame["has_command"], "NPC retained input in TTL comparison")
                require(not any(frame[name] for name in ("forward", "strafe", "turn")), "NPC retained movement keys in TTL comparison")
    idle_starts = {actor: evidence.sample(f"{actor}-idle-0", actor) for actor in ("hero", "0")}
    # Settling itself can drift on a slope; record that drift rather than call
    # different post-run footprints an equal-point controller comparison.
    result["comparison"]["idle_settled_physical_root_gap_cm"] = horizontal(
        vector(idle_starts["hero"], "physical_root_cm"), vector(idle_starts["0"], "physical_root_cm"))
    for window in ("1200-2200", "2200-4200"):
        result["comparison"][window] = {
            channel: dict(hero_cm=result["actors"]["hero"]["late_windows"][window][channel]["net_horizontal_cm"],
                          npc_cm=result["actors"]["0"]["late_windows"][window][channel]["net_horizontal_cm"])
            for channel in POSITION_CHANNELS}
    result["comparison"]["idle_only"] = {
        channel: dict(hero_cm=result["actors"]["hero"]["idle_only"]["trajectory"][channel]["net_horizontal_cm"],
                      npc_cm=result["actors"]["0"]["idle_only"]["trajectory"][channel]["net_horizontal_cm"])
        for channel in POSITION_CHANNELS}
    idle_ends = {actor: evidence.sample(f"{actor}-idle-3", actor) for actor in ("hero", "0")}
    idle_vectors = {actor: difference(vector(idle_ends[actor], "physical_root_cm"),
                                      vector(idle_starts[actor], "physical_root_cm")) for actor in ("hero", "0")}
    start_error = distance3d(vector(idle_starts["hero"], "physical_root_cm"), vector(idle_starts["0"], "physical_root_cm"))
    end_error = distance3d(vector(idle_ends["hero"], "physical_root_cm"), vector(idle_ends["0"], "physical_root_cm"))
    vector_error = distance3d(idle_vectors["hero"], idle_vectors["0"])
    paired_sample_errors = {str(second): distance3d(
        vector(result["actors"]["hero"]["idle_only"]["samples"][str(second)], "physical_root_cm"),
        vector(result["actors"]["0"]["idle_only"]["samples"][str(second)], "physical_root_cm"))
        for second in (0, 1, 2, 3)}
    idle_maximum = {actor: result["actors"][actor]["idle_only"]["trajectory"]["physical_root_cm"]["max_3d_from_first_cm"]
                    for actor in ("hero", "0")}
    controllers = {actor: result["actors"][actor]["idle_only"]["controller"] for actor in ("hero", "0")}
    all_cv = all(value["cv_active_all_frames"] and value["cv_diagnostics_valid_all_frames"] for value in controllers.values())
    zero_requests = all(value["all_cv_requests_and_animation_root_zero"] for value in controllers.values())
    support_ok = not any(result["actors"][actor]["idle_only"]["support"]["support_issue_detected"] for actor in ("hero", "0"))
    equal_placement_and_vectors = max(start_error, end_error, vector_error, *paired_sample_errors.values()) <= 1
    proof = dict(start_3d_error_cm=start_error, end_3d_error_cm=end_error, idle_vectors_cm=idle_vectors,
                 idle_vector_3d_error_cm=vector_error, paired_sample_3d_errors_cm=paired_sample_errors,
                 maximum_idle_3d_motion_cm=idle_maximum, same_placement_and_motion_within_1cm=equal_placement_and_vectors,
                 both_cv_active_and_valid_all_frames=all_cv,
                 both_zero_cv_requests_and_animation_root_all_frames=zero_requests,
                 both_supported_without_detected_loss=support_ok)
    result["comparison"]["idle_validation"] = proof
    classification = "unclassified"
    if all(value <= 1 for value in idle_maximum.values()) and equal_placement_and_vectors:
        classification = "stationary"
        result["status"] = "passed"
    elif (all(value > 1 for value in idle_maximum.values()) and equal_placement_and_vectors
          and all_cv and zero_requests and support_ok):
        classification = "shared_character_virtual_passive_drift"
        result["status"] = "passed"
        result["controller_limitation"] = dict(classification=classification, idle_validation=proof,
            interpretation="Both controllers drift from the same idle placement with zero native CV requests and animation root; physical immobility is not satisfied.")
    result["classification"] = classification
    result["comparison"]["classification"] = classification
    result["comparison"]["interpretation"] = (
        "Hero uses natural PlayerControl key release; NPC uses the executor's deadman release. "
        "Early distances may differ during the hero's uninterruptible run-entry animation. "
        "Late windows retain native CV velocity/ground data, and idle-only runs start from "
        "the same authored placement without running first. Only the explicit idle validation "
        "above can classify shared passive CV drift; an unmatched result stays unclassified."
    )
    return result


def head(evidence):
    snapshots = {row["tag"]: row for row in jsonl(evidence.output / "keys-eye-snapshots.jsonl")}
    result = evidence.metrics
    result["views"] = {}
    walks = {}
    for yaw in (0, -60, 60):
        tag = f"head-{yaw}"
        if tag not in snapshots:
            raise EvidenceError(f"Missing eye snapshot {tag}")
        snapshot = snapshots[tag]
        if len(snapshot["rays"]) < 250:
            raise EvidenceError(f"Incomplete navigation eye snapshot: {len(snapshot['rays'])} rays")
        require(snapshot["animated_head"], f"{tag}: eye snapshot does not use animated head")
        start, end = evidence.sample(f"{tag}-start"), evidence.sample(f"{tag}-end")
        movement = displacement(start, end)
        walks[yaw] = movement
        distance = horizontal(movement, (0, 0, 0))
        body_change = abs(angle_delta(end["body_yaw_deg"], start["body_yaw_deg"]))
        view = vector(snapshot, "forward")
        result["views"][str(yaw)] = dict(forward=view, body_yaw_deg=start["body_yaw_deg"],
            body_change_deg=body_change, walk_cm=distance, ray_count=len(snapshot["rays"]),
            origin_cm=vector(snapshot, "origin_cm"))
        require(distance > 100, f"{tag}: no forward walking: {distance:.2f} cm")
        require(body_change <= 1, f"{tag}: looking turned body by {body_change:.2f} degrees")
    baseline = result["views"]["0"]["forward"]
    baseline_angle = math.degrees(math.atan2(baseline[2], baseline[0]))
    for yaw in (-60, 60):
        view = result["views"][str(yaw)]
        angle = math.degrees(math.atan2(view["forward"][2], view["forward"][0]))
        delta = angle_delta(angle, baseline_angle)
        view["eye_rotation_from_neutral_deg"] = delta
        error = horizontal(walks[yaw], walks[0]) / horizontal(walks[0], (0, 0, 0))
        view["walk_vector_relative_error"] = error
        require(45 <= abs(delta) <= 75, f"Head {yaw}: eye did not follow ±60-degree head: {delta:.2f} degrees")
        require(error <= .1, f"Head {yaw}: walking direction changed >10%: {error:.4f}")
        # Compare an identical ray index: rotating only snapshot metadata cannot
        # satisfy this check while leaving the actual navigation ray fan behind.
        rays = snapshots["head-0"]["rays"]
        center_index = min(range(len(rays)), key=lambda index: rays[index]["yaw_deg"] ** 2 + rays[index]["pitch_deg"] ** 2)
        a = vector(rays[center_index], "direction")
        b = vector(snapshots[f"head-{yaw}"]["rays"][center_index], "direction")
        ray_delta = angle_delta(math.degrees(math.atan2(b[2], b[0])), math.degrees(math.atan2(a[2], a[0])))
        view["ray_rotation_from_neutral_deg"] = ray_delta
        require(abs(angle_delta(ray_delta, delta)) <= 5, f"Head {yaw}: actual eye rays did not follow head")
    require(result["views"]["-60"]["eye_rotation_from_neutral_deg"] *
            result["views"]["60"]["eye_rotation_from_neutral_deg"] < 0, "Eye failed opposite head directions")
    return result


def stress(evidence):
    endings = [row for row in evidence.events if row.get("kind") == "keys_stress_end"]
    if len(endings) != 1:
        raise EvidenceError("Missing completed stress span/profile")
    result = evidence.metrics
    result.update(endings[0])
    start_time = evidence.samples["stress-start"]["game_ms"]
    end_time = evidence.samples["stress-end"]["game_ms"]
    result["actor_support"] = {}
    support_issues = []
    for actor in map(str, range(10)):
        frames = [frame for frame in evidence.frames("stress", actor)
                  if start_time <= frame["game_ms"] <= end_time]
        diagnostics = support_metrics(frames)
        result["actor_support"][actor] = diagnostics
        if diagnostics["support_issue_detected"]:
            support_issues.append(dict(actor=actor, first=diagnostics["first_support_issue"]))
    result["actors_with_support_issues"] = support_issues
    require(result["actors"] == 10 and result["elapsed_ms"] >= 60000, f"Incomplete ten-NPC 60-second span: {result}")
    require(result["invalid_roots"] == 0, f"Stress invalid roots: {result}")
    require(len(result["travel_cm"]) == 10 and min(result["travel_cm"]) > 50,
            f"Some stress actors never moved: {result['travel_cm']}")
    require(result["executor_ticks"] >= 1000 and result["executor_mean_us"] > 0, f"Missing executor timing: {result}")
    require(result["partial_peer_ticks"] > 0 and result["peer_checks"] > 0,
            "No evidence of sequential NPC ticks observing an already-ticked peer")
    for actor in map(str, range(10)):
        final = evidence.sample("stress-expired", actor)
        require(not final["has_command"] and final["keys_enabled"], f"Stress TTL failed on actor {actor}")
    result["partial_peer_fraction"] = result["partial_peer_ticks"] / result["executor_ticks"]
    requested = result.get("requested_motion_frames", 0)
    if requested:
        result["still_motion_fraction"] = result["still_motion_frames"] / requested
    # Random keys deliberately reverse and change animation before a step. The
    # still-frame rate is diagnostic; it cannot attribute a stall to tick order.
    result["tick_order_interpretation"] = (
        "Sequential updates expose some already-started peer actions. All ten actors moved; "
        "requested/still-motion and near-peer counts include animation starts and ordinary contacts. "
        "Travel can include falls, so support evidence is checked separately. "
        "This run is not a causal comparison with synchronous decisions."
    )
    if support_issues:
        raise Limitation("world_support_loss", f"Static support loss detected in stress actors: {support_issues}")
    return result


def void(evidence, case="void"):
    result = evidence.metrics
    result.update(status="observed", measurement_only=True, actors={},
                  approach_gait={"void": "walk", "void-jump": "run+jump", "void-replay": "recorded stress tail"}[case],
                  intended_probe_cm=[6754, 262, -6115])
    starts = {actor: evidence.sample(f"{actor}-start", actor) for actor in ("hero", "0")}
    if distance3d(vector(starts["hero"], "physical_root_cm"), vector(starts["0"], "physical_root_cm")) > 5:
        raise EvidenceError("Void comparison actors did not start at the same physical root")
    if abs(angle_delta(starts["hero"]["body_yaw_deg"], starts["0"]["body_yaw_deg"])) > 1:
        raise EvidenceError("Void comparison actors did not face the same approach")
    support_issues = []
    for actor in ("hero", "0"):
        end = evidence.sample(f"{actor}-end", actor)
        frames = [frame for frame in evidence.frames(f"{actor}-{case}", actor)
                  if starts[actor]["game_ms"] <= frame["game_ms"] <= end["game_ms"]]
        diagnostics = support_metrics(frames)
        probe = result["intended_probe_cm"]
        closest = min(frames, key=lambda frame: horizontal(vector(frame, "physical_root_cm"), probe))
        actor_result = dict(start=diagnostic_snapshot(starts[actor]), end=diagnostic_snapshot(end),
                            diagnostics=diagnostics,
                            minimum_distance_to_probe_cm=horizontal(vector(closest, "physical_root_cm"), probe),
                            closest_to_probe=diagnostic_snapshot(closest))
        result["actors"][actor] = actor_result
        actor_result["reached_recorded_gap_within_40cm"] = actor_result["minimum_distance_to_probe_cm"] <= 40
        require(diagnostics["positions"]["physical_root_cm"]["max_horizontal_from_first_cm"] > 1,
                f"Void approach {actor} never moved")
        if actor == "0":
            require(end["keys_enabled"] and not end["has_command"], "Void input did not expire")
        if diagnostics["support_issue_detected"]:
            support_issues.append(dict(actor=actor, first=diagnostics["first_support_issue"]))
    result["actors_with_support_issues"] = support_issues
    if support_issues:
        raise Limitation("world_support_loss", f"Same-start input {case} exposes missing stock-world support: {support_issues}")
    return result


def analyze(case, output, mode, allow_related=False):
    events_path = output / "events.jsonl"
    if events_path.is_file():
        events = jsonl(events_path)
        failures = [event for event in events if event.get("kind") == "keys_error"]
        if not failures:
            failures = [event for event in events if event.get("kind") == "ragdoll_error"
                        and "keys" in str(event.get("reason", "")).lower()]
        if failures:
            reasons = "; ".join(str(event.get("reason", event)) for event in failures)
            raise EvidenceError(f"Keys fixture preparation failed: {reasons}; see {events_path}")
    log = output / "bench.log"
    if not log.is_file():
        raise EvidenceError(f"Missing bench log: {log}")
    text = log.read_text(encoding="utf-8", errors="replace")
    bad = re.search(r"unknown (?:keys|command)|keys (?:failed|error)|keys:.*(?:failed|error)", text, re.I)
    if bad:
        raise EvidenceError(f"Bench preparation rejected a command: {bad.group(0)}; see {log}")
    evidence = Evidence(output)
    evidence.allow_related = allow_related
    actors = [item for row in evidence.rows for item in row["actors"] if item["actor"] != "hero"]
    if not actors or any(bool(actor["npc_cv_flag"]) != bool(mode) for actor in actors):
        raise EvidenceError(f"NPC controller flag does not match requested mode {mode}")
    actual_cv_samples = sum(bool(actor["cv_active"]) for actor in actors)
    walking = [actor for actor in actors if (actor["forward"] or actor["strafe"]) and not actor["air"] and not actor["falling"]]
    walking_cv_samples = sum(bool(actor["cv_active"]) for actor in walking)
    if case in ("parity", "jump", "climb", "ttl", "ttl-compare", "void", "void-jump", "void-replay") and bool(walking_cv_samples) != bool(mode):
        raise EvidenceError(f"Requested mode {mode} did not exercise corresponding NPC controller; active samples={actual_cv_samples}")
    result = {"status": "passed", "output": str(output), "fixture": evidence.setup,
              "mode": mode, "npc_cv_active_samples": actual_cv_samples,
              "npc_walking_samples": len(walking), "npc_walking_cv_active_samples": walking_cv_samples}
    analyzers = {"parity": parity, "ttl": ttl, "ttl-compare": ttl_compare, "head": head,
                 "stress": stress, "void": void, "void-jump": lambda evidence: void(evidence, "void-jump"),
                 "void-replay": lambda evidence: void(evidence, "void-replay")}
    try:
        result.update(analyzers[case](evidence) if case in analyzers else obstacle(evidence, case))
    except Limitation as exc:
        result.update(evidence.metrics)
        result.update(status="limitation", classification=exc.classification, error=str(exc))
        write_json(output / "result.json", result)
        return result
    except (CheckFailed, EvidenceError) as exc:
        result.update(evidence.metrics)
        result.update(status="failed" if isinstance(exc, CheckFailed) else "incomplete", error=str(exc))
        write_json(output / "result.json", result)
        raise
    write_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--ini", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path.home() / "gothic-ai/body-simulation.lock")
    parser.add_argument("--mode", choices=("0", "1", "all"), default="all")
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    parser.add_argument("--seed", type=int, default=73013)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--lock-timeout", type=float, default=1800)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--generate-only", action="store_true")
    action.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    if args.seed < 0 or args.seed >= 2 ** 32:
        parser.error("--seed must be uint32")
    for name in ("runner", "assets", "save", "ini", "output", "lock"):
        value = getattr(args, name)
        if value is not None:
            if not value.is_absolute():
                parser.error(f"--{name} must be an absolute path")
            setattr(args, name, value.resolve())
    if not args.generate_only and not args.analyze_only:
        if sys.platform != "linux":
            parser.error("Runner checks must run on Ryzen/Linux")
        for name in ("runner", "assets", "save"):
            value = getattr(args, name)
            if value is None or not value.exists():
                parser.error(f"--{name} must reference an existing path")
        if not os.access(args.runner, os.X_OK):
            parser.error("--runner is not executable")
    if args.ini is not None and not args.ini.is_file():
        parser.error("--ini must reference an existing file")
    args.output.mkdir(parents=True, exist_ok=True)
    summary_path = args.output / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"seed": args.seed, "cases": {}}
    if summary["seed"] != args.seed:
        parser.error("Existing summary has another seed; use a fresh --output")
    modes = (0, 1) if args.mode == "all" else (int(args.mode),)
    cases = CASES if args.case == "all" else (args.case,)
    found_limitation = False
    for mode in modes:
        for case in cases:
            key = f"mode-{mode}/{case}"
            try:
                summary["cases"][key] = run_case(args, mode, case)
            except (EvidenceError, CheckFailed) as exc:
                summary["cases"][key] = {"status": "failed" if isinstance(exc, CheckFailed) else "incomplete",
                                          "error": str(exc), "output": str(args.output / key)}
                write_json(summary_path, summary)
                raise
            write_json(summary_path, summary)
            print(f"{key}: {summary['cases'][key]['status']}", flush=True)
            found_limitation |= summary["cases"][key]["status"] == "limitation"
    if found_limitation:
        print(f"Completed with recorded limitations; see {summary_path}", file=sys.stderr)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
