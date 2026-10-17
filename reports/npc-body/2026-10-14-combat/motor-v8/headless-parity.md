# Headless mechanics parity

Status: **matched**; profile `full`, comparison `headless_schedule_invariance`.

Same-source rendered/headless mechanics with per-tick CPU root motion and collision results, in addition to archived-reference comparison.

| Seed | Status | Reference / headless duration, ms | Ordered checks | Differences |
|---|---|---:|---:|---:|
| 17 | matched | 14133 / 14133 | 294 | 0 |
| 29 | matched | 15600 / 15600 | 364 | 0 |
| 43 | matched | 23567 / 23567 | 419 | 0 |

Predeclared tolerances: `event_time_ms` = 34, `position_cm` = 2.0, `yaw_deg` = 0.5, `animation_scalar` = 1e-06.

- Seed 17: unexercised reference guards: def_par_window.
- Seed 29: unexercised reference guards: def_par_window.
- Seed 43: unexercised reference guards: def_par_window.

The JSON preserves the first 25 differences per seed and the total count. Raw traces remain the source of evidence.

This comparison checks headless wall-time invariance. It does not establish rendered/headless collision parity or alone close stage 2.

- Time is relative to episode_start. Actor lifetime IDs are mapped to start actor/target roles; absolute game clocks and IDs are not expected to match.
- 34 ms is one fixed 30 Hz step; 2 cm and 0.5 degrees bound trajectory drift. This does not permit different choices, damage or terminal outcomes.
- Animation windows are engine guard predicates. Censored intervals remain censored; an unexercised guard is reported as unexercised.
- Native source nondeterminism may make a historical trace diverge. Report the first ordered mismatch, reproduce it with the archived binary/same-source render, and isolate the cause; never loosen limits after a failed measurement.
