# Headless arena validation

Status: **complete**. Resets: 100.

Diagnostic executed policy transitions: 176; end-to-end 2.471/s; episodes 0.168/s.
Actor-update-usable transitions: 176; end-to-end 2.471/s including discarded episode time.
Discarded benchmark episodes: 0/12; discarded transitions: 0.
Small PPO collection gate: True. small PPO collection is feasible.

Collection forecasts exclude model inference, optimizer and future trajectory-recorder overhead.

| Decisions | Diagnostic collection seconds | Actor-update collection seconds |
|---:|---:|---:|
| 32768 | 13261.40 | 13261.40 |
| 100000 | 40470.58 | 40470.58 |
| 1000000 | 404705.84 | 404705.84 |

| Episode | Scenario | Provider | Outcome | Status | Transitions |
|---|---|---|---|---|---:|
| distance | distance | scripted | task_success | complete | 4 |
| attack | attack | scripted | task_success | complete | 1 |
| block | block | scripted | task_success | complete | 2 |
| native_terminal | native_duel | native | death_or_down | complete | 0 |
| duel_timeout | native_duel | native | time_limit | complete | 0 |
| reset-000 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-001 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-002 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-003 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-004 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-005 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-006 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-007 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-008 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-009 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-010 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-011 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-012 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-013 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-014 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-015 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-016 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-017 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-018 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-019 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-020 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-021 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-022 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-023 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-024 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-025 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-026 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-027 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-028 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-029 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-030 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-031 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-032 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-033 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-034 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-035 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-036 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-037 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-038 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-039 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-040 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-041 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-042 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-043 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-044 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-045 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-046 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-047 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-048 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-049 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-050 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-051 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-052 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-053 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-054 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-055 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-056 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-057 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-058 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-059 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-060 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-061 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-062 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-063 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-064 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-065 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-066 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-067 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-068 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-069 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-070 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-071 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-072 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-073 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-074 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-075 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-076 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-077 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-078 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-079 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-080 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-081 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-082 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-083 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-084 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-085 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-086 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-087 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-088 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-089 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-090 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-091 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-092 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-093 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-094 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-095 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-096 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-097 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-098 | reset_stress | scripted | infrastructure | complete | 3 |
| reset-099 | reset_stress | scripted | infrastructure | complete | 3 |
| paired-17-0-native | native_duel | native | death_or_down | complete | 0 |
| paired-17-0-scripted | native_duel | scripted | control_lost | complete | 12 |
| paired-17-1-native | native_duel | native | death_or_down | complete | 0 |
| paired-17-1-scripted | native_duel | scripted | death_or_down | complete | 42 |
| paired-29-0-native | native_duel | native | death_or_down | complete | 0 |
| paired-29-0-scripted | native_duel | scripted | time_limit | complete | 45 |
| paired-29-1-native | native_duel | native | death_or_down | complete | 0 |
| paired-29-1-scripted | native_duel | scripted | control_lost | complete | 22 |
| paired-43-0-native | native_duel | native | death_or_down | complete | 0 |
| paired-43-0-scripted | native_duel | scripted | control_lost | complete | 16 |
| paired-43-1-native | native_duel | native | death_or_down | complete | 0 |
| paired-43-1-scripted | native_duel | scripted | death_or_down | complete | 23 |
| throughput-000 | native_duel | scripted | time_limit | complete | 14 |
| throughput-001 | native_duel | scripted | time_limit | complete | 15 |
| throughput-002 | native_duel | scripted | time_limit | complete | 15 |
| throughput-003 | native_duel | scripted | time_limit | complete | 14 |
| throughput-004 | native_duel | scripted | time_limit | complete | 15 |
| throughput-005 | native_duel | scripted | time_limit | complete | 15 |
| throughput-006 | native_duel | scripted | time_limit | complete | 14 |
| throughput-007 | native_duel | scripted | time_limit | complete | 15 |
| throughput-008 | native_duel | scripted | time_limit | complete | 15 |
| throughput-009 | native_duel | scripted | time_limit | complete | 14 |
| throughput-010 | native_duel | scripted | time_limit | complete | 15 |
| throughput-011 | native_duel | scripted | time_limit | complete | 15 |
| reset-cleanup | reset_stress | native | running | complete | 0 |


- One runner, CPU headless simulation; no model or optimizer was run.
- Reproducibility covers initial semantic state, not bitwise identity of complete fights.
- control_lost marks a candidate LOS boundary; it is neither a win nor a time-limit truncation, and its fragment is excluded from actor updates.
- Paired diagnostic seeds and mirrored placements do not establish learner quality.
