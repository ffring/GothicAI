"""Water settling checks on synthetic native events; no bench recordings needed."""
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
             "density_kg_m3": 28, "sleeping": True, "submerged_fraction": .6,
             "world_bottom_cm": 119, "water_y_cm": 125, "world_top_cm": 129}
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


@pytest.mark.parametrize("fraction", [0, 1, .021, .028, .5],
                         ids=["dry", "submerged", "old-barrel-draft", "old-crate-draft", "wrong-target"])
def test_crossing_surface_needs_wooden_fragment_target_submersion(rows, fraction):
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


@pytest.fixture
def settled():
    # These physical densities still include native hollow/open-space mass
    # multipliers. Only the displaced-volume targets differ for wooden Vobs.
    measurements = [
        ("barrel", 21, 1 / 3), ("crate", 28, .5), ("table", 28, .9),
        ("arrow", 700, .7), ("wood_item", 700, .7),
        ("sword", 7800, 1), ("stone", 2200, 1),
    ]
    return {"case": "deep", "bodies": [
        {"label": label, "density_kg_m3": density, "submerged_fraction": fraction,
         "world_bottom_cm": 125 - 10 * fraction, "water_y_cm": 125,
         "world_top_cm": 135 - 10 * fraction, "sleeping": True, "first_sleep_ms": 500}
        for label, density, fraction in measurements
    ]}


def test_resting_uses_class_targets_without_changing_physical_density(settled):
    result = {body["what"]: body for body in water_compare.resting(settled)}
    assert {label: result[label]["submerged_fraction"] for label in ("barrel", "crate", "table")} == {
        "barrel": 1 / 3, "crate": .5, "table": .9,
    }
    assert {label: result[label]["density_kg_m3"] for label in ("barrel", "crate", "table")} == {
        "barrel": 21, "crate": 28, "table": 28,
    }
    assert all(result[label]["behaviour"] == "floats" for label in ("barrel", "crate", "table"))


@pytest.mark.parametrize("label,fraction", [("barrel", .021), ("crate", .028), ("table", .028)])
def test_resting_rejects_old_mass_density_draft_for_wooden_vobs(settled, label, fraction):
    body = next(body for body in settled["bodies"] if body["label"] == label)
    body.update(submerged_fraction=fraction, world_bottom_cm=125 - 10 * fraction,
                world_top_cm=135 - 10 * fraction)
    with pytest.raises(ValueError, match=f"{label}: displaced volume"):
        water_compare.resting(settled)


def test_resting_keeps_item_material_density_targets(settled):
    result = {body["what"]: body for body in water_compare.resting(settled)}
    assert result["arrow"]["submerged_fraction"] == result["wood_item"]["submerged_fraction"] == .7
    assert result["sword"]["behaviour"] == result["stone"]["behaviour"] == "sinks"


@pytest.mark.parametrize("label,fraction", [("arrow", .6), ("wood_item", .6),
                                           ("sword", .9), ("stone", .9)])
def test_resting_rejects_changed_item_displacement_targets(settled, label, fraction):
    next(body for body in settled["bodies"] if body["label"] == label)["submerged_fraction"] = fraction
    with pytest.raises(ValueError, match=label):
        water_compare.resting(settled)
