# Body API validation

Status: **complete**.

| Case | Status | Native events | Commands |
|---|---|---:|---:|
| continue | complete | 9 | 2 |
| approach | complete | 6 | 1 |
| turn_to_target | complete | 6 | 1 |
| strafe_left | complete | 6 | 1 |
| strafe_right | complete | 6 | 1 |
| jump_back | complete | 6 | 1 |
| attack_front | complete | 6 | 1 |
| attack_left | complete | 6 | 1 |
| attack_right | complete | 6 | 1 |
| block | complete | 6 | 1 |
| wait_200 | complete | 6 | 1 |
| wait_300 | complete | 6 | 1 |
| mask_purity | complete | 2 | 0 |
| mask_false | complete | 3 | 1 |
| range_mask | complete | 3 | 1 |
| collision_mask | complete | 4 | 1 |
| duplicate | complete | 5 | 1 |
| out_of_order | complete | 5 | 1 |
| stale_session | complete | 2 | 0 |
| stale_world | complete | 2 | 0 |
| stale_generation | complete | 2 | 0 |
| stale_disconnect | complete | 2 | 0 |
| stale_goal | complete | 3 | 1 |
| stale_observation | complete | 3 | 1 |
| expired | complete | 3 | 1 |
| unknown_target | complete | 3 | 1 |
| death | complete | 3 | 0 |
| target_gone | complete | 3 | 0 |
| reset | complete | 4 | 1 |
| pause_resume | complete | 6 | 1 |
| ownership | complete | 9 | 1 |
| accepted_expired | complete | 4 | 1 |
| goal_changed_pending | complete | 5 | 1 |
| realtime_timeout | complete | 7 | 1 |
| realtime_disconnect | complete | 7 | 1 |
| lockstep_timeout | complete | 8 | 1 |
| action_chain | complete | 12 | 3 |


- No neural model or training is part of this stage.
- Fixture mutations establish test states; all action execution, animation, collision and damage use the native engine.
- Continue is a continuation of an existing segment and does not claim a new animation start.
- Wall watchdog timing is measured independently from simulation time.
