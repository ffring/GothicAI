"""Synthetic navigation-guidance geometry and streaming checks; run on Ryzen."""

from pathlib import Path
import gzip
import json
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nav_demos
import nav_guidance
from nav_guidance import analyze_records, summarize, turn_rule, world_to_body, wrapped_delta


def bearings(degrees):
    radians = np.radians(np.asarray(degrees, dtype=np.float64))
    return np.column_stack((np.sin(radians), np.zeros_like(radians), np.cos(radians)))


def records_for(road_vectors, corner_vectors=None, *, yaw=0, goal_distance=1000, version=1):
    """Place teacher corners in world coordinates independently of the analyzer."""
    road_vectors = np.asarray(road_vectors, dtype=np.float64)
    count = len(road_vectors)
    corner_vectors = road_vectors if corner_vectors is None else np.asarray(corner_vectors, dtype=np.float64)
    yaw = np.broadcast_to(np.asarray(yaw, dtype=np.float64), (count,))
    records = np.zeros(count, dtype=nav_demos.RECORD_DTYPES[version])
    records["time_ms"] = np.arange(1, count + 1) * nav_demos.PERIOD_MS
    records["body_yaw_deg"] = yaw
    records["position_cm"] = [100, 200, -300]
    direction = 'carrot_direction' if version == 2 else 'next_direction'
    records["roads"][direction] = road_vectors
    records["roads"]["goal_distance_cm"] = goal_distance
    # Inverse of the recorder's local frame: forward is world +X at yaw zero.
    c, s = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    x, y, z = corner_vectors.T
    world_delta = np.column_stack((z * c - x * s, y, x * c + z * s))
    records["teacher_hint"]["next_corner_cm"] = records["position_cm"] + 1000 * world_delta
    return records


def test_world_to_body_cardinal_axes_preserve_height_and_vector_length():
    delta = np.tile([[2, 7, 0], [0, 7, 2]], (4, 1))
    yaw = np.repeat([0, 90, 180, -90], 2)
    expected = [[0, 7, 2], [2, 7, 0], [-2, 7, 0], [0, 7, 2],
                [0, 7, -2], [-2, 7, 0], [2, 7, 0], [0, 7, -2]]
    np.testing.assert_allclose(world_to_body(delta, yaw), expected, atol=1e-6)


def test_angles_cover_zero_right_angle_and_reverse_without_counting_invalid_vectors():
    roads = [[0, 0, 1], [1, 0, 0], [0, 0, -1], [0, 0, 0],
             [0, 0, 1], [0, 1, 0], [0, 0, 1]]
    corners = [[0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
               [0, 0, 0], [0, 0, 1], [0, 1, 0]]
    stats = analyze_records(records_for(roads, corners))
    assert stats.steps == 7
    np.testing.assert_allclose(stats.angles_3d, [0, 90, 180, 90, 90], atol=1e-5)
    np.testing.assert_allclose(stats.angles_xz, [0, 90, 180], atol=1e-5)
    assert stats.valid_rule_steps == 3
    assert stats.confusion_roads.sum() == 3
    assert stats.confusion_corner.sum() == 3


def test_turn_rule_keeps_exact_ten_degree_boundaries_neutral():
    angles = np.array([-180, -10.001, -10, 0, 10, 10.001, 180])
    np.testing.assert_array_equal(turn_rule(angles), [1, 1, 0, 0, 0, -1, -1])


def test_confusion_rows_are_teacher_turn_and_columns_are_signed_rule():
    records = records_for(bearings([20, 0, -20]), bearings([-20, 0, 20]), yaw=[0, 90, -90])
    records["keys"]["turn"] = [-.2, 0, .7]
    stats = analyze_records(records)
    np.testing.assert_array_equal(stats.confusion_roads, np.eye(3, dtype=np.int64))
    np.testing.assert_array_equal(stats.confusion_corner, np.fliplr(np.eye(3, dtype=np.int64)))
    np.testing.assert_allclose(stats.angles_xz, [40, 0, 40], atol=1e-5)
    assert stats.valid_rule_steps == 3


def test_wrapped_delta_uses_short_arc_and_half_open_degree_interval():
    actual = wrapped_delta(np.array([-179, 179, 180, -180, 540]),
                           np.array([179, -179, 0, 0, 0]))
    np.testing.assert_allclose(actual, [2, -2, -180, -180, -180], atol=1e-6)


def test_crossing_plus_minus_180_is_two_degrees_and_not_a_road_jump():
    stats = analyze_records(records_for(bearings([179, -179])))
    assert stats.adjacent_pairs == 1
    assert stats.road_jumps == 0
    assert stats.world_road_jumps == 0


def test_reverse_is_a_jump_and_later_endpoint_at_five_metres_is_near_goal():
    records = records_for(bearings([0, 180]), goal_distance=[1000, 500])
    stats = analyze_records(records)
    assert stats.adjacent_pairs == 1
    assert stats.road_jumps == 1
    assert stats.world_road_jumps == 1
    assert stats.road_jumps_near_goal == 1


def test_jump_threshold_is_strictly_more_than_ninety_degrees():
    stats = analyze_records(records_for(bearings([0, 90, 180])))
    assert stats.adjacent_pairs == 2
    assert stats.road_jumps == 0
    assert stats.world_road_jumps == 0


def test_turning_body_does_not_create_a_jump_in_world_road_direction():
    # Both commands still point world +X while the body turns by 180 degrees.
    stats = analyze_records(records_for(bearings([0, 180]), yaw=[0, 180]))
    assert stats.road_jumps == 1
    assert stats.world_road_jumps == 0


def test_3d_jumps_include_height_changes_with_unchanged_horizontal_bearing():
    stats = analyze_records(records_for([[0, 1, .1], [0, -1, .1], [0, 0, 0]],
                                         goal_distance=[1000, 500, 500]))
    assert stats.adjacent_pairs_3d == 1
    assert stats.road_jumps_3d == 1
    assert stats.road_jumps_3d_near_goal == 1
    assert stats.road_jumps == 0


@pytest.mark.parametrize("second_time", [100, 300])
def test_new_episode_or_nonconsecutive_time_does_not_compare_across_boundary(second_time):
    first = records_for(bearings([0]))
    second = records_for(bearings([180]))
    second["time_ms"] = second_time
    independent = analyze_records(second)
    disconnected = analyze_records(second, previous=first[-1])
    for stats in (independent, disconnected):
        assert stats.steps == 1
        assert stats.adjacent_pairs == 0
        assert stats.road_jumps == 0
        assert stats.road_jumps_near_goal == 0
        assert stats.world_road_jumps == 0


def test_chunk_boundary_preserves_one_comparison_and_all_episode_statistics():
    records = records_for(bearings([0, 90, -90, 0, 180]),
                          goal_distance=[1000, 1000, 500, 1000, 500])
    whole = analyze_records(records)
    first = analyze_records(records[:2])
    second = analyze_records(records[2:], previous=records[1])
    assert whole.adjacent_pairs == 4
    assert whole.road_jumps == 2
    assert whole.road_jumps_near_goal == 2
    for field in ("steps", "valid_rule_steps", "adjacent_pairs", "adjacent_pairs_3d",
                  "road_jumps_3d", "road_jumps_3d_near_goal", "road_jumps",
                  "road_jumps_near_goal", "world_road_jumps"):
        assert getattr(first, field) + getattr(second, field) == getattr(whole, field)
    for field in ("confusion_roads", "confusion_corner"):
        np.testing.assert_array_equal(getattr(first, field) + getattr(second, field), getattr(whole, field))
    for field in ("angles_3d", "angles_xz"):
        np.testing.assert_allclose(np.concatenate((getattr(first, field), getattr(second, field))),
                                   getattr(whole, field), atol=1e-6)


def test_v2_compares_carrot_to_actual_steering_and_reports_both_turn_denominators():
    records = records_for(bearings([25, 0, -25, 0]), bearings([30, 0, -30, 0]), version=2)
    # The first turn is wrong; neutral decisions must not inflate turning_accuracy.
    records['keys']['turn'] = [1, 0, 1, 0]
    result = summarize([analyze_records(records)])
    np.testing.assert_allclose(result['angle_3d']['median_deg'], 2.5, atol=1e-5)
    assert result['roads_rule']['accuracy'] == .75
    assert result['roads_rule']['turning_accuracy'] == .5
    assert result['roads_rule']['steps'] == 4
    assert result['roads_rule']['turning_steps'] == 2
    assert result['road_jump_fraction'] == 0


def test_v2_chunk_boundary_counts_jump_once_and_episode_boundary_excludes_it():
    records = records_for(bearings([0, 120, 125]), version=2)
    chunks = [analyze_records(records[:1]), analyze_records(records[1:], previous=records[0])]
    result = summarize([nav_guidance.merge_stats(chunks)])
    assert result['road_jumps'] == 1
    assert result['adjacent_pairs'] == 2
    assert result['road_jump_fraction'] == .5
    separate = summarize([analyze_records(records[:1]), analyze_records(records[1:])])
    assert separate['road_jumps'] == 0
    assert separate['adjacent_pairs'] == 1


def test_source_cli_includes_failed_attempts_and_separates_format_versions(tmp_path, monkeypatch):
    source = tmp_path / 'episodes'
    for version, bearing, success in ((1, 0, True), (2, 180, False)):
        directory = source / f'v{version}'
        directory.mkdir(parents=True)
        records = records_for(bearings([bearing, bearing]), version=version)
        with gzip.open(directory / 'samples.bin.gz', 'wb') as stream:
            stream.write(nav_demos.HEADER.pack(nav_demos.MAGIC, version, nav_demos.RECORD_BYTES,
                                              nav_demos.RAY_COUNT, nav_demos.NEAR_SIZE,
                                              nav_demos.FAR_SIZE, nav_demos.PERIOD_MS))
            stream.write(records.tobytes())
        (directory / 'episode.json').write_text(json.dumps(dict(decisions=2, success=success)))
    output = tmp_path / 'guidance.json'
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(sys, 'argv', ['nav_guidance.py', '--source', str(source), '--output', str(output),
                                    '--no-lock', '--batch-size', '1', '--chunk-records', '1',
                                    '--pause-seconds', '0'])
    nav_guidance.main()
    result = json.loads(output.read_text())
    assert result['sources'] == {'source': str(source)}
    assert result['groups']['source_all']['episodes'] == 2
    assert result['groups']['source_all']['steps'] == 4
    assert result['groups']['source_all']['adjacent_pairs'] == 2
    assert result['groups']['source_all']['road_jumps'] == 0
    assert result['groups']['source_v1']['steps'] == 2
    assert result['groups']['source_v2']['steps'] == 2
