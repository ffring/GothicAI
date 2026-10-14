# Physical tolerance diagnostic: declared field classification

Declared **14 October 2026, 04:11:02 Moscow (01:11:02 UTC), before v12 full results**. This is a separately named diagnostic, `physical_tolerance_diagnostic`; it is **not applied** and **does not change the current exact document gate**. No engine, comparator, timing gate, scenario or assertion is changed. Classification comes from TestBench exports and source units, not v11/v12 error magnitudes.

The [JSON classification](physical-tolerance-classification.json) covers all **323 columns**, including their export formats. Header order and every expanded `fprintf` format were checked against [TestBench](../../../OpenGothic/common/testbench.cpp). The existing `all_numeric_columns_exact` result must remain separately visible.

| Rule | Threshold | Fields / source units |
|---|---|---|
| Hero position | Euclidean **strictly <0.1 cm in every row** | `x,y,z`, exported to 0.01 cm; compare squared decimal distance <0.01 cm². |
| Other spatial scalars | ≤0.1 cm each | Body offsets/floor; foot ray heights, smoothed heights, contact error/slack and joint XYZ; animation pop; crouch/drop; bow/hand/head distances and `rh_*`. Whole-number `ai_lead` stays exact. |
| Orientation | ≤0.1° each | Body, foot IK, gaze, lean slope/roll/pitch, aim, bow, gestures, flinch, sway/spread and NPC archer angles. Direct absolute differences; no wrapping or near-singularity exemption. |
| Normalized weights/components | ≤0.001 each | IK/gaze/aim/animation/morph weights; plant amounts; stance fractions; draw/fatigue; lips/faces; flinch strength/height; probabilities; ray-normal components. Contact flags stay exact. |
| Linear speed | ≤0.1 cm/s | `speed`, `ln_speed`, `aw_speed`; one source `%.1f` quantum. Whole-number `an_speed` stays exact. |
| Angular speed | ≤0.1°/s | `ln_turn`; one source `%.1f` quantum. |
| Screen motion | ≤0.001 percentage point of screen height | `m_cross`, source `%.3f`; no guessed conversion to world cm. |
| Mixed semantic progress | Exact | `m_disturb` is a dimensionless 0..2 disturbance; `bd_nock` is a 0..2 quiver/string phase. Neither is a normalized weight. |
| Integers, times, text and outcomes | Exact | Every `%d/%u/%llu` field, all whole-number exports, all float seconds (`bl_time`, `m_hold`, `m_breath`), all names/layers, HP, event times and combat outcomes. |

Require complete validated execution/evidence, identical row coverage and the full ordered schema. Unknown, missing or duplicate fields fail. Every matched row and every field must satisfy its rule; there are no percentile, majority or inactive-field exceptions. Finite status and semantic sentinels must match exactly before continuous comparison. A missed-ray `-inf` is allowed only on both sides with the same corresponding `has/toehas=0` flag.

For spatial values this diagnostic allows up to one millimetre even when the exporter resolves 0.1 mm; for high-precision angles it allows 0.1° even when the exporter resolves 0.001°. These are physical bounds, **not proof of rounding noise**. At a one-metre lever arm, 0.1° is about 1.75 mm, so angle and spatial bounds are not interchangeable. Other spatial coordinates are checked individually; their limits alone do not imply a 0.1 cm Euclidean bound on a three-component derived point.

Speeds use one export quantum in their own units. At 30 fps, 0.1 cm/s corresponds to about 0.0033 cm per frame; the independent strict hero-position guard catches accumulated displacement. Integer states/counters and float-valued time measurements stay exact. Normalized fields exported only to 0.01 cannot tolerate a one-digit difference under the 0.001 rule.

Keep ordered combat counts, actors, results, zones, damage, HP, dice outcomes and all event timestamps exact and present. Existing continuous combat comparisons remain required; this CSV diagnostic never replaces them. Physics p50 and p95 must still be no higher than Bullet, and E2E assertions/readiness remain unchanged. Report `all_numeric_columns_exact` plus per-field maximum delta, threshold, exceedance count and first failing rows. A passing physical diagnostic never grants a document pass.

The user waiver for correct tiny-sphere normals concerns the separate sphere-query oracle. It does **not** approve these wider acceptance thresholds, relax foot-ray contact/normal checks, or excuse movement/combat failures. This diagnostic only reports differences; the document gate stays unchanged.

Complete grouped field inventory follows; each field occurs exactly once. Units, formats and rules are explicit in JSON.

- **text_exact**, text, export `%s`, exact: `seg`, `an_bone`, `an_layers`.
- **integer_exact**, integer/state/count, export `%llu`, exact: `frame`.
- **time_ms_exact**, ms, export `%llu`, exact: `t`, `lip_t`.
- **hero_xyz**, cm, export `%.2f`, < 0.1: `x`, `y`, `z`.
- **orientation**, degree, export `%.1f`, ≤ 0.1: `rot`, `l_knee`, `l_tilt`, `l_roll`, `l_yaw`, `l_out`, `r_knee`, `r_tilt`, `r_roll`, `r_yaw`, `r_out`, `fl_knee`, `fl_tilt`, `fl_roll`, `fl_yaw`, `fl_out`, `fr_knee`, `fr_tilt`, `fr_roll`, `fr_yaw`, `fr_out`, `ln_slope`, `bl_angle`, `aw_twist`, `aw_chest`, `bd_angle`, `bd_square`, `bd_cant`, `f_yaw`.
- **speed_cm_s**, cm/s, export `%.1f`, ≤ 0.1: `speed`, `ln_speed`, `aw_speed`.
- **integer_exact**, integer/state/count, export `%u`, exact: `bs`, `walk`, `occl`, `nlegs`, `bad`, `bl_active`, `bl_started`, `lip_f0`, `lip_f1`, `lip_picks`, `ge_id`, `ge_mood`, `ge_starts`, `ge_cuts`, `ge_stops`, `ge_nods`, `ge_lnods`, `bk_n`, `bk_pause`, `bk_shift`, `bk_double`, `f_hits`.
- **integer_exact**, integer/state/count, export `%d`, exact: `ik_valid`, `ik_on`, `ik_hardoff`, `l_has`, `l_toehas`, `r_has`, `r_toehas`, `fl_has`, `fl_toehas`, `fr_has`, `fr_toehas`, `g_on`, `g_src`, `pg_on`, `pg_src`, `ln_on`, `bl_on`, `a_on`, `a_steady`, `m_stage`, `m_holding`, `m_short`, `bd_hold`, `lip_valid`, `lip_on`, `ge_on`, `ge_talk`, `ge_listen`, `ge_think`, `bk_on`, `f_on`, `hp_player`, `hp_subject`, `ai_on`, `ai_want`.
- **normalized**, dimensionless fraction/weight, export `%.3f`, ≤ 0.001: `ik_w`, `l_plant`, `r_plant`, `fl_plant`, `fr_plant`, `g_w`, `pg_w`, `a_w`, `m_draw`, `m_fatigue`, `bd_w`, `bd_draw`, `lip_level`, `lip_mouth`, `lip_open`, `lip_int`, `ge_aside`, `bk_w`, `face_neutral`, `face_friendly`, `face_angry`, `face_hostile`, `face_afraid`, `face_pain`.
- **spatial**, cm, export `%.2f`, ≤ 0.1: `pelvis`, `chest`, `pelvis_target`, `pelvis_limit`, `floor`, `l_gy`, `l_toey`, `l_smooth`, `l_err`, `l_slack`, `l_hip_x`, `l_hip_y`, `l_hip_z`, `l_kneea_x`, `l_kneea_y`, `l_kneea_z`, `l_anka_x`, `l_anka_y`, `l_anka_z`, `l_toea_x`, `l_toea_y`, `l_toea_z`, `l_kneef_x`, `l_kneef_y`, `l_kneef_z`, `l_ankf_x`, `l_ankf_y`, `l_ankf_z`, `l_toef_x`, `l_toef_y`, `l_toef_z`, `r_gy`, `r_toey`, `r_smooth`, `r_err`, `r_slack`, `r_hip_x`, `r_hip_y`, `r_hip_z`, `r_kneea_x`, `r_kneea_y`, `r_kneea_z`, `r_anka_x`, `r_anka_y`, `r_anka_z`, `r_toea_x`, `r_toea_y`, `r_toea_z`, `r_kneef_x`, `r_kneef_y`, `r_kneef_z`, `r_ankf_x`, `r_ankf_y`, `r_ankf_z`, `r_toef_x`, `r_toef_y`, `r_toef_z`, `fl_gy`, `fl_toey`, `fl_smooth`, `fl_err`, `fl_slack`, `fl_hip_x`, `fl_hip_y`, `fl_hip_z`, `fl_kneea_x`, `fl_kneea_y`, `fl_kneea_z`, `fl_anka_x`, `fl_anka_y`, `fl_anka_z`, `fl_toea_x`, `fl_toea_y`, `fl_toea_z`, `fl_kneef_x`, `fl_kneef_y`, `fl_kneef_z`, `fl_ankf_x`, `fl_ankf_y`, `fl_ankf_z`, `fl_toef_x`, `fl_toef_y`, `fl_toef_z`, `fr_gy`, `fr_toey`, `fr_smooth`, `fr_err`, `fr_slack`, `fr_hip_x`, `fr_hip_y`, `fr_hip_z`, `fr_kneea_x`, `fr_kneea_y`, `fr_kneea_z`, `fr_anka_x`, `fr_anka_y`, `fr_anka_z`, `fr_toea_x`, `fr_toea_y`, `fr_toea_z`, `fr_kneef_x`, `fr_kneef_y`, `fr_kneef_z`, `fr_ankf_x`, `fr_ankf_y`, `fr_ankf_z`, `fr_toef_x`, `fr_toef_y`, `fr_toef_z`, `an_pop`, `rh_x`, `rh_y`, `rh_z`.
- **normal_component**, unit-vector component, export `%.3f`, ≤ 0.001: `l_nx`, `l_ny`, `l_nz`, `r_nx`, `r_ny`, `r_nz`, `fl_nx`, `fl_ny`, `fl_nz`, `fr_nx`, `fr_ny`, `fr_nz`.
- **orientation**, degree, export `%.2f`, ≤ 0.1: `g_err`, `g_yaw`, `g_pitch`, `g_yawgoal`, `g_pitchgoal`, `g_head`, `g_chest`, `g_waist`, `g_animyaw`, `g_animpitch`, `g_hipsyaw`, `g_fyaw`, `g_fpitch`, `pg_err`, `pg_yaw`, `ln_roll`, `ln_pitch`, `a_yaw`, `a_pitch`, `a_animyaw`, `a_animpitch`, `a_turn`, `a_bend`, `a_err`, `a_lean`, `a_side`, `ge_pitch`, `ge_yaw`, `ge_roll`, `f_bend`, `f_twist`, `f_head`.
- **speed_degree_s**, degree/s, export `%.1f`, ≤ 0.1: `ln_turn`.
- **whole_speed_exact**, cm/s (whole-number export), export `%.0f`, exact: `an_speed`.
- **time_seconds_exact**, s, export `%.3f`, exact: `bl_time`.
- **spatial**, cm, export `%.1f`, ≤ 0.1: `ik_crouch`, `aw_drop`, `bd_span`, `bd_travel`, `bd_miss`, `bd_follow`, `bd_fit`, `bd_offset`, `hd_hand`, `hd_fore`, `hd_arrow`, `bd_overgrip`, `bd_atmiddle`, `bd_nockgap`, `bd_bowmove`, `bd_bowmiss`.
- **normalized**, dimensionless fraction/weight, export `%.2f`, ≤ 0.001: `m_crouch`, `m_peek`, `m_cant`, `m_walk`, `aw_w`, `bd_held`, `lip_alpha`, `f_str`, `f_ht`, `ai_pv`, `ai_ps`.
- **time_seconds_exact**, s, export `%.2f`, exact: `m_hold`, `m_breath`.
- **dimensionless_exact**, dimensionless disturbance (0..2), export `%.3f`, exact: `m_disturb`.
- **orientation**, degree, export `%.3f`, ≤ 0.1: `m_amp`, `m_sway_yaw`, `m_sway_pitch`, `m_off`, `m_spread`, `ai_sigma`, `ai_errx`, `ai_erry`.
- **screen_percent**, percentage point of screen height, export `%.3f`, ≤ 0.001: `m_cross`.
- **phase_exact**, dimensionless reload phase (0..2), export `%.2f`, exact: `bd_nock`.
- **whole_spatial_exact**, cm (whole-number export), export `%.0f`, exact: `ai_lead`.

Source units: [Foot IK](../../../OpenGothic/common/graphics/mesh/footik.h), [Gaze](../../../OpenGothic/common/graphics/mesh/gaze.h), [Lean](../../../OpenGothic/common/graphics/mesh/lean.h), [Aim](../../../OpenGothic/common/graphics/mesh/aim.h), [AimWalk](../../../OpenGothic/common/graphics/mesh/aimwalk.h), [BowDraw](../../../OpenGothic/common/graphics/mesh/bowdraw.h), [Archer](../../../OpenGothic/common/world/objects/archer.h), [ArcherAi](../../../OpenGothic/common/game/archerai.h), [Flinch](../../../OpenGothic/common/graphics/mesh/flinch.h), [LipSync](../../../OpenGothic/common/graphics/mesh/lipsync.h), [AnimBlend](../../../OpenGothic/common/graphics/mesh/animblend.h).

Application reference: the historical declaration above remains unchanged. The reporting-only [Jolt v13 evaluation](physical-tolerance-v13.md) applies these predeclared rules and records **FAIL (10/11 scenarios)**; it changes no document gate or threshold.
