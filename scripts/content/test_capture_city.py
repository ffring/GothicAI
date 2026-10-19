"""Validate city shot coverage and actual replacement census without a game or SSH."""

import csv
import json
import os
from pathlib import Path
import subprocess

from PIL import Image
import pytest


SCRIPT = Path(__file__).with_name("capture-city.sh")
SCENARIO = SCRIPT.with_name("city.bench")
CITY2_SCENARIO = SCRIPT.with_name("city-2.bench")
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


def test_city2_preserves_city_camera_and_captures_house_in_both_lights():
    def commands(path):
        return [line.split("#", 1)[0].strip() for line in path.read_text().splitlines()
                if line.split("#", 1)[0].strip()]

    original = commands(SCENARIO)
    scenario = commands(CITY2_SCENARIO)
    end = original.index("shot city_night") + 1
    assert scenario[:end] == original[:end]
    shots = [line[5:] for line in scenario if line.startswith("shot ")]
    assert len(shots) == len(set(shots))
    assert set(shots) == {
        "city_a", "city", "city_night", "harbour", "market", "market_night",
        "facade_close", "facade_close_night", "facade_corner", "facade_corner_night",
    }
    hour = None
    at_shot = {}
    for command in scenario:
        if command.startswith("set time "):
            hour = command.removeprefix("set time ")
        elif command.startswith("shot "):
            at_shot[command[5:]] = hour
    for name in ("city", "harbour", "market", "facade_close", "facade_corner"):
        assert at_shot[name] == "12 0"
    for name in ("market", "facade_close", "facade_corner"):
        assert at_shot[name + "_night"] == "23 0"
    assert scenario.count("cam hold") == 2
    assert "goto pos 9137 2725 3745" in scenario
    assert "goto pos 9162 2350 3207" in scenario


@pytest.mark.parametrize("set_name", ["city-2", "city-2-imagegen"])
def test_city2_remote_rejects_other_set_directory(set_name):
    result = subprocess.run(
        ["bash", str(SCRIPT), "--remote", str(Path.home() / "gothic-ai/city-set-1"),
         "fixture", set_name],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_capture_rejects_unsafe_set_before_creating_output(tmp_path):
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", str(SCRIPT), "--set", "../../other", "--content-root", str(tmp_path),
         "--output", str(output)], capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "--set must be city-1 or city-2" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("set_name,flux,reason", [
    ("city-2-imagegen", False, "requires --flux-root"),
    ("city-2", True, "--flux-root requires --set city-2-imagegen"),
])
def test_flux_root_is_required_only_for_triple_capture(tmp_path, set_name, flux, reason):
    output = tmp_path / "output"
    command = ["bash", str(SCRIPT), "--set", set_name, "--content-root", str(tmp_path),
               "--output", str(output)]
    if flux:
        command.extend(["--flux-root", str(tmp_path)])
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert reason in result.stderr
    assert not output.exists()


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
        f"materials: {int(variant != 'before')} entries from fixture\n"
        "[bench] goto MARKT\n[bench] shot market\n[bench] done\n"
    )
    replacement = {"name": "WALL.TGA", "kind": "texture", "opens": 1,
                   "replacement": "/content/after/materials/wall/albedo.dds"}
    original = {"name": "WALL-C.TEX", "kind": "texture", "opens": 1, "source": "Textures.vdf"}
    census = {"files": [] if variant != "before" else [original],
              "replacements": [replacement] if variant != "before" else []}
    (directory / "census.json").write_text(json.dumps(census))
    return directory, scenario, index, census, original


def run_check(directory, scenario, index, variant="after"):
    return subprocess.run(
        ["bash", str(SCRIPT), "--check", str(directory), str(scenario), str(index), variant],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("variant", ["before", "flux", "after"])
@pytest.mark.parametrize("relative", [False, True])
def test_census_accepts_original_baseline_and_own_replacement(tmp_path, variant, relative):
    directory, scenario, index, census, _ = fixture_capture(tmp_path, variant)
    if relative and variant != "before":
        census["replacements"][0]["replacement"] = "materials/wall/albedo.dds"
        (directory / "census.json").write_text(json.dumps(census))
    result = run_check(directory, scenario, index, variant)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"replaced={int(variant != 'before')} originals_opened={int(variant == 'before')}" in result.stdout


@pytest.mark.parametrize("set_name,variants,count", [
    ("city-1", ("before", "after"), 10),
    ("city-2", ("before", "after"), 8),
    ("city-2-imagegen", ("before", "flux", "after"), 5),
])
def test_composition_preserves_pairs_and_builds_required_triple_columns(tmp_path, set_name, variants, count):
    captures = tmp_path / "captures"
    colors = {"before": (200, 20, 20), "flux": (20, 200, 20), "after": (20, 20, 200)}
    for variant in variants:
        directory = captures / variant
        directory.mkdir(parents=True)
        for spot in ("city", "market", "harbour", "upper", "house", "facade_close", "facade_corner"):
            for suffix in ("", "_night"):
                Image.new("RGB", (96, 54), colors[variant]).save(directory / f"{spot}{suffix}.png")
    output = tmp_path / "boards"
    result = subprocess.run(["bash", str(SCRIPT), "--compose", str(captures), str(output), set_name],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    boards = list(output.glob("*.jpg"))
    assert len(boards) == count
    if set_name == "city-2-imagegen":
        assert {path.stem for path in boards} == {
            "facade_close", "facade_close_night", "market", "facade_corner", "harbour"}
    for path in boards:
        with Image.open(path) as board:
            assert board.size == (96 * len(variants), 94)
            for column, variant in enumerate(variants):
                pixel = board.getpixel((column * 96 + 48, 65))
                assert max(abs(actual - expected) for actual, expected in zip(pixel, colors[variant])) < 5


@pytest.mark.parametrize("set_name,variants", [
    ("city-2", ("before", "after")),
    ("city-2-imagegen", ("before", "flux", "after")),
])
def test_remote_dispatch_checks_each_variant_against_its_index(tmp_path, set_name, variants):
    task = tmp_path / "gothic-ai" / ("city-set-" + set_name.removeprefix("city-"))
    capture = task / "inputs/fixture"
    tools = capture / "runtime/tools"
    tools.mkdir(parents=True)
    (tools / "capture-city.sh").write_text(SCRIPT.read_text())
    (tools / "city.bench").write_text("goto MARKT\nshot market\n")
    for variant in variants:
        directory = capture / "content" / variant / "materials"
        directory.mkdir(parents=True)
        # Distinct map names catch validation against another variant's index.
        index = {} if variant == "before" else {"WALL.TGA": {"albedo": f"{variant}/albedo.dds"}}
        (directory / "materials.json").write_text(json.dumps(index))
    (tools / "run-ryzen.sh").write_text('''#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" "$3" <<'PY'
import json
import os
from pathlib import Path
import sys
variant = Path(sys.argv[2]).read_text().split()[0]
root = Path(sys.argv[1]) / variant
root.mkdir(parents=True)
index = json.loads((Path(os.environ['OPENGOTHIC_CONTENT']) / 'materials/materials.json').read_text())
(root / 'exit.txt').write_text('0')
(root / 'market.png').write_bytes(b'fixture')
(root / 'game.log').write_text(f'materials: {len(index)} entries from fixture\\n'
                               '[bench] goto MARKT\\n[bench] shot market\\n[bench] done\\n')
original = {'name': 'WALL.TGA', 'kind': 'texture', 'source': 'Textures.vdf', 'opens': 1}
replacements = [{'name': name, 'kind': 'texture', 'opens': 1,
                 'replacement': 'materials/' + entry['albedo']} for name, entry in index.items()]
Path(os.environ['OPENGOTHIC_CENSUS']).write_text(json.dumps({
    'files': [original] if variant == 'before' else [], 'replacements': replacements}))
PY
''')
    result = subprocess.run(["bash", str(SCRIPT), "--remote", str(task), "fixture", set_name],
                            capture_output=True, text=True, env={**os.environ, "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert [line.split(":", 1)[0] for line in result.stdout.splitlines()] == list(variants)
    assert {path.name for path in (task / "runs/fixture").iterdir()} == set(variants)


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
