#!/usr/bin/env python3
"""One bounded-memory recurrent PPO update for a recorded navigation population.

Run on Ryzen/Linux inside gq batch. Collection and checkpoint selection belong to
nav-rl-ppo-loop.sh. The actor and exported NAVBC format remain unchanged. Each
sampled episode is replayed from zero state before optimization; sampled logits
and log probabilities must match the collector. PPO windows start from recorded
recurrent states, then replay a detached burn-in. The privileged critic has its
own recurrent state. Truncated populations bootstrap from bootstrap.bin.gz,
whereas true terminal transitions bootstrap zero. Invalid-path intervals carry
no reward or loss and cut GAE recursion.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import torch
from torch import nn

import nav_demos
from nav_bc_data import HEADS, CLASSES, PREPROCESSING, VECTOR_SIZE, encode_features
from nav_bc_model import NavPolicy, export_weights
from nav_bc_numpy import NumpyPolicy
from nav_rl import categorical_log_probs, iter_samples

PRIVILEGED_SIZE = 6
REWARD_WEIGHTS = dict(progress_cm=.01, arrived=10., blocked=-.05,
                      fall_damage=-.1, death=-10., jump=-.1, climb=-.1, time=-.01)


def privileged(state):
    """Only the critic receives true Detour distance/direction and contact flags."""
    direction = np.asarray(state["first_direction"], dtype=np.float32)
    if direction.shape != (3,) or not np.isfinite(direction).all():
        raise ValueError("critic first_direction must contain three finite values")
    path = float(state["path_cm"])
    if not np.isfinite(path) or path < 0:
        raise ValueError("critic Detour path must be finite and nonnegative")
    return np.asarray([min(path / 1000., 50.), *direction,
                       float(state["blocked"]), float(state["in_air"])], dtype=np.float32)


def reward_components(sample, weights=REWARD_WEIGHTS, previous_action=None):
    source = dict(sample["rewards"])
    previous = [0] * 6 if previous_action is None else previous_action
    for index, name in ((4, "jump"), (5, "climb")):
        source[name] = int(sample["action_indices"][index] != 0 and previous[index] == 0)
    components = {name: weight * (1. if name == "time" else float(source[name]))
                  for name, weight in weights.items()}
    if not all(np.isfinite(value) for value in components.values()):
        raise ValueError("nonfinite reward")
    if not sample.get("path_valid", False):
        components = dict.fromkeys(components, 0.)
    return components


def generalized_advantage(rewards, values, valid, terminated, bootstrap=0., gamma=.995, lam=.95):
    """GAE for one episode; invalid intervals split it into independent stretches."""
    rewards, values = np.asarray(rewards), np.asarray(values)
    valid, terminated = np.asarray(valid, bool), np.asarray(terminated, bool)
    advantage = np.zeros(len(rewards), np.float32)
    carry = 0.
    for index in range(len(rewards) - 1, -1, -1):
        if not valid[index]:
            carry = 0.
            continue
        # A valid interval guarantees its endpoint state is valid, even if the
        # following interval leaves the mesh. Bootstrap that state; the invalid
        # interval has already zeroed carry and contributes no reward or loss.
        continuation = not terminated[index]
        following = float(values[index + 1]) if index + 1 < len(rewards) else float(bootstrap)
        delta = float(rewards[index]) + gamma * following * continuation - float(values[index])
        carry = delta + gamma * lam * continuation * carry
        advantage[index] = carry
    returns = np.where(valid, advantage + values, 0).astype(np.float32)
    return advantage, returns


class NavCritic(nn.Module):
    """An independent observation encoder/GRU plus training-only privileged input."""
    def __init__(self, actor):
        super().__init__()
        import copy
        self.near_encoder = copy.deepcopy(actor.near_encoder)
        self.far_encoder = copy.deepcopy(actor.far_encoder)
        self.vector_encoder = nn.Sequential(nn.Linear(actor.vector_size + PRIVILEGED_SIZE, 128), nn.ReLU())
        with torch.no_grad():
            self.vector_encoder[0].weight[:, :actor.vector_size].copy_(actor.vector_encoder[0].weight)
            self.vector_encoder[0].weight[:, actor.vector_size:].zero_()
            self.vector_encoder[0].bias.copy_(actor.vector_encoder[0].bias)
        self.fusion = copy.deepcopy(actor.fusion)
        self.gru = copy.deepcopy(actor.gru)
        self.value = nn.Linear(actor.hidden_size, 1)
        nn.init.zeros_(self.value.weight)
        nn.init.zeros_(self.value.bias)
        self.hidden_size = actor.hidden_size

    def forward(self, near, far, vector, priv, hidden=None):
        batch, length = vector.shape[:2]
        encoded = torch.cat((self.near_encoder(near.reshape(-1, 9, 32, 32)),
                             self.far_encoder(far.reshape(-1, 9, 25, 25)),
                             self.vector_encoder(torch.cat((vector, priv), -1).reshape(batch * length, -1))), -1)
        recurrent, hidden = self.gru(self.fusion(encoded).reshape(batch, length, 128), hidden)
        return self.value(recurrent).squeeze(-1), hidden


def load_actor(path, device):
    """Import existing portable weights without requiring an original torch checkpoint."""
    portable = NumpyPolicy(path)
    if portable.vector_size != VECTOR_SIZE or portable.header["preprocessing"].get("navdemo_version") != 2:
        raise ValueError("PPO requires the existing NAVDEMO v2 actor")
    actor = NavPolicy(portable.vector_size, portable.hidden_size)
    actor.load_state_dict({name: torch.from_numpy(value.copy()) for name, value in portable.weights.items()})
    return actor.to(device), portable.header["preprocessing"]


def policy_terms(logits, actions, reference_logits=None):
    log_probability = torch.zeros(actions.shape[:-1], device=actions.device)
    entropy, kl = {}, torch.zeros_like(log_probability)
    offset = 0
    for index, (name, classes) in enumerate(zip(HEADS, CLASSES)):
        logp = logits[name].log_softmax(-1)
        probability = logp.exp()
        log_probability = log_probability + logp.gather(-1, actions[..., index:index+1]).squeeze(-1)
        entropy[name] = -(probability * logp).sum(-1)
        if reference_logits is not None:
            reference = reference_logits[..., offset:offset+classes].log_softmax(-1)
            kl = kl + (probability * (logp - reference)).sum(-1)
        offset += classes
    return log_probability, entropy, kl


def clipped_policy_loss(new_logp, old_logp, advantage, clip=.2):
    ratio = (new_logp - old_logp).clamp(-20, 20).exp()
    objective = torch.minimum(ratio * advantage, ratio.clamp(1-clip, 1+clip) * advantage)
    return -objective, ratio


def tensor_features(episode, start, stop, device):
    return {name: torch.from_numpy(episode[name][None, start:stop]).to(device)
            for name in ("near", "far", "vector")}


def read_episode(directory, weights):
    """Stream NAVDEMO raw records, retaining only actor inputs and compact metadata."""
    metadata = json.loads((directory / "episode.json").read_text())
    count = int(metadata["decisions"])
    if count <= 0 or nav_demos.episode_version(directory) != 2:
        raise ValueError(f"Expected nonempty NAVDEMO v2 episode: {directory}")
    layout = nav_demos.ray_layout(directory)
    arrays = dict(near=np.empty((count, 9, 32, 32), np.float32),
                  far=np.empty((count, 9, 25, 25), np.float32),
                  vector=np.empty((count, VECTOR_SIZE), np.float32),
                  priv=np.empty((count, PRIVILEGED_SIZE), np.float32),
                  actions=np.empty((count, 6), np.int64), old_logp=np.empty(count, np.float32),
                  recorded_logits=np.empty((count, sum(CLASSES)), np.float32),
                  rewards=np.empty(count, np.float32), valid=np.empty(count, bool),
                  terminated=np.empty(count, bool))
    samples = iter(iter_samples(directory))
    sums, absolute = Counter(), Counter()
    offset, last, turn_count, blocked, jump_presses, climb_presses = 0, None, 0, 0, 0, 0
    for records in nav_demos.iter_chunks(directory, chunk_records=32):
        features = encode_features(nav_demos.learner_input(records), layout)
        size = len(records)
        if offset + size > count:
            raise ValueError(f"NAVDEMO count exceeds episode metadata: {directory}")
        for name, values in features.items():
            arrays[name][offset:offset+size] = values
        for local, record in enumerate(records):
            sample = next(samples, None)
            if sample is None or sample["time_ms"] != int(record["time_ms"]):
                raise ValueError(f"Sample/NAVDEMO timestamp mismatch: {directory}")
            if last is not None and (last["terminated"] or last["truncated"] or last["next_time_ms"] != sample["time_ms"]):
                raise ValueError(f"Noncontiguous or post-terminal sample: {directory}")
            if sample["terminated"] and sample["truncated"]:
                raise ValueError("Transition cannot be both terminated and truncated")
            index = offset + local
            action = np.asarray(sample["action_indices"])
            expected = categorical_log_probs(sample["logits"], action)
            if (np.max(np.abs(expected - np.asarray(sample["log_probs"]))) > 1e-4 or
                    abs(float(expected.sum()) - sample["log_prob"]) > 1e-4):
                raise ValueError(f"Corrupt sampled log probabilities: {directory}:{index}")
            arrays["actions"][index] = action
            arrays["old_logp"][index] = sample["log_prob"]
            arrays["recorded_logits"][index] = sample["logits"]
            arrays["priv"][index] = privileged(sample["critic_before"])
            arrays["valid"][index] = bool(sample["path_valid"])
            arrays["terminated"][index] = bool(sample["terminated"])
            previous = None if last is None else last["action_indices"]
            jump_presses += int(action[4] != 0 and (previous is None or previous[4] == 0))
            climb_presses += int(action[5] != 0 and (previous is None or previous[5] == 0))
            components = reward_components(sample, weights, previous)
            arrays["rewards"][index] = sum(components.values())
            sums.update(components)
            absolute.update({key: abs(value) for key, value in components.items()})
            turn_count += int(action[0] == 1 and action[1] == 1 and action[2] != 1)
            blocked += int(sample["rewards"]["blocked"] != 0)
            last = sample
        offset += size
    if offset != count or next(samples, None) is not None or not (last["terminated"] or last["truncated"]):
        raise ValueError(f"Incomplete sampled episode: {directory}")
    bootstrap = None
    if last["truncated"]:
        endpoint = directory / "bootstrap.bin.gz"
        if not endpoint.exists():
            raise ValueError(f"Truncated episode requires final actor observation: {endpoint}")
        records = list(nav_demos.iter_chunks(endpoint, 2))
        if len(records) != 1 or len(records[0]) != 1 or records[0]["time_ms"][0] != last["next_time_ms"]:
            raise ValueError(f"Invalid bootstrap observation: {endpoint}")
        bootstrap = encode_features(nav_demos.learner_input(records[0]), layout)
        bootstrap["priv"] = privileged(last["critic_after"])[None]
        if not last["critic_after"].get("valid", True):
            bootstrap = None
    return arrays, bootstrap, dict(decisions=count, valid_decisions=int(arrays["valid"].sum()),
               reward_sums=dict(sums), reward_absolute_sums=dict(absolute),
               turn_count=turn_count, blocked_count=blocked, jump_presses=jump_presses,
               climb_presses=climb_presses, success=bool(metadata["success"]))


@torch.inference_mode()
def replay_episode(episode, bootstrap, actor, critic, reference, args):
    count = len(episode["actions"])
    starts = np.arange(0, count, args.segment)
    burn_starts = np.maximum(0, starts - args.burn_in)
    boundaries = sorted(set([0, count, *burn_starts.tolist(), *range(0, count, args.segment)]))
    actor_states, critic_states = {}, {}
    actor_h = critic_h = reference_h = None
    values, reference_logits = np.empty(count, np.float32), np.empty((count, sum(CLASSES)), np.float32)
    error, logit_error = 0., 0.
    for start, stop in zip(boundaries, boundaries[1:]):
        actor_states[start] = (np.zeros(actor.hidden_size, np.float32) if actor_h is None else actor_h[0, 0].cpu().numpy().copy())
        critic_states[start] = (np.zeros(critic.hidden_size, np.float32) if critic_h is None else critic_h[0, 0].cpu().numpy().copy())
        features = tensor_features(episode, start, stop, args.device)
        logits, actor_h = actor(**features, hidden=actor_h)
        reference_heads, reference_h = reference(**features, hidden=reference_h)
        priv = torch.from_numpy(episode["priv"][None, start:stop]).to(args.device)
        value, critic_h = critic(**features, priv=priv, hidden=critic_h)
        actions = torch.from_numpy(episode["actions"][None, start:stop]).to(args.device)
        logp, _, _ = policy_terms(logits, actions)
        flat = torch.cat([logits[name] for name in HEADS], -1)[0].cpu().numpy()
        logit_error = max(logit_error, float(np.abs(flat - episode["recorded_logits"][start:stop]).max()))
        error = max(error, float(np.abs(logp[0].cpu().numpy() - episode["old_logp"][start:stop]).max()))
        if error > args.replay_tolerance or logit_error > args.replay_tolerance:
            raise ValueError(f"Collection policy replay mismatch: logp={error:.8g}, logits={logit_error:.8g}; tolerance={args.replay_tolerance:g}")
        values[start:stop] = value[0].cpu().numpy()
        reference_logits[start:stop] = torch.cat([reference_heads[name] for name in HEADS], -1)[0].cpu().numpy()
    final_value = 0.
    if bootstrap is not None:
        features = {key: torch.from_numpy(value[None]).to(args.device) for key, value in bootstrap.items()}
        final_value = float(critic(**features, hidden=critic_h)[0][0, 0])
    episode["advantage"], episode["returns"] = generalized_advantage(
        episode["rewards"], values, episode["valid"], episode["terminated"], final_value, args.gamma, args.gae_lambda)
    episode.update(old_values=values, reference_logits=reference_logits, starts=starts,
                   actor_hidden=np.stack([actor_states[int(start)] for start in burn_starts]),
                   critic_hidden=np.stack([critic_states[int(start)] for start in burn_starts]))
    del episode["recorded_logits"]
    return error, logit_error


def load_cache(path):
    with np.load(path, allow_pickle=False) as stream:
        return {name: stream[name] for name in stream.files}


def batch_arrays(episode, windows, args, burn=False):
    """All windows in one batch have equal burn-in; no artificial padded GRU steps."""
    starts = episode["starts"][windows]
    length = min(args.burn_in, int(starts[0])) if burn else args.segment
    if burn:
        starts = starts - length
    arrays = {}
    for name in ("near", "far", "vector", "priv", "actions", "old_logp", "advantage", "returns", "old_values", "reference_logits", "valid"):
        source = episode[name]
        data = np.zeros((len(starts), length, *source.shape[1:]), dtype=source.dtype)
        for row, start in enumerate(starts):
            stop = min(int(start) + length, len(source))
            data[row, :stop-int(start)] = source[int(start):stop]
        arrays[name] = torch.from_numpy(data).to(args.device)
    return arrays


def features_of(batch):
    return {name: batch[name] for name in ("near", "far", "vector")}


def train_episode(episode, actor, critic, actor_optimizer, critic_optimizer, args, rng, advantage_mean, advantage_std):
    # Group only the episode-start window separately, since its burn-in is zero.
    grouped = {}
    for window, start in enumerate(episode["starts"]):
        grouped.setdefault(min(args.burn_in, int(start)), []).append(window)
    stats, steps = Counter(), 0
    actor.train(not args.critic_only)
    critic.train()
    for burn_length, windows in grouped.items():
        rng.shuffle(windows)
        for offset in range(0, len(windows), args.batch):
            selected = windows[offset:offset+args.batch]
            batch = batch_arrays(episode, selected, args)
            mask = batch["valid"]
            count = int(mask.sum())
            if not count:
                continue
            actor_h = torch.from_numpy(episode["actor_hidden"][selected][None]).to(args.device)
            critic_h = torch.from_numpy(episode["critic_hidden"][selected][None]).to(args.device)
            if burn_length:
                burn = batch_arrays(episode, selected, args, burn=True)
                with torch.no_grad():
                    if not args.critic_only:
                        _, actor_h = actor(**features_of(burn), hidden=actor_h)
                    _, critic_h = critic(**features_of(burn), priv=burn["priv"], hidden=critic_h)
            critic_optimizer.zero_grad(set_to_none=True)
            values, _ = critic(**features_of(batch), priv=batch["priv"], hidden=critic_h.detach())
            value_loss = (values[mask] - batch["returns"][mask]).square().mean() * .5
            (args.value_coef * value_loss).backward()
            nn.utils.clip_grad_norm_(critic.parameters(), args.max_grad_norm, error_if_nonfinite=True)
            critic_optimizer.step()
            stats["value_loss"] += float(value_loss.detach()) * count
            if not args.critic_only:
                actor_optimizer.zero_grad(set_to_none=True)
                logits, _ = actor(**features_of(batch), hidden=actor_h.detach())
                logp, entropy, kl = policy_terms(logits, batch["actions"], batch["reference_logits"])
                advantage = (batch["advantage"] - advantage_mean) / max(advantage_std, 1e-6)
                losses, ratio = clipped_policy_loss(logp, batch["old_logp"], advantage, args.clip)
                entropy_total = sum(entropy.values())
                policy_loss = losses[mask].mean()
                loss = policy_loss + args.kl_coef * kl[mask].mean() - args.entropy_coef * entropy_total[mask].mean()
                loss.backward()
                nn.utils.clip_grad_norm_(actor.parameters(), args.max_grad_norm, error_if_nonfinite=True)
                actor_optimizer.step()
                stats["policy_loss"] += float(policy_loss.detach()) * count
                with torch.no_grad():
                    logratio = logp - batch["old_logp"]
                    stats["approx_kl_old"] += float(((ratio - 1) - logratio)[mask].sum())
                    stats["clip_fraction"] += float(((ratio - 1).abs() > args.clip)[mask].sum())
            steps += count
    return stats, steps


@torch.inference_mode()
def post_update_statistics(paths, actor, args):
    """Exact full-episode replay after updates, independent of PPO window states."""
    actor.eval()
    sums, count = Counter(), 0
    for path in paths:
        episode = load_cache(path)
        hidden = None
        for start in range(0, len(episode["actions"]), args.segment):
            stop = min(start + args.segment, len(episode["actions"]))
            features = tensor_features(episode, start, stop, args.device)
            logits, hidden = actor(**features, hidden=hidden)
            actions = torch.from_numpy(episode["actions"][None, start:stop]).to(args.device)
            reference = torch.from_numpy(episode["reference_logits"][None, start:stop]).to(args.device)
            mask = torch.from_numpy(episode["valid"][None, start:stop]).to(args.device)
            logp, entropy, kl = policy_terms(logits, actions, reference)
            sums["kl_reference"] += float(kl[mask].sum())
            old = torch.from_numpy(episode["old_logp"][None, start:stop]).to(args.device)
            logratio = (logp - old).clamp(-20, 20)
            sums["post_approx_kl_old"] += float((logratio.exp() - 1 - logratio)[mask].sum())
            for name, value in entropy.items():
                sums[f"entropy_{name}"] += float(value[mask].sum())
            count += int(mask.sum())
    return {"kl_reference": sums["kl_reference"] / max(count, 1),
            "post_approx_kl_old": sums["post_approx_kl_old"] / max(count, 1),
            "entropy_by_head": {name: sums[f"entropy_{name}"] / max(count, 1) for name in HEADS}}


def update(args):
    if sys.platform != "linux":
        raise ValueError("Navigation model training runs on Ryzen/Linux under gq")
    if args.segment < 1 or not 0 <= args.burn_in <= args.segment or args.batch < 1 or args.epochs < 1:
        raise ValueError("Invalid segment/burn-in/batch/epochs")
    for name in ("source", "output", "reference"):
        path = getattr(args, name)
        if not path.is_absolute():
            raise ValueError(f"--{name} must be an absolute path")
        setattr(args, name, path.resolve())
    if args.resume is not None and not args.resume.is_absolute():
        raise ValueError("--resume must be an absolute path")
    args.device = str(torch.device(args.device))
    torch.set_num_threads(args.threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    if args.device.startswith("cuda"):
        total = torch.cuda.get_device_properties(args.device).total_memory
        torch.cuda.set_per_process_memory_fraction(min(1., args.max_gpu_gb * 1024**3 / total), args.device)
        torch.cuda.reset_peak_memory_stats(args.device)
    args.output.mkdir(parents=True, exist_ok=True)
    cache = args.output / "cache"
    cache.mkdir(exist_ok=True)
    actor, preprocessing = load_actor(args.reference, args.device)
    reference, _ = load_actor(args.reference, args.device)
    reference.eval().requires_grad_(False)
    critic = NavCritic(actor).to(args.device)
    actor_optimizer = torch.optim.Adam(actor.parameters(), lr=args.lr)
    critic_optimizer = torch.optim.Adam(critic.parameters(), lr=args.critic_lr)
    reference_hash = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    if args.resume:
        state = torch.load(args.resume, map_location=args.device, weights_only=False)
        if state["reference_sha256"] != reference_hash:
            raise ValueError("Resume checkpoint belongs to a different frozen reference policy")
        actor.load_state_dict(state["actor"])
        critic.load_state_dict(state["critic"])
        actor_optimizer.load_state_dict(state["actor_optimizer"])
        critic_optimizer.load_state_dict(state["critic_optimizer"])
        # Explicit CLI learning rates remain authoritative after a resume.
        for group in actor_optimizer.param_groups:
            group["lr"] = args.lr
        for group in critic_optimizer.param_groups:
            group["lr"] = args.critic_lr
    actor.eval()
    critic.eval()
    paths = sorted(path.parent for path in args.source.rglob("samples.jsonl"))
    if not paths:
        raise ValueError(f"No population recordings in {args.source}")
    started, total, weighted, absolute, prepared = time.monotonic(), Counter(), Counter(), Counter(), []
    advantage_sum = advantage_square = replay_error = replay_logits_error = 0.
    for number, directory in enumerate(paths):
        episode, bootstrap, summary = read_episode(directory, REWARD_WEIGHTS)
        error, logit_error = replay_episode(episode, bootstrap, actor, critic, reference, args)
        replay_error, replay_logits_error = max(replay_error, error), max(replay_logits_error, logit_error)
        valid = episode["valid"]
        advantage_sum += float(episode["advantage"][valid].astype(np.float64).sum())
        advantage_square += float(np.square(episode["advantage"][valid].astype(np.float64)).sum())
        for name in ("decisions", "valid_decisions", "turn_count", "blocked_count", "jump_presses", "climb_presses", "success"):
            total[name] += summary[name]
        weighted.update(summary["reward_sums"])
        absolute.update(summary["reward_absolute_sums"])
        destination = cache / f"episode-{number:06d}.npz"
        np.savez_compressed(destination, **episode)
        prepared.append(destination)
        del episode, bootstrap
    if not total["valid_decisions"]:
        raise ValueError("No valid Detour intervals for PPO")
    advantage_mean = advantage_sum / total["valid_decisions"]
    advantage_std = max(0., advantage_square / total["valid_decisions"] - advantage_mean**2)**.5
    actor.requires_grad_(not args.critic_only)
    aggregate, trained_steps = Counter(), 0
    rng = random.Random(args.seed + args.iteration)
    for _ in range(args.epochs):
        order = prepared.copy()
        rng.shuffle(order)
        for path in order:
            episode = load_cache(path)
            measures, steps = train_episode(episode, actor, critic, actor_optimizer, critic_optimizer,
                                             args, rng, advantage_mean, advantage_std)
            aggregate.update(measures)
            trained_steps += steps
            del episode
    post = post_update_statistics(prepared, actor, args)
    stats = dict(iteration=args.iteration, episodes=len(paths), decisions=total["decisions"],
                 valid_decisions=total["valid_decisions"], invalid_path_fraction=1-total["valid_decisions"]/total["decisions"],
                 mean_reward=sum(weighted.values())/total["valid_decisions"],
                 reward_components={key: value/total["valid_decisions"] for key, value in weighted.items()},
                 reward_component_abs_fraction={key: value/max(sum(absolute.values()), 1e-12) for key, value in absolute.items()},
                 collected_success_rate=total["success"]/len(paths),
                 turn_in_place_fraction=total["turn_count"]/total["decisions"],
                 blocked_fraction=total["blocked_count"]/total["decisions"],
                 jump_presses=total["jump_presses"], climb_presses=total["climb_presses"],
                 replay_log_prob_max_error=replay_error, replay_logits_max_error=replay_logits_error,
                 actor_updated=not args.critic_only, kl_coef=args.kl_coef, entropy_coef=args.entropy_coef,
                 reward_weights=REWARD_WEIGHTS, advantage_mean=advantage_mean, advantage_std=advantage_std,
                 timeout_bootstrap="final_observation_and_recurrent_critic_state", **post,
                 **{name: aggregate[name]/max(trained_steps, 1) for name in ("policy_loss", "value_loss", "approx_kl_old", "clip_fraction")},
                 seconds=time.monotonic()-started,
                 peak_cuda_gb=torch.cuda.max_memory_allocated(args.device)/1024**3 if args.device.startswith("cuda") else 0.)
    state = dict(version=1, config=dict(vector_size=actor.vector_size, hidden_size=actor.hidden_size),
                 actor=actor.state_dict(), critic=critic.state_dict(), actor_optimizer=actor_optimizer.state_dict(),
                 critic_optimizer=critic_optimizer.state_dict(), reference_sha256=reference_hash,
                 iteration=args.iteration, stats=stats, args={key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
                 torch_rng_state=torch.get_rng_state(), numpy_rng_state=np.random.get_state(), python_rng_state=random.getstate())
    torch.save(state, args.output / "last.pt")
    export_weights(actor, args.output / "policy.navbc", preprocessing)
    (args.output / "stats.json").write_text(json.dumps(stats, indent=2, allow_nan=False) + "\n")
    print(json.dumps(stats, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("update")
    for name in ("source", "output", "reference"):
        command.add_argument(f"--{name}", type=Path, required=True)
    command.add_argument("--resume", type=Path)
    command.add_argument("--iteration", type=int, required=True)
    command.add_argument("--critic-only", action="store_true")
    command.add_argument("--device", default="cuda")
    command.add_argument("--threads", type=int, default=4)
    command.add_argument("--max-gpu-gb", type=float, default=3.8)
    command.add_argument("--epochs", type=int, default=2)
    command.add_argument("--batch", type=int, default=8)
    command.add_argument("--segment", type=int, default=32)
    command.add_argument("--burn-in", type=int, default=16)
    command.add_argument("--lr", type=float, default=3e-5)
    command.add_argument("--critic-lr", type=float, default=3e-4)
    command.add_argument("--kl-coef", type=float, default=.1)
    command.add_argument("--entropy-coef", type=float, default=.003)
    command.add_argument("--value-coef", type=float, default=.5)
    command.add_argument("--gamma", type=float, default=.995)
    command.add_argument("--gae-lambda", type=float, default=.95)
    command.add_argument("--clip", type=float, default=.2)
    command.add_argument("--max-grad-norm", type=float, default=.5)
    command.add_argument("--replay-tolerance", type=float, default=1e-4)
    command.add_argument("--seed", type=int, default=2187)
    args = parser.parse_args()
    update(args)


if __name__ == "__main__":
    main()
