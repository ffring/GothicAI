"""Shared city-set scope and placement rules; no image inputs."""

TARGET_LUMINANCE_TOLERANCE = 0.002

CITY_2_MODULES = (
    "NW_CITY_WALL_03.TGA",
    "NW_CITY_WINDOW_03.TGA",
    "NW_CITY_FACHWERKBASEMENT_WINDOW_01.TGA",
    "NW_CITY_FACHWERKBASEMENT_CHAIN_01.TGA",
    "NW_CITY_FACHWERKBASEMENT_EMPTY_01.TGA",
    "NW_CITY_WOODBEAM_02.TGA",
    "NW_CITY_ROOF_01.TGA",
    "NW_CITY_HAFEN_HOLZWAND_01.TGA",
)

# Physical U/V proportions of the architectural repeat, before square UV packing.
MODULE_ASPECT_RATIOS = {
    "NW_CITY_WALL_03.TGA": 2.00 / 2.06,
    "NW_CITY_WINDOW_03.TGA": 4.00 / 3.75,
    "NW_CITY_FACHWERKBASEMENT_WINDOW_01.TGA": 1.0,
    "NW_CITY_FACHWERKBASEMENT_CHAIN_01.TGA": 2.95 / 2.61,
    "NW_CITY_FACHWERKBASEMENT_EMPTY_01.TGA": 1.0,
    "NW_CITY_WOODBEAM_02.TGA": 1.15 / 10.14,
}
MODULE_REPEAT_AXES = {"NW_CITY_WOODBEAM_02.TGA": "v"}

ANTITILE_BY_CLASS = {
    "cobble": "rotate", "flagstone": "rotate", "masonry_dressed": "offset",
    "masonry_rubble": "rotate", "plaster": "rotate", "planks_floor": "offset",
    "planks_wall": "offset", "timber_beam": "offset", "roof_shingle": "offset",
    "roof_tiles": "offset", "thatch": "offset", "dirt_path": "rotate", "mud": "rotate",
}


def selected_rows(rows, set_name):
    """Keep CSV order and deduplicate the facade/include overlap in city-2."""
    selected = [row for row in rows if row.get("decision") == "include"
                or (set_name == "city-2" and row.get("texture") in CITY_2_MODULES)]
    names = [row["texture"] for row in selected]
    if len(names) != len(set(names)):
        raise ValueError(f"mapping repeats selected texture in {set_name}")
    return selected


def antitile_mode(row):
    """The city-2 brief uses class policy for homogeneous surfaces."""
    return ANTITILE_BY_CLASS[row["class"]] if row["decision"] == "include" else "off"
