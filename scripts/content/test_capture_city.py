"""Validate city shot coverage and actual replacement census without a game or SSH."""

import csv
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


def fps_fixture(tmp_path):
    results = []
    for variant in ("before", "after"):
        directory = tmp_path / variant
        directory.mkdir()
        def frame(begin, flags=0, outcome=1, dt=0):
            return {"flags": flags, "outcome": outcome, "dt": dt,
                    "begin_ns": begin, "end_ns": begin + 1000}
        rows = [frame(0, flags=4)]
        # Fixed dt advances game time on fence-busy attempts too: 19.8 s game time,
        # 5 s wall time, and 300 presented frames is a complete, valid FPS window.
        rows.extend(frame(number * 1000000, outcome=1 if number % 2 else 2, dt=33)
                    for number in range(1, 601))
        rows.append(frame(5000001000, flags=4))
        rows.extend(frame(6000000000 + number * 1000000) for number in range(3))
        rows.extend(frame(7000000000 + number * 1000000, flags=4) for number in range(3))
        rows.extend(frame(8000000000 + number * 1000000) for number in range(3))
        rows.append(frame(9000000000, flags=4))
        with (directory / "frames.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        results.append({"label": variant, "exit": "0", "done": True, "overflow": 0,
                        "spots": {"city": {"fps": 60, "frames": 300, "wall_ms": 5000}}})
    (tmp_path / "summary.json").write_text(json.dumps(results))
    return results


def run_fps_check(directory):
    return subprocess.run(["bash", str(SCRIPT), "--check-fps", str(directory), "city"],
                          capture_output=True, text=True)


def test_fps_check_requires_both_actual_measurements(tmp_path):
    fps_fixture(tmp_path)
    result = run_fps_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "before/city: fps=60.000 frames=300 wall_ms=5000.000 game_ms=19800" in result.stdout
    assert "after/city: fps=60.000 frames=300 wall_ms=5000.000 game_ms=19800" in result.stdout


@pytest.mark.parametrize("failure", ["profile_missing", "profile_empty", "window_missing", "frames_missing",
                                     "fps_missing", "fps_nan", "fps_inconsistent", "window_short", "overflow",
                                     "markers_missing", "wall_inconsistent"])
def test_fps_check_rejects_successful_game_without_valid_frame_measurement(tmp_path, failure):
    results = fps_fixture(tmp_path)
    metric = results[1]["spots"]["city"]
    if failure == "profile_missing":
        (tmp_path / "after/frames.csv").unlink()
    elif failure == "profile_empty":
        (tmp_path / "after/frames.csv").write_text("")
    elif failure == "window_missing":
        results[1]["spots"] = {}
    elif failure == "frames_missing":
        del metric["frames"]
    elif failure == "fps_missing":
        del metric["fps"]
    elif failure == "fps_nan":
        metric["fps"] = float("nan")
    elif failure == "fps_inconsistent":
        metric["fps"] = 30
    elif failure == "window_short":
        profile = tmp_path / "after/frames.csv"
        with profile.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows[1:601]:
            row["dt"] = "1"
        with profile.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
    elif failure == "overflow":
        results[1]["overflow"] = 1
    elif failure == "markers_missing":
        profile = tmp_path / "after/frames.csv"
        profile.write_text(profile.read_text().replace("4,", "0,"))
    elif failure == "wall_inconsistent":
        metric["wall_ms"] = 20000
    (tmp_path / "summary.json").write_text(json.dumps(results))
    result = run_fps_check(tmp_path)
    assert result.returncode != 0, result.stdout
    assert "capture-city FPS: after" in result.stderr
