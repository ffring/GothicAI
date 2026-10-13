# Native melee baseline

Status: **complete**. Scenario `melee-v1`, trace schema v1.

| Seed | Duration, ms | Steps | Outcome |
|---|---:|---:|---|
| 17 | 14133 | 424 | actor_down |
| 29 | 17234 | 517 | target_down |
| 43 | 9800 | 294 | target_down |

| Seed / actor ID | Instruction queues / with consume | Animation hooks accepted / pose changed | HP loss | Blocks | Snapshot p95/max, ms |
|---|---:|---:|---:|---:|---:|
| 17 / 3 `BDT_1003_BANDIT_M` | 11 / 11 | 33 / 32 | 300 | 0 | 66 / 66 |
| 17 / 4 `PAL_255_RITTER` | 33 / 33 | 34 / 33 | 0 | 0 | 66 / 66 |
| 29 / 5 `BDT_1003_BANDIT_M` | 28 / 28 | 37 / 36 | 0 | 0 | 66 / 66 |
| 29 / 6 `PAL_255_RITTER` | 21 / 20 | 36 / 35 | 300 | 2 | 66 / 66 |
| 43 / 7 `BDT_1003_BANDIT_M` | 16 / 16 | 24 / 23 | 0 | 0 | 66 / 66 |
| 43 / 8 `PAL_255_RITTER` | 14 / 14 | 25 / 24 | 300 | 0 | 66 / 66 |

| Seed / actor ID | New instruction queue interval p95, ms | Consume interval p95, ms | Animation hook interval p95, ms | Choice association to first hook / consume p95, ms |
|---|---:|---:|---:|---:|
| 17 / 3 | 2433 | 2500 | 1167 | 367 / 433 |
| 17 / 4 | 1933 | 1167 | 1133 | 34 / 133 |
| 29 / 5 | 1934 | 1167 | 1133 | 34 / 233 |
| 29 / 6 | 3000 | 3067 | 866 | 334 / 433 |
| 43 / 7 | 1933 | 1167 | 1133 | 34 / 234 |
| 43 / 8 | 1133 | 1134 | 634 | 334 / 334 |

| Seed / actor ID / engine guard | Complete intervals | Min / median / p95 / max, ms | Censored left/right |
|---|---:|---|---:|
| 17 / 3 / `prehit` | 2 | 66 / 216.5 / 367 / 367 | 0/0 |
| 17 / 3 / `defence` | 6 | 466 / 466.5 / 467 / 467 | 0/0 |
| 17 / 3 / `jumpback` | 6 | 466 / 466.5 / 467 / 467 | 0/0 |
| 17 / 3 / `attack_anim` | 2 | 66 / 216.5 / 367 / 367 | 0/0 |
| 17 / 3 / `def_window` | 6 | 466 / 466.5 / 467 / 467 | 0/0 |
| 17 / 3 / `def_par_window` | 0 | None / None / None / None | 0/0 |
| 17 / 4 / `prehit` | 10 | 100 / 1133.0 / 1134 / 1134 | 0/1 |
| 17 / 4 / `defence` | 0 | None / None / None / None | 0/0 |
| 17 / 4 / `jumpback` | 0 | None / None / None / None | 0/0 |
| 17 / 4 / `attack_anim` | 6 | 1133 / 1900.0 / 1900 / 1900 | 0/1 |
| 17 / 4 / `def_window` | 10 | 433 / 766.0 / 767 / 767 | 0/0 |
| 17 / 4 / `def_par_window` | 0 | None / None / None / None | 0/0 |
| 29 / 5 / `prehit` | 11 | 100 / 1133 / 1134 / 1134 | 0/1 |
| 29 / 5 / `defence` | 0 | None / None / None / None | 0/0 |
| 29 / 5 / `jumpback` | 0 | None / None / None / None | 0/0 |
| 29 / 5 / `attack_anim` | 7 | 1133 / 1900 / 1900 / 1900 | 0/1 |
| 29 / 5 / `def_window` | 11 | 433 / 766 / 767 / 767 | 0/0 |
| 29 / 5 / `def_par_window` | 0 | None / None / None / None | 0/0 |
| 29 / 6 / `prehit` | 1 | 100 / 100 / 100 / 100 | 0/0 |
| 29 / 6 / `defence` | 7 | 466 / 467 / 467 / 467 | 0/0 |
| 29 / 6 / `jumpback` | 7 | 466 / 467 / 467 / 467 | 0/0 |
| 29 / 6 / `attack_anim` | 1 | 100 / 100 / 100 / 100 | 0/0 |
| 29 / 6 / `def_window` | 7 | 466 / 467 / 467 / 467 | 0/0 |
| 29 / 6 / `def_par_window` | 0 | None / None / None / None | 0/0 |
| 43 / 7 / `prehit` | 6 | 100 / 616.5 / 1134 / 1134 | 0/1 |
| 43 / 7 / `defence` | 0 | None / None / None / None | 0/0 |
| 43 / 7 / `jumpback` | 0 | None / None / None / None | 0/0 |
| 43 / 7 / `attack_anim` | 3 | 1133 / 1900 / 1900 / 1900 | 0/1 |
| 43 / 7 / `def_window` | 6 | 433 / 599.5 / 767 / 767 | 0/0 |
| 43 / 7 / `def_par_window` | 0 | None / None / None / None | 0/0 |
| 43 / 8 / `prehit` | 2 | 400 / 400.0 / 400 / 400 | 0/0 |
| 43 / 8 / `defence` | 4 | 466 / 466.5 / 467 / 467 | 0/0 |
| 43 / 8 / `jumpback` | 4 | 466 / 466.5 / 467 / 467 | 0/0 |
| 43 / 8 / `attack_anim` | 2 | 400 / 400.0 / 400 / 400 | 0/0 |
| 43 / 8 / `def_window` | 6 | 33 / 466.0 / 467 / 467 | 0/0 |
| 43 / 8 / `def_par_window` | 0 | None / None / None / None | 0/0 |

The JSON report preserves setup, terminal fields, event counts, consumed primitives and metadata.

- native_consume means queue consumption; accepted/pose_changed are reported separately and do not prove a hit
- native_attempt counts animation hooks (set_anim/do_attack), including weapon draw, idle and hit reactions; it is not restricted to new fight decisions
- choice_to_first_attempt_ms is elapsed game time to the first animation hook sharing the current/last materialized choice_id; this association is not policy latency or proof that the choice caused the animation
- animation hooks may carry primitive 0 or a previous queue choice; raw hook, primitive and pose fields remain in the trace
- guard durations come from engine booleans at the recorded sample phase; timestamps are quantized by simulation steps
- left/right-censored guard intervals are excluded from duration quantiles
- timeout is truncation, never a victory; missing terminal is infrastructure interruption
- trace lifetime IDs, not script symbols, distinguish participants and episodes
