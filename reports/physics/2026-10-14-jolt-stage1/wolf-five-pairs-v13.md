# Wolf timing observations with protocol deviation

The actual order was original V7 followed by the same immutable Jolt v13 binary in all five pairs. The frozen protocol required Jolt first in pairs2 and4. This execution deviation invalidates the declared pooled protocol verdict. No rerun or selection is performed. All ten runs are retained. The clean serialized original reference is not used for performance. The observed numerical comparison pools exactly 7,570 samples per backend after removing the first 32 frames of each run, then uses sorted_values[int(p*(n-1))]. Its numbers are retained as observations; they cannot be labeled a passed frozen protocol. Pair ratios and their median are supplemental. The fixed full-suite result remains in [the main performance table](bench-comparison.md). No favorable run is selected.

| Pair | Original p50/p95 ms | Jolt p50/p95 ms | Ratio p50/p95 | Observed quantiles no higher |
|---|---:|---:|---:|---|
| 1 | 14.543/243.112 | 10.263/240.561 | 0.7057/0.9895 | True |
| 2 | 14.112/247.730 | 10.443/198.067 | 0.7400/0.7995 | True |
| 3 | 14.587/256.064 | 10.315/217.774 | 0.7072/0.8505 | True |
| 4 | 14.402/254.303 | 10.404/202.377 | 0.7224/0.7958 | True |
| 5 | 14.027/260.635 | 10.287/202.917 | 0.7334/0.7785 | True |

Declared pooled protocol verdict: INVALID. Observed pooled numerical condition: True. Original p50/p95: 14.329/254.901 ms; Jolt: 10.346/212.686 ms. Pooled ratios: 0.7220/0.8344.

Observed pairs with both quantiles no higher: 5/5. Median paired p50/p95 ratio: 0.7224/0.7995. These values describe all ten observed runs. They do not validate the violated protocol or alter the independent fixed full-suite result. The launch command also omitted explicit MemoryMax12GiB/TasksMax128 and did not record exact per-case start timestamps.
