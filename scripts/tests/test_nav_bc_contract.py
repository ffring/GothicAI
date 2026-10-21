"""The parity fixture preserves the NAVDEMO road schema selected by the weights."""
import gzip
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_nav_policy as contract


@pytest.mark.parametrize("version", [1, 2])
def test_synthetic_records_use_requested_road_schema(version):
    layout, samples = contract.synthetic_records(2, version)
    records = np.concatenate(list(samples))
    assert layout.shape == (contract.demos.RAY_COUNT, 3)
    assert records.dtype == contract.demos.RECORD_DTYPES[version]
    roads = records["roads"]
    target = "carrot_direction" if version == 2 else "next_direction"
    np.testing.assert_allclose(np.linalg.norm(roads[target], axis=1), 1, atol=1e-6)
    np.testing.assert_array_equal(roads["beyond_known" if version == 2 else "goal_in_known"], [0, 1])


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("weight_version", [1, 2])
def test_synthetic_weight_header_declares_input_composition(tmp_path, version, weight_version):
    weights = tmp_path / "fixture.navbc"
    contract.synthetic_weights(weights, version, weight_version)
    with weights.open("rb") as stream:
        header = json.loads(stream.readline())
    preprocessing = header["preprocessing"]
    assert preprocessing["navdemo_version"] == version
    assert preprocessing["roads_schema"] == ("carrot_v2" if version == 2 else "next_v1")
    assert preprocessing["roads_fields"] == contract.ROAD_FIELDS[version]
    assert preprocessing["input_fields"] == ["body", "roads", "eye", "memory_near", "memory_far"]
    assert preprocessing["vector_order"] == ["eye", "roads", "body"]
    assert header["architecture"]["inputs"]["vector"] == [2166]
    assert header["version"] == weight_version
    assert {name: head["classes"] for name, head in header["architecture"]["heads"].items()} == contract.HEAD_SIZES[weight_version]
    policy = contract.NumpyPolicy(weights)
    assert policy.format_version == weight_version
    assert policy.head_names == tuple(contract.HEAD_SIZES[weight_version])
    assert policy.temperatures == dict.fromkeys(policy.head_names, 1.0)
    assert contract.weight_input_version(header, 2166) == version
    # Fixture generation must not alter the training metadata globally.
    assert contract.data.PREPROCESSING["navdemo_version"] == 2
    assert contract.data.PREPROCESSING["roads_schema"] == "carrot_v2"


@pytest.mark.parametrize("weight_version", [1, 2])
def test_numpy_softmax_uses_each_head_temperature(tmp_path, weight_version):
    weights = tmp_path / "fixture.navbc"
    sizes = contract.HEAD_SIZES[weight_version]
    temperatures = {name: .5 + index for index, name in enumerate(sizes)}
    contract.synthetic_weights(weights, format_version=weight_version, temperatures=temperatures)
    policy = contract.NumpyPolicy(weights)
    logits = {name: np.arange(classes, dtype=np.float32) for name, classes in sizes.items()}
    for name, actual in policy.probabilities(logits).items():
        expected = np.exp((logits[name].astype(np.float64) - logits[name].max()) / temperatures[name])
        np.testing.assert_allclose(actual, expected / expected.sum(), rtol=1e-14)


@pytest.mark.parametrize("action", [0, 1, 2])
def test_numpy_v2_decodes_one_mutually_exclusive_maneuver(tmp_path, action):
    weights = tmp_path / "fixture.navbc"
    contract.synthetic_weights(weights)
    policy = contract.NumpyPolicy(weights)
    logits = {name: np.zeros(classes, dtype=np.float32) for name, classes in policy.head_sizes.items()}
    logits["maneuver"][:] = -1000
    logits["maneuver"][action] = 1000
    keys = policy.sample(logits, np.random.default_rng(3))
    assert keys["jump"] is (action == 1)
    assert keys["climb"] is (action == 2)


@pytest.mark.parametrize("bad_value", [0, -1, float("nan"), float("inf")])
def test_numpy_rejects_invalid_exported_temperature(tmp_path, bad_value):
    weights = tmp_path / "fixture.navbc"
    temperatures = dict.fromkeys(contract.HEAD_SIZES[2], 1.0)
    temperatures["maneuver"] = bad_value
    contract.synthetic_weights(weights, temperatures=temperatures)
    with pytest.raises(ValueError, match="temperatures"):
        contract.NumpyPolicy(weights)


def test_numpy_rejects_v2_legacy_head_mapping(tmp_path):
    weights = tmp_path / "fixture.navbc"
    contract.synthetic_weights(weights)
    with weights.open("rb") as stream:
        header, payload = json.loads(stream.readline()), stream.read()
    header["architecture"]["heads"]["maneuver"]["values"] = [-1, 0, 1]
    weights.write_bytes(json.dumps(header).encode() + b"\n" + payload)
    with pytest.raises(ValueError, match="maneuver"):
        contract.NumpyPolicy(weights)


@pytest.mark.parametrize("version", [1, 2])
def test_fixture_features_preserve_each_versions_road_values(version):
    layout, samples = contract.synthetic_records(1, version)
    record = next(samples)
    roads = record["roads"]
    target, distance, goal, goal_distance, remainder, flag = contract.ROAD_FIELDS[version]
    roads[target] = [.25, -.5, .75]
    roads[distance] = 1500
    roads[goal] = [-.1, .2, .3]
    roads[goal_distance] = 60000
    roads[remainder] = 1750
    roads[flag] = 1
    original = record.copy()
    features = contract.fixture_features(record, layout, version)
    offset = contract.demos.RAY_COUNT * 9
    np.testing.assert_array_equal(features["vector"][0, offset:offset + 10],
                                  np.array([.25, -.5, .75, 1.5, -.1, .2, .3, 50, 1.75, 1], dtype=np.float32))
    np.testing.assert_array_equal(record, original)
    if version == 1:
        with pytest.raises(ValueError, match="requires NAVDEMO v2"):
            contract.data.encode_features(contract.demos.learner_input(record), layout)
    with pytest.raises(ValueError, match="record dtype"):
        contract.fixture_features(record, layout, 3 - version)


def test_supplied_weights_require_matching_vector_size_and_version_schema():
    header = {"preprocessing": contract.fixture_preprocessing(2)}
    with pytest.raises(ValueError, match="vector_size=2166"):
        contract.weight_input_version(header, 2174)
    with pytest.raises(ValueError, match="roads_schema"):
        contract.weight_input_version({"preprocessing": {**header["preprocessing"], "navdemo_version": 1}}, 2166)
    legacy_header = {"preprocessing": {"vector_order": ["eye", "roads", "body"]}}
    assert contract.weight_input_version(legacy_header, 2166) == 1


def test_recorded_fixture_filters_navdemo_version(tmp_path):
    for version in (1, 2):
        folder = tmp_path / str(version)
        folder.mkdir()
        layout, samples = contract.synthetic_records(2, version)
        (folder / "episode.json").write_text(json.dumps({"decisions": 2, "eye_layout": layout.tolist()}))
        with gzip.open(folder / "samples.bin.gz", "wb") as stream:
            demos = contract.demos
            stream.write(demos.HEADER.pack(demos.MAGIC, version, demos.RECORD_BYTES, demos.RAY_COUNT,
                                           demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
            for record in samples:
                stream.write(record.tobytes())
    source, _, samples = contract.recorded_steps(tmp_path, 2, 2)
    assert source.parent.name == "2"
    assert all(sample.dtype == contract.demos.RECORD_V2_DTYPE for sample in samples)
    with pytest.raises(ValueError, match="NAVDEMO v2"):
        contract.recorded_steps(tmp_path / "1", 2, 2)
