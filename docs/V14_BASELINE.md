# V14 baseline

Phase 0 record (master directive §2, §20 step 1) of what the V13 release does on the V14 build host, measured before
any V14 change. All channel results are SIMULATED (the VNX-DNA channel simulator); no DNA was synthesised, stored or
sequenced. Evidence category: A (software codec) and D (simulated channel).

## 1. Revisions

| role | revision | checkout |
|---|---|---|
| V13 release (base of V14) | tag `v13.0.0` = `58171b8` | `/root/vnx-dna-lab/v14-base` (detached, pristine) |
| V12 (tier denominator) | `build/v12` = `6bf7749` | `/root/vnx-dna-lab/v13-base` (detached, pristine) |
| V14 tree at measurement time | `build/v14`, no code change from 58171b8 (`src/` and `tests/` identical) | `/root/vnx-dna-lab/v14` |

## 2. Host

x86_64 Intel Xeon Gold 6240 @ 2.60 GHz, 8 logical CPUs (4 cores / 8 threads), ~31 GiB RAM, Linux 6.8.0-146-generic
(Ubuntu 24.04), root. Python 3.12.3, NumPy 2.5.3, gcc 13.3, clang 18, rustc/cargo 1.75. Kernel modules `nbd` and
`ublk_drv` present (not loaded), `CONFIG_IO_URING=y`. No ARM64 toolchain, no Raspberry Pi. Every native kernel and
the V13 engine built in place in all three trees (`python -m vnxdna.native` reports `all_native: true`; V12 has no
engine). Heavy runs used the lab's systemd scope in `vnxdna.slice` (MemoryMax 12 GiB for the suite, 8 GiB for
benchmarks); the 1-minute load is recorded with every row.

## 3. Test suite

| revision | result | time | workers |
|---|---|---|---|
| 58171b8 | **4,454 passed, 4 skipped, 0 failed** | 434.5 s | 6 (pytest-xdist) |

This is the V13 release gate, reproduced. The 4 skips are the 3 opt-in install tests and the CPU-level test. Command:
`pytest -q -o addopts= -p no:cacheprovider -n 6`; log kept in the lab (`results/full-suite-58171b8-20261018-1021.txt`).

## 4. End-to-end decode A/B (V12 vs V13)

`experiments/v14/bench/phase0-ab/` — `bench.py run --scenarios indel-w1,indel-w4,clean-w4`, 4 MiB input, 5 seeds
per scenario, paired per seed, alternating order, one discarded warm-up; ratio = V12 seconds / V13 seconds; 95 % CI
= percentile bootstrap of the median ratio (with n = 5 it equals the min–max range). 45 runs, all SUCCESS, outputs
identical to the input in every tree, 0 false successes. Load 1.5–2.8.

| scenario | V12 median | V13 median | V12/V13 here [95 % CI] | V13 report | reproduced |
|---|---|---|---|---|---|
| indel, 1 worker | 8.921 s | 3.174 s | 2.811x [2.769, 2.968] | 2.885x [2.732, 2.989] | yes (CIs overlap) |
| indel, 4 workers | 2.847 s | 1.659 s | 1.741x [1.688, 2.115] | 1.704x [1.644, 1.770] | yes |
| clean, 4 workers | 1.301 s | 0.582 s | 2.257x [2.010, 2.427] | 2.200x [2.095, 2.372] | yes |

Peak RSS (median per tree): V13 99.9 / 100.9 / 98.8 MiB, V12 105.5 / 415.9 / 381.3 MiB (the three scenarios in
order). The V14 tree (same code) measured V13/V14 = 0.994–1.012, which is this host's A/A band for the V13 engine.

None of the 2x/5x/10x tiers of the V14 brief is claimed by these numbers: they are the V13 starting point.

## 5. Pool-mode stream decode scaling (V13)

`experiments/v14/bench/phase0-pool/` — V13 tree, 1 worker, E MiB in 1 MiB extents, coverage 3 / 1 % substitutions
(seed 7), shuffled pool, 3 reps per E, run alone (load 0.4–1.1). All 9 runs SUCCESS with output equal to the input.

| extents | median | range | x vs previous E | peak RSS |
|---|---|---|---|---|
| 4 | 4.45 s | 4.38–4.49 s | — | 102.6 MiB |
| 8 | 16.83 s | 16.40–17.17 s | 3.78 | 102.0 MiB |
| 16 | 63.69 s | 63.45–64.77 s | 3.78 | 103.4 MiB |

Doubling E multiplies the time by ~3.8 (O(extents x pool): each extent's decode reads the whole pool). This is the
Phase 6 workstream A target. The V13 report's 6.1 s / 98 s came from another harness; the scaling agrees, the
absolute times are not compared.

## 6. Not verified at baseline

| item | status |
|---|---|
| sanitizers (ASan/UBSan, MSan), `cargo test`, fuzz smoke | not re-run on 58171b8; run on the V14 code in P6 |
| web console tests (`npm ci && npm test`) | never run in V13 (no `node_modules`); P6 runs them or records why not |
| the V13 `ab-final` 5.23x | not a V13 result (pre-fix scheduler that skipped distinct strands); never used as a baseline |
