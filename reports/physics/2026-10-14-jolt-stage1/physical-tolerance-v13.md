# Predeclared physical-tolerance diagnostic: Jolt v13

**Reporting-only diagnostic: FAILED. The exact document gate remains unchanged and is still FAIL.** The [classification](physical-tolerance-classification.md) was declared on 14 October 2026 at 04:11:02 Moscow, before v12 full results; its bounds and rules were not changed for this evaluation.

Final accepted candidate evidence is the preregistered **resource-valid corrective run** [jolt-numeric-v13-corrected-resource](../../../data/physics/stage1/jolt-numeric-v13-corrected-resource/manifest.json). All 11 final metric CSVs were independently verified **byte-identical** to the retained first run using the [equivalence proof](../../../data/physics/stage1/corrective-metric-equivalence-v13.json). The existing decimal numerical calculations are therefore reused unchanged; inherited combat/body-count invariants were checked against the regenerated final strict comparison. The first run remains resource-deviation history and is not selected as the final result.

Before samples, [resource provenance](../../../data/physics/stage1/jolt-numeric-v13-corrected-resource-metadata/resource-provenance-before-samples.json) recorded CPU quota `2s` (200%), MemoryMax `12884901888` (12 GiB), TasksMax `128`, User `kaifat`. The [pipeline](../../../data/physics/stage1/jolt-numeric-v13-corrected-resource-metadata/pipeline.json) records the run from **02:50:29 to 02:55:09 UTC on 14 October 2026**. These provenance updates change no diagnostic bound, field result or verdict.

The complete clean Bullet reference / Jolt v13 pair contains **15,899 matched rows across 11 scenarios, each with all 323 declared columns**. Header order, source export precision, execution metadata, scenario hashes, recording/event/timing evidence and row coverage were validated. Every field was compared in every row with decimal arithmetic; hero squared distance must be strictly below 0.01 cm². There is no inactive-field or sentinel exemption.

| Scenario | Rows | Hero max cm | Field exceedances | Diagnostic | Exact serialized numeric gate |
|---|---:|---:|---:|---|---|
| smoke | 120 | 0.000000 | 0 | True | True |
| ik-moves | 1350 | 0.014142 | 0 | True | False |
| ik-stairs | 1680 | 0.024495 | 0 | True | False |
| ik-sweep | 2700 | 0.000000 | 1 | False | False |
| lean | 804 | 0.000000 | 0 | True | True |
| aim | 1716 | 0.010000 | 0 | True | False |
| manual-aim | 3510 | 0.000000 | 0 | True | True |
| crossbow | 718 | 0.000000 | 0 | True | True |
| quad-wolf | 1260 | 0.000000 | 0 | True | True |
| quad-zoo | 1080 | 0.000000 | 0 | True | False |
| flinch | 961 | 0.000000 | 0 | True | True |

All hero vectors satisfy the strict <0.1 cm rule. All inherited discrete metrics, ordered combat results/timestamps and body-event **counts** agree. This does not claim full body-event payload equality. The existing [strict comparison](safe-reference-bench-comparison.md) remains authoritative for exact numerical agreement.

The retained failures are:

- `ik-sweep / s2_down / frame 1`: `l_toey` is Bullet `4182.77` versus Jolt `-inf`; finite/nonfinite marker differs; inactive flags do not waive it. CSV lines 528 / 528; `l_toehas` is `0` / `0`.

The [compact JSON](physical-tolerance-v13.json) reports every field's maximum delta, declared threshold, exceedance count and first failing row; `max_finite_delta` is retained separately when a finite/nonfinite mismatch makes `max_delta=Infinity`. Text deltas are null because text equality has no distance unit. The ignored [full results](../../../data/physics/stage1/physical-tolerance-v13/full-results.json) contain per-scenario statistics for every field; the [implementation](../../../data/physics/stage1/physical-tolerance-v13/apply.py) and [input hashes](../../../data/physics/stage1/physical-tolerance-v13/input-hashes.json) are beside them.

Numeric reference runner SHA-256: `47b9c43cd5d778368e61b35ecb8ccb7a1c0ce98d1d90dc8c79133104d86c0f00`; final Jolt runner: `818360cdea0d96fd4d9c6be51cac631e879824f5f6e86cb1e9e54d6dae9a3165`. The strict parser SHA-256 is `e2cdbdc2c2ee00efee43604dbb52b9e0f35d08c6860bb12530796766ec8e56f9`. Classification rules SHA-256: `ad715b54d7718b7b185ee42177198c4f450ab5fab7a3834c40c202d607b74148`. Original numerical inputs were stable during evaluation. Final corrective metric identity, resource metadata and inherited strict invariants were verified locally; current and historical input hashes are retained separately.

The current final corrective [performance comparison against unmodified V7](bench-comparison.md) is referenced without alteration by this diagnostic; safe-reference timings are not used for that criterion. Engine, comparator, scenarios, thresholds, assertions and timing gates were not modified. This diagnostic grants no document pass and applies no tiny-sphere-normal waiver.
