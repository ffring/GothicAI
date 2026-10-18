"""Asset-free tests for split isolation, categorical math and RL transition data."""
import gzip
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("nav_rl", Path(__file__).resolve().parents[1] / "nav_rl.py")
rl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rl)
demos = rl.module("nav_demos")


def test_historical_sources_exclude_all_attempts_and_both_directions(tmp_path):
    (tmp_path / "old.jsonl").write_text(json.dumps(dict(start="A", goal="B", success=False)) + "\n")
    (tmp_path / "index.json").write_text(json.dumps(dict(episodes=[dict(pair=["C", "D"], split="train")])))
    (tmp_path / "pairs.csv").write_text("region,start,goal\nmarket,E,F\n")
    assert rl.historical_pairs([tmp_path]) == {("A", "B"), ("B", "A"), ("C", "D"), ("D", "C"), ("E", "F"), ("F", "E")}
    with pytest.raises(ValueError, match="Missing exclusion"):
        rl.historical_pairs([tmp_path / "missing"])


def candidates():
    return [dict(region=region, start=f"{region}-S{n}", goal=f"{region}-G{n}", detour_complete=True)
            for region in rl.REGIONS for n in range(8)]


def test_selection_is_seeded_unique_stratified_and_excludes_reverse():
    pool = candidates()
    excluded = {(pool[0]["goal"], pool[0]["start"])}
    selected = rl.select_pairs(pool, excluded, 3, 91)
    assert selected == rl.select_pairs(list(reversed(pool)), excluded, 3, 91)
    assert selected != rl.select_pairs(pool, excluded, 3, 92)
    assert all((p["start"], p["goal"])[::-1] not in excluded for p in selected)
    assert len(selected) == 18
    assert [p["index"] for p in selected] == list(range(18))
    assert rl.Counter(p["region"] for p in selected) == {r: 3 for r in rl.REGIONS}


def test_selection_rejects_insufficient_region_and_duplicate_undirected_pairs():
    pool = candidates()
    with pytest.raises(ValueError, match="Need 9 unused"):
        rl.select_pairs(pool, set(), 9, 1)
    pool.append(dict(region="market", start=pool[0]["goal"], goal=pool[0]["start"]))
    selected = rl.select_pairs(pool, set(), 8, 1)
    assert len({frozenset((p["start"], p["goal"])) for p in selected}) == 48


def test_frozen_selection_cannot_be_replaced(tmp_path):
    path = tmp_path / "pairs.jsonl"
    rl.write_pairs(path, candidates())
    rl.write_pairs(path, candidates())
    assert rl.load_pairs(path) == candidates()
    with pytest.raises(ValueError, match="Refusing to replace"):
        rl.write_pairs(path, candidates()[1:])


def test_categorical_log_probs_use_independent_stable_softmax():
    logits = [1000, 1001, 1002, -1002, -1001, -1000, 4, 5, 6, 30, 31, -100, 100, 0, 0]
    action = [0, 2, 1, 1, 0, 1]
    expected = [1000 - (1002 + math.log(1 + math.exp(-1) + math.exp(-2))),
                -math.log(1 + math.exp(-1) + math.exp(-2)),
                -1 - math.log(1 + math.exp(-1) + math.exp(-2)),
                -math.log(1 + math.exp(-1)), -200, -math.log(2)]
    np.testing.assert_allclose(rl.categorical_log_probs(logits, action), expected, atol=1e-12)
    heads = dict(zip(rl.HEADS, np.split(np.array(logits), np.cumsum(rl.CLASSES)[:-1])))
    np.testing.assert_array_equal(rl.categorical_log_probs(heads, action), rl.categorical_log_probs(logits, action))


@pytest.mark.parametrize("action,logits", [([3, 0, 0, 0, 0, 0], [0] * 15),
                                           ([0, 0, 0, 0, 0, .5], [0] * 15),
                                           ([0] * 6, [0] * 14),
                                           ([0] * 6, [float("nan")] * 15)])
def test_categorical_log_probs_reject_corruption(action, logits):
    with pytest.raises(ValueError):
        rl.categorical_log_probs(logits, action)


def episode(path, success=True):
    path.mkdir()
    records = np.zeros(2, dtype=demos.RECORD_V2_DTYPE)
    records["time_ms"] = [0, 100]
    with gzip.open(path / "samples.bin.gz", "wb") as stream:
        stream.write(demos.HEADER.pack(demos.MAGIC, 2, demos.RECORD_BYTES, demos.RAY_COUNT,
                                      demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
        stream.write(records.tobytes())
    (path / "episode.json").write_text(json.dumps(dict(decisions=2, success=success, elapsed_ms=200)))
    log_probs = [-math.log(n) for n in rl.CLASSES]
    samples = [dict(time_ms=n * 100, next_time_ms=(n + 1) * 100, logits=[0] * 15, action_indices=[1] * 6,
                    log_probs=log_probs, log_prob=sum(log_probs),
                    path_before_cm=1000 - 500 * n, path_after_cm=500 - 500 * n,
                    rewards=dict(progress_cm=500, arrived=int(n == 1 and success), blocked=0,
                                 fall_damage=0, death=0, jump=0, climb=0, time_ms=100),
                    terminated=n == 1 and success, truncated=n == 1 and not success) for n in range(2)]
    (path / "samples.jsonl").write_text("".join(json.dumps(sample) + "\n" for sample in samples))
    actions = np.zeros((3, 9))
    actions[:, 0] = [0, 100, 200]
    actions[:2, 4:7] = 1
    actions[-1, 4] = 1
    np.savetxt(path / "actions.txt", actions)
    return samples


def save_samples(path, samples):
    (path / "samples.jsonl").write_text("".join(json.dumps(sample) + "\n" for sample in samples))


def test_sidecar_roundtrip_validates_cpp_math_and_telescoping_progress(tmp_path):
    path = tmp_path / "episode"
    samples = episode(path)
    assert list(rl.iter_samples(path)) == samples
    result = rl.check_episode(path)
    assert result["decisions"] == 2
    assert result["max_log_prob_error"] < 1e-15
    assert result["reward_sums"]["progress_cm"] == 1000
    assert result["successful_progress_relative_error"] == 0


@pytest.mark.parametrize("corruption,error", [
    (lambda samples: samples[1].update(log_prob=0), "Log-prob error"),
    (lambda samples: samples[1].update(time_ms=0), "Nonincreasing"),
    (lambda samples: samples[1]["rewards"].update(progress_cm=3), "Detour path difference"),
    (lambda samples: samples[1].update(path_before_cm=503), "Detour path difference"),
    (lambda samples: samples[1]["rewards"].update(death=-1), "unsigned reward"),
])
def test_sidecar_rejects_corrupt_transition(tmp_path, corruption, error):
    path = tmp_path / "episode"
    samples = episode(path)
    corruption(samples)
    save_samples(path, samples)
    with pytest.raises(ValueError, match=error):
        rl.check_episode(path)


def test_failed_episode_keeps_nonzero_residual_and_negative_progress(tmp_path):
    path = tmp_path / "failed"
    samples = episode(path, success=False)
    samples[1].update(path_after_cm=600)
    samples[1]["rewards"].update(progress_cm=-100)
    save_samples(path, samples)
    result = rl.check_episode(path)
    assert result["reward_sums"]["progress_cm"] == 400
    assert result["successful_progress_relative_error"] is None


def test_population_throughput_uses_concurrent_span_not_sum(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "checked_paths", lambda *a, **k: None)
    monkeypatch.setattr(rl, "load_pairs", lambda p: candidates())
    monkeypatch.setattr(rl, "new_runner", lambda *a: SimpleNamespace(close=lambda: None))
    def worker(args, index, barrier):
        barrier.wait()
        return dict(decisions=100 + index, collector_wall_s=10 + index)
    monkeypatch.setattr(rl, "population_worker", worker)
    args = SimpleNamespace(parallel=4, pairs=None, output=tmp_path, population=20,
                           record=False, action_mode="sample")
    rl.population(args)
    result = rl.read_json(tmp_path / "throughput-n20-r4.json")
    assert result["collection_wall_s"] == 13
    assert result["decisions_per_second"] == pytest.approx(406 / 13)


def test_drive_requires_previously_frozen_teacher_set():
    with pytest.raises(ValueError, match="frozen"):
        # Exercise the guard before any runner or weights can be loaded.
        original = rl.load_pairs
        try:
            rl.load_pairs = lambda _: candidates()
            rl.drive(SimpleNamespace(pairs=Path("unused")))
        finally:
            rl.load_pairs = original


def test_sidecar_selected_action_must_equal_applied_keys(tmp_path):
    path = tmp_path / "episode"
    episode(path)
    actions = np.loadtxt(path / "actions.txt")
    actions[0, 1] = 1
    np.savetxt(path / "actions.txt", actions)
    with pytest.raises(ValueError, match="Selected action differs"):
        rl.check_episode(path)


def test_sidecar_clock_must_equal_navdemo_input_clock(tmp_path):
    path = tmp_path / "episode"
    episode(path)
    with gzip.open(path / "samples.bin.gz", "rb") as stream:
        header = stream.read(demos.HEADER.size)
        records = np.frombuffer(stream.read(), dtype=demos.RECORD_V2_DTYPE).copy()
    records["time_ms"][1] += 1
    with gzip.open(path / "samples.bin.gz", "wb") as stream:
        stream.write(header)
        stream.write(records.tobytes())
    with pytest.raises(ValueError, match="NAVDEMO/sample timestamps"):
        rl.check_episode(path)


def test_equivalence_keeps_reference_command_and_places_only_far_hero(tmp_path):
    args = SimpleNamespace(roads=Path('/roads.json'), npc='NPC', limit_ms=240000,
                           weights=Path('/policy.navbc'), seed=7,
                           far_waypoint='NW_FARM1_ENTRANCE_01')
    pair = dict(start='START', goal='GOAL')
    reference = rl.equivalence_commands(args, pair, tmp_path / 'pair.txt', 'reference')
    near = rl.equivalence_commands(args, pair, tmp_path / 'pair.txt', 'near')
    far = rl.equivalence_commands(args, pair, tmp_path / 'pair.txt', 'far')
    assert reference == ['roads load /roads.json', 'roads on NPC',
                         'navdemos drive NPC START GOAL 240000 /policy.navbc 0 7', 'navdemos wait']
    assert not any(command.startswith('goto ') for command in near)
    assert near[2].endswith('1 7 argmax 1 1 near 0')
    assert far[2] == 'goto NW_FARM1_ENTRANCE_01'
    assert far[3].endswith('1 7 argmax 1 1 far 0')


def test_comparison_requires_route_identity_success_and_five_percent_time():
    reference = {('A', 'B'): dict(success=True, elapsed_ms=100000)}
    assert rl.comparison_metrics(reference, {('A', 'B'): dict(success=True, elapsed_ms=105000)})['passed']
    assert not rl.comparison_metrics(reference, {('A', 'B'): dict(success=True, elapsed_ms=105001)})['passed']
    assert not rl.comparison_metrics(reference, {('A', 'B'): dict(success=False, elapsed_ms=100000)})['passed']
    with pytest.raises(ValueError, match='identical route sets'):
        rl.comparison_metrics(reference, {('A', 'C'): dict(success=True, elapsed_ms=100000)})


def test_equivalence_selects_first_pairs_and_resets_each_comparison(tmp_path, monkeypatch):
    pairs = candidates()
    monkeypatch.setattr(rl, 'checked_paths', lambda *a, **k: None)
    monkeypatch.setattr(rl, 'load_pairs', lambda _: pairs)
    closed = []
    monkeypatch.setattr(rl, 'new_runner', lambda *a: SimpleNamespace(close=lambda: closed.append(True)))
    calls = []
    def scenario(args, runner, name, commands, directory):
        _, mode, index = name.split('-')
        index = int(index)
        calls.append((index, mode, args.seed, commands))
        episode = dict(start=pairs[index]['start'], goal=pairs[index]['goal'], success=True,
                       elapsed_ms=1000, decisions=10, demo_version=2, drive=True,
                       min_hero_distance_cm=7000 if mode == 'far' else 500,
                       max_hero_distance_cm=8000 if mode == 'far' else 1500)
        destination = directory / 'episode.json' if mode == 'reference' else directory / 'episodes/0/episode.json'
        rl.write_json(destination, episode)
    monkeypatch.setattr(rl, 'scenario', scenario)
    args = SimpleNamespace(pairs=None, count=2, output=tmp_path, seed=7, npc='NPC',
                           roads=Path('/roads.json'), weights=Path('/policy.navbc'),
                           limit_ms=240000, far_waypoint='NW_FARM1_ENTRANCE_01', min_far_cm=6000,
                           resume=False)
    rl.equivalence(args)
    assert [(index, mode) for index, mode, _, _ in calls] == [
        (0, 'reference'), (0, 'near'), (0, 'far'), (1, 'reference'), (1, 'near'), (1, 'far')]
    assert all(seed == 7 for _, _, seed, _ in calls)
    assert closed == [True]
    result = rl.read_json(tmp_path / 'equivalence.json')
    assert result['passed']
    assert result['n'] == 2
    assert result['drive_vs_population']['reference_arrived'] == 2
    assert all(pair['far_enough'] for pair in result['far_distances'])
    assert list(rl.rows(tmp_path / 'selected-pairs.jsonl')) == pairs[:2]
