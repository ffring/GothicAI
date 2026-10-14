# Shared-edge ray normal compatibility

Version scope: the measurements and native-normal descriptions below record the historical v11 correction (also retained by v12). Frozen v13 additionally exports the original scalar source-plane normal for closest identity/unit-scale landscape hits with a valid original fraction; other categories and all-hit queries retain native normals. The separate [107-face arithmetic proof and current scope](source-ray-normal-compatibility.md) explain that addition. Historical v11 results below are not the final v13 acceptance verdict.

Status: the v11 closest-hit source-plane refinement passed the 100,000-ray oracle with zero hit, material, sector, or normal mismatches and **99.997% point agreement** (three errors greater than 0.1 cm, down from 17 in v10). Its isolated 210-frame movement proof and full smoke hero positions matched original Bullet exactly. The remaining full movement-suite results are still pending.

The seven residual normal differences were face-selection differences. Normals recomputed from the original vertices of each Jolt-selected face agreed with Jolt within 1.5e-5, while Bullet had selected another face. The triangles were well conditioned (minimum altitudes 1.13–3.53 m); replacing compressed normals with original normals would preserve the wrong face and would not resolve these cases.

An isolated all-native-hit diagnostic found the Bullet-selected face for every ray. Each matching face shares exactly two original vertex positions with the initial face, and belongs to the same body. The original hit points have minimum barycentric coordinates between -1.073e-4 and 1.423e-5, locating the ambiguity at an original triangle edge.

| Original query ID | Native source face → selected source face | Native face original plane fraction | Selected face original plane fraction | Selection |
| --- | --- | --- | --- | --- |
| 16727 | 255807 → 255799 | 0.0614742413 | 0.0614741594 | Nearest valid plane |
| 35743 | 69515 → 69516 | 0.163712278 (invalid) | 0.163712323 | Native face fails original triangle edge test |
| 40358 | 85536 → 85528 | 0.544298887 | 0.544298887 | Exact fraction tie: lower source triangle ID |
| 45108 | 251155 → 251152 | 0.648753226 | 0.648753166 | Nearest valid plane |
| 64160 | 221237 → 221238 | 0.452845722 | 0.452843964 | Nearest valid plane |
| 93300 | 221687 → 221688 | 0.192900538 (invalid) | 0.192900568 | Native face fails original triangle edge test |
| 97350 | 92333 → 92353 | 0.108205862 | 0.108205222 | Nearest valid plane |

The closest-ray correction runs only when the selected native hit has an original minimum barycentric coordinate <= 1e-4. This uses the existing compatibility edge allowance; it does not enlarge the ray tolerance. A negative coordinate also triggers the check because a point bounded to the compressed native face can lie just outside its original face.

The initial conversion captures a Jolt `TransformedShape` and original local ray under the existing body read lock. The lock is released before a native all-hit query against that shape snapshot. Candidates must belong to that same body and share at least two exact original vertex positions with the initial face. Valid candidates are ranked by the original scalar plane-intersection and triangle-edge arithmetic, with the original triangle ID as the deterministic tie breaker for exactly equal float fractions. There is no fraction window or material preference.

Jolt determines the hit, body, available candidates, and selected native normal. Original mesh data selects among native hits; it does not provide a second collision mesh. Existing coincident-face material/sector resolution still applies. Public all-hit queries retain their sorted native list, native fractions and points, and initial result, and do not run this closest-hit correction.

For the closest query, v11 additionally exports the valid original source-plane fraction of the final selected/resolved triangle and its corresponding endpoint interpolation. Invalid source-plane intersections preserve the native fraction and point. This refinement happens independently of the edge trigger, including for interior floor hits, and adds no collision query or tolerance change. The edge trigger still uses the initial native hit fraction, so the export refinement does not alter Jolt's candidate collection or its geometric selection.

`tests/physics_contract.cpp::testSharedRayEdge` retains the original two faces and ray 97350 as an asset-free regression. It checks that native all-hit ordering chooses the wood face first, the closest query chooses the nearer original stone plane, the output retains that candidate's native normal and metadata while exporting its original fraction/point, and an interior ray retains its ordinary face. `testOriginalFloorProjection` also checks an interior horizontal floor whose distant anchor triangles expose a compressed height error: the closest output recovers the original height while the all-hit output stays native. Both fixtures passed in the combined v11 Linux `check-physics` run, preserved in `data/physics/stage1/jolt-final-v11/build-refined-v11.log`. The full v11 fixed oracle confirmed the new point agreement and zero hit/material/sector/normal mismatches.

The source-plane refinement addresses a separate upstream input drift. The movement fixture's `slopeAt` uses five `groundY` heights at the center and offsets of ±40 cm to choose heading; small compressed floor-height differences therefore change later capsule positions. In the unchanged `ik-stairs-firstspot.bench` prefix, v10's maximum hero-position difference was **7.674 cm**, while the isolated source-plane export runner reproduced **all 210 hero XYZ rows exactly** against original Bullet (maximum **0 cm**). It also removed the early stop at frame 16. The native and bounded-GJK-axis contact variants did not eliminate the drift. This is a causal focused proof, not a substitute for the full suite; detailed evidence is in [contact-firstspot-rawplane.json](contact-firstspot-rawplane.json).

The v11 query run measured Jolt ray latency at p50/p95 **2.315/2.443 μs** (v10: **2.238/2.358 μs**). The original run measured **188.094/252.113 μs** while a low-priority compilation was active, so that comparison alone is not a neutral performance measurement. The separate final neutral smoke physics p50/p95 passed: Bullet **15.133/244.615 ms**, Jolt v11 **10.705/198.152 ms**; all smoke hero XYZ rows matched exactly. The full `ik-moves` run also retained all body-state values, with maximum hero-position difference **0.0141 cm**. The remaining movement scenarios must still determine overall migration acceptance.

Diagnostic inputs and detailed candidate records are preserved locally in `data/physics/stage1/normal-boundary-analysis.json` and `normal-boundary-candidates.json` (large ignored evidence). This tracked report records the decision and limits without requiring those files.
