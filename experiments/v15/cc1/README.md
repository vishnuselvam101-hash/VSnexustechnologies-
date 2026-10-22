# V15 CC-1: decode speed (SIMULATED inputs)

Every input is produced by the channel simulator. All numbers are software timings of the codec on one host, not
physical DNA measurements. Protocol: `PREREG.md` (committed before any change was measured) and its two amendments.

- Host: Intel Xeon Gold 6240 (KVM, 8 vCPU), 31 GB RAM, Linux 6.8, Python 3.12, `OPENBLAS_NUM_THREADS=1`; no other heavy
  job; every tree with its own native libraries (all native, none older than its source; the drivers refuse otherwise).
- Trees: v12 = 6bf7749 (tier denominator), v14.0.0 = 7369e09, final = this branch (src at d944783).
- Primary workloads (P6E manifest): T-A `T-A-indel-4m-w1` (indel, 4 MiB, 1 worker), T-B `T-B-clean-4m-w4` (clean,
  4 MiB, 4 workers). Ratio = time(a)/time(b) per seed; median; percentile-bootstrap 95 % CI (10000, seed 11).

## 1. Profile of v14.0.0 (before)

Stage shares, median of 18 seeds (`profile/stage-shares.json`, from `final/primary.jsonl`):

| workload | wall s | pass 1 (engine) | pass 2 | verify | probe + setup + output |
|---|---|---|---|---|---|
| T-A w1 | 3.109 | 87.1 % | 7.7 % | 0.4 % | 4.9 % |
| T-B w4 | 0.593 | 58.5 % | 16.7 % | 3.5 % | 21.2 % |

Engine counters (s): T-A w1: cheap attempts 1.061, full attempts (DP) 0.733, prep 0.314, grouping 0.234, round
bookkeeping 0.216, scan 0.077, spill 0.054. T-B w4: round bookkeeping 0.100, grouping 0.067, prep 0.060 (parallel),
scan 0.049, cheap 0.044 (parallel), spill 0.011.

Instrumented builds (scratch, one run each, indicative) split the T-B w4 serial engine time further: the V13-rule
table inserts after each round 0.07-0.08 s (the largest serial piece), union-find grouping 0.044, batch array resets
0.012, claim closing 0.010. The layout probe took 0.087-0.093 s per decode (sample 0.028, frame heads 0.012, two
candidate scorings ~0.04). In pass 2 the fast bucket path spent ~0.04 s (T-B) building a set of every verified
address that no result depended on.

Serial fraction (Amdahl, from wall time at 1 and 4 workers, 3 seeds, `profile/serial-fraction.json`):

| workload | v14.0.0 T1 / T4 / T8 s | s (w4) | final T1 / T4 / T8 s | s (w4) |
|---|---|---|---|---|
| T-A input | 3.071 / 1.651 / 1.488 | 0.383 | 2.862 / 1.596 / 1.338 | 0.410 |
| T-B input | 0.859 / 0.595 / 0.545 | 0.590 | 0.712 / 0.483 / 0.437 | 0.570 |

The kept changes cut serial and parallel work alike, so the serial fraction hardly moved.

## 2. Changes

Per change: golden test (`tests/v15/test_cc1_golden.py`: outputs, normalised reports and the engine's per-read decision
trace of 8 SIMULATED pools identical to 14.0.0 at 1 and 3 threads), the differential check (`diffcheck.py`, rows in
`diff/`: all 60 inputs of the P6E campaign at smoke size at 1/2/4 workers plus 20 primary 4 MiB inputs at 1/4/8 workers,
output bytes + reports identical to v14.0.0, 0 false successes, identical across worker counts), paired A/B with A/A.
Keep rule: base/new CI lower bound > 1 and above the new/newaa CI upper bound.

| # | change | component (base/new) | end to end T-B (base/new) [CI] | A/A [CI] | end to end T-A [CI] | decision |
|---|---|---|---|---|---|---|
| 1 | pass 2: drop the unused known-address set (37f4502) | pass 2: T-B 2.216 [2.026, 2.328], T-A 1.361 [1.293, 1.508] | 1.113 [1.062, 1.153] | 0.998 [0.975, 1.011] | 1.018 [0.977, 1.024] | kept |
| 2 | engine tables: fixed-distance prefetch for the V13 inserts, 2 MiB pages (c7906ea) | engine attempt stage T-B 1.140 [1.104, 1.241] | 1.081 [1.06, 1.117] | 0.982 [0.96, 1.018] | 1.013 [0.98, 1.042] | kept |
| 3 | engine: scan the next window on a helper thread at >= 2 threads (7ddd8a7) | pass 1 T-B 1.185 [1.083, 1.425] | 1.104 [1.04, 1.294] | 1.005 [0.993, 1.074] | 0.964 [0.916, 1.04] (w1: path unchanged) | **reverted** (8efce8d): lower bound below the A/A upper bound |
| 4 | layout probe: flat sample, frame heads on demand, shared exact-length matrices (d944783) | probe 0.087-0.093 -> 0.067-0.077 s | 1.063 [1.035, 1.111] | 0.993 [0.971, 1.023] | 1.016 [1.002, 1.031] (A/A 0.997 [0.979, 1.015]) | kept (T-B) |

Changes 1-3: n = 9 seeds. Change 4: n = 18 under amendments 1-2; its first run (`ab/c4-probe-superseded.jsonl`) showed
a position bias (A/A 0.956) that amendment 2 removed (per-seed input warm-up, six-permutation order).

Tried and dropped before an A/B (instrumented scratch builds showed no component gain): partitioned parallel table
inserts (0.073-0.082 s vs 0.067-0.074 s serial: the inserts are bound by memory throughput, not by one thread);
parallel grouping with a concurrent first-reader map (0.053-0.062 s at 4 threads vs 0.047 s serial); prefetching in the
grouping loop; MADV_POPULATE_READ on scan windows; smaller parallel chunks for prep/screen (prep 0.060 -> 0.057 s).

Fail-fast in the strand retry rung: a sound skip rule exists (the retry can add at most one symbol per read the first
run did not attempt, so it cannot succeed when the failed rows miss more symbols than that), but it never fires: on the
failing mixed-4m-w4 seeds the first run left ~320,000 reads unattempted against at most a few hundred missing symbols.
No rule with useful power keeps every decision on decodable inputs, so none was implemented. The retry costs 8.3-8.7 s
of the ~18 s time to FAILURE (`final/secondary.jsonl`, fallback records).

Native safety (change 2, and change 3 before its revert): `benchmarks/v13/native_engine/sanitizers.sh` with the engine,
scheduler and V15 tests: gcc ASan + UBSan PASS (78 / 86 tests), `cargo test -D warnings` PASS (26 tests, incl. the new
ZeroTable test), debug-assertions + overflow-checks build PASS, canary caught (`native-safety/`).

## 3. Final measurement (amended protocol, n = 18 seeds; `final/`)

| workload | v12 s | v14.0.0 s | final s | v12/final [CI] | v14.0.0/final [CI] | final/finalaa [CI] | target |
|---|---|---|---|---|---|---|---|
| T-A indel 4 MiB w1 | 8.847 | 3.109 | 2.986 | 2.956 [2.899, 3.024] | 1.042 [1.035, 1.046] | 1.002 [0.983, 1.012] | >= 5x: **missed** |
| T-B clean 4 MiB w4 | 1.334 | 0.593 | 0.480 | 2.774 [2.713, 2.88] | 1.236 [1.216, 1.274] | 0.995 [0.977, 1.006] | >= 3x: **missed** |

Registered seeds only (n = 9): T-A v12/final 3.002 [2.869, 3.058], v14/final 1.042 [1.015, 1.054]; T-B v12/final
2.916 [2.752, 3.062], v14/final 1.273 [1.234, 1.313] (`final/primary-registered-vs-extension.json`). In-run v12/v14:
T-A 2.857, T-B 2.265 (the V14 report: 2.848 / 2.209). Components final vs v14.0.0: pass 2 T-B 2.245 [2.17, 2.436],
T-A 1.331 [1.302, 1.366]; pass 1 T-B 1.095 [1.069, 1.138], T-A 1.021 [1.015, 1.03].

Secondary (v14.0.0/final; A/A in brackets): indel-16m-w4 1.056 [1.047, 1.144] (0.996 [0.935, 1.045], n = 3);
sub-64m-w4 1.141 [1.133, 1.189] (0.984 [0.966, 1.019], n = 3); mixed-4m-w4 (3 of 5 seeds FAIL in every version)
1.005 [0.999, 1.016]: time to FAILURE unchanged.

Remaining serial share. T-B (final, 4 workers, 0.480 s): probe + setup + output 0.102, pass 2 + verify 0.064, and in
the engine the round bookkeeping 0.073, grouping 0.068, scan 0.049, spill 0.011: ~0.37 s (~76 %) runs on one thread.
3x at the v12 median needs <= 0.445 s; a CI lower bound >= 3 needs about 0.42 s. T-A (1 worker by definition) is CPU
bound: 5x needs <= 1.77 s, 41 % less work; the largest pieces are the cheap attempts (1.05 s: inner RS 12-14 % of
cycles, speculative projection, orientation scan) and the DP alignment of full attempts (0.73 s).

## 4. Tests

Full suite (gate 4852/5/0): **4867 passed, 5 skipped, 0 failed** (`suite/suite-d944783.txt`): +8 golden decode cases
and +7 probe-equivalence cases (`tests/v15/test_cc1_probe.py` runs the 14.0.0 probe as the reference). No test was
weakened, skipped or deleted.

## Reproduce

```
PYTHONPATH=src python experiments/v15/cc1/ab.py prep --workload T-B-clean-4m-w4 --seeds 95100-95117 --dir D
PYTHONPATH=src python experiments/v15/cc1/ab.py run --workload T-B-clean-4m-w4 --seeds 95100-95117 --dir D \
    --config v12=<6bf7749 tree> --config v14=<v14.0.0 tree> --config final=. --config finalaa=. \
    --order-scheme rot6 --warm-input --out rows.jsonl
python experiments/v15/cc1/ab.py summary rows.jsonl --pair v12/final --pair v14/final --pair final/finalaa
PYTHONPATH=src python experiments/v15/cc1/diffcheck.py prep --dir S        # campaign inputs at smoke size
PYTHONPATH=src python experiments/v15/cc1/diffcheck.py run --dir S --base <v14.0.0 tree> --new . --workers 1,2,4 --out d.jsonl
PYTHONPATH=src python experiments/v15/cc1/timeline.py --work D/T-B-clean-4m-w4-95100 --workers 4
```
