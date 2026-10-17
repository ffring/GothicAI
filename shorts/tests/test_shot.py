from pathlib import Path

import pytest

from gothic_shorts.shot import ShotError, load

SHOT = '''
title = "Проба"
hook = "Зацепка"
npc = "NONE_100_XARDAS"
music = 0.2
script = """
cam shoulder {npc}   # over the hero's shoulder
rec main
hero Ксардас, привет.
waitreply
hero Пока.
stop
"""
'''


def test_hero_lines_become_speak_with_their_recording(tmp_path):
    path = tmp_path / "probe.toml"
    path.write_text(SHOT)
    shot = load(path)
    assert (shot.name, shot.title, shot.music) == ("probe", "Проба", 0.2)
    assert shot.hero_lines == ["Ксардас, привет.", "Пока."]
    wav = tmp_path / "a.wav"
    scenario = shot.scenario({"Ксардас, привет.": wav, "Пока.": wav})
    assert scenario.splitlines() == [
        "cam shoulder NONE_100_XARDAS",
        "rec main",
        f"ptt {wav.resolve()} Ксардас, привет.",
        "waitreply",
        f"ptt {wav.resolve()} Пока.",
        "stop",
    ]


def test_a_shot_has_to_record(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('script = "hero Привет"')
    with pytest.raises(ShotError, match="rec"):
        load(path)


@pytest.mark.parametrize(
    "setting, expected", [("", True), ("brand = true\n", True), ("brand = false\n", False)]
)
def test_brand_defaults_on_and_reads_toml(tmp_path, setting, expected):
    path = tmp_path / "probe.toml"
    path.write_text(setting + SHOT)
    shot = load(path)
    assert shot.brand is expected
    assert "brand" not in shot.extra


def test_the_shots_in_the_repository_load():
    for path in (Path(__file__).parents[1] / "shots").glob("*.toml"):
        assert load(path).hero_lines
