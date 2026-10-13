"""Checks for the historical random-branch fixture, without running an engine."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("body_headless_suite", Path(__file__).parents[1] / "body_headless_suite.py")
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def write_trace(tmp_path, attempts):
    rows = [dict(kind="body_combat", phase="episode_start", seq=1, t_game=100,
                 seed=17, actor_id=3, target_id=4)]
    rows += [dict(kind="body_combat", phase="native_attempt", seq=index+2, t_game=200,
                  actor_id=3, target_id=4, actor="SAME_SYMBOL", target="SAME_SYMBOL",
                  accepted=True, pose_changed=True, requested_anim=35,
                  layers_after=[{"name": "T_1HPARADE_0_A3"}], **change)
             for index, change in enumerate(attempts)]
    rows.append(dict(kind="body_combat", phase="episode_end", seq=len(rows)+1, t_game=500))
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row)+"\n" for row in rows))
    return path


def test_fixture_captures_only_random_clip_and_maps_lifetime_ids(tmp_path):
    path = write_trace(tmp_path, [{}])
    fixture = suite.animation_fixture(path)
    assert fixture["source_events_sha256"] == suite.sha256(path)
    assert fixture["entries"] == [dict(seed=17, role="actor", relative_time_ms=100,
                                       animation="T_1HPARADE_0_A3", variant=2, source_seq=2)]
    assert not set(fixture["entries"][0]) & {"hp", "outcome", "primitive", "requested_anim"}


def test_duplicate_random_opportunity_is_rejected(tmp_path):
    path = write_trace(tmp_path, [{}, {}])
    with pytest.raises(RuntimeError, match="duplicate branch"):
        suite.animation_fixture(path)


def test_fixture_cannot_use_incomplete_episode(tmp_path):
    path = write_trace(tmp_path, [{}])
    path.write_text("\n".join(path.read_text().splitlines()[:-1])+"\n")
    with pytest.raises(RuntimeError, match="incomplete"):
        suite.animation_fixture(path)
