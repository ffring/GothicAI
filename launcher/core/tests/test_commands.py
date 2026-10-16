import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from gothic_launcher.commands import (
    LaunchError,
    Layout,
    Ports,
    child_env,
    find_layout,
    game,
    latest_save,
    services,
)
from gothic_launcher.settings import Config

PORTS = Ports(server=50001, asr=50002, tts=50003)
LAYOUT = Layout(
    Path("/repo"), Path("/bin/uv"), Path("/repo/Gothic2Notr"), Path("/games/G2"), Path("/home/game")
)


@pytest.fixture
def config(tmp_path):
    return Config.load(tmp_path)


def by_name(procs):
    return {p.name: p for p in procs}


def test_services_get_their_settings_and_find_each_other(config, tmp_path):
    config.set("voice.voxtral_url", "http://gpu:47830")
    config.set("tts.provider", "voxcpm")
    config.set("tts.fallback", "none")
    config.set("tts.loudness", "-16")
    config.set("llm.model", "gpt-6.1-sol")
    config.set("llm.decider_url", "ws://gpu:47832")
    config.set("llm.quick_url", "http://gpu:47834/v1")
    config.set("secrets.inworld_api_key", "iw-secret")
    config.set("secrets.openai_api_key", "sk-secret")

    procs = services(config, LAYOUT, PORTS, tmp_path)

    assert [p.name for p in procs] == ["asr", "tts", "server"]
    asr, tts, server = by_name(procs).values()
    assert asr.argv[:5] == ["/bin/uv", "run", "--project", "/repo/asr-service", "gothic-asr"]
    assert asr.argv[5:] == ["serve", "--port", "50002", "--provider", "voxtral", "--fallback", "chatgpt"]
    assert asr.env["GOTHIC_VOXTRAL_URL"] == "http://gpu:47830"
    assert tts.argv[5:] == [
        "serve",
        "--port",
        "50003",
        "--provider",
        "voxcpm",
        "--fallback",
        "none",
        "--loudness=-16",
    ]
    assert tts.env["INWORLD_API_KEY"] == "iw-secret"
    assert server.health == "http://127.0.0.1:50001/health" and server.cwd == tmp_path
    args = server.argv
    assert args[args.index("--asr-url") + 1] == "ws://127.0.0.1:50002"
    assert args[args.index("--tts-url") + 1] == "ws://127.0.0.1:50003"
    assert args[args.index("--model") + 1] == "gpt-6.1-sol" and "--no-console" in args
    assert args[args.index("--decider-url") + 1] == "ws://gpu:47832"
    assert args[args.index("--quick-url") + 1] == "http://gpu:47834/v1" and "--no-quick" not in args
    assert args[args.index("--game-dir") + 1] == "/games/G2"
    for p in procs:  # keys never in argv: they would end up in logs and `ps`
        assert not any("secret" in a for a in p.argv)


def test_voice_and_npc_voices_can_be_off(config, tmp_path):
    config.set("voice.enabled", "false")
    config.set("tts.enabled", "false")
    procs = services(config, LAYOUT, PORTS, tmp_path)
    assert [p.name for p in procs] == ["server"]
    assert "--no-voice" in procs[0].argv and "--no-tts" in procs[0].argv


def test_the_quick_model_is_on_unless_turned_off(config, tmp_path):
    assert "--no-quick" not in by_name(services(config, LAYOUT, PORTS, tmp_path))["server"].argv
    config.set("llm.quick", "false")
    assert "--no-quick" in by_name(services(config, LAYOUT, PORTS, tmp_path))["server"].argv


def test_the_game_runs_in_its_home_and_finds_the_server(config):
    config.set("game.chat_scale", "1.5")
    g = game(config, LAYOUT, PORTS)
    assert g.argv == ["/repo/Gothic2Notr", "-g", "/games/G2"]
    assert g.cwd == Path("/home/game") and not g.restart and g.health is None
    assert g.env == {"GOTHIC_AI_URL": "ws://127.0.0.1:50001", "GOTHIC_AI_CHAT_SCALE": "1.5"}
    assert game(config, LAYOUT, PORTS, save="3").argv[-2:] == ["-save", "3"]
    assert game(config, LAYOUT, PORTS, new=True).argv[-1] == "-nomenu"
    config.set("game.dev_mode", "true")
    assert game(config, LAYOUT, PORTS).argv[-1] == "-devmode"


def test_the_game_gets_the_sharper_textures_when_they_are_made(config, tmp_path):
    assert "OPENGOTHIC_TEXTURES" not in game(config, LAYOUT, PORTS).env
    with_hd = Layout(LAYOUT.repo, LAYOUT.uv, LAYOUT.game_bin, LAYOUT.game_dir, LAYOUT.game_home, tmp_path)
    assert game(config, with_hd, PORTS).env["OPENGOTHIC_TEXTURES"] == str(tmp_path)


def test_the_game_gets_the_fracture_cache_when_made(config, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENGOTHIC_FRACTURE", raising=False)
    assert "OPENGOTHIC_FRACTURE" not in game(config, LAYOUT, PORTS).env
    layout = replace(LAYOUT, fracture_cache=tmp_path)
    assert game(config, layout, PORTS).env["OPENGOTHIC_FRACTURE"] == str(tmp_path)


@pytest.mark.parametrize("override", ["/another/fracture", ""])
def test_the_game_preserves_an_explicit_fracture_override(config, tmp_path, monkeypatch, override):
    monkeypatch.setenv("OPENGOTHIC_FRACTURE", override)
    layout = replace(LAYOUT, fracture_cache=tmp_path)
    assert game(config, layout, PORTS).env["OPENGOTHIC_FRACTURE"] == override


def test_latest_save(tmp_path):
    assert latest_save(tmp_path) is None
    for i, slot in enumerate(["1", "0", "12"]):
        p = tmp_path / f"save_slot_{slot}.sav"
        p.write_bytes(b"")
        os.utime(p, (time.time() + i, time.time() + i))
    (tmp_path / "save_slot_x.sav").write_bytes(b"")
    assert latest_save(tmp_path) == "12"


def test_the_inherited_environment_cannot_override_the_settings(monkeypatch):
    monkeypatch.setenv("GOTHIC_TTS_PROVIDER", "codex")
    monkeypatch.setenv("INWORLD_API_KEY", "from-the-shell")
    monkeypatch.setenv("HOME_TEST_VALUE", "kept")
    monkeypatch.setenv("VIRTUAL_ENV", "/launcher/.venv")
    env = child_env({"GOTHIC_AI_URL": "ws://127.0.0.1:1"})
    assert "GOTHIC_TTS_PROVIDER" not in env and "INWORLD_API_KEY" not in env and "VIRTUAL_ENV" not in env
    assert env["HOME_TEST_VALUE"] == "kept" and env["GOTHIC_AI_URL"] == "ws://127.0.0.1:1"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_find_layout_lists_everything_missing(config, tmp_path):
    repo = tmp_path / "repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server" / "pyproject.toml").write_text("")
    config.set("paths.repo", str(repo))
    config.set("paths.uv", "/bin/sh")
    with pytest.raises(LaunchError) as err:
        find_layout(config)
    assert "not built" in str(err.value) and "game folder is not set" in str(err.value)

    game_dir = tmp_path / "Gothic II"
    (game_dir / "DATA").mkdir(parents=True)  # case differs between installs
    binary = repo / "OpenGothic" / "build" / "opengothic" / "Gothic2Notr"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    config.set("game.dir", str(game_dir))
    layout = find_layout(config)
    assert layout.game_bin == binary and layout.game_dir == game_dir and layout.uv == Path("/bin/sh")

    bundled = binary.parent / "OpenGothic Dev.app" / "Contents" / "MacOS" / "Gothic2Notr"
    bundled.parent.mkdir(parents=True)
    bundled.symlink_to("../../../Gothic2Notr")  # as scripts/make-dev-app.sh makes it
    assert find_layout(config).game_bin == bundled  # the game gets its own name and icon in the Dock


def test_free_ports_differ():
    ports = Ports.free()
    assert len({ports.server, ports.asr, ports.tts}) == 3
