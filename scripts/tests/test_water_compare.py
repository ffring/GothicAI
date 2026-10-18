"""Water fragment checks on synthetic native events; no bench recordings needed."""
import importlib.util
import math
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "water_compare", Path(__file__).resolve().parents[1] / "water_compare.py")
water_compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(water_compare)


@pytest.fixture
def rows():
    return [
        {"kind": "water_setup", "bodies": [{"label": "crate", "zen_id": 42}]},
        {"kind": "water_checkpoint", "label": "fragments", "bodies": [
            {"category": 13, "zen_id": 42, "piece": index,
             "density_kg_m3": 28, "sleeping": True, "submerged_fraction": .028,
             "world_bottom_cm": 124.72, "water_y_cm": 125, "world_top_cm": 134.72}
            for index in range(12)
        ]},
    ]


def test_twelve_floating_sleeping_wooden_fragments_pass(rows):
    result = water_compare.fragments(rows)
    assert result["crate_zen_id"] == 42
    assert result["count"] == result["sleeping_count"] == result["wet_count"] == 12


def test_twelve_sleeping_fragments_350_to_400_cm_below_water_fail(rows):
    for piece in rows[1]["bodies"]:
        piece.update(world_bottom_cm=-275, world_top_cm=-225, submerged_fraction=1)
    with pytest.raises(ValueError):
        water_compare.fragments(rows)


def test_one_sunk_original_crate_fragment_among_floaters_fails(rows):
    rows[1]["bodies"][-1].update(
        world_bottom_cm=-275, world_top_cm=-225, submerged_fraction=1)
    with pytest.raises(ValueError):
        water_compare.fragments(rows)


def test_dry_sleeping_fragment_above_surface_fails(rows):
    rows[1]["bodies"][0].update(
        world_bottom_cm=126, world_top_cm=136, submerged_fraction=0)
    with pytest.raises(ValueError):
        water_compare.fragments(rows)


@pytest.mark.parametrize("fraction", [0, 1, .5], ids=["dry", "submerged", "wrong-density"])
def test_crossing_surface_needs_partial_density_consistent_submersion(rows, fraction):
    rows[1]["bodies"][0]["submerged_fraction"] = fraction
    with pytest.raises(ValueError):
        water_compare.fragments(rows)


@pytest.mark.parametrize("field,value", [
    ("water_y_cm", None),
    ("world_bottom_cm", -math.inf),
    ("water_y_cm", math.nan),
    ("world_top_cm", math.inf),
    ("submerged_fraction", math.nan),
    ("submerged_fraction", math.inf),
])
def test_missing_water_or_nonfinite_measurements_fail(rows, field, value):
    rows[1]["bodies"][0][field] = value
    with pytest.raises(ValueError):
        water_compare.fragments(rows)


def test_floating_fragment_must_still_be_sleeping(rows):
    rows[1]["bodies"][0]["sleeping"] = False
    with pytest.raises(ValueError, match="still moving"):
        water_compare.fragments(rows)
