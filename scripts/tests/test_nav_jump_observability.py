"""Sensor privacy, ray geometry, fall labels and held-out threshold diagnostics."""
import gzip
import json
import csv
import subprocess
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nav_jump_observability as visibility

demos = visibility.nav_demos


def fixture_records(count=3):
    records = np.zeros(count, dtype=demos.RECORD_V2_DTYPE)
    records["time_ms"] = np.arange(1, count + 1)*100
    records["keys"]["forward"] = 1
    records["keys"]["walk"] = 1
    records["body"]["velocity_cm_s"][:, 2] = 100
    records["roads"]["carrot_direction"][:, 2] = 1
    layout = np.tile([0., 0., 1000.], (demos.RAY_COUNT, 1))
    layout[:3, :2] = [[-10, -45], [0, -45], [10, -45]]
    layout[3, 1] = -45
    records["eye"]["distance_cm"] = 1000
    records["eye"]["distance_cm"][:, :3] = 180*np.sqrt(2)
    records["eye"]["distance_cm"][:, 3] = 100*np.sqrt(2)
    records["eye"]["hit"][:, :4] = 1
    records["eye"]["kind"][:, :4] = 1
    records["eye"]["normal_y"][:, :3] = 1
    for name in ("memory_near", "memory_far"):
        records[name]["height_valid"] = 1
        records[name]["ground_height_cm"] = 12345
    return records, layout


def write_episode(path, split="train", site="place", records=None):
    if records is None:
        records, layout = fixture_records()
        records["keys"]["jump"][0] = 1
        records["eye"]["hit"][1:, 3] = 0
    else:
        _, layout = fixture_records()
    path.mkdir(parents=True)
    metadata = dict(start=path.name, goal="goal", split=split, site_id=site,
                    transition_id=site + "-link", eye_layout=layout.tolist())
    (path / "episode.json").write_text(json.dumps(metadata))
    with gzip.open(path / "samples.bin.gz", "wb") as stream:
        stream.write(demos.HEADER.pack(demos.MAGIC, 2, demos.RECORD_BYTES,
                                      demos.RAY_COUNT, demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
        stream.write(records.tobytes())
    return path


def test_geometry_uses_movement_frame_and_only_learner_inputs():
    records, layout = fixture_records(1)
    records["body"]["head_yaw_deg"] = 90
    records["body"]["velocity_cm_s"] = [100, 0, 0]
    records["memory_near"]["channels"][0, 32, 39, 3] = 200
    expected = visibility.sensor_features(demos.learner_input(records), layout)
    row = expected[0]
    assert row["movement_deg"] == pytest.approx(90)
    assert row["carrot_movement_angle_deg"] == pytest.approx(-90)
    assert row["eye_height_proxy_cm"] == pytest.approx(180, abs=.01)
    assert row["obstacle_distance_cm"] == pytest.approx(100, abs=.01)
    assert row["visible_hit_height_cm"] == pytest.approx(80, abs=.01)
    assert row["obstacle_ray"] == 3
    assert row["near_ledge"] == pytest.approx(200/255)
    records["position_cm"] = [99999, -99999, 99999]
    records["body_yaw_deg"] = 123
    records["teacher_hint"]["path_cm"] = 99999
    records["keys"]["climb"] = 1
    for name in ("memory_near", "memory_far"):
        records[name]["ground_height_cm"] += 20000
    actual = visibility.sensor_features(demos.learner_input(records), layout)
    for key, value in row.items():
        if isinstance(value, float) and np.isnan(value):
            assert np.isnan(actual[0][key])
        else:
            assert actual[0][key] == value
    with pytest.raises(ValueError, match="learner_input"):
        visibility.sensor_features({**demos.learner_input(records), "teacher_hint": records["teacher_hint"]}, layout)


def test_rotation_composition_is_not_simple_yaw_pitch_addition():
    layout = np.array([[90., 0., 1000.], [0., 0., 1000.], [0., 90., 1000.]])
    directions = visibility.ray_directions(layout, 90, 30)
    np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1)
    np.testing.assert_allclose(directions[0], [0, 0, -1], atol=1e-6)
    np.testing.assert_allclose(directions[1], [np.sqrt(3)/2, .5, 0], atol=1e-6)
    np.testing.assert_allclose(directions[2], [-.5, np.sqrt(3)/2, 0], atol=1e-6)


def test_missing_ground_or_cached_rays_never_fabricates_height():
    records, layout = fixture_records(2)
    records["memory_near"]["height_valid"][0] = 0
    records["memory_far"]["height_valid"][0] = 0
    records["eye"]["age_ms"][1, :4] = 500
    rows = visibility.sensor_features(demos.learner_input(records), layout)
    assert np.isnan(rows[0]["eye_height_proxy_cm"])
    assert np.isnan(rows[0]["visible_hit_height_cm"])
    assert rows[0]["ahead_surface_distance_cm"] == pytest.approx(100)
    assert rows[1]["ahead_hit_rays"] == 0
    assert np.isnan(rows[1]["obstacle_distance_cm"])


def test_drop_is_last_grounded_decision_before_uncommanded_substantial_fall():
    times = np.arange(8)*100
    heights = np.array([100, 100, 95, 70, 35, 30, 30, 30])
    flags = np.zeros((8, 5), dtype=np.uint8)
    flags[2:5, :2] = 1
    jump = np.zeros(8, dtype=bool)
    moving = np.ones(8, dtype=bool)
    result = visibility.drop_onsets(times, heights, flags, jump, jump.copy(), moving)
    np.testing.assert_array_equal(np.flatnonzero(result), [1])
    # A real jump, a staircase (no airborne state), and a shallow step are not drops.
    jump[1] = True
    assert not visibility.drop_onsets(times, heights, flags, jump, np.zeros(8, dtype=bool), moving).any()
    assert not visibility.drop_onsets(times, heights, np.zeros_like(flags), jump*False, jump*False, moving).any()
    assert not visibility.drop_onsets(times, heights/10, flags, jump*False, jump*False, moving).any()


def feature_row(split, distance, height, label):
    return dict(split=split, obstacle_distance_cm=distance, visible_hit_height_cm=height,
                jump=label, climb=False, ordinary_walk=not label)


def test_rule_thresholds_fit_train_only_and_penalize_walking_false_positives():
    training = [feature_row("train", 80, 80, True), feature_row("train", 90, 80, True),
                feature_row("train", 150, 80, False), feature_row("train", 80, 200, False)]
    held_out = [feature_row("test", 590, 290, True)]*50
    rule = visibility.fit_rule(training)
    assert visibility.fit_rule(training + held_out) == rule
    assert rule["distance_cm"] == 100
    assert rule["max_height_cm"] == 100
    assert rule["train_f1"] == 1
    assert not visibility.predict_rule(held_out, rule).any()
    assert visibility.fit_rule([feature_row("unsplit", 80, 80, True)]) is None


def test_analysis_streaming_labels_cross_chunk_boundaries_and_does_not_rewrite(tmp_path):
    records, _ = fixture_records(8)
    records["position_cm"][:, 1] = [100, 100, 95, 70, 35, 30, 30, 30]
    records["body"]["flags"][2:5, :2] = 1
    source = write_episode(tmp_path / "episode", records=records)
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    report1, rows1 = visibility.analyze([source], chunk_records=1)
    report3, rows3 = visibility.analyze([source], chunk_records=3)
    assert report1["counts"]["drop_onset"] == 1
    assert rows1[1]["drop_onset"]
    assert visibility._clean(rows1) == visibility._clean(rows3)
    assert report1 == report3
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before
    assert not report1["geometry"]["exact"]
    assert "omits eye origin" in report1["limitations"][0]


def test_external_site_split_is_used_and_leakage_rejected(tmp_path):
    train = write_episode(tmp_path / "a", split="unsplit", site="")
    test = write_episode(tmp_path / "b", split="unsplit", site="")
    metadata = tmp_path / "routes.json"
    rows = [dict(episode="a", site_id="train-site", transition_id="train-link", split="train"),
            dict(start="b", goal="goal", site_id="test-site", transition_id="test-link", split="test")]
    metadata.write_text(json.dumps(dict(routes=rows)))
    report, decisions = visibility.analyze([train, test], metadata)
    assert report["rule"]["fit_split"] == "train"
    assert {row["split"] for row in decisions} == {"train", "test"}
    rows[1]["site_id"] = "train-site"
    metadata.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="crosses splits"):
        visibility.analyze([train, test], metadata)


def test_duplicate_recordings_and_input_budget_are_rejected(tmp_path):
    source = write_episode(tmp_path / "episode")
    with pytest.raises(ValueError, match="duplicate recording"):
        visibility.analyze([source, source / "samples.bin.gz"])
    with pytest.raises(ValueError, match="input budget"):
        visibility.analyze([source], max_input_gb=1e-12)


def test_cli_writes_portable_json_and_csv_and_protects_sources(tmp_path):
    source = write_episode(tmp_path / "episode")
    output, csv_path = tmp_path / "analysis.json", tmp_path / "decisions.csv"
    command = [sys.executable, "-I", visibility.__file__, str(source),
               "--output", str(output), "--csv", str(csv_path)]
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text())
    assert payload["counts"]["jump"] == 1
    assert "NaN" not in output.read_text()
    with csv_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert rows[0]["jump"] == "True"
    command[command.index("--output") + 1] = str(source / "episode.json")
    rejected = subprocess.run(command, text=True, capture_output=True)
    assert rejected.returncode != 0
    assert "outside recording source" in rejected.stderr
    assert json.loads((source / "episode.json").read_text())["start"] == "episode"
