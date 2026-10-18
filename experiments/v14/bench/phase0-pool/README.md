# Phase 0 pool-decode scaling reference (SIMULATED)

`V13_TREE=/root/vnx-dna-lab/v14-base python experiments/v14/bench/pool_bench.py run --trees v13 --extents 4,8,16 --reps 3 --out experiments/v14/bench/phase0-pool`
on 2026-10-18, V13 tree 58171b8, lab host (Xeon Gold 6240, 8 vCPU, Linux 6.8), systemd scope in `vnxdna.slice`
(MemoryMax 8 GiB), run alone on the host (1-minute load 0.4–1.1). Workload per E: E MiB of random data encoded as a
stream of 1 MiB extents, reads simulated at coverage 3 with 1 % substitutions (seed 7), shuffled pool decoded at
1 worker with `pool_bench.py one` (V13 `decode_stream` in pool mode).

| extents | pool bytes (FASTQ) | median s (3 reps) | range s | x vs previous E | peak RSS MiB |
|---|---|---|---|---|---|
| 4 | 251,683,006 | 4.45 | 4.38–4.49 | — | 102.6 |
| 8 | 503,477,122 | 16.83 | 16.40–17.17 | 3.78 | 102.0 |
| 16 | 1,007,641,530 | 63.69 | 63.45–64.77 | 3.78 | 103.4 |

All 9 runs SUCCESS with output equal to the input. Doubling the extents (and therefore the pool) multiplies the
time by ~3.8, close to the 4x of O(extents x pool): each extent's decode reads the whole pool. This is the defect
Phase 6 workstream A targets. The V13 report measured 6.1 s (4 MiB) and 98 s (16 MiB) with `stream_volume_bench.py`
(a different harness, different host): the absolute numbers differ, the scaling (x14.3 here vs x16.1 there from 4 to
16 extents) agrees.

An earlier attempt stopped after one row when the original session was cut, and a rerun failed at its tree check
because a relative `V13_TREE` was not resolved (fixed in `pool_bench.py`); neither produced rows in this directory.
`pool-summary.json` is the harness's own summary of `pool.jsonl`.
