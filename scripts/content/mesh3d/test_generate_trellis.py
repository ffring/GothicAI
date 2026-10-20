"""Numerical/file contract tests. No model, network or GPU imports."""
import json
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from generate_trellis import MemoryJournal, read_rgba, sample_voxel_attributes, save_raw, srgb_to_linear


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in mesh3d CPU checks")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_sparse_attributes_use_voxel_centers_and_world_origin():
    coords = [[5, 0, 0], [0, 0, 0], [2, 0, 0]]
    attrs = np.array([[1, 0, 0, 0.8, 0.2], [0, 1, 0, 0, 1], [0, 0, 1, 0.1, 0.9]])
    origin, size = np.array([-0.5, -1, 2]), 0.25
    query = np.array([[0.6, 0.5, 0.5], [2.7, 0.5, 0.5], [5.5, 0.5, 0.5]])
    result = sample_voxel_attributes(origin + size * query, coords, attrs, origin, size)
    np.testing.assert_allclose(result, attrs[[1, 2, 0]])
    assert result.dtype == np.float32
    # The decision boundary is between voxel centers, not between integers.
    adjacent = sample_voxel_attributes([[0.9, 0.5, 0.5], [1.1, 0.5, 0.5]],
                                       [[0, 0, 0], [1, 0, 0]], [[0], [1]], [0, 0, 0], 1)
    np.testing.assert_array_equal(adjacent, [[0], [1]])


def test_raw_formats_have_exact_fields_dtypes_and_roundtrip(tmp_path):
    vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    faces = [[0, 1, 2]]
    color = srgb_to_linear([[0, 0.5, 1]] * 3)
    for pbr in (False, True):
        path = tmp_path / f"{pbr}.npz"
        fields = {"metallic": [0, 0.5, 1], "roughness": [1, 0.5, 0]} if pbr else {}
        assert save_raw(path, vertices, faces, color, **fields) == {"vertices": 3, "faces": 1}
        with np.load(path, allow_pickle=False) as saved:
            assert set(saved.files) == {"vertices", "faces", "color", *fields}
            assert saved["vertices"].shape == saved["color"].shape == (3, 3)
            assert saved["faces"].shape == (1, 3)
            assert saved["faces"].dtype == np.int32
            for name in set(saved.files) - {"faces"}:
                assert saved[name].dtype == np.float32
            for name in fields:
                assert saved[name].shape == (3,)
            np.testing.assert_allclose(saved["color"][0], [0, 0.21404114, 1], atol=1e-7)


@pytest.mark.parametrize("field,value,match", [
    ("faces", [[0, 1, 9]], "index"),
    ("color", [[1.1, 0, 0]] * 3, "0..1"),
    ("color", [[np.nan, 0, 0]] * 3, "nonfinite"),
    ("metallic", [0, 0, 0], "together"),
])
def test_raw_rejects_invalid_contract(tmp_path, field, value, match):
    arrays = dict(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]],
                  color=[[0.5, 0.5, 0.5]] * 3)
    arrays[field] = value
    with pytest.raises(ValueError, match=match):
        save_raw(tmp_path / "raw.npz", **arrays)
    assert not (tmp_path / "raw.npz").exists()


def test_rgba_requires_transparency_and_foreground(tmp_path):
    path = tmp_path / "input.png"
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    image.paste((100, 60, 20, 255), (4, 4, 12, 12))
    image.save(path)
    assert read_rgba(path).mode == "RGBA"
    for mode, color in (("RGB", (100, 60, 20)), ("RGBA", (100, 60, 20, 255)), ("RGBA", (0, 0, 0, 0))):
        Image.new(mode, (16, 16), color).save(path)
        with pytest.raises(ValueError):
            read_rgba(path)


def test_check_imports_no_gpu_dependency_in_cpu_module():
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_live_metadata_is_durable_before_model_returns(tmp_path):
    # No CUDA import: the counter is a stub, while writes/threading are real.
    peak = {"bytes": 2**20}
    cuda = SimpleNamespace(is_initialized=lambda: True)
    for name in ("memory_allocated", "memory_reserved", "max_memory_allocated", "max_memory_reserved"):
        setattr(cuda, name, lambda: peak["bytes"])
    meta = {"attempts": []}
    journal = MemoryJournal(tmp_path, meta, SimpleNamespace(cuda=cuda))
    try:
        journal.begin_attempt("512", 1)
        saved = json.loads((tmp_path / "meta.json").read_text())
        assert saved["active_attempt"] == 0
        assert saved["attempts"][0]["outcome"] == "running"
        assert saved["attempts"][0]["peak_allocated_mib"] == 1
        peak["bytes"] = 2 * 2**20
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            saved = json.loads((tmp_path / "meta.json").read_text())
            if saved["peak_allocated_mib"] == 2:
                break
            time.sleep(0.05)
        assert saved["peak_allocated_mib"] == 2
        assert saved["peak_rss_mib"] > 0
        records = [json.loads(line) for line in (tmp_path / "memory.jsonl").read_text().splitlines()]
        assert records[-1]["active_attempt"] == 0
        assert records[-1]["pipeline_type"] == "512"
        assert records[-1]["peak_allocated_mib"] == 2
        journal.update_attempt(outcome="ok", stage="finished")
        journal.finish_attempt()
    finally:
        journal.stop()
    saved = json.loads((tmp_path / "meta.json").read_text())
    assert saved["active_attempt"] is None
    assert saved["attempts"][0]["outcome"] == "ok"
