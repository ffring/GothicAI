# Safe-reference IK analysis before v12

This read-only analysis compares the completed, logging-free serialized original reference (`bullet-safe-numeric-v1`) with Jolt v11 (`jolt-numeric-v11`). It does not change the exact benchmark gate or establish a new tolerance. The v12 suite and its contact correction are separate pending evidence.

## Wolf structural result

All 1,260 wolf metric rows have identical foot/toe hit flags, body state, walk mode, IK enable/valid/hard-off flags, leg count, invalid-bone count, HP and flinch-hit count. Every exported ground-normal component also matches for all four legs. The nearest nonzero accepted ground normal to `FootIk::isWall`'s `ny < 0.5` threshold has `ny = 0.740`, a margin of 0.240. These data do not support a wall-threshold explanation.

The structural comparison fails because the event file contains 10,998 `body_combat` records in the safe reference and 10,926 in Jolt. Counts for notes, output, recording start and recording stop all match; parsed ordered shot/damage/fight outcomes match. The first 269 body-combat records match. The next original event is a background WARG's periodic perception at step 1127, `w4_down` frame 4, scenario time 37,533 ms. Jolt's next such event is at step 1157, frame 34, time 38,533 ms. Original V7 has the same first divergence as the safe reference.

The first later WARG snapshot in the original is at `[13175.313477, 3823.145264, 18517.699219]` cm; Jolt's first WARG snapshot is at `[15089.469727, 4350.169434, 20173.490234]` cm. Both target a MOLERAT. `WARG` is a script instance name shared by multiple creatures, and body-event IDs are allocated when actors or targets first appear in an event. This is insufficient evidence to claim that one creature moved between those positions. At the first original perception event, the player position matches both backends: `[13680.67, 3998.86, 18096.41]` cm, heading `-120.9` degrees, and all eight foot/toe hit flags are one.

If v12 retains this event residual, a focused trace should identify each creature by `WorldObjects::npcId`, retain the unchanged wolf scenario/setup, and capture steps 1060–1160: WARG/MOLERAT positions, current and selected enemy IDs, process policy, perception timer, player distance, senses and any visibility-ray result. Existing trace names and discovery IDs cannot provide that identification.

## Continuous pose differences

The following scenarios have matching discrete metrics under the safe reference. These are measured maximum differences in the exported data, not acceptance rules. Joint distance is the maximum Euclidean distance between matching exported joint XYZ triples. The angle column includes the foot-IK debug knee/tilt/roll/yaw/outward angles.

| Scenario | Hero maximum cm | Joint maximum cm | IK angle maximum degrees | Gaze angle maximum degrees |
| --- | ---: | ---: | ---: | ---: |
| smoke | 0 | 0.0173 | 0.1 | 0 |
| ik-moves | 0.0141 | 0.0436 | 0.1 | 0.01 |
| ik-stairs | 0.0245 | 0.0520 | 0.4 | 0.01 |
| manual-aim | 0 | 0.0374 | 0.1 | 0 |
| crossbow | 0 | 0.0300 | 0.1 | 0.01 |
| quad-wolf | 0 | 0.0500 | 2.9 | 0.05 |
| quad-zoo | 0 | 0.1562 | 1.9 | 0.02 |
| flinch | 0 | 0.0141 | 0.1 | 0.02 |

IK and gaze weights match in this subset. The large v11 movement/contact differences in `ik-sweep`, `lean` and `aim` must be assessed against v12 rather than attributed to ray-normal precision here. The largest zoo joint difference is the Shadowbeast's right final knee at `Shadowbeast_0_across` frame 12.

The largest wolf yaw difference occurs at `w5_stand` frame 13, front-right paw: 70.0 versus 67.1 degrees. Its animated ankle/toe coordinates are identical at the CSV's precision, its final toe X differs by 0.03 cm, and the original final toe's horizontal displacement from its ankle is only 0.576 cm. The front-left maximum is 2.5 degrees at `w5_up` frame 15, with a 0.02 cm toe-X difference and 0.524 cm horizontal toe displacement. These are sensitive horizontal direction measurements: the code normalizes the toe-minus-ankle XZ vector and uses `acos` of its dot product with the animated direction. The animated hip-to-ankle distance divided by the sum of its two leg lengths is 0.902 and 0.953 in those cases. A nearly straight leg is therefore not established as the cause of these maxima.

The existing CSV writes normal components to three decimals, joint coordinates to two decimals, and debug foot angles to one decimal. Equal CSV normals cannot prove equal input normals below 0.001. The earlier seven-ray investigation's raw-normal agreement within 1.5e-5 concerned different queries and cannot prove the wolf's cause.

## Proposed isolated normal proof

After formal runs release the host, a diagnostic may preserve the full original scenario and stop after the first wolf record. It should log only the player's full-precision animated joints/headings, `FootIk::gather` floor/reach/ray endpoints and point/normal results, and `FootIk::update/apply` smoothed normals and final joints. Run the safe original and frozen Jolt with the same observation overhead. Determine the first differing input before changing output normals.

For each selected Jolt mesh hit, a side-by-side diagnostic can report original triangle ID, native normal and the original vertex-cross normal transformed by inverse scale and body rotation. This requires no additional collision mesh or query. Only if those normals are the first differing input should an isolated closest-ray export variant replace the normal with that original-triangle normal; native hit/body/candidates and current source-plane point remain unchanged. The paired pose and full oracle must then demonstrate the effect before any production change. No such variant was compiled or run during this analysis, and no physics source was edited.

Source context: `FootIk::isWall`, `gather`, `apply`, and `solve`; `Npc::traceCombatSnapshot` and `perceptionProcess`; `TestBench::bodyEvent` and CSV formatting. Companion [JSON evidence](safe-reference-ik-analysis.json) records file hashes and selected rows.
