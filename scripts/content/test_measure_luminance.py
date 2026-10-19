"""The original-texture boundary exports scalars and changes only its CSV column."""
from copy import deepcopy
import csv
import importlib.util
import io
from pathlib import Path

import pytest


DIRECTORY = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("measure_luminance", DIRECTORY / "measure_luminance.py")
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def test_parser_accepts_only_scalars_for_the_exact_pilot():
    assert measurement.parse_measurements("texture,target_luminance\nA.TGA,0.125\nB.TGA,0\n",
                                           ["A.TGA", "B.TGA"]) == {"A.TGA": 0.125, "B.TGA": 0.0}


@pytest.mark.parametrize("value,message", [
    ("texture,pixels\nA.TGA,123\n", "only texture,target_luminance"),
    ("texture,target_luminance\nA.TGA,0.1\nA.TGA,0.2\n", "repeats texture"),
    ("texture,target_luminance\nA.TGA,NaN\n", "finite"),
    ("texture,target_luminance\nA.TGA,1.01\n", "finite"),
    ("texture,target_luminance\nB.TGA,0.1\n", "texture set differs"),
    ("texture,target_luminance\nA.TGA\n", "malformed CSV row"),
])
def test_parser_rejects_wrong_or_unusable_measurements(value, message):
    with pytest.raises(ValueError, match=message):
        measurement.parse_measurements(value, ["A.TGA"])


def test_atomic_update_adds_one_column_and_preserves_all_existing_fields(tmp_path):
    rows = []
    for name, decision in (("A.TGA", "include"), ("NW_CITY_WALL_03.TGA", "module"), ("C.TGA", "panel")):
        row = {field: "unchanged, including quoted value" for field in measurement.FIELDS[:-2]}
        row.update(texture=name, decision=decision)
        rows.append(row)
    before = deepcopy(rows)
    path = tmp_path / "mapping.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=measurement.FIELDS[:-2])
        writer.writeheader()
        writer.writerows(rows)
    assert measurement.read_mapping(path) == rows
    updated = measurement.update_mapping(path, rows, {"A.TGA": 0.12345, "NW_CITY_WALL_03.TGA": 0.05})
    assert rows == before
    assert [{key: row[key] for key in measurement.FIELDS[:-2]} for row in updated] == before
    assert [row["target_luminance"] for row in updated] == ["0.12345", "0.05", ""]
    assert measurement.read_mapping(path) == updated


def test_bad_measurement_does_not_modify_mapping(tmp_path):
    path = tmp_path / "mapping.csv"
    path.write_text("must remain unchanged\n")
    rows = [{"texture": "A.TGA", "decision": "include"}]
    with pytest.raises(ValueError, match="finite"):
        measurement.update_mapping(path, rows, {"A.TGA": float("nan")})
    assert path.read_text() == "must remain unchanged\n"


def properties_csv(values):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=measurement.PROPERTIES_FIELDS, lineterminator="\n")
    writer.writeheader()
    for name, luminance, coverage in values:
        row = {field: coverage for field in measurement.PROPERTIES_FIELDS}
        row.update(texture=name, target_luminance=luminance, opaque="false", width=512, height=512)
        writer.writerow(row)
    return stream.getvalue()


def test_city3_measurement_includes_rope_and_alpha_and_excludes_metal(monkeypatch):
    rows = [
        {"texture": "FLOOR.TGA", "decision": "include", "class": "flagstone"},
        {"texture": "PANEL.TGA", "decision": "panel", "class": "painted_panel"},
        {"texture": "IRON.TGA", "decision": "panel", "class": "iron_panel"},
        {"texture": "ROPE.TGA", "decision": "strip", "class": "rope_strip"},
        {"texture": "NW_CITY_FLAG_01.TGA", "decision": "panel", "class": "cloth_panel"},
    ]
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        class Result:
            stdout = properties_csv([("FLOOR.TGA", .12, 1), ("PANEL.TGA", .2, 1),
                                     ("ROPE.TGA", .08, 1), ("NW_CITY_FLAG_01.TGA", .4, .86)])
        return Result()
    monkeypatch.setattr(measurement.subprocess, "run", run)
    assert measurement.measure(Path("extractor"), Path("game"), rows, "city-3", with_coverage=True) == ({
        "FLOOR.TGA": 0.12, "PANEL.TGA": 0.2, "ROPE.TGA": .08, "NW_CITY_FLAG_01.TGA": .4},
        {"NW_CITY_FLAG_01.TGA": .86})
    assert calls == [(["extractor", "--properties", "game", "FLOOR.TGA", "PANEL.TGA", "ROPE.TGA", "NW_CITY_FLAG_01.TGA"],
                      {"check": True, "capture_output": True, "text": True})]


def test_city3_update_changes_only_its_selected_scalar_values(tmp_path):
    rows = []
    for name, decision, material_class in (("FLOOR.TGA", "include", "flagstone"),
            ("PANEL.TGA", "panel", "painted_panel"), ("IRON.TGA", "panel", "iron_panel")):
        row = {field: "unchanged" for field in measurement.FIELDS}
        row.update(texture=name, decision=decision, **{"class": material_class}, target_luminance="0.5")
        rows.append(row)
    before = deepcopy(rows)
    path = tmp_path / "mapping.csv"
    updated = measurement.update_mapping(path, rows, {"FLOOR.TGA": 0.1, "PANEL.TGA": 0.2}, "city-3")
    assert rows == before
    assert [row["target_luminance"] for row in updated] == ["0.1", "0.2", "0.5"]
    preserved = [field for field in measurement.FIELDS if field != "target_luminance"]
    assert [{key: row[key] for key in preserved} for row in updated] == [
        {key: row[key] for key in preserved} for row in before]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.01, 1.01])
def test_properties_parser_rejects_invalid_coverage(value):
    text = properties_csv([("NW_CITY_IVY_FULL_01.TGA", .1, value)])
    with pytest.raises(ValueError, match="target_coverage.*finite"):
        measurement.parse_properties(text, ["NW_CITY_IVY_FULL_01.TGA"])


def test_coverage_update_preserves_intent_and_outside_set_scalars(tmp_path):
    rows = []
    for name, decision, material_class in (("NW_CITY_IVY_FULL_01.TGA", "panel", "foliage_panel"),
            ("IRON.TGA", "panel", "iron_panel")):
        row = {field: "unchanged, quoted" for field in measurement.FIELDS}
        row.update(texture=name, decision=decision, **{"class": material_class},
                   target_luminance="0.1", target_coverage="0.25")
        rows.append(row)
    before = deepcopy(rows)
    updated = measurement.update_mapping(tmp_path / "mapping.csv", rows,
        {"NW_CITY_IVY_FULL_01.TGA": .11}, "city-3", coverages={"NW_CITY_IVY_FULL_01.TGA": .945392608643})
    assert rows == before
    assert updated[0]["target_coverage"] == "0.945392608643"
    assert updated[1] == before[1]
    assert {field: updated[0][field] for field in measurement.FIELDS[:-2]} == {
        field: before[0][field] for field in measurement.FIELDS[:-2]}


@pytest.mark.parametrize("coverages,message", [
    ({}, "texture set differs"), ({"NW_CITY_IVY_FULL_01.TGA": float("nan")}, "finite"),
])
def test_invalid_coverage_update_does_not_touch_csv(tmp_path, coverages, message):
    path = tmp_path / "mapping.csv"
    path.write_text("unchanged\n")
    rows = [{"texture": "NW_CITY_IVY_FULL_01.TGA", "decision": "panel", "class": "foliage_panel"}]
    with pytest.raises(ValueError, match=message):
        measurement.update_mapping(path, rows, {"NW_CITY_IVY_FULL_01.TGA": .1}, "city-3", coverages=coverages)
    assert path.read_text() == "unchanged\n"
