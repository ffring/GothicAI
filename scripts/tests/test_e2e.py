"""Game startup contention checks without starting a server or game."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


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
                        env=dict(os.environ, TEST_PLATFORM=platform, BUSY_PROCESS=process,
                                 CALLS=str(calls), SEEN=str(Path(temp) / "seen")),
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(done.stdout, "ready\n")
                    observed = calls.read_text().splitlines() if calls.exists() else []
                    self.assertEqual(observed, ["pgrep", "sleep 5", "pgrep"] if platform == "Darwin" else [])


if __name__ == "__main__":
    unittest.main()
