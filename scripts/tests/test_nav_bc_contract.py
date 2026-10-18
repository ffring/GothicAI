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


def test_synthetic_weight_header_declares_v2_input_composition(tmp_path):
    weights = tmp_path / "fixture.navbc"
    contract.synthetic_weights(weights)
    with weights.open("rb") as stream:
        header = json.loads(stream.readline())
    preprocessing = header["preprocessing"]
    assert preprocessing["navdemo_version"] == 2
    assert preprocessing["roads_schema"] == "carrot_v2"
    assert preprocessing["roads_fields"] == ["carrot_direction", "carrot_distance_cm", "goal_direction",
                                              "goal_distance_cm", "remaining_known_cm", "beyond_known"]
    assert preprocessing["input_fields"] == ["body", "roads", "eye", "memory_near", "memory_far"]
    assert preprocessing["vector_order"] == ["eye", "roads", "body"]
    assert header["architecture"]["inputs"]["vector"] == [2166]


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
