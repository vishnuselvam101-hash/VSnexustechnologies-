# Phase 6 workstream A: linear-time shuffled-pool decode (SIMULATED)

`V13_TREE=/root/vnx-dna-lab/v14-base PYTHONPATH=/root/vnx-dna-lab/v14/src python experiments/v14/bench/pool_bench.py run
--extents 4,8,16[,32] --reps N --out experiments/v14/bench/p6a-pool`
on 2026-10-18, V13 tree 58171b8 vs V14 `build/v14` (this change), lab host (8 vCPU, Linux 6.8), systemd scope in
`vnxdna.slice` (MemoryMax 12 GiB, CPUQuota 600%). Workload per E: E MiB of random data encoded as a stream of 1 MiB
extents, reads simulated at coverage 3 with 1% substitutions (seed 7), shuffled into ONE pool and decoded at 1 worker
through `decode_stream` with the pool path forced (`by_range=False`). E = 4/8/16 are 2 reps; E = 32 is 1 rep.

| extents | pool records | v13 median s | v14 median s | ratio v13/v14 | v14 pass1 s | v14 partition s | v14 pass2 s | v14 peak RSS MiB |
|---|---|---|---|---|---|---|---|---|
| 4  | ~203k  | 4.51   | 1.38  | 3.3x  | 1.10 | 0.024 | ~0.26 | 98.2 |
| 8  | ~406k  | 16.26  | 2.46  | 6.6x  | 1.93 | 0.039 | ~0.49 | 98.8 |
| 16 | 812,062 | 63.61  | 4.90  | 13.0x | 4.06 | 0.095 | 0.72  | 99.9 |
| 32 | 1,623,235 | 252.29 | 10.02 | 25.2x | 7.95 | 0.236 | 1.56  | 101.8 |

All runs SUCCESS, every output byte-for-byte equal to the input, zero false successes, `by_pool_fallback = 0` (the
linear path carried every extent). V13 time multiplies by ~3.9 per doubling (O(extents x pool)); V14 by ~1.8-2.0
(linear in the pool). The V14 cost is dominated by the single pass 1; the partition/index is a small ~O(pool) term and
per-extent pass 2 touches only each extent's own partition, so the total no longer grows from per-extent rescans. Peak
RSS stays ~100 MiB and does not grow with the extent count.

Rows: `pool.jsonl`; medians and ranges: `pool-summary.json`. SIMULATED (software codec rates on this host; the pool is
produced by the channel simulator).
