#!/usr/bin/env python3
"""Offline NAVDEMO v2 jump/climb/drop visibility, using learner_input() only.

    python3 -I scripts/nav_jump_observability.py /absolute/nav-demos \
        --metadata /absolute/routes.json --output /absolute/visibility.json \
        --csv /absolute/decisions.csv

Metadata is optional JSON (a list, or {"episodes": [...]}/{"routes": [...]}) or
CSV. Rows identify an episode by episode/directory/id, or by start and goal, and
may contain site_id, transition_id and split (train/validation/test). Existing
episode.json fields are also accepted. No split is invented: unsplit data never
fits the rule. The JSON explicitly states limitations of reconstructing ray
geometry; a visible surface sample is not a measurement of an obstacle's top.
This fits only a small descriptive threshold rule, never a navigation model.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nav_demos


INPUT_NAMES = frozenset(("body", "roads", "previous_keys", "eye", "memory_near", "memory_far"))
PARTS = ("train", "validation", "test", "unsplit")
LIMITATIONS = [
    "NAVDEMO v2 omits eye origin, head roll and cached ray poses: exact ray hit height above feet cannot be recovered.",
    "Reported eye geometry assumes zero head roll and no horizontal eye offset; only fresh rays are used.",
    "Feet height is the nearest valid remembered ground, not privileged actor position. Eye height is inferred from fresh ground rays and matching memory cells.",
    "Visible hit height is a surface sample, not an obstacle top; height is unknown when ground rays cannot anchor eye height.",
    "Movement direction uses observed horizontal velocity >=20cm/s, otherwise the carrot, otherwise body forward; current teacher keys never select input rays.",
    "Drop onset labels use future downward displacement and airborne flags, exclude recent jump/climb, and are diagnostics rather than teacher action heads.",
]


def _angle(value: np.ndarray) -> np.ndarray:
    return (value + 180) % 360 - 180


def _reference(memory: np.ndarray, cell_cm: float) -> tuple[float, bool]:
    size = memory.shape[0]
    z, x = np.indices(memory.shape)
    distance = ((z + .5 - size / 2) ** 2 + (x + .5 - size / 2) ** 2)
    valid = memory["height_valid"] != 0
    if not valid.any():
        return 0., False
    selected = np.argmin(np.where(valid, distance, np.inf))
    return float(memory["ground_height_cm"].flat[selected]), True


def ray_directions(layout: np.ndarray, head_yaw_deg: float, head_pitch_deg: float) -> np.ndarray:
    """Body x=right,y=up,z=forward; zero-roll approximation to EyePose basis.

    Compose rotations (adding pitch/yaw separately is incorrect off-axis).
    NavigationEye uses forward*cos(v)*cos(h)+up*sin(v)+right*cos(v)*sin(h).
    """
    yaw, pitch = np.deg2rad([head_yaw_deg, head_pitch_deg])
    forward = np.array([np.sin(yaw)*np.cos(pitch), np.sin(pitch), np.cos(yaw)*np.cos(pitch)])
    right = np.array([np.cos(yaw), 0., -np.sin(yaw)])
    up = np.cross(forward, right)
    h, v = np.deg2rad(np.asarray(layout)[:, :2]).T
    return (np.cos(v)*np.cos(h))[:, None]*forward + np.sin(v)[:, None]*up + (np.cos(v)*np.sin(h))[:, None]*right


def _eye_height(memory: np.ndarray, points: np.ndarray, ground: np.ndarray,
                reference: float, reference_known: bool) -> tuple[float, int, float]:
    """Anchor ray y to memory ground; do not assume a fixed 180 cm head height."""
    if not reference_known:
        return np.nan, 0, np.nan
    size = memory.shape[0]
    col = np.floor(points[:, 0] / 25 + size / 2).astype(int)
    row = np.floor(points[:, 2] / 25 + size / 2).astype(int)
    inside = ground & (col >= 0) & (col < size) & (row >= 0) & (row < size)
    ids = np.flatnonzero(inside)
    cells = memory[row[ids], col[ids]]
    ids = ids[cells["height_valid"] != 0]
    estimates = memory["ground_height_cm"][row[ids], col[ids]] - reference - points[ids, 1]
    estimates = estimates[(estimates >= 40) & (estimates <= 260)]
    if len(estimates) < 3:
        return np.nan, len(estimates), np.nan
    spread = float(np.percentile(estimates, 75) - np.percentile(estimates, 25))
    if spread > 50:
        return np.nan, len(estimates), spread
    return float(np.median(estimates)), len(estimates), spread


def _memory_ahead(memory: np.ndarray, cell_cm: float, movement_deg: float,
                  reference: float, known: bool) -> dict[str, float]:
    size = memory.shape[0]
    z, x = np.indices(memory.shape, dtype=float)
    z, x = (z + .5 - size / 2)*cell_cm, (x + .5 - size / 2)*cell_cm
    distance = np.hypot(z, x)
    ahead = (distance >= 25) & (distance <= 600) & (np.abs(_angle(np.rad2deg(np.arctan2(x, z)) - movement_deg)) <= 20)
    result = {}
    for name in ("ledge", "drop"):
        probability = memory["channels"][..., nav_demos.CHANNELS.index(name)].astype(float) / 255
        candidates = ahead & (probability > 0)
        result[name] = float(np.max(probability[ahead], initial=0))
        result[f"{name}_distance_cm"] = float(np.min(distance[candidates])) if candidates.any() else np.nan
    valid = ahead & (memory["height_valid"] != 0)
    heights = memory["ground_height_cm"][valid] - reference
    result["min_ground_height_cm"] = float(heights.min()) if known and len(heights) else np.nan
    result["max_ground_height_cm"] = float(heights.max()) if known and len(heights) else np.nan
    return result


def sensor_features(inputs: dict[str, np.ndarray], layout: np.ndarray,
                    max_ray_age_ms: int = 0) -> list[dict[str, Any]]:
    """Extract descriptive features from the strict learner boundary, never labels.

    Null/NaN heights mean unavailable geometry, never a fabricated zero height.
    Output is small: raw sensor maps are discarded after each input chunk.
    """
    if frozenset(inputs) != INPUT_NAMES:
        raise ValueError("sensor_features accepts only nav_demos.learner_input() fields")
    if inputs["roads"].dtype != nav_demos.ROADS_V2_DTYPE:
        raise ValueError("NAVDEMO v2 is required")
    layout = np.asarray(layout, dtype=float)
    if layout.shape != (nav_demos.RAY_COUNT, 3) or not np.isfinite(layout).all() or (layout[:, 2] <= 0).any():
        raise ValueError("invalid ray layout")
    if max_ray_age_ms < 0:
        raise ValueError("max_ray_age_ms must be nonnegative")
    result = []
    for i in range(len(inputs["body"])):
        body, roads, eye = inputs["body"][i], inputs["roads"][i], inputs["eye"][i]
        velocity = body["velocity_cm_s"]
        carrot = roads["carrot_direction"]
        speed = float(np.hypot(velocity[0], velocity[2]))
        direction, source = (velocity, "velocity") if speed >= 20 else (carrot, "carrot")
        if np.hypot(direction[0], direction[2]) < 1e-6:
            direction, source = np.array([0., 0., 1.]), "body_forward"
        movement = float(np.rad2deg(np.arctan2(direction[0], direction[2])))
        carrot_angle = float(np.rad2deg(np.arctan2(carrot[0], carrot[2]))) if np.hypot(carrot[0], carrot[2]) > 1e-6 else np.nan
        near, far = inputs["memory_near"][i], inputs["memory_far"][i]
        reference, known = _reference(near, 25)
        if not known:
            reference, known = _reference(far, 200)
        directions = ray_directions(layout, body["head_yaw_deg"], body["head_pitch_deg"])
        points = directions * eye["distance_cm"][:, None]
        distance = np.hypot(points[:, 0], points[:, 2])
        fresh = eye["age_ms"] <= max_ray_age_ms
        hit = fresh & (eye["hit"] != 0) & np.isin(eye["kind"], [1, 2, 3])
        ground = hit & np.isin(eye["kind"], [1, 2]) & (eye["normal_y"] >= .7) & (directions[:, 1] < -.05)
        eye_height, anchors, spread = _eye_height(near, points, ground, reference, known)
        ray_angle = np.rad2deg(np.arctan2(directions[:, 0], directions[:, 2]))
        ahead = np.abs(_angle(ray_angle - movement)) <= 15
        visible = hit & ahead & (np.abs(eye["normal_y"]) < .7)
        # Heights refer to sampled vertical surfaces, never to their unseen top.
        height = points[:, 1] + eye_height
        obstacle = visible & (height >= 25) & (height <= 300)
        nearest = int(np.argmin(np.where(obstacle, distance, np.inf))) if obstacle.any() else -1
        nearest_visible = int(np.argmin(np.where(visible, distance, np.inf))) if visible.any() else -1
        landing = hit & ahead & (eye["normal_y"] >= .7) & (height < -50)
        drop_ray = int(np.argmin(np.where(landing, distance, np.inf))) if landing.any() else -1
        row = dict(movement_deg=movement, movement_source=source, speed_cm_s=speed,
                   carrot_angle_deg=carrot_angle,
                   carrot_movement_angle_deg=float(_angle(carrot_angle - movement)),
                   fresh_rays=int(fresh.sum()), ahead_hit_rays=int((hit & ahead).sum()),
                   eye_height_proxy_cm=eye_height, eye_height_anchors=anchors,
                   eye_height_iqr_cm=spread, ground_reference_known=bool(known),
                   obstacle_ray=nearest,
                   obstacle_distance_cm=float(distance[nearest]) if nearest >= 0 else np.nan,
                   visible_hit_height_cm=float(height[nearest]) if nearest >= 0 else np.nan,
                   ahead_surface_distance_cm=float(distance[nearest_visible]) if nearest_visible >= 0 else np.nan,
                   ahead_surface_height_cm=float(height[nearest_visible]) if nearest_visible >= 0 else np.nan,
                   drop_ray=drop_ray,
                   drop_distance_cm=float(distance[drop_ray]) if drop_ray >= 0 else np.nan,
                   drop_height_cm=float(height[drop_ray]) if drop_ray >= 0 else np.nan)
        for name, memory, cell in (("near", near, 25), ("far", far, 200)):
            row.update({f"{name}_{key}": value for key, value in _memory_ahead(memory, cell, movement, reference, known).items()})
        result.append(row)
    return result


def drop_onsets(times: np.ndarray, height_cm: np.ndarray, flags: np.ndarray,
                jump: np.ndarray, climb: np.ndarray, moving: np.ndarray,
                horizon_ms: int = 800, minimum_drop_cm: float = 50) -> np.ndarray:
    """Label the last supported decision before a substantial uncommanded fall.

    World height/future body flags are used here only as outcome labels, never
    passed to sensor_features() or the rule. A slope or a held walking key alone
    is insufficient. An airborne episode prefix cannot create an onset.
    """
    count = len(times)
    result = np.zeros(count, dtype=bool)
    airborne = (flags[:, 0] != 0) | (flags[:, 1] != 0)
    for i in range(1, count):
        if not airborne[i] or airborne[i - 1] or not moving[i - 1]:
            continue
        start = np.searchsorted(times, max(0, int(times[i]) - horizon_ms))
        end = np.searchsorted(times, int(times[i]) + horizon_ms, side="right")
        if jump[start:i + 1].any() or climb[start:i + 1].any() or flags[start:i + 1, 3:].any():
            continue
        # Stop at the first landing: a later unrelated lower surface is not this fall.
        landing = np.flatnonzero(~airborne[i:end])
        if len(landing):
            end = i + int(landing[0]) + 1
        if end > i and float(height_cm[i - 1]) - float(np.min(height_cm[i:end])) >= minimum_drop_cm:
            result[i - 1] = True
    return result


def predict_rule(rows: list[dict], rule: dict | None) -> np.ndarray:
    if rule is None:
        return np.zeros(len(rows), dtype=bool)
    return np.asarray([rule["min_height_cm"] <= row["visible_hit_height_cm"] <= rule["max_height_cm"] and
                       row["obstacle_distance_cm"] < rule["distance_cm"] for row in rows], dtype=bool)


def _binary_metrics(target: np.ndarray, predicted: np.ndarray) -> dict:
    tp = int((target & predicted).sum()); fp = int((~target & predicted).sum())
    fn = int((target & ~predicted).sum()); tn = int((~target & ~predicted).sum())
    return dict(positives=tp + fn, controls=fp + tn, explained=tp, false_positives=fp,
                coverage=tp / (tp + fn) if tp + fn else None,
                walking_false_positive_rate=fp / (fp + tn) if fp + tn else None,
                precision=tp / (tp + fp) if tp + fp else None)


def fit_rule(rows: list[dict]) -> dict | None:
    """Choose height/distance thresholds on TRAIN sites only, maximizing F1.

    Walking controls penalize a rule that labels every visible wall a jump. Raw
    positive coverage, walking FPR and per-head coverage are reported separately.
    """
    training = [row for row in rows if row["split"] == "train" and (row["jump"] or row["climb"] or row["ordinary_walk"])]
    target = np.asarray([row["jump"] or row["climb"] for row in training], dtype=bool)
    if not target.any() or target.all():
        return None
    best = None
    for low in (25, 50, 75, 100):
        for high in (100, 150, 200, 250, 300):
            if high <= low:
                continue
            for distance in (75, 100, 150, 200, 300, 400, 600):
                rule = dict(min_height_cm=low, max_height_cm=high, distance_cm=distance)
                predicted = predict_rule(training, rule)
                tp = int((target & predicted).sum()); fp = int((~target & predicted).sum()); fn = int((target & ~predicted).sum())
                score = (2*tp / (2*tp + fp + fn) if 2*tp + fp + fn else 0, -fp, -distance, low, -high)
                if best is None or score > best[0]:
                    best = (score, rule)
    return {**best[1], "fit_split": "train", "objective": "F1 against ordinary walking", "train_f1": best[0][0]}


def _number_summary(rows: list[dict], name: str) -> dict:
    values = np.asarray([row[name] for row in rows], dtype=float)
    values = values[np.isfinite(values)]
    return dict(known=len(values), median=float(np.median(values)) if len(values) else None,
                p10=float(np.percentile(values, 10)) if len(values) else None,
                p90=float(np.percentile(values, 90)) if len(values) else None)


def summarize(rows: list[dict], rule: dict | None) -> dict:
    result = {}
    metrics = ("obstacle_distance_cm", "visible_hit_height_cm", "ahead_surface_distance_cm", "ahead_surface_height_cm", "drop_distance_cm", "drop_height_cm",
               "near_ledge", "near_drop", "far_ledge", "far_drop", "carrot_angle_deg", "carrot_movement_angle_deg")
    for split in (*PARTS, "all"):
        selected = [row for row in rows if split == "all" or row["split"] == split]
        grouped = {}
        for label in ("jump", "climb", "drop_onset", "ordinary_walk"):
            group = [row for row in selected if row[label]]
            prediction = predict_rule(group, rule)
            grouped[label] = dict(decisions=len(group), rule_matches=int(prediction.sum()),
                                  rule_coverage=float(prediction.mean()) if len(group) and rule else None,
                                  features={name: _number_summary(group, name) for name in metrics})
        controls = [row for row in selected if row["jump"] or row["climb"] or row["ordinary_walk"]]
        missed = [row for row in selected if (row["jump"] or row["climb"]) and
                  not predict_rule([row], rule)[0]] if rule else []
        reasons = dict(no_fresh_direction_hit=0, no_eye_height_anchor=0,
                       no_vertical_surface_in_direction=0, no_surface_sample_25_to_300cm=0,
                       outside_fitted_height_or_distance=0)
        for row in missed:
            if not row["ahead_hit_rays"]:
                reason = "no_fresh_direction_hit"
            elif not np.isfinite(row["eye_height_proxy_cm"]):
                reason = "no_eye_height_anchor"
            elif not np.isfinite(row["ahead_surface_distance_cm"]):
                reason = "no_vertical_surface_in_direction"
            elif not np.isfinite(row["visible_hit_height_cm"]):
                reason = "no_surface_sample_25_to_300cm"
            else:
                reason = "outside_fitted_height_or_distance"
            reasons[reason] += 1
        result[split] = dict(decisions=len(selected), missed_jump_climb_reasons=reasons,
                             rule=_binary_metrics(np.asarray([row["jump"] or row["climb"] for row in controls], dtype=bool), predict_rule(controls, rule)) if rule else None,
                             groups=grouped)
    return result


def _metadata_rows(path: Path | None) -> list[dict]:
    if path is None:
        return []
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("episodes", value.get("routes"))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("metadata must contain episode/route rows")
    return value


def _episode_metadata(samples: Path, external: list[dict]) -> dict:
    original = json.loads((samples.parent / "episode.json").read_text(encoding="utf-8"))
    names, pairs = [], []
    identities = {str(samples), str(samples.parent), samples.parent.name}
    for row in external:
        identity = row.get("episode", row.get("directory", row.get("id")))
        by_name = identity is not None and str(identity) in identities
        by_pair = row.get("start") == original.get("start") and row.get("goal") == original.get("goal") and row.get("start") is not None
        if by_name:
            names.append(row)
        elif by_pair:
            pairs.append(row)
    matches = names or pairs
    if len(matches) > 1:
        raise ValueError(f"ambiguous metadata for {samples}")
    metadata = {**original, **(matches[0] if matches else {})}
    part = metadata.get("split", "unsplit") or "unsplit"
    part = "validation" if part == "val" else part
    if part not in PARTS:
        raise ValueError(f"unsupported split {part!r} in {samples}")
    return dict(split=part, site_id=str(metadata.get("site_id", metadata.get("site", "")) or ""),
                transition_id=str(metadata.get("transition_id", metadata.get("transition", "")) or ""),
                region=str(metadata.get("region", "unknown")), success=metadata.get("success"))


def analyze(paths: list[Path], metadata_path: Path | None = None, chunk_records: int = 32,
            max_input_gb: float = 10, max_ray_age_ms: int = 0) -> tuple[dict, list[dict]]:
    files = []
    for path in paths:
        found = nav_demos.episode_paths(path)
        if not found:
            raise ValueError(f"no NAVDEMO recordings in {path}")
        files.extend(file.resolve() for file in found)
    if len(files) != len(set(files)):
        raise ValueError("duplicate recording source")
    size = sum(file.stat().st_size for file in files)
    if max_input_gb <= 0 or size > max_input_gb*1024**3:
        raise ValueError(f"compressed recordings exceed {max_input_gb:g} GiB input budget")
    external = _metadata_rows(metadata_path)
    episodes = [(file, _episode_metadata(file, external)) for file in files]
    owners = {}
    for file, metadata in episodes:
        for key in ("site_id", "transition_id"):
            if metadata[key]:
                identity = (key, metadata[key])
                if identity in owners and owners[identity] != metadata["split"]:
                    raise ValueError(f"{key} {metadata[key]!r} crosses splits")
                owners[identity] = metadata["split"]
    rows = []
    for samples, metadata in episodes:
        if nav_demos.episode_version(samples) != 2:
            raise ValueError(f"NAVDEMO v2 required: {samples}")
        layout = nav_demos.ray_layout(samples)
        episode_rows, times, heights, flags, jumps, climbs, moving = [], [], [], [], [], [], []
        for records in nav_demos.iter_chunks(samples, chunk_records):
            features = sensor_features(nav_demos.learner_input(records), layout, max_ray_age_ms)
            for i, feature in enumerate(features):
                keys = records["keys"][i]
                move = bool(keys["forward"] != 0 or keys["strafe"] != 0)
                episode_rows.append(dict(episode=str(samples.parent), decision=len(episode_rows), time_ms=int(records["time_ms"][i]),
                                         **metadata, **feature, jump=bool(keys["jump"]), climb=bool(keys["climb"]),
                                         jump_onset=False, climb_onset=False, drop_onset=False,
                                         ordinary_walk=move and not (keys["jump"] or keys["climb"]) and not records["body"]["flags"][i].any()))
                times.append(int(records["time_ms"][i])); heights.append(float(records["position_cm"][i, 1]))
                flags.append(records["body"]["flags"][i].copy())
                jumps.append(bool(keys["jump"])); climbs.append(bool(keys["climb"])); moving.append(move)
        if not episode_rows:
            raise ValueError(f"empty recording: {samples}")
        times_array = np.asarray(times, dtype=np.int64)
        drops = drop_onsets(times_array, np.asarray(heights), np.asarray(flags), np.asarray(jumps), np.asarray(climbs), np.asarray(moving))
        drop_times = times_array[drops]
        for i, row in enumerate(episode_rows):
            row["drop_onset"] = bool(drops[i])
            row["jump_onset"] = jumps[i] and (i == 0 or not jumps[i - 1])
            row["climb_onset"] = climbs[i] and (i == 0 or not climbs[i - 1])
            # Pre-fall approach decisions are not ordinary walking controls.
            if np.any(np.abs(drop_times - times_array[i]) <= 800):
                row["ordinary_walk"] = False
        rows.extend(episode_rows)
    rule = fit_rule(rows)
    limitations = list(LIMITATIONS)
    if any(not metadata["site_id"] for _, metadata in episodes):
        limitations.append("Some episodes lack site_id: transition identity alone cannot verify the required 30m spatial split separation.")
    if any(metadata["split"] == "unsplit" for _, metadata in episodes):
        limitations.append("Unsplit episodes are descriptive only and never used to fit thresholds or claim held-out coverage.")
    if rule is None:
        limitations.append("No rule fitted: train requires both jump/climb labels and ordinary walking controls.")
    result = dict(navdemo_version=2, episodes=len(episodes), input_bytes=size, decisions=len(rows),
                  rule=rule, limitations=limitations,
                  counts={name: sum(bool(row[name]) for row in rows) for name in ("jump", "climb", "jump_onset", "climb_onset", "drop_onset", "ordinary_walk")},
                  site_split_consistent=True, spatial_separation_verified=False,
                  geometry=dict(exact=False, max_ray_age_ms=max_ray_age_ms,
                                decisions_with_eye_height=sum(bool(np.isfinite(row["eye_height_proxy_cm"])) for row in rows)),
                  by_split=summarize(rows, rule))
    return result, rows


def _clean(value: Any) -> Any:
    if isinstance(value, np.generic):
        return _clean(value.item())
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--chunk-records", type=int, default=32)
    parser.add_argument("--max-input-gb", type=float, default=10)
    parser.add_argument("--max-ray-age-ms", type=int, default=0)
    args = parser.parse_args()
    if args.output.resolve() == args.csv.resolve():
        parser.error("JSON and CSV destinations must differ")
    # Outputs cannot overwrite any source artifact, including episode.json.
    for destination in (args.output, args.csv):
        for source in args.sources:
            root = source if source.is_dir() else source.parent
            if destination.resolve().is_relative_to(root.resolve()):
                parser.error("output destinations must be outside recording source directories")
        if args.metadata and destination.resolve() == args.metadata.resolve():
            parser.error("output cannot overwrite metadata")
    result, rows = analyze(args.sources, args.metadata, args.chunk_records, args.max_input_gb, args.max_ray_age_ms)
    for path in (args.output, args.csv):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with args.csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: "" if isinstance(value, float) and not np.isfinite(value) else value for key, value in row.items()} for row in rows)
    print(json.dumps(dict(episodes=result["episodes"], decisions=result["decisions"], counts=result["counts"], rule=result["rule"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
