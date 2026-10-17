# Native body trajectories

Status: **complete**. BC coverage ready: **False**.

| Episode | Status | Expert labels | Usable BC labels | Outcome |
|---|---|---:|---:|---|
| native-17-0 | complete | 51 | 49 | death_or_down |
| native-17-1 | complete | 45 | 40 | death_or_down |
| native-29-0 | complete | 30 | 28 | death_or_down |
| native-29-1 | complete | 30 | 28 | death_or_down |
| native-43-0 | complete | 35 | 32 | death_or_down |
| native-43-1 | complete | 28 | 26 | death_or_down |
| native-61-0 | complete | 37 | 34 | death_or_down |
| native-61-1 | complete | 30 | 27 | death_or_down |
| native-73-0 | complete | 27 | 24 | death_or_down |
| native-73-1 | complete | 50 | 42 | death_or_down |
| native-89-0 | complete | 51 | 48 | death_or_down |
| native-89-1 | complete | 50 | 47 | death_or_down |
| native-101-0 | complete | 39 | 37 | death_or_down |
| native-101-1 | complete | 32 | 30 | death_or_down |
| native-113-0 | complete | 21 | 20 | death_or_down |
| native-113-1 | complete | 21 | 20 | death_or_down |
| native-127-0 | complete | 41 | 39 | death_or_down |
| native-127-1 | complete | 40 | 38 | death_or_down |
| scripted-distance | complete | 0 | 0 | task_success |
| scripted-attack | complete | 0 | 0 | task_success |
| scripted-block | complete | 0 | 0 | task_success |
| ood-distance-800 | complete | 43 | 41 | death_or_down |
| ood-distance-1000 | complete | 32 | 31 | death_or_down |

Insufficient train classes: turn_to_target, jump_back, wait_300.
Missing train conditions: wall.

Production service identity unchanged: **True**. All services active: **False**.
gothic-voxcpm was inactive/dead with MainPID=0 before collection and remained so afterward; availability was not established.

Reporter regrade: Regrade unchanged production service identity against its actual baseline; an already inactive service may remain inactive. No collection, dataset, engine or mechanics criterion changed.
Collected reporter SHA: `45bb794958027efe2562730cee38f5543e1e1017a02cfae3487b89eea6fe7158`.
Actual reporter SHA: `dcde0f738f1ba89b8571824f92ec923b335514aee56d5cf0311f33d943d64bc8`.


- This validates a diagnostic corpus and pipeline; no model, optimizer or on-policy rollout ran.
- Environment/corpus readiness is separate from the 20-label-per-class and condition coverage gate.
- OOD contains new separations in the same arena; geometry/opponent diversity remains future work.
