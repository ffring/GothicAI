"""Stage 9.3 PPO: rewards add up to the 9.2 score, GAE, minibatches, and (on the model host) the
no-update ratio of padded batched replays against row-by-row behaviour sampling."""
from __future__ import annotations

import math
from pathlib import Path
import random
import sys

import numpy
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_rl_collect import assign, sample_action, score  # noqa: E402
from body_rl_ppo import buckets, carried, episode_rewards, gae  # noqa: E402


def arrays(labels, hp, target_hp, t_ms):
    return {"labels": numpy.asarray(labels, numpy.int16), "hp": numpy.asarray(hp, numpy.float32),
            "target_hp": numpy.asarray(target_hp, numpy.float32), "t_ms": numpy.asarray(t_ms, numpy.int64)}


def summary(reason, primary_hp, opponent_hp, elapsed):
    return {"reason": reason, "primary_hp": primary_hp, "primary_hp_max": 300, "opponent_hp": opponent_hp,
            "opponent_hp_max": 300, "elapsed_game_ms": elapsed, "task": "native_duel"}


@pytest.mark.parametrize("reason,primary,opponent", [("opponent_down", 230, 0), ("primary_down", 0, 90),
                                                      ("target_visibility_lost", 160, 300), ("time_limit", 300, 300)])
def test_rewards_add_up_to_the_episode_score(reason, primary, opponent):
    data = arrays([-1, 3, -1, 6, -1, 9, -1], [1, 1, 1, 0.9, -1, 0.8, 0.8], [1, 1, 1, 1, 0.7, 0.5, 0.4],
                  [100, 150, 200, 900, 950, 1700, 1750])
    row = summary(reason, primary, opponent, 4000)
    decisions, rewards, dts = episode_rewards(data, row, 60000)
    assert decisions.tolist() == [1, 3, 5]
    assert math.isclose(rewards.sum(), score(row, 60000), abs_tol=1e-6)
    assert dts.tolist() == [0.75, 0.8, 0.0]
    # damage taken before the second decision is charged to the first one
    assert math.isclose(rewards[0], 0.5*(0.9-1.0), abs_tol=1e-6)


def test_missing_health_is_carried_forward():
    assert carried(numpy.asarray([-1, 0.5, -1, 0.25])).tolist() == [1.0, 0.5, 0.5, 0.25]


def test_gae_with_lambda_one_is_reward_to_go():
    rewards, values, dts = numpy.asarray([0.1, -0.2, 1.0]), numpy.asarray([0.3, 0.2, 0.5]), numpy.asarray([0.5, 0.5, 0.0])
    advantages, returns = gae(rewards, values, dts, gamma_per_s=1.0, lam=1.0)
    assert numpy.allclose(returns, [0.9, 0.8, 1.0])
    assert numpy.allclose(advantages, returns-values)
    advantages, returns = gae(rewards, values, dts, gamma_per_s=0.81, lam=0.0)  # one-step TD, gamma^0.5 = 0.9
    assert numpy.allclose(returns, [0.1+0.9*0.2, -0.2+0.9*0.5, 1.0])


def test_minibatches_cover_every_episode_once_within_the_row_budget():
    episodes = [{"arrays": {"labels": numpy.zeros(length)}} for length in (50, 400, 60, 380, 55, 1200, 70)]
    batches = buckets(episodes, 800, random.Random(3))
    assert sorted(index for batch in batches for index in batch) == list(range(7))
    for batch in batches:
        longest = max(len(episodes[index]["arrays"]["labels"]) for index in batch)
        assert len(batch) == 1 or longest*len(batch) <= 800


def test_sampled_log_probability_matches_the_sampling_distribution():
    scores, mask = [0.0, 1.0, 2.0, -1.0]+[0.0]*8, [False, True, True, False]+[True]*8
    action, logp = sample_action(scores, mask, 1.0, random.Random(1))
    allowed = [index for index in range(1, 12) if mask[index]]
    total = sum(math.exp(scores[index]) for index in allowed)
    assert math.isclose(logp, scores[action]-math.log(total), abs_tol=1e-12)
    assert sample_action(scores, mask, 0.0, random.Random(1)) == (2, 0.0)


def test_padded_batched_replay_equals_row_by_row_behaviour():
    torch = pytest.importorskip("torch")
    if sys.platform != "linux":
        pytest.skip("body model forwards run on the model host")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"body-service/src"))
    from argparse import Namespace
    from body_rl_ppo import make_critic, prepass
    from gothic_body.models import ActorConfig, create_actor
    torch.manual_seed(3)
    actor = create_actor(ActorConfig("gru", feature_count=6)).eval()
    reference = create_actor(ActorConfig("gru", feature_count=6)).eval()
    rng, episodes = random.Random(4), []
    for length in (7, 19, 12):
        features = numpy.random.default_rng(length).normal(size=(length, 6)).astype(numpy.float32)
        masks, labels, logps, state = numpy.zeros((length, 12), bool), numpy.full(length, -1, numpy.int16), numpy.zeros(length), None
        for step in range(length):
            decide = step % 3 == 2
            mask = [not decide]+[rng.random() < 0.7 or index == 4 for index in range(1, 12)]
            with torch.no_grad():
                logits, state = actor.step(torch.tensor(features[step:step+1]), torch.tensor([mask]), state,
                                           torch.tensor([step == 0]))
            masks[step] = mask
            if decide:
                labels[step], logps[step] = sample_action(logits[0].tolist(), mask, 1.0, rng)
        resets = numpy.zeros(length, bool)
        resets[0] = True
        decisions = numpy.flatnonzero(labels >= 1)
        episodes.append({"arrays": {"features": features, "masks": masks, "resets": resets, "labels": labels, "logp": logps},
                         "decisions": decisions})
    stats = prepass(actor, reference, make_critic(6, 256), episodes, Namespace(prepass_batch=2), "cpu")
    assert stats["decisions"] == sum(len(item["decisions"]) for item in episodes)
    assert stats["max_abs_logp_diff"] < 1e-5


def test_schedule_keeps_tasks_and_seats_only_pool_duels():
    tasks = [[kind, 1000+index, index % 2, 300] for index, kind in enumerate(["native_duel", "block"]*200)]
    assign(tasks, seed=7, wall_share=0.5, opponents=["a.pt", "b.pt"], native_share=0.5, learner_seat="random")
    assert all(len(entry) == 7 and entry[1] == 1000+index for index, entry in enumerate(tasks))
    assert all(entry[5] == "native" for entry in tasks if entry[0] == "block")
    assert all(entry[6] == "primary" for entry in tasks if entry[5] == "native")
    pool = [entry for entry in tasks if entry[5] != "native"]
    assert 70 < len(pool) < 130 and {entry[5] for entry in pool} == {"a.pt", "b.pt"}
    assert {entry[6] for entry in pool} == {"primary", "opponent"}
    assert 150 < sum(entry[4] == "near_wall" for entry in tasks) < 250
    again = [[kind, 1000+index, index % 2, 300] for index, kind in enumerate(["native_duel", "block"]*200)]
    assign(again, seed=7, wall_share=0.5, opponents=["a.pt", "b.pt"], native_share=0.5, learner_seat="random")
    assert again == tasks
    native = [["native_duel", 1, 0, 300]]
    assign(native, seed=7, wall_share=0.0, opponents=[], native_share=0.5, learner_seat="opponent")
    assert native == [["native_duel", 1, 0, 300, "open", "native", "primary"]]
