"""Personal install/config and queue boundaries, without SSH or a real game."""
import ctypes
import importlib.machinery
import importlib.util
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


def load(name, filename):
    path = Path(__file__).resolve().parents[1] / filename
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


install = load("ryzen_play", "ryzen-play.sh")
session = load("ryzen_play_session", "ryzen-play-session.sh")


class RyzenPlayTests(unittest.TestCase):
    def test_arguments(self):
        self.assertEqual(install.arguments(["install"]).action, "install")
        self.assertEqual(install.arguments(["update"]).action, "update")
        self.assertFalse(session.arguments([]).smoke)
        self.assertTrue(session.arguments(["--smoke"]).smoke)
        with patch("sys.stderr"):
            for arguments in ([], ["setup"], ["install", "unexpected"]):
                with self.assertRaises(SystemExit) as error:
                    install.arguments(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_settings_copy_without_secrets_or_mac_paths_and_without_overwrite(self):
        source = {
            "game": {"dir": "/Mac/Game", "dev_mode": False, "chat_scale": 1.25},
            "paths": {"repo": "/Mac/Repo", "uv": "/Mac/uv", "game_bin": "/Mac/Game.app",
                      "game_home": "/Mac/Saves"},
            "llm": {"backend": "chatgpt", "reasoning_effort": "medium", "decider_url": "ws://models:1",
                    "body_url": "tcp://body:2", "quick": True},
            "voice": {"provider": "voxtral", "voxtral_url": "http://models:3"},
            "tts": {"provider": "inworld", "voxcpm_url": "ws://models:4"},
            "secrets": {"openai_api_key": "must-not-copy"},
        }
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / ".config/gothic-ai/settings.toml"
            repo = home / "gothic-ai/play"
            self.assertTrue(install.create_settings(path, source, repo, home))
            content = path.read_text()
            result = tomllib.loads(content)
            self.assertEqual(result["game"]["dir"], str(home / "gothic-ai/game"))
            self.assertEqual(result["paths"], {"repo": str(repo), "uv": str(home / ".local/bin/uv")})
            for section in ("llm", "voice", "tts"):
                self.assertEqual(result[section], source[section])
            self.assertNotIn("must-not-copy", content)
            self.assertNotIn("/Mac/", content)
            self.assertEqual(list(path.parent.iterdir()), [path])
            path.write_text("# user's settings\n")
            self.assertFalse(install.create_settings(path, source, repo, home))
            self.assertEqual(path.read_text(), "# user's settings\n")

    def test_prepare_keeps_build_and_all_uv_sync_inside_the_queue(self):
        repo = Path("/tmp/play")
        command = install.build_command(repo, "prepare-source")
        separator = command.index("--")
        options = command[:separator]
        self.assertEqual(options[options.index("--class") + 1], "quick")
        self.assertNotIn("--display-exclusive", options)
        self.assertEqual(command[separator + 1:], [install.sys.executable, "-c", "prepare-source", "__prepare"])
        with patch.dict(install.os.environ), \
                patch.object(install.Path, "cwd", return_value=repo), \
                patch.object(install.shutil, "which", return_value="/tool"), \
                patch.object(install.Path, "is_file", return_value=True), \
                patch.object(install, "run") as run:
            install.prepare()
        calls = [call.args[0] for call in run.call_args_list]
        self.assertIn("-DCMAKE_BUILD_TYPE=RelWithDebInfo", calls[0])
        self.assertEqual(calls[1][1:3], ["--build", repo / "OpenGothic/build"])
        self.assertEqual([call[-1] for call in calls[2:]],
                         [repo / name for name in ("server", "asr-service", "tts-service", "launcher/core")])
        self.assertTrue(all("--locked" in call for call in calls[2:]))

    def test_play_exclusive_six_hour_live_request(self):
        command = session.session_command(Path("/repo"), microphone=False)
        for flag, value in (("--class", "quick"), ("--threads", "8"), ("--mem", "8G"),
                            ("--gpu-mem", "3G"), ("--max", "6h")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--interactive", command)
        self.assertIn("--display-exclusive", command)
        self.assertIn("--continue", command)
        self.assertIn("--no-voice", command)
        self.assertNotIn("--smoke", command)
        smoke = session.session_command(Path("/repo"), smoke=True)
        self.assertIn("--smoke", smoke)
        self.assertIn("--new", smoke)
        self.assertNotIn("--continue", smoke)

    def test_portaudio_checks_loadable_library_not_only_a_filename(self):
        with patch.object(ctypes, "CDLL") as load_library:
            self.assertTrue(session.portaudio_available())
            load_library.assert_called_once_with("libportaudio.so.2")
        with patch.object(ctypes, "CDLL", side_effect=OSError("not installed")):
            self.assertFalse(session.portaudio_available())

    def test_desktop_opens_visible_terminal_with_game_icon(self):
        entry = install.desktop_entry(Path("/home/player/gothic-ai/play"))
        self.assertIn("Name=Gothic AI\n", entry)
        self.assertIn("Exec=ptyxis --new-window", entry)
        self.assertIn("scripts/ryzen-play-session.sh", entry)
        self.assertIn("Icon=/home/player/gothic-ai/play/launcher/app/src-tauri/icons/128x128.png", entry)


if __name__ == "__main__":
    unittest.main()
