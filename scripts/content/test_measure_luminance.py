"""The original-texture boundary exports scalars and changes only its CSV column."""
from copy import deepcopy
import csv
import importlib.util
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
        row = {field: "unchanged, including quoted value" for field in measurement.FIELDS[:-1]}
        row.update(texture=name, decision=decision)
        rows.append(row)
    before = deepcopy(rows)
    path = tmp_path / "mapping.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=measurement.FIELDS[:-1])
        writer.writeheader()
        writer.writerows(rows)
    assert measurement.read_mapping(path) == rows
    updated = measurement.update_mapping(path, rows, {"A.TGA": 0.12345, "NW_CITY_WALL_03.TGA": 0.05})
    assert rows == before
    assert [{key: row[key] for key in measurement.FIELDS[:-1]} for row in updated] == before
    assert [row["target_luminance"] for row in updated] == ["0.12345", "0.05", ""]
    assert measurement.read_mapping(path) == updated


def test_bad_measurement_does_not_modify_mapping(tmp_path):
    path = tmp_path / "mapping.csv"
    path.write_text("must remain unchanged\n")
    rows = [{"texture": "A.TGA", "decision": "include"}]
    with pytest.raises(ValueError, match="finite"):
        measurement.update_mapping(path, rows, {"A.TGA": float("nan")})
    assert path.read_text() == "must remain unchanged\n"


def test_city3_measurement_uses_opaque_nonmetal_scope(monkeypatch):
    rows = [
        {"texture": "FLOOR.TGA", "decision": "include", "class": "flagstone"},
        {"texture": "PANEL.TGA", "decision": "panel", "class": "painted_panel"},
        {"texture": "IRON.TGA", "decision": "panel", "class": "iron_panel"},
        {"texture": "ROPE.TGA", "decision": "strip", "class": "rope_strip"},
    ]
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        class Result:
            stdout = "texture,target_luminance\nFLOOR.TGA,0.12\nPANEL.TGA,0.2\n"
        return Result()
    monkeypatch.setattr(measurement.subprocess, "run", run)
    assert measurement.measure(Path("extractor"), Path("game"), rows, "city-3") == {
        "FLOOR.TGA": 0.12, "PANEL.TGA": 0.2}
    assert calls == [(["extractor", "game", "FLOOR.TGA", "PANEL.TGA"],
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
    assert [{key: row[key] for key in measurement.FIELDS[:-1]} for row in updated] == [
        {key: row[key] for key in measurement.FIELDS[:-1]} for row in before]
