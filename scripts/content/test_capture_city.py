"""Validate city shot coverage and actual replacement census without a game or SSH."""

import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).with_name("capture-city.sh")
SCENARIO = SCRIPT.with_name("city.bench")
ROOT = SCRIPT.parents[2]


def test_city_scenario_preserves_render_measurement_and_visits_authored_sites():
    def commands(path):
        return [line.split("#", 1)[0].strip() for line in path.read_text().splitlines()
                if line.split("#", 1)[0].strip()]

    original = commands(ROOT / "reports/render-features/2026-10-18/tools/rf.bench")
    scenario = commands(SCENARIO)
    end = original.index("shot city_night") + 1
    assert scenario[:end] == original[:end]
    assert [line[5:] for line in scenario if line.startswith("goto ")] == [
        "NW_CITY_MERCHANT_SHOP01_FRONT_03", "MARKT", "NW_CITY_HABOUR_06_B",
        "NW_CITY_UPTOWN_PATH_13", "NW_CITY_MERCHANT_HUT_02_IN_02",
    ]
    for spot in ("city", "market", "harbour", "upper", "house"):
        assert scenario.count(f"shot {spot}") == 1
        assert scenario.count(f"shot {spot}_night") == 1
    assert scenario.count("set time 12 0") == 5
    assert scenario.count("set time 23 0") == 5


def fixture_capture(tmp_path, variant="after"):
    directory = tmp_path / variant
    directory.mkdir()
    scenario = tmp_path / "city.bench"
    scenario.write_text("goto MARKT\nshot market\n")
    index = tmp_path / "materials.json"
    index.write_text(json.dumps({"WALL.TGA": {"albedo": "wall/albedo.dds"}}))
    (directory / "exit.txt").write_text("0\n")
    (directory / "market.png").write_bytes(b"screenshot fixture")
    (directory / "game.log").write_text(
        f"materials: {int(variant == 'after')} entries from fixture\n"
        "[bench] goto MARKT\n[bench] shot market\n[bench] done\n"
    )
    replacement = {"name": "WALL.TGA", "kind": "texture", "opens": 1,
                   "replacement": "/content/after/materials/wall/albedo.dds"}
    original = {"name": "WALL-C.TEX", "kind": "texture", "opens": 1, "source": "Textures.vdf"}
    census = {"files": [] if variant == "after" else [original],
              "replacements": [replacement] if variant == "after" else []}
    (directory / "census.json").write_text(json.dumps(census))
    return directory, scenario, index, census, original


def run_check(directory, scenario, index, variant="after"):
    return subprocess.run(
        ["bash", str(SCRIPT), "--check", str(directory), str(scenario), str(index), variant],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("variant", ["before", "after"])
@pytest.mark.parametrize("relative", [False, True])
def test_census_accepts_original_baseline_and_own_replacement(tmp_path, variant, relative):
    directory, scenario, index, census, _ = fixture_capture(tmp_path, variant)
    if relative and variant == "after":
        census["replacements"][0]["replacement"] = "materials/wall/albedo.dds"
        (directory / "census.json").write_text(json.dumps(census))
    result = run_check(directory, scenario, index, variant)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"replaced={int(variant == 'after')} originals_opened={int(variant == 'before')}" in result.stdout


def test_census_rejects_compiled_original_alias_even_with_replacement(tmp_path):
    directory, scenario, index, census, original = fixture_capture(tmp_path)
    census["files"].append(original)
    (directory / "census.json").write_text(json.dumps(census))
    result = run_check(directory, scenario, index)
    assert result.returncode != 0
    assert "original target textures opened: WALL" in result.stderr


@pytest.mark.parametrize("failure", ["missing_replacement", "wrong_map", "missing_shot", "goto_failed", "bad_event"])
def test_capture_rejects_incomplete_or_incorrect_work(tmp_path, failure):
    directory, scenario, index, census, _ = fixture_capture(tmp_path)
    if failure == "missing_replacement":
        census["replacements"] = []
    elif failure == "wrong_map":
        census["replacements"][0]["replacement"] = "/other/materials/other/albedo.dds"
    elif failure == "missing_shot":
        (directory / "market.png").unlink()
    elif failure == "goto_failed":
        with (directory / "game.log").open("a") as log:
            log.write("[bench] goto failed: MARKT\n")
    elif failure == "bad_event":
        (directory / "events.jsonl").write_text('{"kind":"error","reason":"fixture"}\n')
    (directory / "census.json").write_text(json.dumps(census))
    result = run_check(directory, scenario, index)
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("escape", ["../outside.dds", "/outside.dds", "escape.dds"])
def test_packed_set_rejects_dds_paths_escaping_material_directory(tmp_path, escape):
    source = tmp_path / "content/materials"
    source.mkdir(parents=True)
    (tmp_path / "content/outside.dds").write_bytes(b"not a generated map")
    (source / "escape.dds").symlink_to(tmp_path / "content/outside.dds")
    (source / "materials.json").write_text(json.dumps({
        "WALL.TGA": {"albedo": escape, "normal": "normal.dds", "orm": "orm.dds"},
    }))
    result = subprocess.run(
        ["bash", str(SCRIPT), "--content-root", str(source.parent), "--output", str(tmp_path / "output")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "unsafe DDS path" in result.stderr
