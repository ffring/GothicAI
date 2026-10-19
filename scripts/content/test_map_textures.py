"""Synthetic census fixtures cover the city mapping contract without game pixels."""

from copy import deepcopy
import csv
from decimal import Decimal
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).with_name("map_textures.py")
SPEC = importlib.util.spec_from_file_location("map_textures", SCRIPT)
mapping = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mapping)
CENSUS_FIELDS = ("world", "texture", "area_m2", "uv_m_per_unit_median", "tiled_area_fraction", "vob_count")


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def dataset(tmp_path):
    source = [{
        "world": "NEWWORLD.ZEN", "texture": f"NW_CITY_TEST_{index:02d}.TGA",
        "area_m2": "10.25", "uv_m_per_unit_median": "5.01",
        "tiled_area_fraction": "0.8", "vob_count": "0",
    } for index in range(71)]
    source[-1]["texture"] = "NW_HARBOUR_TEST.TGA"
    rows = [{
        "texture": row["texture"], "decision": "include", "class": "cobble",
        "state": "very worn", "scale": "2.51", "area_m2": row["area_m2"],
        "uv_m_per_unit_median": row["uv_m_per_unit_median"], "reason": "tileable surface",
    } for row in source]
    rows[-1].update(decision="exclude", **{"class": "", "state": "", "scale": "", "reason": "object atlas"})
    census_path = tmp_path / "census.csv"
    mapping_path = tmp_path / "materials.csv"

    def save():
        write_csv(census_path, CENSUS_FIELDS, source)
        write_csv(mapping_path, mapping.FIELDS, rows)

    save()
    return source, rows, census_path, mapping_path, save


def test_scale_uses_uv_divided_by_tile_and_decimal_rounding():
    assert mapping.material_scale(Decimal("5.01"), "cobble") == Decimal("2.51")
    assert mapping.material_scale(Decimal("5.01"), "flagstone") == Decimal("1.67")
    assert mapping.material_scale(Decimal("5.01"), "timber_beam") == Decimal("3.34")


def test_complete_scope_and_cli_summary_are_read_only(dataset, capsys):
    source, rows, census_path, mapping_path, save = dataset
    source.extend([
        dict(source[0], texture="OTHER.TGA"),
        dict(source[0], world="OLDWORLD.ZEN"),
    ])
    save()
    before = mapping_path.read_bytes()
    assert mapping.check_mapping(mapping_path, census_path) == rows
    assert mapping.main(["check", "--mapping", str(mapping_path), "--census", str(census_path)]) == 0
    assert mapping_path.read_bytes() == before
    output = capsys.readouterr().out
    assert "rows=71 include=70 exclude=1" in output
    assert "717.50 / 727.75 m2 (98.59%)" in output


@pytest.mark.parametrize("field,value", [
    ("area_m2", "10.26"), ("uv_m_per_unit_median", "5.02"), ("scale", "0.40"),
])
def test_check_rejects_stale_derived_values(dataset, field, value):
    source, rows, census_path, mapping_path, save = dataset
    rows[0][field] = value
    save()
    with pytest.raises(mapping.MappingError, match=f"stale {field}"):
        mapping.check_mapping(mapping_path, census_path)


@pytest.mark.parametrize("change,message", [
    ("missing", "missing textures"), ("extra", "extra textures"), ("duplicate", "duplicate mapping texture"),
])
def test_mapping_must_cover_scope_exactly(dataset, change, message):
    source, rows, census_path, mapping_path, save = dataset
    if change == "missing":
        rows.pop()
    elif change == "extra":
        rows.append(dict(rows[0], texture="NW_CITY_EXTRA.TGA"))
    else:
        rows.append(deepcopy(rows[0]))
    save()
    with pytest.raises(mapping.MappingError, match=message):
        mapping.check_mapping(mapping_path, census_path)


@pytest.mark.parametrize("change,message", [
    ("missing", "expected 71 city/harbour textures, got 70"),
    ("extra", "expected 71 city/harbour textures, got 72"),
    ("duplicate", "duplicate census texture"),
])
def test_census_scope_is_fixed_and_unique(dataset, change, message):
    source, rows, census_path, mapping_path, save = dataset
    if change == "missing":
        source.pop()
    elif change == "extra":
        source.append(dict(source[0], texture="NW_CITY_EXTRA.TGA"))
    else:
        source.append(deepcopy(source[0]))
    save()
    with pytest.raises(mapping.MappingError, match=message):
        mapping.check_mapping(mapping_path, census_path)


@pytest.mark.parametrize("field,value,message", [
    ("decision", "pending", "decision"),
    ("class", "stone", "unknown class"),
    ("class", "", "unknown class"),
    ("state", "worn", "2–5 English words"),
    ("state", "one two three four five six", "2–5 English words"),
    ("state", "очень старый", "2–5 English words"),
    ("state", "worn 2", "2–5 English words"),
    ("reason", " ", "reason must be nonempty"),
    ("area_m2", "NaN", "finite"),
    ("area_m2", "-1", "nonnegative"),
    ("uv_m_per_unit_median", "0", "positive"),
    ("scale", "Infinity", "finite"),
    ("scale", "0", "positive"),
])
def test_invalid_mapping_fields_have_diagnostics(dataset, field, value, message):
    source, rows, census_path, mapping_path, save = dataset
    rows[0][field] = value
    save()
    with pytest.raises(mapping.MappingError, match=message):
        mapping.check_mapping(mapping_path, census_path)


@pytest.mark.parametrize("field,value", [("class", "cobble"), ("state", "very worn"), ("scale", "2.51")])
def test_excluded_rows_have_no_generation_fields(dataset, field, value):
    source, rows, census_path, mapping_path, save = dataset
    rows[-1][field] = value
    save()
    with pytest.raises(mapping.MappingError, match=f"exclude requires empty {field}"):
        mapping.check_mapping(mapping_path, census_path)


def test_tiled_fraction_threshold_is_inclusive(dataset):
    source, rows, census_path, mapping_path, save = dataset
    source[0]["tiled_area_fraction"] = "0.6"
    save()
    mapping.check_mapping(mapping_path, census_path)
    source[0]["tiled_area_fraction"] = "0.5999"
    save()
    with pytest.raises(mapping.MappingError, match="tiled_area_fraction >= 0.6"):
        mapping.check_mapping(mapping_path, census_path)


@pytest.mark.parametrize("field,value,message", [
    ("area_m2", "bad", "finite number"),
    ("area_m2", "-1", "nonnegative"),
    ("uv_m_per_unit_median", "0", "positive"),
    ("uv_m_per_unit_median", "NaN", "finite"),
    ("tiled_area_fraction", "Infinity", "finite"),
    ("tiled_area_fraction", "1.1", "at most 1"),
])
def test_invalid_census_measurements_are_rejected(dataset, field, value, message):
    source, rows, census_path, mapping_path, save = dataset
    source[0][field] = value
    save()
    with pytest.raises(mapping.MappingError, match=message):
        mapping.check_mapping(mapping_path, census_path)


def test_update_recalculates_source_numbers_and_scale_preserving_decisions(dataset):
    source, rows, census_path, mapping_path, save = dataset
    source[0].update(area_m2="20.125", uv_m_per_unit_median="6.015")
    source[-1].update(area_m2="0", uv_m_per_unit_median="1.23")
    rows[0]["class"] = "flagstone"
    manual = [{field: row[field] for field in ("texture", "decision", "class", "state", "reason")} for row in rows]
    save()
    updated = mapping.update_mapping(mapping_path, census_path)
    assert updated[0]["scale"] == "2.01"
    assert updated[0]["area_m2"] == "20.125"
    assert updated[0]["uv_m_per_unit_median"] == "6.015"
    assert updated[-1]["area_m2"] == "0"
    assert updated[-1]["uv_m_per_unit_median"] == "1.23"
    assert updated[-1]["scale"] == ""
    assert [{field: row[field] for field in manual[0]} for row in updated] == manual
    assert mapping.check_mapping(mapping_path, census_path) == updated


def test_invalid_update_does_not_write_partial_results(dataset):
    source, rows, census_path, mapping_path, save = dataset
    rows[0]["area_m2"] = "old"
    rows[-1]["reason"] = ""
    save()
    before = mapping_path.read_bytes()
    with pytest.raises(mapping.MappingError, match="reason must be nonempty"):
        mapping.update_mapping(mapping_path, census_path)
    assert mapping_path.read_bytes() == before


@pytest.mark.parametrize("text,message", [
    ("", "empty CSV"),
    ("texture,texture\n", "duplicate CSV column"),
    ("texture,decision\n", "expected CSV columns in order"),
    (",".join(mapping.FIELDS) + "\nshort,row\n", "expected 8 CSV fields, got 2"),
])
def test_malformed_mapping_csv(dataset, text, message):
    source, rows, census_path, mapping_path, save = dataset
    mapping_path.write_text(text)
    with pytest.raises(mapping.MappingError, match=message):
        mapping.check_mapping(mapping_path, census_path)


def test_cli_failures_are_actionable_without_traceback(tmp_path):
    missing = tmp_path / "missing.csv"
    result = subprocess.run([
        sys.executable, str(SCRIPT), "check", "--mapping", str(missing), "--census", str(missing),
    ], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Material mapping check failed:" in result.stderr
    assert "missing.csv" in result.stderr
    assert "Traceback" not in result.stderr
