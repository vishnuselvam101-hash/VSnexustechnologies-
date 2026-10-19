# V14 benchmark report

Master directive §20 (performance sequence) and §21; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P9: "codec, I/O,
cache, scheduler, backend and simulator numbers kept separate; benchmarks reproducible from committed commands".

**Status:** §1 filled from the full P6E suite run at `build/v14` 584763f (2026-10-19, results in
`experiments/v14/bench/p6e-channels/full/`); §4 and §7 point to the phase measurements committed with each phase (no
new runs); §2, §3, §5 and §6 are NOT MEASURED; §9 lists the trade-offs. All channel numbers are SIMULATED.

## 0. Rules for every section

* **Order (§20):** 1 verified V13 baseline (docs/V14_BASELINE.md) -> 2 complete integrated path -> 3 correctness and
  durability (the E2E matrix of [V14_VALIDATION_PLAN.md](V14_VALIDATION_PLAN.md) green) -> 4 these benchmarks -> 5
  profile -> 6 optimise measured bottlenecks -> 7 re-run correctness/compatibility/fault tests -> 8 target hardware ->
  9 trade-offs. Section 4 numbers are not taken before step 3 passes on the same commit.
* **Separation:** codec, logical I/O, cache, scheduler, electronic backend, simulator end to end and physical DNA
  are separate sections; a number from one is never combined with or quoted as another. Physical DNA: no section
  exists, because no physical experiment exists (evidence category E is not claimed by V14).
* **Evidence category per section:** A software codec; B software storage device (modelled backend latencies are model
  inputs, not measurements of a medium); C electronic-backend persistence; D SIMULATED DNA channel.
* **Host:** record CPU model, cores, RAM, kernel, governor/slice limits, other load (`uptime`), rustc and Python
  versions, commit SHA and dirty state. Run under `VNX_LAB_GOVERN=systemd` (one slice, fixed CPUQuota) with no other
  heavy job; a contended run is labelled and never compared with a quiet one.
* **Statistics:** fixed workloads and seeds, at least 5 repetitions (one discarded warm-up), raw per-run rows kept,
  median with min-max (n <= 5) or percentile-bootstrap 95 % CI; comparisons are paired per seed. No target is chosen
  after seeing the data; a tier (2x/5x/10x) is claimed only when the CI lower bound clears it.
* **Pi:** desktop numbers are never Pi numbers (docs/V14_RASPBERRY_PI_PROFILE.md, P10/P11).

## 1. Codec (category A; SIMULATED inputs)

What: encode and decode throughput and peak RSS of the codec alone (no device), clean and noisy SIMULATED reads,
shuffled-pool decode scaling with the number of extents, pass-1/pass-2 split. Baseline: V13 58171b8 and V12 6bf7749
(tier denominator).

Commands:

```
BASE_TREE=/root/vnx-dna-lab/v13-base V13_TREE=/root/vnx-dna-lab/v14-base PYTHONPATH=src \
    python experiments/v14/bench/bench.py run          # A/B/C end-to-end codec scenarios, paired per seed
PYTHONPATH=src python experiments/v14/bench/bench.py summary
PYTHONPATH=src python experiments/v14/bench/pool_bench.py run --trees v13,v14 --extents 4,8,16,32
PYTHONPATH=src python experiments/v14/bench/pool_bench.py summary
```

Results (full P6E suite, `experiments/v14/bench/p6e-channels/full/`, run 2026-10-19 07:29–08:5x IST at 584763f;
host: Intel Xeon Gold 6240, 8 vCPU, 31 GB RAM, Linux 6.8, Python 3.12; configurations v12 = 6bf7749, v13 = 58171b8,
v14 = 584763f, v14aa = the same v14 tree again as the in-run A/A control). Ratios are time(denominator)/time(v14),
median with percentile-bootstrap 95 % CI over paired seeds; "faster/slower beyond noise" when the CI excludes 1.

**Phase A: timing-valid (each workload alone on an otherwise idle host).**

| workload | n | v12/v14 | v13/v14 | v14/v14aa (A/A) | V14 target | verdict |
|---|---|---|---|---|---|---|
| T-A indel 4 MiB, 1 worker | 5 | **2.848** [2.627, 2.899] | 0.990 [0.94, 1.007] | 1.006 [0.951, 1.031] | ≥ 5x (CI lower bound) | **MISSED** |
| T-B clean 4 MiB, 4 workers | 5 | **2.209** [2.111, 2.326] | 0.998 [0.929, 1.047] | 1.038 [0.953, 1.098] | ≥ 3x (CI lower bound) | **MISSED** |
| T-E encode 4 MiB, 1 worker | 5 | 0.990 [0.978, 1.034] | 1.015 [1.0, 1.027] | 1.015 [0.904, 1.027] | no material regression | met (within noise) |
| T-E encode 4 MiB, 4 workers | 5 | 0.996 [0.982, 1.008] | 0.992 [0.979, 1.03] | 1.007 [0.971, 1.015] | no material regression | met (within noise) |
| indel 16 MiB, 4 workers | 3 | 1.615 [1.529, 1.649] | **0.948 [0.944, 0.981]** | 1.011 [1.003, 1.022] | — | v14 **slower than v13** beyond noise (~5 %) |
| substitution 64 MiB, 4 workers | 3 | 1.587 [1.524, 1.627] | **0.977 [0.905, 0.981]** | 1.011 [0.91, 1.02] | — | v14 **slower than v13** beyond noise (~2 %) |

Reading: V14 keeps all of V13's single-archive decode speed (2.2–2.9x over V12) but adds none on these workloads;
the V14 gains are on paths these workloads do not exercise (P6-A linear shuffled-pool decode 3.3–25x vs V13 at
E = 4–32, `experiments/v14/bench/p6a-pool/`; P6-B parallel pass 2 1.18x at 4 workers on pools,
`experiments/v14/bench/p6b-profile/`; P6-C no v13 fallback on zero/repetitive payloads, 4 MiB w4 RSS 489.8 →
114.2 MiB, `experiments/v14/bench/p6c-scheduler/`). The ~5 % (indel-16m-w4) and ~2 % (substitution-64m-w4) regressions vs V13 are consistent with
the per-read bookkeeping P6-C measured on the V13-rule path (docs/V14_SCHEDULER.md §9.4); it is reported, not
explained away. Both primary targets (5x / 3x) are **missed**; the remaining bottleneck is pass 1 in the native
engine, which runs serially (V15 CC-1).

**Phase B: correctness campaign (channel, payload, adversarial groups run three at a time; timings CONTENDED and
not used for any speed claim).**

| group | runs | false successes | wrong archive | crashes | expectation misses | versions disagree |
|---|---|---|---|---|---|---|
| channel (11 workloads) | 280 | 0 | 0 | 0 | 48 | 2 |
| payload (5) | 120 | 0 | 0 | 0 | 0 | 0 |
| adversarial (8) | 96 | 0 | 0 | 0 | 0 | 0 |
| target + large (phase A) | 104 | 0 | 0 | 0 | 0 | 0 |
| **total** | **600** | **0** | **0** | **0** | **48** | **2** |

* Expectation misses are preregistered "must succeed" workloads that FAIL in every version at this size:
  `mixed-4m-w4` and `mixed-harsh-model-1m-w4` (some seeds), `dropout-uneven`, `dropout10-model` (known
  preregistration error, reported in P6E), `lowcov2`, `lowcov-edge-indel`: channels beyond the outer code's capacity.
  All are explicit FAILURE, never wrong bytes. "Versions disagree" = V12 failed where V13/V14 succeeded.
* Payload group: every structured payload (zeros, repetitive, binary, text) decodes in all versions; with P6-C,
  V14 decodes zero/repetitive payloads on the engine without the v13 fallback.
* Adversarial group: foreign archives, truncated/malformed/empty pools are refused with typed errors; junk reads,
  PCR-jackpot duplicates and poisoned consensus copies decode to the correct bytes.
* Known cost found by this run: on undecodable inputs V14 takes ~2x as long as V13 to report FAILURE (e.g.
  mixed-4m-w4 seed 98001: v14 17.95 s vs v13 9.64 s) because of the P6-C strand retry rung. V15 item: skip the
  retry when pass 1 already shows the input is beyond capacity.
* Deviation from the preregistered protocol: phase B ran three groups in parallel to save time; its timings are
  labelled contended and excluded from every ratio above. Correctness results are unaffected.

## 2. Logical I/O overhead (category B)

What: VNX-IO request latency (p50/p99) and throughput by request size (4 KiB-8 MiB) and queue depth (1-64), aligned
vs unaligned (read-modify-write), over `MemoryMedia` so the backend costs ~0; the volume's journal fsync cost reported
separately (journal on tmpfs vs on disk).

Commands: none. **NOT MEASURED**: no benchmark tool exists for this layer (a `vnxd-io-bench` binary is a V15 item,
docs/V15_FINALIZATION_PLAN.md §12).

Results: no dedicated V14 measurement was run. Functional behaviour is covered by the P9 E2E matrix (43/44 rows pass). The block-device throughput in §7 is the end-to-end figure; logical-I/O overhead is not separated. **NOT MEASURED** (listed as a V15 item).

## 3. Cache (category B)

What: hit/miss latency, hit ratio on fixed access traces (sequential, uniform random, Zipf 0.9/1.2), write-back vs
write-through ack latency and flush cost, eviction rate vs cache quota, read-ahead benefit on sequential traces.

Commands: none. **NOT MEASURED** (the planned `vnxd-io-bench` cache traces are a V15 item).

Results: cache effects are visible only in the §7 block-device figures (cache none vs write-through vs write-back). No isolated cache benchmark was run. **NOT MEASURED** separately.

## 4. Scheduler / load balancer (category B; modelled backend)

What: queue wait, task latency, throughput, utilisation, memory and contention, single vs multi-worker, under the
`electronic` and `dna-sim` latency models (model inputs). P5 measured this before integration
(experiments/v14/bench/p5-balancer/); P9 re-runs it on the integrated commit.

Commands:

```
cargo build --release --offline --locked --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml
python experiments/v14/bench/p5-balancer/run.py      # vnxd-lb-bench --profile electronic|dna-sim --io-workers N --lb-workers N
```

Results: P5 load-balancer measurements, committed with Phase 5 at `experiments/v14/bench/p5-balancer/` (see docs/V14_PHASE5_REPORT.md); not re-run in P9.

## 5. Electronic backend (category C)

What: put/get/sync latency and throughput of the P7 file-image adapter on the host disk, fsync cost, recovery time
after a SIGKILL (journal replay + orphan collection) as a function of journal length and extent count.

Commands: none. **NOT MEASURED** (`vnxd-io-bench --media file-image` and timed recovery are V15 items).

Results: no isolated electronic-backend benchmark; the P7 image backend is covered functionally (conformance + restart tests, docs/V14_PHASE7_REPORT.md). **NOT MEASURED**.

## 6. Simulator end to end (category D; SIMULATED)

What: the whole device over the P7 DNA-simulator adapter: write -> encode -> SIMULATED channel -> decode -> read,
latency per extent, throughput, recovery rate per channel model (the P6 E channel suite), peak RSS. Never compared
with the electronic backend in one figure.

Commands: none for the device path. **NOT MEASURED** (`vnxd-io-bench --media dna-sim` is a V15 item). The codec-only
channel suite is §1 (`experiments/v14/bench/p6e-channels/suite.py`).

Results: the DNA simulator adapter (P7 dnasim) is covered by conformance and round-trip tests over the real codec; no end-to-end simulator timing was taken. **NOT MEASURED**.

## 7. Block device (category B/C; after P8)

What: `fio` sequential/random read/write on `/dev/nbdN` (bs 4 KiB-1 MiB, iodepth 1-32), filesystem-level copy and
fsync latency on ext4, service RSS and CPU; reported separately for the memory, file-image and dna-sim backends.

Commands (root, `nbd` module, release build of `vnxd`; the P8 harness, not `fio`; no `fio` runs were made):
`python experiments/v14/device/p8-nbd/run.py --work <scratch dir> --out experiments/v14/device/p8-nbd/results.json`.
Only the file-image backend was measured; memory and dna-sim backends are NOT MEASURED.

Results (P8, `experiments/v14/device/p8-nbd/results.json`, measured at 86c16c0 on this host, 64 MiB raw O_DIRECT, median MiB/s; these measure this host's disk and page cache through the V14 stack, not any DNA medium): cache none write 52.6 / read 306.3; write-through 47.4 / 926.4; write-back 58.3 / 874.1.

## 8. Memory, recovery, Pi

Peak RSS per configuration (all sections record it); recovery time (§5); Pi numbers only from the hardware (P10/P11).

## 9. Trade-offs and limitations

* **Targets missed.** Both primary V14 speed targets are missed: T-A indel 4 MiB w1 v12/v14 2.848 [2.627, 2.899]
  (target ≥ 5x) and T-B clean 4 MiB w4 2.209 [2.111, 2.326] (target ≥ 3x). Those speedups over V12 were already
  delivered by V13; V14 adds none to them.
* **V14 = V13 on single-archive decode**, with two small regressions beyond noise at 4 workers: indel 16 MiB 0.948
  [0.944, 0.981] (~5 % slower) and substitution 64 MiB 0.977 [0.905, 0.981] (~2 % slower). The likely cause is the
  per-read bookkeeping on the V13-rule path (docs/V14_SCHEDULER.md §9.4); it was not profiled to closure.
* **Time to FAILURE doubles on undecodable inputs.** The P6-C strand retry rung re-runs the engine once before the
  V13-path fallback, so an input beyond the outer code's capacity takes ~2x as long to be refused (mixed-4m-w4 seed
  98001: v14 17.95 s vs v13 9.64 s). Correctness is unaffected (explicit FAILURE, never wrong bytes).
* **P6-B is pool-only.** Parallel pass 2 gives 1.18x end to end at 4 workers on shuffled pools (E = 8 and 16) and
  nothing on single archives, where pass 1 (serial grouping in the native engine) dominates.
* **The memory win is narrow.** Peak RSS falls from 489.8 to 114.2 MiB (4 MiB, w4) only on zero/repetitive payloads,
  which no longer fall back to the v13 path; on ordinary payloads RSS is unchanged (~100 MiB).
* **Gains are on pools.** P6-A linear shuffled-pool decode (3.3-25x vs V13 at E = 4-32) is the main measured V14
  codec gain; its n is small (2 per point, 1 at E = 32).
* **Not measured:** §2 logical I/O, §3 cache, §5 electronic backend, §6 simulator end to end; §4 was not re-run on the
  integrated commit; §7 covers one backend on one host's disk. No Pi numbers (no hardware). No physical DNA numbers
  (no physical experiment exists).
* **Protocol deviation:** §1 phase B ran three groups in parallel; its timings are excluded from every ratio.
