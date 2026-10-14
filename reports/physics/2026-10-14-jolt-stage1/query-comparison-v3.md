# Physics query comparison

Inputs: `6941d182dd1b68f2fb500387a04a4cd0e7d5dbfce4f8b22b40dfbefc78c1fbb4`

User accepted native Jolt normals for 0.1<=radius<=1cm (1–10mm); raw normal differences remain reported. Points, hits, material and sector receive no exception.

| Query | Count | Hit mismatch | Point mismatch | Raw normal mismatch | Exempt normal | Nonexempt normal | Material mismatch | Sector mismatch | Point match |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ray | 100000 | 0 | 18 | 7 | 0 | 7 | 1 | 1 | 99.982000% |
| ray/vertical | 50000 | 0 | 6 | 3 | 0 | 3 | 0 | 0 | 99.988000% |
| ray/oblique | 50000 | 0 | 12 | 4 | 0 | 4 | 1 | 1 | 99.976000% |
| sphere | 100000 | 39 | 370 | 6726 | 6331 | 395 | 76 | 99 | 99.591000% |
| sphere/vertical | 50000 | 22 | 166 | 6289 | 6107 | 182 | 55 | 90 | 99.624000% |
| sphere/radius=100cm | 16516 | 2 | 142 | 150 | 0 | 150 | 35 | 52 | 99.128118% |
| sphere/radius=0.1cm | 16482 | 26 | 32 | 4855 | 4855 | 0 | 9 | 7 | 99.648101% |
| sphere/oblique | 50000 | 17 | 204 | 437 | 224 | 213 | 21 | 9 | 99.558000% |
| sphere/radius=50cm | 16658 | 2 | 95 | 110 | 0 | 110 | 22 | 26 | 99.417697% |
| sphere/radius=5cm | 16600 | 3 | 30 | 55 | 0 | 55 | 3 | 3 | 99.801205% |
| sphere/radius=25cm | 16990 | 0 | 59 | 80 | 0 | 80 | 6 | 10 | 99.652737% |
| sphere/radius=1cm | 16754 | 6 | 12 | 1476 | 1476 | 0 | 1 | 1 | 99.892563% |

Timing measures warmed query batches, excluding output writes. It does not measure simulation/frame cost.

| Query | Bullet p50 ns | Jolt p50 ns | Bullet p95 ns | Jolt p95 ns |
|---|---:|---:|---:|---:|
| ray | 188094.1 | 164633.3 | 252112.5 | 173017.4 |
| sphere | 3264164.3 | 342077.7 | 4241367.4 | 415192.8 |
