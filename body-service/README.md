# Gothic body service

Numerical GRU-256 and an eight-observation MLP baseline for native NPC combat primitives.
The package provides dataset projection, BC training, evaluation and a bounded JSONL runtime.

All model imports, forwards and training run on **dev-ubuntu**. Use the repository's
[stage 7 workflow](../docs/npc-body-bc.md) and [remote launcher](../scripts/body-bc-remote.sh).
It creates a separate uv environment and freezes package/helper/config bytes per run.
Raw game data and checkpoints stay on the server.

```sh
PYTHONPATH=body-service/src:scripts python -m gothic_body.cli --help
```

Commands: `build-dataset`, `verify`, `overfit`, `train`, `evaluate`, `serve`, `serve-net`.
The CLI parser is lightweight; model commands enforce Linux execution and explicit CPU/CUDA selection.

`serve` reads bounded JSONL requests and returns matching IDs. Commands include `reset`,
`observe`, `submit`, `poll`, `status`, `end_episode`, and `close`. Reset requires native
`session_epoch`, `world_epoch`, and `initial_game_ms`. A service instance uses one immutable
checkpoint and train normalizer; hidden state is isolated by NPC identity and generation.

Training accepts the validated periodic-only dataset view. Native exact decision snapshots
are causal label evidence, never extra recurrent inputs. Scripted expert records require
registered provenance and real physical outcomes. Test/OOD never select checkpoints.

## TCP service and deployment

On dev-ubuntu, `gothic-body serve-net --checkpoint PATH --host "$BODY_HOST" --port 47835
--device cpu --inference-cpu-threads 2` serves JSON lines over TCP. Each connection has an
independent runtime episode. Requests carry `id` and `cmd`: `reset` requires
`session_epoch`, `world_epoch`, and `initial_game_ms`; `step` accepts `observation` (one
periodic bridge row) and `now_game_ms`. Other commands are `status`, `end_episode`, and
`close`. The service assigns actor-local stream sequences, updates memory on every row,
and arbitrates eligible decisions. Each control generation starts its stream at 1;
stale generations cannot reset the current stream. Responses preserve request order and IDs and include
the process result, `action_id` (or `null`), and an explicit fallback reason when needed.
The `process` receipt omits only local `inference_ms` and `backend_timing` diagnostics
so network and in-process replay responses can be compared exactly.
Each request is bounded by the same `MAX_REQUEST_BYTES` as `serve`.

`reset` with `include_capabilities: true` advertises `memory_only_step: true`.
An optional `step` field `memory_only: true`
updates recurrent memory without an action, even when the row permits a decision.
`serve-net` defaults to `--allow-stale-memory-only`, accepting these rows beyond the
100 game-ms action deadline; `--no-allow-stale-memory-only` restores age checks for all rows.
The runtime's default age check and the arena/collector behavior are unchanged.

Deploy into a separate directory so the training environment remains independent.
The editable package needs `scripts/` beside `body-service/` for its feature packer:

```sh
ssh dev-ubuntu 'mkdir -p ~/gothic-ai/body-net'
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  body-service scripts dev-ubuntu:gothic-ai/body-net/
ssh dev-ubuntu '~/gothic-ai/body-net/body-service/deploy/install.sh'
```

The installer creates `~/gothic-ai/body-net/.venv` with CPU Torch, installs the user unit,
and reloads systemd without starting or enabling it. Set `BODY_HOST` in
`~/gothic-ai/gothic-ai.env` to the server's listen address. `BODY_PORT` defaults to 47835;
`BODY_CHECKPOINT` defaults to `~/gothic-ai/body/models/working/best.pt` (use an absolute
path for overrides). The unit uses CPU inference with two threads. After review, start it
with `systemctl --user enable --now gothic-body.service`; inspect logs with
`journalctl --user -u gothic-body -f`.

Create a reference on dev-ubuntu, from `~/gothic-ai/body-net`, then copy it and the
whole recorded episode directory to the Mac (the checker needs reset metadata beside
`observations.jsonl`):

```sh
# dev-ubuntu; replace /path/to/episode with a collector episode kept with --keep-journals
.venv/bin/python scripts/body_policy_net_check.py runtime /path/to/episode/observations.jsonl \
  --checkpoint ~/gothic-ai/body/models/working/best.pt --output /tmp/body-reference.jsonl
# Mac, from the repository; BODY_HOST is the service's listen address
scp -r dev-ubuntu:/path/to/episode /tmp/body-net-episode
scp dev-ubuntu:/tmp/body-reference.jsonl /tmp/body-reference.jsonl
python3 scripts/body_policy_net_check.py net /tmp/body-net-episode/observations.jsonl \
  --host "$BODY_HOST" --port 47835 --expect /tmp/body-reference.jsonl
```

The Mac command uses only stdlib and prints steps, decisions, fallbacks and round-trip
p50/p95/max; `--expect` checks every response field and returns nonzero on a mismatch.
