"""Game startup contention checks without starting a server or game."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "e2e.sh"


class LocalCheckWaitTests(unittest.TestCase):
    def test_only_mac_waits_for_existing_pytest_or_game(self):
        source = SCRIPT.read_text()
        guard = source[source.index("# Recheck Mac contention"):source.index("GAME_ENV=(")]
        harness = r'''
uname() { printf '%s\n' "$TEST_PLATFORM"; }
pgrep() {
  echo pgrep >> "$CALLS"
  if [ ! -e "$SEEN" ]; then
    : > "$SEEN"
    printf '%s\n' "$BUSY_PROCESS" | grep -E "$2"
  else
    return 1
  fi
}
sleep() { printf 'sleep %s\n' "$1" >> "$CALLS"; }
'''
        for platform in ("Darwin", "Linux"):
            for process in (
                "/usr/bin/python3 /tmp/server/.venv/bin/pytest tests/test_protocol.py",
                "/tmp/build/Gothic2Notr -g /tmp/gothic",
            ):
                with self.subTest(platform=platform, process=process), tempfile.TemporaryDirectory() as temp:
                    calls = Path(temp) / "calls"
                    done = subprocess.run(
                        ["bash", "-ec", harness + guard + "echo ready"],
                        check=False,
                        env=dict(os.environ, TEST_PLATFORM=platform, BUSY_PROCESS=process,
                                 CALLS=str(calls), SEEN=str(Path(temp) / "seen")),
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(done.stdout, "ready\n")
                    observed = calls.read_text().splitlines() if calls.exists() else []
                    self.assertEqual(observed, ["pgrep", "sleep 5", "pgrep"] if platform == "Darwin" else [])


class SoakControlTests(unittest.TestCase):
    def test_control_selects_native_body_without_relaxing_other_launch_requirements(self):
        source = SCRIPT.read_text()
        selection = source[source.index('SCENARIO="${SCENARIO:-m0}"'):source.index('ASR="${ASR:-fake}"')]
        cases = [
            ("neural-soak", "1", "", "", "fake", 0, "--no-neural-body"),
            ("neural-soak", "1", "tcp://policy:47835", "", "fake", 0, "--no-neural-body"),
            ("neural-soak", "0", "tcp://policy:47835", "", "fake", 0, "--neural-body"),
            ("neural-soak", "0", "", "", "fake", 2, None),
            ("neural-soak", "1", "", "/tmp/save.sav", "fake", 2, None),
            ("neural-soak", "1", "", "", "chatgpt", 2, None),
            ("neural-load", "1", "tcp://policy:47835", "", "fake", 2, None),
        ]
        for scenario, control, url, save, llm, status, body_flag in cases:
            with self.subTest(scenario=scenario, control=control, url=url, save=save, llm=llm):
                env = dict(os.environ, SCENARIO=scenario, GOTHIC_SOAK_CONTROL=control,
                           GOTHIC_BODY_URL=url, SAVE=save, LLM=llm, GOTHIC_TTS_RECORD="0")
                result = subprocess.run(
                    ["bash", "-euc", selection + '\nprintf "%s" "${BODY_ARGS[@]}"'],
                    check=False,
                    capture_output=True, text=True, timeout=5, env=env,
                )
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                if body_flag is not None:
                    self.assertEqual(result.stdout, body_flag)


if __name__ == "__main__":
    unittest.main()
