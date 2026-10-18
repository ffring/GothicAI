#!/usr/bin/env python3
"""Compare recorded road guidance with mesh corners and teacher turn labels.

Read-only analysis on Ryzen; no engine, policy or training imports. The original
BC index selects training pairs, but every successful teacher episode of those
pairs is read through nav_demos.iter_chunks(). All round-one DAgger drives are
read, including failures and control. A shared flock is released between small
episode batches. Output is aggregate numeric evidence, never sensor recordings.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nav_demos as demos


EPS = 1e-9


def world_to_body(delta, yaw_deg):
    """navdemos.cpp local(): body x=right, y=up, z=forward; yaw0 faces world+X."""
    delta = np.asarray(delta, dtype=np.float64)
    yaw = np.deg2rad(np.asarray(yaw_deg, dtype=np.float64))
    c, s = np.cos(yaw), np.sin(yaw)
    x, y, z = np.moveaxis(delta, -1, 0)
    return np.stack((-x * s + z * c, y, x * c + z * s), axis=-1)


def wrapped_delta(a, b):
    return (np.asarray(a) - np.asarray(b) + 180.) % 360. - 180.


def turn_rule(angles_deg):
    angle = np.asarray(angles_deg)
    return np.where(angle > 10., -1, np.where(angle < -10., 1, 0)).astype(np.int8)


@dataclass
class EpisodeStats:
    steps: int = 0
    valid_rule_steps: int = 0
    angles_3d: np.ndarray = field(default_factory=lambda: np.empty(0))
    angles_xz: np.ndarray = field(default_factory=lambda: np.empty(0))
    confusion_roads: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), np.int64))
    confusion_corner: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), np.int64))
    adjacent_pairs: int = 0
    adjacent_pairs_3d: int = 0
    road_jumps_3d: int = 0
    road_jumps_3d_near_goal: int = 0
    road_jumps: int = 0
    road_jumps_near_goal: int = 0
    world_road_jumps: int = 0
    world_road_jumps_near_goal: int = 0
    adjacent_pairs_near_goal: int = 0


def analyze_records(records, previous=None):
    """One chunk; previous is the preceding raw record of THIS episode only."""
    roads = records['roads']['next_direction'].astype(np.float64)
    delta = (records['teacher_hint']['next_corner_cm'].astype(np.float64) -
             records['position_cm'].astype(np.float64))
    yaw = records['body_yaw_deg'].astype(np.float64)
    corner = world_to_body(delta, yaw)
    goal_distance = records['roads']['goal_distance_cm']
    truth = records['keys']['turn']
    for value in (roads, corner, yaw, goal_distance, truth):
        if not np.isfinite(value).all():
            raise ValueError('non-finite guidance, pose, goal distance or label')
    rnorm, cnorm = np.linalg.norm(roads, axis=1), np.linalg.norm(corner, axis=1)
    valid3 = (rnorm > EPS) & (cnorm > EPS)
    dot = np.einsum('ij,ij->i', roads[valid3], corner[valid3])
    angles3 = np.degrees(np.arccos(np.clip(dot / (rnorm[valid3] * cnorm[valid3]), -1, 1)))
    road_valid = np.hypot(roads[:, 0], roads[:, 2]) > EPS
    valid_xz = road_valid & (np.hypot(corner[:, 0], corner[:, 2]) > EPS)
    road_angle = np.degrees(np.arctan2(roads[:, 0], roads[:, 2]))
    corner_angle = np.degrees(np.arctan2(corner[:, 0], corner[:, 2]))
    true_class = np.sign(truth[valid_xz]).astype(np.int64) + 1
    confusions = []
    for angle in (road_angle, corner_angle):
        predicted = turn_rule(angle[valid_xz]).astype(np.int64) + 1
        confusions.append(np.bincount(true_class * 3 + predicted, minlength=9).reshape(3, 3))

    times = records['time_ms'].astype(np.int64)
    jump_roads = roads
    if previous is not None:
        previous = np.asarray(previous).reshape(-1)[-1]
        old_road = previous['roads']['next_direction'].astype(np.float64)
        jump_roads = np.vstack((old_road, roads))
        road_angle = np.r_[np.degrees(np.arctan2(old_road[0], old_road[2])), road_angle]
        road_valid = np.r_[np.hypot(old_road[0], old_road[2]) > EPS, road_valid]
        yaw = np.r_[previous['body_yaw_deg'], yaw]
        times = np.r_[int(previous['time_ms']), times]
        goal_distance = np.r_[previous['roads']['goal_distance_cm'], goal_distance]
    adjacent = (np.diff(times) == demos.PERIOD_MS) & road_valid[:-1] & road_valid[1:]
    jump_norms = np.linalg.norm(jump_roads, axis=1)
    valid_jump3 = jump_norms > EPS
    adjacent3 = (np.diff(times) == demos.PERIOD_MS) & valid_jump3[:-1] & valid_jump3[1:]
    # For nonzero vectors, dot<0 is exactly a 3D angle greater than 90 degrees.
    jumps3 = (np.einsum('ij,ij->i', jump_roads[:-1], jump_roads[1:]) < 0) & adjacent3
    near = goal_distance[1:] <= 500.  # Location of the later decision, 3D Euclidean distance.
    jumps = (np.abs(wrapped_delta(road_angle[1:], road_angle[:-1])) > 90.) & adjacent
    # Control for changes caused by rotating the body's coordinate frame itself.
    world_angle = road_angle + yaw
    world_jumps = (np.abs(wrapped_delta(world_angle[1:], world_angle[:-1])) > 90.) & adjacent
    return EpisodeStats(
        steps=len(records), valid_rule_steps=int(valid_xz.sum()), angles_3d=angles3,
        angles_xz=np.abs(wrapped_delta(
            np.degrees(np.arctan2(roads[valid_xz, 0], roads[valid_xz, 2])),
            corner_angle[valid_xz])),
        confusion_roads=confusions[0], confusion_corner=confusions[1],
        adjacent_pairs=int(adjacent.sum()), adjacent_pairs_3d=int(adjacent3.sum()),
        road_jumps_3d=int(jumps3.sum()), road_jumps_3d_near_goal=int((jumps3 & near).sum()),
        road_jumps=int(jumps.sum()),
        road_jumps_near_goal=int((jumps & near).sum()),
        world_road_jumps=int(world_jumps.sum()),
        world_road_jumps_near_goal=int((world_jumps & near).sum()),
        adjacent_pairs_near_goal=int((adjacent & near).sum()),
    )


def merge_stats(parts):
    result = EpisodeStats()
    for name in ('steps', 'valid_rule_steps', 'adjacent_pairs', 'adjacent_pairs_3d',
                 'road_jumps_3d', 'road_jumps_3d_near_goal', 'road_jumps',
                 'road_jumps_near_goal', 'world_road_jumps',
                 'world_road_jumps_near_goal', 'adjacent_pairs_near_goal'):
        setattr(result, name, sum(getattr(p, name) for p in parts))
    for name in ('confusion_roads', 'confusion_corner'):
        setattr(result, name, sum((getattr(p, name) for p in parts), np.zeros((3, 3), np.int64)))
    for name in ('angles_3d', 'angles_xz'):
        setattr(result, name, np.concatenate([getattr(p, name) for p in parts]) if parts else np.empty(0))
    return result


def ratio(n, d):
    return float(n / d) if d else None


def angle_summary(values):
    return dict(steps=len(values), median_deg=float(np.median(values)) if len(values) else None,
                gt45_count=int((values > 45).sum()), gt45_fraction=ratio((values > 45).sum(), len(values)),
                gt90_count=int((values > 90).sum()), gt90_fraction=ratio((values > 90).sum(), len(values)))


def rule_summary(cm):
    support = cm.sum(axis=1)
    recall = np.divide(cm.diagonal(), support, out=np.zeros(3), where=support > 0)
    return dict(steps=int(cm.sum()), accuracy=ratio(cm.trace(), cm.sum()),
                balanced_accuracy=float(recall[support > 0].mean()) if support.any() else None,
                turning_accuracy=ratio(cm[0, 0] + cm[2, 2], support[0] + support[2]),
                labels=[-1, 0, 1], support=support.tolist(), confusion=cm.tolist())


def summarize(parts):
    s = merge_stats(parts)
    return dict(episodes=len(parts), steps=s.steps,
                angle_3d=angle_summary(s.angles_3d), angle_xz=angle_summary(s.angles_xz),
                undefined_3d_steps=s.steps - len(s.angles_3d),
                undefined_xz_steps=s.steps - s.valid_rule_steps,
                adjacent_pairs=s.adjacent_pairs, adjacent_pairs_near_goal=s.adjacent_pairs_near_goal,
                adjacent_pairs_3d=s.adjacent_pairs_3d, road_jumps_3d=s.road_jumps_3d,
                road_jump_3d_fraction=ratio(s.road_jumps_3d, s.adjacent_pairs_3d),
                road_jumps_3d_near_goal=s.road_jumps_3d_near_goal,
                road_jumps=s.road_jumps, road_jump_fraction=ratio(s.road_jumps, s.adjacent_pairs),
                road_jumps_near_goal=s.road_jumps_near_goal,
                near_goal_share_of_jumps=ratio(s.road_jumps_near_goal, s.road_jumps),
                world_road_jumps=s.world_road_jumps,
                world_road_jumps_near_goal=s.world_road_jumps_near_goal,
                roads_rule=rule_summary(s.confusion_roads), corner_rule=rule_summary(s.confusion_corner))


def select_episodes(teacher_source, dagger_source, split_index):
    index = json.loads(split_index.read_text())
    split = {}
    for ep in index['episodes']:
        pair, part = tuple(ep['pair']), ep['split']
        if part not in ('train', 'validation') or (pair in split and split[pair] != part):
            raise ValueError('invalid or conflicting pair split')
        split[pair] = part
    selected, excluded = [], dict(unsuccessful_teacher=0, nontraining_teacher=0)
    for path in demos.episode_paths(teacher_source):
        meta = json.loads((path.parent / 'episode.json').read_text())
        if meta.get('success') is not True:
            excluded['unsuccessful_teacher'] += 1
        elif split.get((meta['start'], meta['goal'])) != 'train':
            excluded['nontraining_teacher'] += 1
        else:
            if meta.get('drive'):
                raise ValueError(f'teacher source contains a learner drive: {path}')
            selected.append(('teacher_train', path, meta))
    teacher_count = len(selected)
    for path in demos.episode_paths(dagger_source):
        meta = json.loads((path.parent / 'episode.json').read_text())
        if meta.get('drive') is not True or meta.get('split') not in ('train', 'validation', 'control'):
            raise ValueError(f'expected a labelled DAgger drive: {path}')
        selected.append(('dagger_' + meta['split'], path, meta))
    if not teacher_count or len(selected) == teacher_count:
        raise ValueError('both successful training demonstrations and DAgger drives are required')
    return selected, excluded


@contextmanager
def shared_lock(path):
    import fcntl
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('teacher-source', 'dagger-source', 'split-index', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--lock', type=Path, default=Path.home() / 'gothic-ai/body-simulation.lock')
    parser.add_argument('--batch-size', type=int, default=4)
    parser.add_argument('--chunk-records', type=int, default=32)
    parser.add_argument('--pause-seconds', type=float, default=1.)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('run the recorded-data analysis on Ryzen/Linux')
    for name, value in vars(args).items():
        if isinstance(value, Path):
            value = value.expanduser()
            if not value.is_absolute():
                parser.error(f'--{name.replace("_", "-")} must be absolute')
            setattr(args, name, value.resolve())
    if args.batch_size < 1 or args.chunk_records < 1 or args.pause_seconds < 0:
        parser.error('positive batch/chunk size and nonnegative pause required')
    for root in (args.teacher_source, args.dagger_source):
        if args.output == root or root in args.output.parents:
            parser.error('output must be outside the read-only recordings')
    selected, excluded = select_episodes(args.teacher_source, args.dagger_source, args.split_index)
    groups = {name: [] for name in ('teacher_train', 'dagger_all', 'dagger_train',
                                   'dagger_validation', 'dagger_control')}
    for start in range(0, len(selected), args.batch_size):
        batch = selected[start:start + args.batch_size]
        print(f'waiting for shared lock: episodes {start + 1}-{start + len(batch)}/{len(selected)}', flush=True)
        with shared_lock(args.lock):
            for group, path, meta in batch:
                chunks, previous = [], None
                for records in demos.iter_chunks(path, args.chunk_records):
                    chunks.append(analyze_records(records, previous))
                    previous = records[-1].copy()
                stats = merge_stats(chunks)
                if not stats.steps or stats.steps != meta['decisions']:
                    raise ValueError(f'episode decision count mismatch: {path}')
                groups[group].append(stats)
                if group.startswith('dagger_'):
                    groups['dagger_all'].append(stats)
                print(f'{group} {path.parent.name}: {stats.steps} decisions', flush=True)
        if start + len(batch) < len(selected):
            time.sleep(args.pause_seconds)
    with shared_lock(args.lock):
        result = dict(
            method=dict(angle='3D arccos and horizontal absolute wrapped bearing difference; zero directions excluded',
                        turn_rule='-sign(atan2(body_x,body_z)) outside 10 degrees; keys.turn sign is truth',
                        rule_denominator='common valid horizontal directions, pooled decisions',
                        jump='wrapped body-frame bearing difference >90 deg; within-episode consecutive 100ms decisions',
                        jump_3d='3D body-frame angle >90 deg; within-episode consecutive 100ms nonzero vectors',
                        near_goal='later decision roads.goal_distance_cm <=500; 3D Euclidean, not path remaining',
                        world_jump='body bearing + body_yaw_deg, to remove the rotating coordinate frame'),
            sources={k: str(getattr(args, k)) for k in ('teacher_source', 'dagger_source', 'split_index')},
            excluded=excluded, groups={k: summarize(v) for k, v in groups.items()},
            max_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(dict(output=str(args.output), max_rss_mib=result['max_rss_mib'])), flush=True)


if __name__ == '__main__':
    main()
