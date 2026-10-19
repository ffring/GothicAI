"""Bounded collection safety and physical transition evidence; no engine/assets."""
import gzip
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nav_jump_record as recorder
import nav_demos as demos


def route(index=0, kind="jump"):
    return dict(index=index, label=f"route-{index}", type=kind, transition_id=index+7,
                start="cm:-100,0,0", goal="cm:300,0,0", site_id=f"site-{index}",
                split="train", region="city", transition_start_m=[0, 0, 0], transition_end_m=[2, 0, 0])


def trace(value, **change):
    return [dict(elapsed_ms=0, active_transition_id=value["transition_id"], active_transition_type=value["type"],
                 on_transition=True, jump=value["type"] == "jump", climb=value["type"] == "climb",
                 transitions=[dict(id=value["transition_id"], type=value["type"])], **change)]


def samples(kind="jump"):
    flags = [[0]*5 for _ in range(4)]
    if kind == "climb":
        flags[1][3] = 1
    else:
        flags[1][0] = flags[1][1] = 1
    positions = [[0, 0, 0], [80, -50 if kind == "drop" else 70, 0], [200, 0, 0], [230, 0, 0]]
    return [0, 100, 200, 300], positions, flags, [kind == "jump", False, False, False], [kind == "climb", False, False, False]


@pytest.mark.parametrize("kind", recorder.TYPES)
def test_requires_planned_id_action_native_dynamics_and_landing(kind):
    value = route(kind=kind)
    evidence, failure = recorder.transition_evidence(value, trace(value), samples(kind))
    assert failure is None
    assert evidence["native_dynamics"] and evidence["landing"]
    assert evidence["event_ms"] == 100 and evidence["landing_ms"] == 200
    wrong = trace(value)
    wrong[0]["transitions"][0]["id"] += 100
    assert recorder.transition_evidence(value, wrong, samples(kind))[1] == "scheduled_transition_not_in_actual_path"
    static = samples(kind)
    static[2][:] = [[0]*5 for _ in range(4)]
    assert recorder.transition_evidence(value, trace(value), static)[1] == "native_action_not_witnessed"
    never_land = samples(kind)
    never_land[1][2:] = [[0, 0, 0], [0, 0, 0]]
    assert recorder.transition_evidence(value, trace(value), never_land)[1] == "landing_not_witnessed"


def test_goal_success_or_action_near_link_is_not_passage():
    value = route()
    wrong = trace(value)
    wrong[0]["active_transition_id"] = 99
    assert recorder.transition_evidence(value, wrong, samples())[1] == "approach_never_on_transition"
    wrong = trace(value)
    wrong[0]["jump"] = False
    assert recorder.transition_evidence(value, wrong, samples())[1] == "teacher_never_pressed_jump"


def test_storage_counts_tree_and_reserves_next_whole_episode(tmp_path):
    (tmp_path / "data").write_bytes(b"x"*128)
    assert recorder.tree_bytes(tmp_path) == 128
    free_disk = lambda _: SimpleNamespace(free=recorder.MIN_FREE_BYTES)
    metrics = recorder.check_storage(tmp_path, free_disk)
    assert metrics["existing_bytes"] == 128
    assert metrics["reserved_bytes"] == 2401*17000
    with pytest.raises(RuntimeError, match="STOP disk"):
        recorder.check_storage(tmp_path, lambda _: SimpleNamespace(free=recorder.MIN_FREE_BYTES - 1))
    # Sparse file tests the threshold without allocating gigabytes.
    with (tmp_path / "large").open("wb") as stream:
        stream.truncate(recorder.MAX_BYTES - recorder.RESERVE_BYTES)
    with pytest.raises(RuntimeError, match="STOP storage"):
        recorder.check_storage(tmp_path, free_disk)


def test_pilot_counts_every_failure_and_stops_below_not_at_40_percent():
    episodes = [dict(type="jump", transition_success=i < 3, transition_failure="approach") for i in range(10)]
    assert recorder.pilot_stop(episodes[:9]) is None
    assert "3/10" in recorder.pilot_stop(episodes)
    episodes[3]["transition_success"] = True
    assert recorder.pilot_stop(episodes) is None
    metrics = recorder.counts(episodes)["jump"]
    assert metrics["attempts"] == 10 and metrics["successes"] == 4
    assert metrics["reach_rate"] == .4 and not metrics["target_70pct"]


def test_collection_order_checks_each_type_early_without_dropping_routes():
    values = [route(index=i, kind=kind) for i, kind in enumerate(["jump"]*15 + ["climb"]*15 + ["drop"]*15)]
    ordered = recorder.collection_order(values)
    assert [value["type"] for value in ordered[:6]] == ["jump", "climb", "drop"]*2
    assert {value["index"] for value in ordered} == set(range(45))
    assert len(ordered) == 45


def test_scenario_uses_unchanged_roads_teacher_v2_and_240s_limit():
    args = SimpleNamespace(cache=Path("/own/cache"), links=Path("/own/accepted-links-cv.txt"), roads=Path("/own/roads.json"), npc="NPC")
    assert recorder.scenario_commands(args) == ["navmesh build /own/cache /own/accepted-links-cv.txt"]
    commands = recorder.scenario_commands(args, route())
    assert commands == ["roads load /own/roads.json", "roads on NPC", "navdemos start-v2 NPC cm:-100,0,0 cm:300,0,0 240000", "navdemos wait"]


def test_resume_preserves_failed_attempt_and_cannot_pass_persisted_stop(tmp_path):
    value = route()
    directory = tmp_path / "episodes/0000"
    directory.mkdir(parents=True)
    for name in recorder.REQUIRED:
        (directory / name).write_text("")
    episode = dict(value, route_index=0, transition_success=False, qualifying_success=False,
                   transition_failure="landing_not_witnessed", collection_stop="STOP teacher jump: 3/10")
    (directory / "episode.json").write_text(json.dumps(episode))
    with pytest.raises(RuntimeError, match="STOP teacher"):
        recorder.existing_episodes(tmp_path, [value])
    del episode["collection_stop"]
    (directory / "episode.json").write_text(json.dumps(episode))
    assert recorder.existing_episodes(tmp_path, [value])[0]["transition_success"] is False
    (directory / "samples.bin.gz").unlink()
    with pytest.raises(RuntimeError, match="no automatic retry"):
        recorder.existing_episodes(tmp_path, [value])
