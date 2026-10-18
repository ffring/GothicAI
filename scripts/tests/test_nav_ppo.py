"""Recurrent PPO math, episode boundaries, bootstrap and actor compatibility.

Model tests run on Ryzen under gq; never launch them on the development Mac.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nav_ppo as ppo


@pytest.fixture(autouse=True)
def single_thread():
    torch.set_num_threads(1)


def sample(action=(2, 1, 1, 1, 1, 0), valid=True):
    return dict(path_valid=valid, action_indices=list(action),
                rewards=dict(progress_cm=125, arrived=1, blocked=1, fall_damage=2,
                             death=1, jump=action[4], climb=action[5], time_ms=100))


def test_reward_weights_units_press_edges_and_invalid_intervals():
    components = ppo.reward_components(sample())
    assert components == pytest.approx(dict(progress_cm=1.25, arrived=10, blocked=-.05,
             fall_damage=-.2, death=-10, jump=-.1, climb=0, time=-.01))
    held = ppo.reward_components(sample(), previous_action=[2, 1, 1, 1, 1, 0])
    assert held["jump"] == 0
    assert not any(ppo.reward_components(sample(valid=False)).values())
    shorter = sample()
    shorter["rewards"]["time_ms"] = 20
    assert ppo.reward_components(shorter)["time"] == -.01


def test_gae_bootstraps_truncation_and_resets_terminal_and_invalid_intervals():
    advantage, returns = ppo.generalized_advantage([1., 2.], [3., 4.], [True, True],
                                                   [False, False], bootstrap=10., gamma=.5, lam=1.)
    np.testing.assert_allclose(advantage, [1.5, 3.])
    np.testing.assert_allclose(returns, [4.5, 7.])
    _, terminal = ppo.generalized_advantage([1., 2.], [3., 4.], [True, True],
                                           [False, True], bootstrap=1000., gamma=.5, lam=1.)
    np.testing.assert_allclose(terminal, [2., 2.])
    _, masked = ppo.generalized_advantage([1., 100000., 2.], [0., 500., 0.],
                      [True, False, True], [False, False, True], bootstrap=0., gamma=.5, lam=1.)
    np.testing.assert_allclose(masked, [251., 0., 2.])
    # Invalid marks an interval, not its entry state: t=0 ends at valid V(s1)=4.
    _, boundary = ppo.generalized_advantage([1., 100000.], [3., 4.],
                      [True, False], [False, False], bootstrap=1000., gamma=.5, lam=1.)
    np.testing.assert_allclose(boundary, [3., 0.])


def test_independent_heads_exact_kl_entropy_and_clipped_surrogate():
    logits = {name: torch.zeros(1, 2, classes) for name, classes in zip(ppo.HEADS, ppo.CLASSES)}
    actions = torch.zeros(1, 2, 6, dtype=torch.int64)
    reference = torch.zeros(1, 2, sum(ppo.CLASSES))
    logp, entropy, kl = ppo.policy_terms(logits, actions, reference)
    torch.testing.assert_close(logp, torch.full((1, 2), -sum(np.log(ppo.CLASSES))))
    assert torch.count_nonzero(kl) == 0
    for name, classes in zip(ppo.HEADS, ppo.CLASSES):
        torch.testing.assert_close(entropy[name], torch.full((1, 2), float(np.log(classes))))
    loss, ratio = ppo.clipped_policy_loss(torch.log(torch.tensor([2., .5])), torch.zeros(2), torch.tensor([1., -1.]))
    torch.testing.assert_close(ratio, torch.tensor([2., .5]))
    torch.testing.assert_close(loss, torch.tensor([-1.2, .8]))


def test_privileged_input_is_normalized_separately_from_actor():
    actual = ppo.privileged(dict(path_cm=1234., first_direction=[0., 0., 1.], blocked=True, in_air=False))
    np.testing.assert_allclose(actual, [1.234, 0., 0., 1., 1., 0.])
    with pytest.raises(ValueError, match="first_direction"):
        ppo.privileged(dict(path_cm=12., first_direction=[float("nan"), 0, 0], blocked=False, in_air=False))


def args(**kwargs):
    return SimpleNamespace(**(dict(segment=2, burn_in=1, device="cpu", replay_tolerance=1e-4,
           gamma=.995, gae_lambda=.95, batch=2, critic_only=False, value_coef=.5,
           max_grad_norm=.5, clip=.2, kl_coef=.1, entropy_coef=.003) | kwargs))


def make_episode(tmp_path, truncated=False):
    directory = tmp_path / "episode"
    directory.mkdir()
    count = 6
    records = np.zeros(count + 1, dtype=ppo.nav_demos.RECORD_V2_DTYPE)
    records["time_ms"] = np.arange(count + 1) * 100
    records["roads"]["carrot_direction"][:, 2] = 1
    records["roads"]["goal_direction"][:, 2] = 1
    records["roads"]["carrot_distance_cm"] = 500
    records["roads"]["goal_distance_cm"] = 1000
    records["body"]["velocity_cm_s"][:, 2] = np.arange(count + 1) * 5
    layout = np.zeros((ppo.nav_demos.RAY_COUNT, 3), np.float32)
    layout[:, 2] = 1000
    for name, data in (("samples.bin.gz", records[:count]), ("bootstrap.bin.gz", records[count:])):
        if name.startswith("bootstrap") and not truncated:
            continue
        with gzip.open(directory / name, "wb") as stream:
            stream.write(ppo.nav_demos.HEADER.pack(ppo.nav_demos.MAGIC, 2, ppo.nav_demos.RECORD_BYTES,
                            ppo.nav_demos.RAY_COUNT, ppo.nav_demos.NEAR_SIZE, ppo.nav_demos.FAR_SIZE, 100))
            stream.write(data.tobytes())
    (directory / "episode.json").write_text(json.dumps(dict(decisions=count, success=not truncated, eye_layout=layout.tolist())))
    torch.manual_seed(123)
    actor = ppo.NavPolicy(ppo.VECTOR_SIZE, hidden_size=16).eval()
    features = ppo.encode_features(ppo.nav_demos.learner_input(records[:count]), layout)
    with torch.no_grad():
        logits, _ = actor(**{key: torch.from_numpy(value[None]) for key, value in features.items()})
    rows = []
    for index in range(count):
        action = [2, 1, 1, 1, int(index < 2), 0]
        flat = [float(value) for head in ppo.HEADS for value in logits[head][0, index]]
        logp = ppo.categorical_log_probs(flat, np.array(action))
        critic = dict(path_cm=1000-index*100, first_direction=[0, 0, 1], blocked=False, in_air=False, valid=True)
        row = sample(action)
        row.update(time_ms=index * 100, next_time_ms=(index + 1) * 100,
                   log_probs=logp.tolist(), log_prob=float(logp.sum()), logits=flat,
                   terminated=index == count-1 and not truncated, truncated=index == count-1 and truncated,
                   critic_before=critic, critic_after=critic)
        row["rewards"].update(arrived=int(index == count-1 and not truncated), death=0)
        rows.append(row)
    (directory / "samples.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
    reference = tmp_path / "round2.navbc"
    preprocessing = dict(ppo.PREPROCESSING, eye_layout=layout.tolist())
    ppo.export_weights(actor, reference, preprocessing)
    return directory, actor, reference


def test_replay_recovers_exact_hidden_at_burn_in_and_resets_each_episode(tmp_path):
    directory, actor, _ = make_episode(tmp_path)
    episode, bootstrap, summary = ppo.read_episode(directory, ppo.REWARD_WEIGHTS)
    critic = ppo.NavCritic(actor).eval()
    error, logit_error = ppo.replay_episode(episode, bootstrap, actor, critic, actor, args())
    assert error < 1e-5 and logit_error < 1e-5
    assert summary["jump_presses"] == 1
    assert summary["climb_presses"] == 0
    windows = [1, 2]
    burn = ppo.batch_arrays(episode, windows, args(), burn=True)
    training = ppo.batch_arrays(episode, windows, args())
    initial = torch.from_numpy(episode["actor_hidden"][windows][None])
    with torch.no_grad():
        _, hidden = actor(**ppo.features_of(burn), hidden=initial)
        replayed, _ = actor(**ppo.features_of(training), hidden=hidden)
        whole, _ = actor(**{key: torch.from_numpy(episode[key][None]) for key in ("near", "far", "vector")})
    for row, window in enumerate(windows):
        start = episode["starts"][window]
        for head in ppo.HEADS:
            torch.testing.assert_close(replayed[head][row], whole[head][0, start:start+2], rtol=1e-5, atol=1e-6)
    # Calling replay again must start the new episode at zeros, independent of the old final state.
    again, bootstrap, _ = ppo.read_episode(directory, ppo.REWARD_WEIGHTS)
    ppo.replay_episode(again, bootstrap, actor, critic, actor, args())
    np.testing.assert_array_equal(again["actor_hidden"], episode["actor_hidden"])
    assert not episode["actor_hidden"][0].any()


def test_replay_rejects_wrong_collector_policy_before_optimization(tmp_path):
    directory, actor, _ = make_episode(tmp_path)
    episode, bootstrap, _ = ppo.read_episode(directory, ppo.REWARD_WEIGHTS)
    episode["recorded_logits"][3, 0] += .1
    with pytest.raises(ValueError, match="policy replay mismatch"):
        ppo.replay_episode(episode, bootstrap, actor, ppo.NavCritic(actor), actor, args())


def test_truncated_bootstrap_uses_final_observation_and_carried_critic_state(tmp_path):
    directory, actor, _ = make_episode(tmp_path, truncated=True)
    episode, bootstrap, _ = ppo.read_episode(directory, ppo.REWARD_WEIGHTS)
    critic = ppo.NavCritic(actor).eval()
    with torch.no_grad():
        critic.value.weight.fill_(.1)
        features = {key: torch.from_numpy(episode[key][None]) for key in ("near", "far", "vector")}
        values, hidden = critic(**features, priv=torch.from_numpy(episode["priv"][None]))
        final_features = {key: torch.from_numpy(value[None]) for key, value in bootstrap.items()}
        final, _ = critic(**final_features, hidden=hidden)
    ppo.replay_episode(episode, bootstrap, actor, critic, actor, args())
    expected = episode["rewards"][-1] + .995 * float(final[0, 0])
    assert episode["returns"][-1] == pytest.approx(expected, abs=1e-6)
    (directory / "bootstrap.bin.gz").unlink()
    with pytest.raises(ValueError, match="requires final actor observation"):
        ppo.read_episode(directory, ppo.REWARD_WEIGHTS)


def test_critic_warmup_changes_only_critic_and_preserves_portable_actor(tmp_path):
    directory, actor, reference = make_episode(tmp_path)
    episode, bootstrap, _ = ppo.read_episode(directory, ppo.REWARD_WEIGHTS)
    critic = ppo.NavCritic(actor).eval()
    options = args(critic_only=True)
    ppo.replay_episode(episode, bootstrap, actor, critic, actor, options)
    actor_before = {key: value.detach().clone() for key, value in actor.state_dict().items()}
    critic_before = critic.value.weight.detach().clone()
    actor_optimizer = torch.optim.Adam(actor.parameters(), lr=1e-3)
    critic_optimizer = torch.optim.Adam(critic.parameters(), lr=1e-3)
    measures, count = ppo.train_episode(episode, actor, critic, actor_optimizer, critic_optimizer,
                                       options, random.Random(3), 0., 1.)
    assert count == 6 and measures["value_loss"] > 0
    for key, value in actor.state_dict().items():
        torch.testing.assert_close(value, actor_before[key], rtol=0, atol=0)
    assert not torch.equal(critic.value.weight, critic_before)
    imported, preprocessing = ppo.load_actor(reference, "cpu")
    exported = tmp_path / "updated.navbc"
    ppo.export_weights(imported, exported, preprocessing)
    assert reference.read_bytes() == exported.read_bytes()


def test_actor_ppo_update_uses_sampled_actions_and_changes_actor(tmp_path):
    directory, actor, _ = make_episode(tmp_path)
    episode, bootstrap, _ = ppo.read_episode(directory, ppo.REWARD_WEIGHTS)
    critic = ppo.NavCritic(actor).eval()
    ppo.replay_episode(episode, bootstrap, actor, critic, actor, args())
    initial = actor.head_forward.weight.detach().clone()
    measures, count = ppo.train_episode(episode, actor, critic,
        torch.optim.Adam(actor.parameters(), lr=1e-3), torch.optim.Adam(critic.parameters(), lr=1e-3),
        args(), random.Random(5), 0., 1.)
    assert count == 6 and np.isfinite(measures["policy_loss"])
    assert not torch.equal(actor.head_forward.weight, initial)
