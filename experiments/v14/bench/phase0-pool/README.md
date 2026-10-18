# Phase 0 pool-decode scaling reference (SIMULATED) — PARTIAL

`experiments/v14/bench/pool_bench.py run --trees v13 --extents 4,8,16 --reps 3` at 58171b8 on the original lab host,
STOPPED after its first row because the host had to be abandoned: `pool.jsonl` holds one row (E = 4 extents of 1 MiB,
pool coverage 3 / 1 % substitutions / seed 7, 1 worker, V13 tree: 4.41 s SUCCESS, output equal to the input; the V13
report measured 6.06 s for the same pool in `stream_volume_bench.py`). Not a usable scaling measurement: re-run the
full command on the build host (docs/V14_STATE.md, resume step 1).
