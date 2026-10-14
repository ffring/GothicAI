# Safe original / corrective final v13 numeric comparison

This numeric reference changes only one mutex around the shared Bullet ray traversal stack. Geometry, queries, movement, scenario, save and seed are unchanged. [Reference provenance](safe-original-reference.json) records its patch and binary. Final Jolt uses the one preregistered corrective resource-valid run; previous firstv13 is retained separately. Safe-reference timings are not used for performance; [unmodified original V7](bench-comparison.md) remains that oracle.

| Scenario | Rows B/J | Position p95/max cm | Discrete same | Ordered combat same | Structural/outcomes | All numeric exact |
|---|---:|---:|---|---|---|---|
| smoke | 120/120 | 0.000/0.000 | True | True | True | True |
| ik-moves | 1350/1350 | 0.000/0.014 | True | True | True | False |
| ik-stairs | 1680/1680 | 0.010/0.024 | True | True | True | False |
| ik-sweep | 2700/2700 | 0.000/0.000 | True | True | False | False |
| lean | 804/804 | 0.000/0.000 | True | True | True | True |
| aim | 1716/1716 | 0.000/0.010 | True | True | True | False |
| manual-aim | 3510/3510 | 0.000/0.000 | True | True | True | True |
| crossbow | 718/718 | 0.000/0.000 | True | True | True | True |
| quad-wolf | 1260/1260 | 0.000/0.000 | True | True | True | True |
| quad-zoo | 1080/1080 | 0.000/0.000 | True | True | True | False |
| flinch | 961/961 | 0.000/0.000 | True | True | True | True |
