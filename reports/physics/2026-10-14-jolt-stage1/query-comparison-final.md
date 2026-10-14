# Physics query comparison

Inputs: `6941d182dd1b68f2fb500387a04a4cd0e7d5dbfce4f8b22b40dfbefc78c1fbb4`

Document ray gate: **True** (at least 99.9% within 0.1cm, all materials/sectors). Full ray+sphere parity: **False**.

User accepted native Jolt normals for 0.1<=radius<=1cm (1–10mm); raw normal differences remain reported. Points, hits, material and sector receive no exception.

| Query | Count | Hit mismatch | Point mismatch | Raw normal mismatch | Exempt normal | Nonexempt normal | Material mismatch | Sector mismatch | Point match |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ray | 100000 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 99.997000% |
| ray/vertical | 50000 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 99.998000% |
| ray/oblique | 50000 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 99.996000% |
| sphere | 100000 | 26 | 315 | 6695 | 6321 | 374 | 75 | 98 | 99.659000% |
| sphere/vertical | 50000 | 20 | 152 | 6282 | 6100 | 182 | 56 | 88 | 99.656000% |
| sphere/radius=100cm | 16516 | 1 | 122 | 146 | 0 | 146 | 37 | 48 | 99.255268% |
| sphere/radius=0.1cm | 16482 | 20 | 20 | 4852 | 4852 | 0 | 8 | 7 | 99.757311% |
| sphere/oblique | 50000 | 6 | 163 | 413 | 221 | 192 | 19 | 10 | 99.662000% |
| sphere/radius=50cm | 16658 | 2 | 89 | 107 | 0 | 107 | 20 | 28 | 99.453716% |
| sphere/radius=5cm | 16600 | 0 | 26 | 48 | 0 | 48 | 2 | 2 | 99.843373% |
| sphere/radius=25cm | 16990 | 0 | 52 | 73 | 0 | 73 | 7 | 11 | 99.693938% |
| sphere/radius=1cm | 16754 | 3 | 6 | 1469 | 1469 | 0 | 1 | 2 | 99.946281% |

Timing measures warmed query batches, excluding output writes. It does not measure simulation/frame cost.

| Query | Bullet p50 ns | Jolt p50 ns | Bullet p95 ns | Jolt p95 ns |
|---|---:|---:|---:|---:|
| ray | 188094.1 | 2270.2 | 252112.5 | 2402.1 |
| sphere | 3264164.3 | 5715.1 | 4241367.4 | 6225.6 |
