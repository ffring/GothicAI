# Body trajectory dataset

Validated episodes: 23.

Splits keep setup family, seed, both roles and related repeats together. OOD is separate.

| Split | Groups | Episodes | Usable expert labels | Coverage gate |
|---|---:|---:|---:|---|
| train | 6 | 15 | 455 | insufficient |
| eval | 2 | 4 | 96 | insufficient |
| test | 1 | 2 | 58 | insufficient |
| ood | 2 | 2 | 72 | insufficient |

Train coverage requires 20 actual usable native expert labels for every included action.

| Action | Train labels | Missing |
|---|---:|---:|
| approach | 101 | 0 |
| turn_to_target | 14 | 6 |
| strafe_left | 39 | 0 |
| strafe_right | 39 | 0 |
| jump_back | 0 | 20 |
| attack_front | 59 | 0 |
| attack_left | 25 | 0 |
| attack_right | 20 | 0 |
| block | 79 | 0 |
| wait_200 | 79 | 0 |
| wait_300 | 0 | 20 |

Missing train conditions: wall.

Normalization and class weights use train observations and usable expert labels only.

Episode outcomes do not establish per-action success. No model quality or release claim is made.
