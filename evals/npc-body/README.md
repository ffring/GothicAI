# Native melee reference, stage 1

`melee-v1.json` versions the setup of `scripts/bench/body-melee.bench`: three seeds (17, 29, 43), two
native human NPCs, each with a sword, leather armour, 300 HP, 60 strength/dexterity, 60% one-hand
chance, master one-hand animations and 30 edge/blunt protection. Each duel stops when either NPC is
down or after 60 seconds of game time. A timeout is truncation, not a victory. The player stands about
12 m away as an immortal observer. Both combatants remain mortal and use native `ZS_ATTACK`/fight AI.

The `bodyduel` bench command owns direct NPC pointers and creates fresh participants for every seed.
It removes the previous pair, unequips inherited gear before clearing the source inventory, equips the configured sword/armour,
sets the stats after equipment callbacks, checks ground/equipment/mortality, and seeds construction
and native combat separately. It fails the scenario on missing script symbols or failed setup. The
bandit and paladin retain hostile guilds. Trace IDs refer to NPC object lifetimes; repeated script
symbols or reused memory addresses do not identify a participant. Melee trace events involving the
player or another third participant make the report incomplete.

## Run on dev-ubuntu

From the repository root, use `scripts/body-baseline-remote.sh` for the user-local server build,
reference run and collection. Keep one game process at a time. The launcher forces the Linux
lavapipe CPU Vulkan renderer under Xvfb. This is **stage 1 reference**, and does not satisfy the stage 2 requirement for a
simulation executable without a graphics device. Mac is used for editing and short parser checks;
the baseline game and all subsequent simulations/training run on the server.

Full setup/run/collection from the Mac (only SSH/rsync and lightweight tooling run locally):

```sh
GOTHIC_DIR='/Volumes/shared/projects/Gothic II' \
BASELINE_SAVE="$PWD/save_slot_1.sav" \
  scripts/body-baseline-remote.sh all melee-reference
```

Use paths to your own assets/reference save if they differ. The launcher stores its own source,
toolchain, assets, binary and runs under `~/gothic-ai/body-baseline/` on `dev-ubuntu`. It uses an
ephemeral user systemd unit with CPU/RAM/time limits; it does not restart Docker or production units.
The scenario records `novideo`; it still uses the current graphical engine. No model service, API,
ASR/TTS process, artificially applied hit, held player attack or learning loop is involved.

After setup, a new run and its local collection can be requested separately:

```sh
scripts/body-baseline-remote.sh run melee-reference-repeat
scripts/body-baseline-remote.sh collect melee-reference-repeat
```

Run names identify immutable output directories; choose a new name for each attempt. `sync`,
`toolchain` and `build` are available as separate actions. The game starts from a copied save and
silent `Gothic.ini`; the Linux launch explicitly selects Gothic II (`-g2`) rather than relying on
asset auto detection. The launcher refuses to start while another game/runner is active.

## Generate a report

`collect` copies raw output to `data/npc-body/reference/<run-name>/` in this repository. The manifest
is `manifest.json`; `resources.txt`, `launcher.log`, `game.log` and `events.jsonl` provide the measured
run evidence. Generate the compact committed report separately:

The pre-headless engine binary stays on the server in `<run>/reference/Gothic2Notr`; source is
archived as `source.tar.gz` with hashes in the manifest. `collect` omits the binary and private save.
The launcher executes the copied binary, so later builds cannot replace the running reference.

```sh
python3 scripts/body_baseline_report.py data/npc-body/reference/melee-reference \
  --manifest data/npc-body/reference/melee-reference/manifest.json \
  --output-dir reports/npc-body/melee-reference
```

Outputs are `body-baseline.md` and `body-baseline.json`. Exit code 0 means the expected seeded setup,
terminals, native choice/consume/attempt evidence, complete guard snapshots with the configured
phase/cadence, and measured damage are present and a manifest was supplied. Exit code 2 means
missing/invalid evidence. A crash or missing terminal never becomes a duel timeout. The report flags
duplicate JSON keys, sequence/time regressions, schema mismatch, setup mismatch, a third combatant,
and non-melee or unknown damage kinds.
It does not assume a requested or consumed animation landed a blow.

The manifest should preserve measured provenance: engine revision/dirty state and binary hash,
scenario/config hash, asset/script manifest and save hashes, host/toolchain/settings, renderer/device,
wall duration, CPU/RSS, GPU renderer allocation and relevant service RSS/availability before/after.
Unavailable measurements must be marked unavailable. The reporter copies this JSON without adding
invented build hashes, resource values or throughput. Raw trace files stay in the ignored run
directory; commit the compact metadata/report and hashes, not private game assets or saves.

## Interpretation and readiness

- `native_choice` identifies a generated fight instruction; `native_consume` records queue
  consumption. `native_attempt` records animation hooks from `set_anim` and `do_attack`, including
  weapon draw, idle and hit reactions while a target is present. Its `accepted` and `pose_changed`
  fields describe animation/pose behavior. These are separate events, and none alone proves damage.
- Animation hooks carry the current or last materialized `choice_id` and queued `primitive`, which
  may be 0. The reported hook count and interval include these animation calls rather than only
  newly selected fight actions. `choice_to_first_attempt_ms` measures elapsed game time to the first
  hook sharing a queue choice ID; it is an association, not policy response latency or proof that
  the choice caused that animation. Read the raw `hook`, `primitive`, requested animation and pose
  fields when interpreting a particular call. Reporter field names remain stable for schema v1.
- Snapshots come from `npc_pre_tick_animation_tags`. Guard booleans and animation layouts are
  privileged telemetry for analysis, not a validated future policy observation stream.
- Guard intervals are consecutive observed engine flags: `prehit`, `defence`, `jumpback`,
  `attack_anim`, `def_window`, `def_par_window`. They are not guessed windup/hit/recovery labels.
  The fixed 30 Hz substep is 33/34 ms; the 50 ms periodic snapshot target is quantized to up to
  67 ms, with additional snapshots on flag/layer edges. Subframe timing remains unknown.
- Duration quantiles exclude intervals already true at the first sample or still true at the
  terminal. Their censored counts remain visible. Animation frame metadata is preserved separately.
- Compare outcomes, HP loss, block/jump-back results, native action distribution, attempt/consume
  behavior and guard timing across these seeds. Three seeds establish an instrumented reference,
  not statistical superiority, training throughput or determinism. Source save/world time remains
  part of provenance; repeated seeds do not promise bitwise-identical traces.

Stage 1 is ready when the contract is checked against these measured native events, the versioned
scenario produces valid mortal melee episodes on `dev-ubuntu`, and the committed report records
timing and provenance without hiding missing evidence. The next gate remains a true headless
runner with parity against this same reference, including animation tags, damage and native queue
consumption.

Parser checks (run once by the coordinating agent, without a game):

```sh
cd server
uv run pytest ../scripts/tests/test_body_baseline_report.py
```


## Stage 2 headless runner

The real CPU runner is documented in [npc-body-headless-runner.md](../../docs/npc-body-headless-runner.md).
It runs on dev-ubuntu without a window, graphics backend or audio device:

```sh
scripts/body-headless-remote.sh preflight
scripts/body-headless-remote.sh sync
scripts/body-headless-remote.sh build
scripts/body-headless-remote.sh run stage2-validation
scripts/body-headless-remote.sh collect stage2-validation
```

The suite performs an actual same-source CPU-rendered control run, then headless lockstep,
delayed lockstep, realtime, archived animation-branch replay, senses, 100 resets and the IPC probe.
Only one game/runner is active at a time. The archived reference remains unchanged. See
[the comparison contract](headless-parity.md) for isolated RNG, exact branch consumption and the
scope of historical replay. `replay <fresh-name>` runs the bounded archival check alone; it does
not establish full stage 2 readiness. A normal historical RNG divergence is retained as diagnostic
output; all other required gates must pass for the full suite to exit 0.

The complete `stage2-v3` run passed on 2026-10-13:
[results and limits](../../reports/npc-body/2026-10-13-headless/headless-report.md).
After collection, generate a compact report without running a game:

```sh
python3 scripts/body_headless_report.py data/npc-body/headless/stage2-v3 \
  --output-dir reports/npc-body/2026-10-13-headless
```

## Stage 3 numeric observations

[body-observe-v1.json](body-observe-v1.json) fixes cadence and overhead budgets for a passive
20 Hz stream from four native NPCs. [Schema/runtime](../../docs/npc-body-observations.md) documents
post-simulation phase, visibility, runtime IDs, advisory availability and bounded queue semantics.

```sh
scripts/body-observe-remote.sh preflight
scripts/body-observe-remote.sh sync
scripts/body-observe-remote.sh build
scripts/body-observe-remote.sh run stage3-validation
scripts/body-observe-remote.sh collect stage3-validation
```

The full suite runs three on/off performance pairs, bounded overflow, a same-phase privacy oracle,
strict native melee parity and ten minutes of realtime. Its reporter validates raw data on dev-ubuntu,
including native combat in every minute. The compact report/provenance and small privacy evidence
are collected locally; long numeric journals remain on the server. `smoke` checks infrastructure only.
No body control or model inference is enabled by subscribing.
