"""Semantic trace checks: lifetime IDs, native stages, censoring and missing terminals."""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "body_baseline_report.py"
SPEC = importlib.util.spec_from_file_location("body_baseline_report", MODULE_PATH)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def trace_fixture():
    config = json.loads(reporter.DEFAULT_CONFIG.read_text())
    config["seeds"] = [17]
    # Script symbols are intentionally identical: trace lifetime IDs are the only reliable key.
    config["actors"] = ["SAME_SYMBOL", "SAME_SYMBOL"]
    rows = []
    def event(phase, actor=1, target=2, time=0, **fields):
        if phase == "phase_snapshot":
            fields = {**dict.fromkeys(config["guard_fields"], False),
                      "sample_phase": config["step"]["sample_phase"], **fields}
        rows.append({"kind": "body_combat", "schema_version": 1, "seq": len(rows)+1,
                     "t_game": time, "step": time//33, "actor_id": actor, "actor": "SAME_SYMBOL",
                     "target_id": target, "target": "SAME_SYMBOL", "phase": phase, **fields})
    event("actor_setup", seed=17, guild=9, **config["setup"])
    event("actor_setup", actor=2, target=1, seed=17, guild=1, **config["setup"])
    event("episode_start", seed=17, timeout_ms=60000, source="native", scenario="melee-v1")
    for actor, target in ((1, 2), (2, 1)):
        event("phase_snapshot", actor, target, time=0, defence=False, prehit=False)
        event("native_choice", actor, target, time=0, choice_id=1, primitive=9)
        event("native_attempt", actor, target, time=0, choice_id=1, primitive=9, accepted=False, pose_changed=False)
        event("native_attempt", actor, target, time=0, choice_id=1, primitive=9, accepted=True, pose_changed=True)
        event("native_consume", actor, target, time=0, choice_id=1, primitive=9)
    event("phase_snapshot", time=33, defence=True, prehit=False)
    event("phase_snapshot", actor=2, target=1, time=33, defence=False, prehit=True)
    event("defence_result", time=66, blocked=True, jumped_back=False, damage_kind="melee")
    event("phase_snapshot", time=99, defence=False, prehit=False)
    event("phase_snapshot", actor=2, target=1, time=99, prehit=True)
    event("damage", actor=2, target=1, time=99, hp_before=300, hp_after=0, net=350, has_hit=True, damage_kind="melee")
    event("phase_snapshot", time=132)
    event("phase_snapshot", actor=2, target=1, time=132, prehit=True)
    event("episode_end", time=132, outcome="target_down", actor_hp=300, target_hp=0, actor_dead=False, target_dead=True)
    return config, rows


def test_lifetime_ids_and_distinct_native_stages():
    config, rows = trace_fixture()
    report = reporter.summarize(rows, config)
    assert report["status"] == "complete"
    first, second = report["episodes"][0]["actors"]
    assert [first["actor_id"], second["actor_id"]] == [1, 2]
    assert first["unique_choices"] == 1 and first["choices_with_consume"] == 1
    assert first["attempts_rejected"] == 1 and first["attempts_accepted"] == 1
    assert second["damage_received"]["net_sum"] == 350
    assert second["damage_received"]["hp_loss_sum"] == 300  # overkill is not actual HP loss
    assert first["windows"]["defence"]["complete_duration_ms"]["min"] == 66
    assert second["windows"]["prehit"]["complete_duration_ms"]["n"] == 0
    assert second["windows"]["prehit"]["right_censored"] == 1


def test_missing_terminal_is_infrastructure_failure_not_timeout():
    config, rows = trace_fixture()
    report = reporter.summarize(rows[:-1], config)
    episode = report["episodes"][0]
    assert report["status"] == "incomplete"
    assert episode["outcome"] is None
    assert not episode["terminated"] and not episode["truncated"]
    assert any("infrastructure interruption" in issue for issue in report["issues"])


def test_timeout_remains_truncation_and_third_target_invalidates_duel():
    config, rows = trace_fixture()
    rows[-1]["outcome"] = "timeout"
    rows[-2]["target_id"] = 999
    report = reporter.summarize(rows, config)
    episode = report["episodes"][0]
    assert episode["truncated"] and not episode["terminated"]
    assert any("third actor/target" in issue for issue in episode["issues"])


def test_json_reader_rejects_nonfinite_and_regressing_sequence(tmp_path):
    config, rows = trace_fixture()
    rows[5]["seq"] = rows[4]["seq"]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows)+"\n{\"kind\":\"body_combat\",\"hp\":NaN}\n"+
                    '{"kind":"body_combat","kind":"melee"}\n')
    read, issues = reporter.read_events(path)
    assert len(read) == len(rows)
    assert any("not increasing" in issue for issue in issues)
    assert any("non-finite JSON" in issue for issue in issues)
    assert any("duplicate JSON key kind" in issue for issue in issues)


def test_roundtrip_preserves_damage_event_envelope(tmp_path):
    config, rows = trace_fixture()
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows)+"\n")
    read, issues = reporter.read_events(path)
    report = reporter.summarize(read, config, issues)
    assert report["status"] == "complete"
    second = report["episodes"][0]["actors"][1]
    assert second["damage_received"]["events"] == 1
    assert second["damage_received"]["hp_loss_sum"] == 300


def test_window_that_starts_before_first_sample_is_left_censored():
    samples = [{"t_game": 0, "defence": True}, {"t_game": 33, "defence": False},
               {"t_game": 66, "defence": True}, {"t_game": 99, "defence": False}]
    window = reporter.guard_windows(samples, "defence", 132)
    assert window["left_censored"] == 1
    assert window["complete_duration_ms"]["n"] == 1
    assert window["complete_duration_ms"]["min"] == 33


def test_sparse_or_incomplete_guard_evidence_cannot_be_complete():
    config, rows = trace_fixture()
    for row in rows:
        if row["phase"] == "phase_snapshot" and row["actor_id"] == 2:
            row.pop("def_window")
            row["sample_phase"] = "unknown_phase"
    report = reporter.summarize(rows, config)
    assert report["status"] == "incomplete"
    assert any("complete guard snapshots" in gap for gap in report["evidence_gaps"])
    assert any("wrong or missing sample phase" in gap for gap in report["evidence_gaps"])
    config, rows = trace_fixture()
    rows = [row for row in rows if not (row["phase"] == "phase_snapshot" and row["actor_id"] == 1
                                       and row["t_game"] in (33, 99))]
    report = reporter.summarize(rows, config)
    assert report["status"] == "incomplete"
    assert any("coverage exceeds 67 ms" in gap for gap in report["evidence_gaps"])


@pytest.mark.parametrize("layers", (None, {"name": "invalid container"}, ["invalid layer"]))
def test_malformed_animation_metadata_is_incomplete_without_crashing(layers):
    config, rows = trace_fixture()
    next(row for row in rows if row["phase"] == "phase_snapshot")["layers"] = layers
    report = reporter.summarize(rows, config)
    assert report["status"] == "incomplete"
    assert any("invalid layers animation metadata container" in issue for issue in report["issues"])


@pytest.mark.parametrize("phase", ["damage", "defence_result"])
@pytest.mark.parametrize("damage_kind", ["ranged", "spell", None])
def test_non_melee_or_unclassified_contact_invalidates_melee_baseline(phase, damage_kind):
    config, rows = trace_fixture()
    for row in rows:
        if row["phase"] == phase:
            if damage_kind is None:
                row.pop("damage_kind")
            else:
                row["damage_kind"] = damage_kind
    report = reporter.summarize(rows, config)
    assert report["status"] == "incomplete"
    assert any(phase in issue and "expected 'melee'" in issue for issue in report["issues"])


@pytest.mark.parametrize("manifest_available", [True, False])
def test_missing_trace_writes_incomplete_artifacts_without_a_timeout(tmp_path, monkeypatch, manifest_available):
    run_dir = tmp_path / "failed-startup"
    run_dir.mkdir()
    manifest = run_dir / "manifest.json"
    if manifest_available:
        manifest.write_text(json.dumps({"kind": "rendered_reference_before_headless", "model": None}))
    output_dir = tmp_path / "reports"
    monkeypatch.setattr("sys.argv", [str(MODULE_PATH), str(run_dir), "--manifest", str(manifest),
                                    "--output-dir", str(output_dir)])
    assert reporter.main() == 2
    report = json.loads((output_dir / "body-baseline.json").read_text())
    assert report["status"] == "incomplete"
    assert report["episodes"] == []
    assert report["events_sha256"] is None
    assert any("missing trace file events.jsonl" in issue and "infrastructure failure" in issue
               for issue in report["issues"])
    markdown = (output_dir / "body-baseline.md").read_text()
    assert "Status: **incomplete**" in markdown
    assert "no native episodes recorded" in markdown
