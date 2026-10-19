"""One selection helper keeps generation, packing and brightness in the same scope."""
import csv
from pathlib import Path

import pytest

from pilot import antitile_mode, CITY_2_MODULES, MODULE_ASPECT_RATIOS, MODULE_REPEAT_AXES, selected_rows


def test_city2_has_22_unique_textures_with_two_shared_include_facade_entries():
    with (Path(__file__).resolve().parents[2] / "data/content/city-materials.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    includes = selected_rows(rows, "city-1")
    pilot = selected_rows(rows, "city-2")
    assert len(includes) == 16
    assert len(pilot) == 22
    assert len({row["texture"] for row in includes} & set(CITY_2_MODULES)) == 2
    assert sum(row["decision"] != "include" for row in pilot) == 6


def test_only_city2_adds_facade_rows_and_preserves_csv_order():
    rows = [{"texture": "A.TGA", "decision": "include"},
            {"texture": "NW_CITY_WALL_03.TGA", "decision": "module"},
            {"texture": "OTHER.TGA", "decision": "module"}]
    assert selected_rows(rows, "city-1") == rows[:1]
    assert selected_rows(rows, "city-2") == rows[:2]
    with pytest.raises(ValueError, match="repeats selected"):
        selected_rows(rows + [rows[0]], "city-2")


def test_include_class_policy_applies_and_architecture_disables_antitile():
    assert antitile_mode({"decision": "include", "class": "planks_wall", "anti_repeat": "off"}) == "offset"
    assert antitile_mode({"decision": "include", "class": "flagstone", "anti_repeat": "on"}) == "rotate"
    assert antitile_mode({"decision": "module", "class": "timber_frame_tile"}) == "off"
    assert MODULE_ASPECT_RATIOS["NW_CITY_WOODBEAM_02.TGA"] == pytest.approx(1.15 / 10.14)
    assert MODULE_REPEAT_AXES["NW_CITY_WOODBEAM_02.TGA"] == "v"
