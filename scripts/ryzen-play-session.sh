#!/usr/bin/env python3
"""Terminal fallback for the same queued desktop session used by the launcher window."""
import os
import sys
from pathlib import Path


def main(argv=None):
    repo = Path(__file__).resolve().parent.parent
    uv = str(Path.home() / ".local/bin/uv")
    os.execv(uv, [uv, "run", "--locked", "--project", str(repo / "launcher/core"),
                  "gothic-launcher", "play", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    main()
