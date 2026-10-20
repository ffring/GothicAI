"""Opt-in regression on an existing corpus; run on Ryzen through gq."""
import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("nav_demos_v4_corpus_collect",
    Path(__file__).resolve().parents[1] / "nav_demos_v4_collect.py")
collect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)


def episode_files(root):
    result = {}
    for metadata in sorted(root.glob("*/train/*/episode.json")):
        for path in sorted(metadata.parent.rglob("*")):
            if path.is_file():
                stat = path.stat()
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                result[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, digest)
    return result


@pytest.mark.skipif(not os.environ.get("NAV_DEMOS_V4_CORPUS_TASK"), reason="Explicit existing Ryzen corpus required")
def test_all_completed_episodes_resume_without_rewriting_any_recording_file(monkeypatch):
    task = Path(os.environ["NAV_DEMOS_V4_CORPUS_TASK"]).resolve()
    expected = int(os.environ["NAV_DEMOS_V4_CORPUS_EXPECT"])
    root = task / "recordings"
    metadata = sorted(root.glob("*/train/*/episode.json"))
    assert len(metadata) == expected
    before = episode_files(root)
    assert before

    def no_game(*args, **kwargs):
        pytest.fail("Existing-corpus regression must never launch an engine")

    monkeypatch.setattr(collect.Worker, "scenario", no_game)
    monkeypatch.setattr(collect.Worker, "ready", no_game)
    options = SimpleNamespace(task=task, include_follow="no", follow_below_gate=True,
        parallel=3, limit_ms=240000, max_bytes=15000000000, wall_minutes=100,
        max_new=2000, round_size=12, verify_existing_only=True, expect_existing=expected)
    result = collect.run(options)
    assert result["resume_verified_episodes"] == expected
    assert result["resume_verification_complete"] is True
    assert len(list(root.glob("*/train/*/episode.json"))) == expected
    assert episode_files(root) == before

    summary = collect.v3.read_json(root / "collection-metrics.json")
    assert len(summary["episodes"]) == expected
    assert all(value["validated"] for value in summary["episodes"])
    assert summary["navigation_memory_version"] == 3
    print(f"Verified {expected} existing episodes; all recording file hashes and mtimes unchanged", flush=True)
