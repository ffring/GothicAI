# Corrective final v13 / original V7 comparison

This is the one preregistered final corrective run under verified SYSTEM CPUQuota200%, MemoryMax12GiB, TasksMax128, Userkaifat. [Actual before-sample resource evidence](formal-resource-provenance.json) and [frozen protocol](corrective-v13-run-protocol.json) are recorded. The firstfullv13 and all invalid repeated-order observations remain separate history; no favorable result was selected. Numeric timing conditions and execution protocol validity are reported separately. The clean [safe numeric reference](safe-reference-bench-comparison.md) handles the original parallel ray-stack race.

Execution and structural/outcome agreement are separate from exact numerical agreement. Failed or truncated scenarios cannot pass the document gate. Physics timing includes public query, movement and transform work between ticks plus the tick itself; it excludes rendering and total frame cost. The document timing gate requires both Jolt quantiles to be no higher than Bullet.

| Scenario | Execution B/J | Bullet/Jolt rows | Matched keys | Position p95/max cm | Structural/outcomes | All numeric exact | Bullet physics p50/p95 μs | Jolt physics p50/p95 μs | Timing gate |
|---|---|---:|---:|---:|---|---|---:|---:|---|
| smoke | True/True | 120/120 | 120 | 0.000/0.000 | True | True | 15133.4/244615.4 | 10976.7/194788.6 | True |
| ik-moves | True/True | 1350/1350 | 1350 | 0.000/0.014 | True | False | 14448.3/225512.1 | 10459.2/195748.9 | True |
| ik-stairs | True/True | 1680/1680 | 1680 | 0.010/0.024 | False | False | 14528.8/251528.8 | 10186.3/214754.5 | True |
| ik-sweep | True/True | 2700/2700 | 2700 | 0.000/0.000 | False | False | 14076.3/247776.8 | 10310.9/206843.2 | True |
| lean | True/True | 804/804 | 804 | 0.000/0.000 | True | False | 14486.4/259683.9 | 10394.3/201284.7 | True |
| aim | True/True | 1716/1716 | 1716 | 0.000/0.010 | False | False | 14359.0/224374.9 | 10416.8/207542.2 | True |
| manual-aim | True/True | 3510/3510 | 3510 | 0.000/0.000 | True | True | 14033.5/260083.8 | 10311.9/234152.5 | True |
| crossbow | True/True | 718/718 | 718 | 0.000/0.000 | True | True | 13551.2/205159.3 | 10629.1/244155.4 | False |
| quad-wolf | True/True | 1260/1260 | 1260 | 0.000/0.000 | True | True | 13965.5/226061.3 | 10449.8/230013.5 | False |
| quad-zoo | True/True | 1080/1080 | 1080 | 0.000/0.000 | False | False | 14322.2/256837.0 | 10188.5/188382.9 | True |
| flinch | True/True | 961/961 | 961 | 0.000/0.000 | True | True | 14241.9/259458.1 | 10331.9/200829.8 | True |
