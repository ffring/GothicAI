"""DAgger labels and previous inputs have deliberately different provenance."""
import gzip
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


SPEC = importlib.util.spec_from_file_location("nav_demos", Path(__file__).resolve().parents[1] / "nav_demos.py")
demos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demos)
DRIVE_SPEC = importlib.util.spec_from_file_location("nav_drive", Path(__file__).resolve().parents[1] / "nav_drive.py")
drive = importlib.util.module_from_spec(DRIVE_SPEC)
DRIVE_SPEC.loader.exec_module(drive)


def write_episode(path, drive=True, corrupt_previous=None, teacher_turn=-1, count=65):
    # Cross the reader's 64-record chunk boundary as well as ordinary adjacent rows.
    path.mkdir()
    records = np.zeros(count, dtype=demos.RECORD_DTYPE)
    records["time_ms"] = 1000 + np.arange(count) * 100
    records["eye"]["distance_cm"] = 1000
    records["keys"]["turn"] = teacher_turn
    records["keys"]["walk"] = 1
    actual = records["keys"].copy()
    if drive:
        actual["forward"] = 1
        actual["turn"] = 1
    records["previous_keys"][1:] = actual[:-1]
    if corrupt_previous is not None:
        records["previous_keys"][corrupt_previous]["turn"] = 0
    metadata = dict(success=False, drive=drive, decisions=count, elapsed_ms=count * 100,
                    eye_layout=[[0, 0, 1000]] * demos.RAY_COUNT)
    (path / "episode.json").write_text(json.dumps(metadata))
    with gzip.open(path / "samples.bin.gz", "wb") as stream:
        stream.write(demos.HEADER.pack(demos.MAGIC, demos.VERSION, demos.RECORD_BYTES,
                                      demos.RAY_COUNT, demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
        stream.write(records.tobytes())
    actions = np.zeros((count + 1, 9))
    actions[:, 0] = np.arange(count + 1) * 100
    for column, field in enumerate(("forward", "strafe", "turn", "walk", "jump", "climb",
                                    "head_yaw_deg", "head_pitch_deg"), 1):
        actions[:-1, column] = actual[field]
    actions[-1, 4] = 1
    np.savetxt(path / "actions.txt", actions)
    return actions


def test_drive_accepts_teacher_labels_with_actual_learner_previous_keys(tmp_path):
    episode = tmp_path / "drive"
    write_episode(episode)
    result = demos.summarize(episode, validate=True)
    assert result["validated"]
    assert result["decisions"] == 65
    # A failed drive is still retained and checked for DAgger.
    assert result["episodes"] == 1
    assert result["key_counts"]["turn_left"] == 65


@pytest.mark.parametrize("row", [1, 64])
def test_drive_rejects_corrupted_previous_keys_within_and_between_chunks(tmp_path, row):
    episode = tmp_path / "drive"
    write_episode(episode, corrupt_previous=row)
    with pytest.raises(ValueError, match="previous keys mismatch"):
        demos.summarize(episode, validate=True)


def test_teacher_actions_are_still_checked_against_teacher_keys(tmp_path):
    episode = tmp_path / "teacher"
    actions = write_episode(episode, drive=False)
    assert demos.summarize(episode, validate=True)["decisions"] == 65
    actions[1, 3] = 1
    np.savetxt(episode / "actions.txt", actions)
    with pytest.raises(ValueError, match=r"actions.turn differs from recorded keys"):
        demos.summarize(episode, validate=True)


def test_teacher_fractional_axis_preserves_existing_navdemo_contract(tmp_path):
    episode = tmp_path / "teacher"
    write_episode(episode, drive=False, teacher_turn=.5)
    result = demos.summarize(episode, validate=True)
    assert result["validated"]
    assert result["decisions"] == 65
    assert result["key_counts"]["turn_right"] == 65


def test_drive_still_rejects_fractional_applied_axis(tmp_path):
    episode = tmp_path / "drive"
    actions = write_episode(episode)
    actions[1, 3] = .5
    np.savetxt(episode / "actions.txt", actions)
    with pytest.raises(ValueError, match=r"actions.turn is not a digital axis"):
        demos.summarize(episode, validate=True)


def write_analysis_episode(path, changing=True):
    actions = write_episode(path, count=7)
    records = np.concatenate(list(demos.iter_chunks(path)))
    actions[:-1, 1:] = 0
    actions[:-1, 4] = 1
    if changing:
        actions[:2, 3] = 1
        actions[2:6, 1] = 1
        actions[6, 3] = -1
    else:
        actions[:-1, 1] = 1
    for column, field in enumerate(drive.ACTION_FIELDS, 1):
        records["keys"][field] = actions[:-1, column]
        records["previous_keys"][field] = np.r_[0, actions[:-2, column]]
    if changing:
        records["keys"]["turn"][[1, 4, 6]] = -.5
        records["keys"]["forward"][2] = 0
        records["keys"]["forward"][6] = 1
    else:
        # This episode has no teacher-requested changes, including at the start.
        records["previous_keys"][0] = records["keys"][0]
    with gzip.open(path / "samples.bin.gz", "wb") as stream:
        stream.write(demos.HEADER.pack(demos.MAGIC, demos.VERSION, demos.RECORD_BYTES,
                                      demos.RAY_COUNT, demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
        stream.write(records.tobytes())
    np.savetxt(path / "actions.txt", actions)
    metadata = json.loads((path / "episode.json").read_text())
    metadata.update(limit_ms=700, teacher_success=True, teacher_elapsed_ms=500)
    (path / "episode.json").write_text(json.dumps(metadata))
    positions = np.zeros((8, 5))
    positions[:, 0] = np.arange(8) * 100
    positions[:, 1] = [0, 0, 0, 0, 100, 200, 200, 200]
    np.savetxt(path / "trajectory.txt", positions)


def test_drive_metrics_measure_applied_actions_and_teacher_action_changes(tmp_path):
    episode = tmp_path / "changing"
    write_analysis_episode(episode)
    result = drive.episode_metrics(episode, demos)
    assert result["travelled_xz_cm"] == 200
    assert result["observed_reason"] == "moving_at_timeout"
    assert result["action_counts"] == dict(forward=4, backward=0, strafe_left=0, strafe_right=0,
                                         turn_left=1, turn_right=2, walk=7, jump=0, climb=0, idle=0, turn_only=3)
    assert result["action_changes"] == dict(forward=2, strafe=0, turn=2, walk=0, jump=0, climb=0, any=2)
    assert result["action_repeat_fraction"] == pytest.approx(4 / 6)
    assert result["action_runs"] == 3
    assert result["longest_action_run_steps"] == 4
    assert result["final_action_run_steps"] == 1
    assert result["final_action"]["turn"] == -1
    assert result["disagreement_counts"] == dict(forward=2, strafe=0, turn=2, walk=0, jump=0, climb=0, any=4)
    assert result["action_change_steps"] == 6
    assert result["action_change_accuracy"] == dict(forward=4 / 6, strafe=1, turn=4 / 6, walk=1,
                                                    jump=1, climb=1, all_heads=2 / 6)

    unchanged = tmp_path / "unchanged"
    write_analysis_episode(unchanged, changing=False)
    no_changes = drive.episode_metrics(unchanged, demos)
    assert no_changes["action_change_steps"] == 0
    assert all(value is None for value in no_changes["action_change_accuracy"].values())
    group = drive.group_metrics([result, no_changes])
    assert group["decisions"] == 14
    assert group["action_change_steps"] == 6
    assert group["action_change_accuracy"] == result["action_change_accuracy"]
    assert group["action_counts"]["forward"] == 11
    assert group["action_counts"]["idle"] == 0
    assert group["action_fraction"]["forward"] == pytest.approx(11 / 14)
    assert group["action_changes"]["any"] == 2
    assert group["action_repeat_fraction"] == pytest.approx(10 / 12)
    assert group["longest_action_run_steps"] == 7
    assert group["action_runs"] == 4


def test_teacher_change_is_counted_at_reader_chunk_boundary(tmp_path):
    episode = tmp_path / "boundary"
    write_episode(episode)
    records = np.concatenate(list(demos.iter_chunks(episode)))
    records["keys"]["turn"][64] = 1
    with gzip.open(episode / "samples.bin.gz", "wb") as stream:
        stream.write(demos.HEADER.pack(demos.MAGIC, demos.VERSION, demos.RECORD_BYTES,
                                      demos.RAY_COUNT, demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
        stream.write(records.tobytes())
    positions = np.zeros((66, 5))
    positions[:, 0] = np.arange(66) * 100
    np.savetxt(episode / "trajectory.txt", positions)
    result = drive.episode_metrics(episode, demos)
    assert result["action_change_steps"] == 1
    assert result["action_change_accuracy"]["turn"] == 1
    assert result["action_change_accuracy"]["forward"] == 0


def test_prepare_keeps_original_train_pairs_and_only_v2_successful_validation(tmp_path):
    bc_cache, teacher_root, output = (tmp_path / name for name in ("cache", "teacher-v2", "drive"))
    bc_cache.mkdir()
    episodes = []
    for split, count in (("validation", 31), ("train", 36)):
        for index in range(count):
            region = f"region-{index % 6}"
            episodes.append(dict(id=f"old__{split}__{index:03d}", split=split, region=region,
                                 pair=[f"{split}-start-{index:03d}", f"{split}-goal-{index:03d}"],
                                 start=f"{split}-start-{index:03d}", goal=f"{split}-goal-{index:03d}"))
    (bc_cache / "index.json").write_text(json.dumps(dict(episodes=episodes)))
    for index, episode in enumerate(reversed(episodes)):
        directory = teacher_root / "episodes" / f"new-{index:03d}"
        directory.mkdir(parents=True)
        teacher = dict(start=episode["start"], goal=episode["goal"], elapsed_ms=1000,
                       teacher_version=2, demo_version=2,
                       success=not (episode["split"] == "validation" and int(episode["start"][-3:]) < 4))
        (directory / "episode.json").write_text(json.dumps(teacher))
        (directory / "samples.bin.gz").touch()
    args = SimpleNamespace(bc_cache=bc_cache, teacher_root=teacher_root, output=output,
                           pairs=None, selection_seed=17)
    drive.prepare(args)
    selected = drive.load_pairs(args)
    validation = [pair for pair in selected if pair["split"] == "validation"]
    train = [pair for pair in selected if pair["split"] == "train"]
    assert len(validation) == 27
    assert all(pair["teacher_success"] for pair in validation)
    assert len(train) == 30
    assert len([pair for pair in selected if pair["split"] == "control"]) == 1
    expected = []
    rng = drive.random.Random(17)
    for region in sorted({episode["region"] for episode in episodes if episode["split"] == "train"}):
        group = sorted((episode for episode in episodes if episode["region"] == region and episode["split"] == "train"),
                       key=lambda episode: (episode["start"], episode["goal"]))
        expected.extend(sorted(rng.sample(group, 5), key=lambda episode: (episode["start"], episode["goal"])))
    assert [pair["source_cache_id"] for pair in train] == [episode["id"] for episode in expected]
    # A repeated perturbed start cannot replace the ordinary reference.
    perturbation = teacher_root / "dagger" / "000"
    perturbation.mkdir(parents=True)
    (perturbation / "episode.json").write_text(json.dumps(dict(teacher_version=2, demo_version=2,
        start="validation-start-000", goal="validation-goal-000", success=True, dagger=0)))
    (perturbation / "samples.bin.gz").touch()
    drive.prepare(args)
    assert len([pair for pair in drive.load_pairs(args) if pair["split"] == "validation"]) == 27
    failed_reference = Path(train[0]["teacher_episode"]) / "episode.json"
    failed_teacher = json.loads(failed_reference.read_text())
    failed_teacher["success"] = False
    failed_reference.write_text(json.dumps(failed_teacher))
    args.output = tmp_path / "drive-with-train-failure"
    drive.prepare(args)
    train_with_failure = [pair for pair in drive.load_pairs(args) if pair["split"] == "train"]
    assert [pair["source_cache_id"] for pair in train_with_failure] == [pair["source_cache_id"] for pair in train]
    assert train_with_failure[0]["teacher_success"] is False


@pytest.mark.parametrize("beta", [-.1, 1.1, float("nan")])
def test_collect_rejects_invalid_mixing_probability(beta):
    with pytest.raises(ValueError, match="teacher-beta"):
        drive.collect(SimpleNamespace(teacher_beta=beta, dagger_collection=True))


def test_measurement_rejects_teacher_control():
    with pytest.raises(ValueError, match="Measured drives"):
        drive.collect(SimpleNamespace(teacher_beta=.5, dagger_collection=False))
