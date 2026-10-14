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

Commands: `build-dataset`, `verify`, `overfit`, `train`, `evaluate`, `serve`.
The CLI parser is lightweight; model commands enforce Linux execution and explicit CPU/CUDA selection.

`serve` reads bounded JSONL requests and returns matching IDs. Commands include `reset`,
`observe`, `submit`, `poll`, `status`, `end_episode`, and `close`. Reset requires native
`session_epoch`, `world_epoch`, and `initial_game_ms`. A service instance uses one immutable
checkpoint and train normalizer; hidden state is isolated by NPC identity and generation.

Training accepts the validated periodic-only dataset view. Native exact decision snapshots
are causal label evidence, never extra recurrent inputs. Scripted expert records require
registered provenance and real physical outcomes. Test/OOD never select checkpoints.
