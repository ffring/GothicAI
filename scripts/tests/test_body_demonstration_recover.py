"""Offline recovery rejects incomplete capture and unaudited timeout drivers."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_demonstration_recover import (close_evidence, collector_guard, digest, full_cases, inventory, recover,
                                        verify_code_inventory, verify_episode_metadata)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row)+"\n" for row in rows))


def close_rows():
    return [{"direction": "request", "wall_monotonic": 100, "value": {"cmd": "close", "id": 42}},
            {"direction": "response", "wall_monotonic": 101, "value": {"id": 42, "ok": True, "done": True,
             "arena": {"outcome": "time_limit"}, "body_trajectory": {"queue_size": 0, "dropped_total": 0,
             "reset_discarded_total": 0, "capture_failed": False, "capture_failures_total": 0, "emitted": 10, "drained": 10}}}]


def test_engine_close_requires_actual_matching_final_response(tmp_path):
    path = tmp_path/"ipc.jsonl"
    rows = close_rows()
    write_jsonl(path, rows)
    proof = close_evidence(path, {"outcome": "time_limit"})
    assert proof["id"] == 42 and proof["response_index"] == 1
    rows[1]["value"]["id"] = 43
    write_jsonl(path, rows)
    with pytest.raises(ValueError, match="response"):
        close_evidence(path, {"outcome": "time_limit"})


def test_compact_recorder_final_matches_exact_full_arena_projection(tmp_path):
    full = {"game_ms": 40294, "terminated": False, "truncated": True, "infrastructure": False,
            "outcome": "time_limit", "reason": "time_limit", "actors": [{"entity_id": 1, "hp": 300}],
            "session_epoch": 7, "world_epoch": 2, "setup": {"geometry": "open"}}
    compact = {"t_game_ms": 40294, "terminated": False, "truncated": True, "infrastructure": False,
               "outcome": "time_limit", "reason": "time_limit"}
    record = {"name": "demo-train-17-0", "initial": {"game_ms": 16294}, "final": full,
              "counts": {"observation": 100, "decision": 10, "execution": 30, "transition": 10}}
    (tmp_path/"episode.json").write_text(json.dumps(record))
    write_jsonl(tmp_path/"arena-events.jsonl", [{"kind": "reset_ready", "arena": record["initial"]},
                                             {"kind": "terminal", "arena": record["final"]}])
    (tmp_path/"records-approved.jsonl").write_text("approved record bytes\n")
    original = {"initial": record["initial"], "final": compact, "counts": record["counts"],
                "records_file": "records.jsonl", "source_files": [{"path": "records.jsonl", "sha256": "rawSHA"}]}
    (tmp_path/"trajectory-manifest-original.json").write_text(json.dumps(original))
    approved = {**original, "records_file": "records-approved.jsonl", "scripted_expert_approval": {"approved": []},
                "source_files": original["source_files"]+[
                    {"path": "records-approved.jsonl", "sha256": digest(tmp_path/"records-approved.jsonl")},
                    {"path": "trajectory-manifest-original.json", "sha256": digest(tmp_path/"trajectory-manifest-original.json")}]}
    verify_episode_metadata(tmp_path, record, approved)
    approved["final"] = {**compact, "t_game_ms": 40293}
    with pytest.raises(ValueError, match="copied episode"):
        verify_episode_metadata(tmp_path, record, approved)
    approved["final"] = compact
    record["final"] = {**full, "actors": [{"entity_id": 1, "hp": 290}]}
    with pytest.raises(ValueError, match="episode.json"):
        verify_episode_metadata(tmp_path, record, approved)


@pytest.mark.parametrize("field,value", [("queue_size", 1), ("dropped_total", 1), ("reset_discarded_total", 1),
                                         ("capture_failed", True), ("capture_failures_total", 1), ("drained", 9)])
def test_engine_close_rejects_unfinished_or_failed_capture(tmp_path, field, value):
    path = tmp_path/"ipc.jsonl"
    rows = close_rows()
    rows[1]["value"]["body_trajectory"][field] = value
    write_jsonl(path, rows)
    with pytest.raises(ValueError, match="capture"):
        close_evidence(path, {"outcome": "time_limit"})


def frozen_driver(*, include_guard=True, analysis_first=False):
    guard = '        if runner.process.returncode != 0:\n            raise RecorderError("demonstration engine did not close cleanly")\n' if include_guard else ""
    analysis = "        if not args.smoke:\n            build_dataset(paths, output)\n"
    return ('def main():\n    try:\n        runner.call({"cmd": "close"})\n        runner.process.wait(timeout=15)\n'+guard+
            (analysis if analysis_first else "")+'        bind_closed_sources(args, suite["episodes"])\n'+
            '        for record in suite["episodes"]:\n            approve_scripted_demonstration(output, registry)\n'+
            ("" if analysis_first else analysis)+"    finally:\n        pass\n")


def test_only_frozen_zero_exit_guard_before_all_approvals_proves_backend_return(tmp_path):
    path = tmp_path/"collector.py"
    path.write_text(frozen_driver())
    proof = collector_guard(path)
    assert proof["inferred"] is True and proof["backend_returncode"] == 0
    path.write_text(frozen_driver(include_guard=False))
    with pytest.raises(ValueError, match="guard"):
        collector_guard(path)
    path.write_text(frozen_driver(analysis_first=True))
    with pytest.raises(ValueError, match="timeout"):
        collector_guard(path)


def test_recovery_inventory_binds_every_file_and_rejects_source_symlinks(tmp_path):
    episode = tmp_path/"episode"
    episode.mkdir()
    (episode/"records-approved.jsonl").write_text("original bytes\n")
    first = inventory(episode)
    (episode/"records-approved.jsonl").write_text("changed bytes\n")
    assert inventory(episode) != first
    (episode/"borrowed").symlink_to(episode/"records-approved.jsonl")
    with pytest.raises(ValueError, match="symlink"):
        inventory(episode)


def test_executable_inventory_ignores_only_exact_cache_components(tmp_path):
    code = tmp_path/"code"
    cache = code/".pytest_cache"/"v"/"cache"/"nodeids"
    cache.parent.mkdir(parents=True)
    cache.write_text("old nodes")
    real = code/"real.py"
    real.write_text("original source")
    files = {str(path.relative_to(tmp_path)): digest(path) for path in (cache, real)}
    cache.write_text("new nodes")
    ignored = verify_code_inventory(tmp_path, code, files)
    assert len(ignored) == 1 and ignored[0]["path"] == str(cache)
    assert ignored[0]["relative_path"] == "code/.pytest_cache/v/cache/nodeids"
    assert ignored[0]["current_sha256"] != ignored[0]["declared_sha256"]
    real.write_text("changed source")
    with pytest.raises(ValueError, match="real.py"):
        verify_code_inventory(tmp_path, code, files)


def test_episode_caches_are_excluded_but_similarly_named_raw_files_are_bound(tmp_path):
    episode = tmp_path/"episode"
    episode.mkdir()
    real = episode/"raw-trajectory.jsonl"
    real.write_text("raw bytes")
    decoy = episode/"__pycache__-raw.jsonl"
    decoy.write_text("real data")
    cache = episode/"__pycache__"/"teacher-source.pyc"
    cache.parent.mkdir()
    cache.write_text("old cache")
    before = inventory(episode)
    cache.write_text("new cache")
    assert inventory(episode) == before and "__pycache__-raw.jsonl" in before
    decoy.write_text("changed data")
    assert inventory(episode) != before


def test_failed_recovery_never_rewrites_original_suite_or_publishes_collected(tmp_path):
    source = tmp_path/"source"
    source.mkdir()
    path = source/"suite.json"
    path.write_text(json.dumps({"kind": "body_demonstrations_stage7_suite", "status": "infrastructure_failure",
                                "suite_scope": "full", "error": "native action failed"}))
    (source/"manifest.json").write_text("{}")
    original = path.read_bytes()
    output = tmp_path/"output"
    with pytest.raises(ValueError, match="post-approval"):
        recover(source, output, tmp_path/"config.json", tmp_path/"code")
    assert path.read_bytes() == original and not output.exists()


def test_full_recovery_retains_all_37_predeclared_groups():
    config = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-demonstrations-v1.json").read_text())
    cases = full_cases(config)
    assert len(cases) == len({case["name"] for case in cases}) == 37
    assert sum(case["geometry"] == "near_wall" for case in cases) == 3
    for case in cases:
        group = json.dumps([config["setup_family"], case["seed"]], separators=(",", ":"))
        assert config["inherited_group_assignments"][group] == case["split"]
