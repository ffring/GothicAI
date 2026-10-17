"""Feature privacy, pooling, pair grouping and compact-cache round trips."""
import gzip
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "nav_bc_data.py"
SPEC = importlib.util.spec_from_file_location("nav_bc_data", MODULE_PATH)
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)
demos = data.nav_demos


def fixture_records(count=3):
    records = np.zeros(count, dtype=demos.RECORD_DTYPE)
    layout = np.tile(np.array([0, 0, 1000], dtype=np.float32), (demos.RAY_COUNT, 1))
    records["eye"]["distance_cm"] = 1000
    records["roads"]["next_direction"][:, 2] = 1
    for name in ("memory_near", "memory_far"):
        memory = records[name]
        middle = memory.shape[1] // 2
        memory["height_valid"][:, middle, middle] = 1
        memory["ground_height_cm"][:, middle, middle] = 12345
        memory["channels"][:, middle, middle] = [255, 255, 0, 0, 0, 0, 0]
    return records, layout


def test_feature_boundary_excludes_privileged_fields_and_absolute_height():
    records, layout = fixture_records()
    expected = data.encode_features(demos.learner_input(records), layout)
    assert expected["near"].shape == (3, 9, 32, 32)
    assert expected["far"].shape == (3, 9, 25, 25)
    assert expected["vector"].shape == (3, data.VECTOR_SIZE)
    records["teacher_hint"]["next_corner_cm"] = 99999
    records["teacher_hint"]["path_cm"] = 99999
    records["position_cm"] = 99999
    records["body_yaw_deg"] = 333
    records["keys"]["turn"] = -1
    # Sensor memory stores world ground heights: translation must cancel locally.
    for name in ("memory_near", "memory_far"):
        records[name]["ground_height_cm"][records[name]["height_valid"] != 0] += 10000
    actual = data.encode_features(demos.learner_input(records), layout)
    for name in expected:
        np.testing.assert_array_equal(actual[name], expected[name])
    with pytest.raises(ValueError, match="learner_input"):
        data.encode_features({**demos.learner_input(records), "teacher_hint": records["teacher_hint"]}, layout)


def test_pooling_ignores_unknown_ground_and_averages_channels():
    records, layout = fixture_records(1)
    near = records["memory_near"]
    near["height_valid"][0, 32, 33] = 1
    near["ground_height_cm"][0, 32, 33] = 12545
    encoded = data.encode_features(demos.learner_input(records), layout)["near"]
    assert encoded[0, 0, 16, 16] == .25
    assert encoded[0, 1, 16, 16] == .25
    assert encoded[0, 7, 16, 16] == .5  # average 0cm and 200cm, divided by 200cm
    assert encoded[0, 8, 16, 16] == .5
    assert encoded[0, 7, 0, 0] == 0


def test_axis_labels_and_engine_turn_sign():
    records, _ = fixture_records()
    records["keys"]["forward"] = [-1, 0, 1]
    records["keys"]["turn"] = [-1, 0, 1]
    records["roads"]["next_direction"] = [[1, 0, 0], [0, 0, 1], [-1, 0, 0]]
    labels = data.encode_labels(records["keys"])
    np.testing.assert_array_equal(labels[:, 0], [0, 1, 2])
    np.testing.assert_array_equal(labels[:, 2], [0, 1, 2])
    baseline = data.road_baseline(demos.learner_input(records))
    np.testing.assert_array_equal(baseline[:, 2], [0, 1, 2])
    np.testing.assert_array_equal(baseline[:, 0], [2, 2, 2])


def test_pair_split_is_deterministic_grouped_and_stratified():
    episodes = [{"start": f"{region}-{index}", "goal": "goal", "region": region}
                for region in ("city", "farm", "forest") for index in range(10)]
    split = data.split_pairs(episodes + episodes[::2])
    assert split == data.split_pairs(episodes[::-1])
    assert sum(part == "validation" for part in split.values()) == 6
    for region in ("city", "farm", "forest"):
        assert sum(part == "validation" and pair[0].startswith(region) for pair, part in split.items()) == 2


def test_prepare_reads_success_only_and_cache_round_trips(tmp_path):
    source, output = tmp_path / "source", tmp_path / "cache"
    records, layout = fixture_records(2)
    for index in range(6):
        episode = source / "episodes" / f"{index:03}"
        episode.mkdir(parents=True)
        metadata = {"success": index != 5, "start": f"start-{index // 2}", "goal": "goal",
                    "region": "forest", "decisions": 2, "eye_layout": layout.tolist()}
        (episode / "episode.json").write_text(json.dumps(metadata))
        with gzip.open(episode / "samples.bin.gz", "wb") as stream:
            stream.write(demos.HEADER.pack(demos.MAGIC, demos.VERSION, demos.RECORD_BYTES,
                                           demos.RAY_COUNT, demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
            stream.write(records.tobytes())
    index = data.prepare(source, output, chunk_records=1)
    assert index["excluded_unsuccessful_episodes"] == 1
    assert len(index["episodes"]) == 5
    assignments = {}
    for entry in index["episodes"]:
        pair = tuple(entry["pair"])
        assert assignments.setdefault(pair, entry["split"]) == entry["split"]
        cached = data.load_cached(output / entry["file"])
        expected = data.encode_features(demos.learner_input(records), layout)
        for name in ("near", "far", "vector"):
            np.testing.assert_allclose(cached[name], expected[name], atol=1 / 255)
        assert cached["labels"].dtype == np.int64
        np.testing.assert_array_equal(cached["situations"][:, 2], [1, 1])
