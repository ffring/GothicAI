"""Full-frame pilot statistics and their per-spot baseline ratios."""

import csv
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image
import pytest


SPEC = importlib.util.spec_from_file_location(
    "capture_quality_pilot", Path(__file__).with_name("capture-quality-pilot.py"))
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def test_flat_frame_has_no_detail_and_uses_linear_rec709(tmp_path):
    path = tmp_path / "green.png"
    Image.new("RGB", (1280, 720), (0, 128, 0)).save(path)
    measured = capture.measure_frame(path)
    assert measured["mean_y"] == pytest.approx(.7152 * ((128 / 255 + .055) / 1.055) ** 2.4)
    assert measured["log_spread"] == 0
    assert measured["detail_k9"] == pytest.approx(0, abs=1e-12)


def test_detail_uses_whole_frame_and_reflected_box(tmp_path):
    path = tmp_path / "stripes.png"
    row = np.full(1280, 128, dtype=np.uint8)
    row[:7] = 16
    row[7:263] = 224
    image = np.broadcast_to(row[None, :, None], (720, 1280, 3))
    Image.fromarray(image).save(path)
    srgb = row.astype(np.float64) / 255
    y = np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)
    log_y = np.log(y + .001)
    box = np.convolve(np.pad(log_y, 4, mode="symmetric"), np.full(9, 1 / 9), mode="valid")
    measured = capture.measure_frame(path)
    assert measured["mean_y"] == pytest.approx(y.mean())
    assert measured["log_spread"] == pytest.approx(np.percentile(log_y, 90) - np.percentile(log_y, 10))
    assert measured["detail_k9"] == pytest.approx(np.abs(log_y - box).mean(), abs=1e-12)


def test_frame_size_is_not_rescaled(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (640, 360)).save(path)
    with pytest.raises(ValueError, match="expected 1280x720, got 640x360"):
        capture.measure_frame(path)


def test_detail_csv_compares_each_spot_with_its_original(tmp_path, monkeypatch):
    def measure(path):
        baseline = 2 if path.stem == "facade" else 4
        scale = {"original": 1, "pilot-2": .75, "pilot-3a": .95, "pilot-3b": 1.05}[path.parent.name]
        return {"mean_y": baseline * scale, "log_spread": baseline * 2 * scale,
                "detail_k9": baseline * 3 * scale}

    monkeypatch.setattr(capture, "SPOTS", ("facade", "stairs"))
    monkeypatch.setattr(capture, "measure_frame", measure)
    output = tmp_path / "report" / "detail.csv"
    variants = ["original", "pilot-2", "pilot-3a", "pilot-3b"]
    capture.detail(tmp_path / "captures", output, variants)
    with output.open() as stream:
        rows = list(csv.DictReader(stream))
    assert [(row["spot"], row["variant"]) for row in rows] == [
        (spot, variant) for spot in ("facade", "stairs") for variant in variants]
    for row in rows:
        for field in ("mean_y", "log_spread", "detail_k9"):
            assert float(row[field + "_ratio"]) == pytest.approx(
                {"original": 1, "pilot-2": .75, "pilot-3a": .95, "pilot-3b": 1.05}[row["variant"]])


def test_iteration3_boards_keep_panel_width_and_variant_order(tmp_path):
    variants = ("original", "pilot-2", "pilot-3a", "pilot-3b")
    colours = ((220, 20, 20), (20, 220, 20), (20, 20, 220), (220, 220, 20))
    captures = tmp_path / "captures"
    for variant, colour in zip(variants, colours):
        (captures / variant).mkdir(parents=True)
        for spot in capture.SPOTS:
            Image.new("RGB", (1280, 720), colour).save(captures / variant / f"{spot}.png")
    output = tmp_path / "boards"
    capture.compose(captures, output, iteration3=True)
    assert {path.name for path in output.iterdir()} == {f"{spot}.jpg" for spot in capture.SPOTS}
    for path in output.iterdir():
        with Image.open(path) as board:
            assert board.format == "JPEG"
            assert board.size == (4 * 768, 432 + 44)
            for panel, colour in enumerate(colours):
                assert board.getpixel((panel * 768 + 384, 260)) == pytest.approx(colour, abs=3)
