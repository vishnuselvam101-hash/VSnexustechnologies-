# Phase 6 workstream B: stage profile + parallel per-extent pass 2 (SIMULATED)

Every input here is produced by the channel simulator. These are software codec timings on the lab host (x86_64, 8 vCPU,
Linux 6.8), not physical DNA measurements.

## 1. Baseline stage profile (b69e70f, idle host, load ~0.1)

`profile_stages.py` (cProfile + the decoder's own stage timers), one decode per process. Files `baseline-profile-*.json`.

| workload | wall s | layout probe | pass 1 (native engine) | partition | pass 2 | verify |
|---|---|---|---|---|---|---|
| pool E=8 (8 x 1 MiB, cov 3, 1 % sub, seed 7), w1 | 2.56 | 0.14 | 1.91 (2.06 incl. probe) | 0.04 | 0.40 (sum over 8 extents) | in pass 2 |
| single 4 MiB archive, clean cov 3, w1 | 0.95 | 0.11 | 0.59 | - | 0.12 | 0.026 |
| single 4 MiB archive, clean cov 3, w4 | 0.69 | 0.11 | 0.32 | - | 0.12 | 0.026 |
| single 4 MiB archive, indel cov 5, w1 | 3.15 | 0.12 | 2.60 | - | 0.28 | 0.014 |

(Under cProfile; the 4 MiB inputs follow the experiments/arch/compare.py recipe, seed 95100, channel seeds 95107 / 95507.)

Findings:
- Pass 1 dominates, and it already runs in the native (Rust) streaming engine. Its internal counters: attempt > prep > group. The engine's grouping (union-find over content windows) is serial: ns_group 0.09 s (w1) / 0.08 s (w4) clean and 0.24 s indel w1 per 4 MiB (`engine-stages-dd7de23.jsonl`; it does not shrink with threads). Making it parallel would need an exact deterministic redesign of the union-find. That is deferred and not attempted here.
- Pass 2 is ~15 % of wall. It is already NumPy-vectorised (`_fast_bucket`, `_resolve_vectorised`) plus the native RS decoder. No single function exceeds ~0.07 s of own time per decode, so a new C pass-2 kernel is **not justified**, and none was added.
- The layout probe costs ~0.08-0.09 s per decode regardless of input size: sample ~0.03, frame heads 0.012, candidate scoring ~0.045 (`probe-stages-dd7de23.jsonl`). Frame heads are computed even when they go unused, but the 0.012 s is below end-to-end noise, so this was left unchanged.
- Each pooled extent's bytes go through five SHA-256 passes (layered integrity checks, ~2.5 ms/MiB each, ~4 % of wall). They are kept deliberately.
- In the linear pool path (Phase 6A), the per-extent pass 2 ran serially after a parallel pass 1. At w4 it was ~23 % of wall: the Amdahl term this change targets.

## 2. Change: per-extent pass 2 in parallel worker processes (pool path, workers > 1)

`vnxdna.pipeline.stream._ParallelPass2` is used when workers > 1, more than one extent is pooled and every extent is pooled. Each extent's `PoolDecoder.decode` then runs in up to `workers` forked processes (at most `workers` tasks in flight). The parent takes the results in manifest order and re-checks length + SHA-256 against the manifest before writing a byte.
- The pool comes from `vnxdna.core.workers.process_pool` (moved there from `vnxdna.v2.workers`, which re-exports it).
  - Workers ignore SIGINT and exit when their parent dies.
  - An interrupt (Ctrl-C, or SIGTERM/SIGHUP through the CLI handler) kills them instead of joining the pool, so it cannot hang.
- A failed extent (a normal noisy-read outcome) pauses the pool around the caller's whole-pool `decode_extent` fallback. In-flight results are kept, the fallback never forks next to the pool's manager thread, and the pool restarts for the next extent.
- A pool that cannot start (fork failure, no "fork" start method) or breaks (a worker died, also between results) is given up: its unfinished extents and all later ones run serially in the parent through the same `PoolDecoder.decode`.
- w1, and mixed by-range/pooled streams, keep the serial loop unchanged.
- Report fields:
  - `pool_pass2_seconds` = the sum of per-extent pass-2 times (with workers: the worker tasks' times, not wall);
  - `pool_pass2_task_extents` (parallel path only) = extents decoded by a worker process.
- The Phase 6A Python pass-1 pool in `PoolDecoder` now also uses `process_pool` (with the same interrupt handling).
- Not changed, and noted: the V12-era pass-1 pool in `_decode_reads` (the non-engine Python path) still builds a plain `ProcessPoolExecutor`. It is outside this workstream.

Output bytes and stream reports are identical to the serial path. Failure/interrupt behaviour is covered by tests/v14/test_pool_parallel.py:
- a delayed worker crash after a consumed result;
- fork failure and missing-context injection;
- repeated fallbacks keeping the pool;
- SIGINT, CLI-SIGTERM and plain SIGTERM leaving no workers, and (except plain SIGTERM, where no Python cleanup can run on any path) no work directories.

## 3. Final A/B: build/v14 dd7de23 (base) vs this change (new)

(Measured on the first version of this change, before the review fixes of section 2. Section 3b re-checks the final code.)

Method: `ab_bench.py` with `BASE_SRC` = dd7de23's src plus the same freshly built native libraries. All kernels are native; `python -m vnxdna.native` reports all_native true and nothing stale.
- One decode per process. Paired per rep, with the order alternating; n = 9 per cell.
- Host idle except one capped (1 core, nice 19) cross-build; load1 1.2-2.0 during the runs. OPENBLAS_NUM_THREADS=1 for both configurations.
- Ratio = base / new (> 1 = new faster). 95 % CI = percentile bootstrap of the median paired ratio (10000 resamples, seed 11).
- "pass 2 wall" = decode seconds - pass-1 - partition seconds, from the decoder's report.
- Raw rows: `final-ab-w1w2w4-n9.jsonl`, `final-aa-w2w4-n9.jsonl`; summary: `final-summary.json`. Every output matched the input SHA-256.

End to end:

| pool | workers | base median s | new median s | ratio median | 95 % CI |
|---|---|---|---|---|---|
| E=8  | 1 | 2.542 | 2.506 | 1.010 | [0.994, 1.035] |
| E=8  | 2 | 1.919 | 1.786 | 1.086 | [1.065, 1.150] |
| E=8  | 4 | 1.660 | 1.418 | 1.184 | [1.138, 1.203] |
| E=16 | 1 | 4.951 | 4.900 | 1.010 | [0.951, 1.051] |
| E=16 | 2 | 3.711 | 3.379 | 1.098 | [1.065, 1.117] |
| E=16 | 4 | 3.059 | 2.596 | 1.184 | [1.129, 1.211] |

Pass-2 component (wall the caller waits for after pass 1 + partition):

| pool | workers | base median s | new median s | ratio median | 95 % CI |
|---|---|---|---|---|---|
| E=8  | 1 | 0.390 | 0.391 | 1.005 | [0.985, 1.011] |
| E=8  | 2 | 0.398 | 0.268 | 1.499 | [1.358, 1.647] |
| E=8  | 4 | 0.400 | 0.182 | 2.238 | [2.046, 2.400] |
| E=16 | 1 | 0.802 | 0.784 | 1.023 | [0.965, 1.051] |
| E=16 | 2 | 0.793 | 0.483 | 1.643 | [1.590, 1.666] |
| E=16 | 4 | 0.786 | 0.314 | 2.530 | [2.304, 2.749] |

A/A control (base vs base, same protocol), end to end: E=8 w2 0.997 [0.979, 1.058]; E=8 w4 0.981 [0.923, 1.025];
E=16 w2 0.987 [0.965, 1.029]; E=16 w4 0.976 [0.894, 1.011]. Every A/A CI includes 1. Every w2/w4 A/B CI lower bound
(≥ 1.065) lies above every A/A CI upper bound (≤ 1.058), so the capped background build does not explain the gain.

Decision: **kept**. A 2.2-2.5x faster pass-2 component (w4) gives a reproducible **1.18x** end-to-end gain at w4 and
1.09-1.10x at w2. Pass 1 is unchanged and remains the limit. No change at w1, as intended.

Memory: the parent's peak RSS is unchanged (~102-103 MiB). Each worker process peaks at ≤ 50 MiB (`maxrss_child_mib` is the largest child, not a sum), so w4 adds at most ~4 x 50 MiB of RSS, part of it shared copy-on-write pages.

## 3b. Re-check after the review fixes (n = 5, w4, same protocol)

`recheck-ab-w4-n5.jsonl` / `recheck-aa-w4-n5.jsonl` (load1 3-4). With n = 5 the bootstrap CI equals the min-max range.

| pool | base s | new s | end-to-end ratio [CI] | pass-2 ratio [CI] | A/A end-to-end [CI] |
|---|---|---|---|---|---|
| E=8, w4  | 1.571 | 1.312 | 1.202 [1.163, 1.884] | 2.141 [2.009, 2.239] | 1.018 [0.945, 1.067] |
| E=16, w4 | 2.925 | 2.453 | 1.194 [1.169, 1.858] | 2.484 [2.302, 2.660] | 0.988 [0.967, 1.007] |

The gain holds: both lower bounds lie above the A/A upper bounds.

## 4. Earlier runs (superseded)

`ab-w1w2w4-n5.jsonl`, `ab-w4-n9.jsonl`, `aa-w4-n9.jsonl` and `rss-w4-e16.jsonl` were taken at b69e70f under a host load of 6-20 from other sessions. They point the same way, but the end-to-end CIs are wide; they are kept for provenance only.

## Reproduce

```
PYTHONPATH=src python experiments/v14/bench/p6b-profile/profile_stages.py pool --reads P/r.fastq --manifest P/s.fasta.stream.json --out O --want-sha <sha of P/in.bin>
BASE_SRC=<base tree>/src PYTHONPATH=src python experiments/v14/bench/p6b-profile/ab_bench.py --pool P8 --pool P16 --workers 1,2,4 --reps 9 --out ab.jsonl [--aa]
python experiments/v14/bench/p6b-profile/summarize.py ab.jsonl
```
Pools: `pool_bench.py`'s `_prepare` (E x 1 MiB random data seed 2000+E, extents 1 MiB, coverage 3, 1 % substitutions, seed 7).
