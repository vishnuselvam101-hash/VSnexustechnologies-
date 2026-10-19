# V14 benchmark report (skeleton)

Master directive §20 (performance sequence) and §21; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P9: "codec, I/O,
cache, scheduler, backend and simulator numbers kept separate; benchmarks reproducible from committed commands".

**Status: SKELETON, no numbers.** Written in the P9 preparation pass (base `build/v14` b69e70f). The lab host was
shared with seven other build jobs at the time, so nothing is measured here: every section is filled after P6-P8 are
merged, on a quiet host (§0), from the committed commands below. A number enters this file only together with its
committed results file, commit, host and repetition count.

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

Results: _to fill_ (`experiments/v14/bench/p9-codec/`).

## 2. Logical I/O overhead (category B)

What: VNX-IO request latency (p50/p99) and throughput by request size (4 KiB-8 MiB) and queue depth (1-64), aligned
vs unaligned (read-modify-write), over `MemoryMedia` so the backend costs ~0; the volume's journal fsync cost reported
separately (journal on tmpfs vs on disk).

Commands: _to add_ — a `vnxd-io-bench` binary next to `vnxd-lb-bench` (same JSON-line output, one configuration per
process) driven by `experiments/v14/bench/p9-io/run.py`.

Results: _to fill_.

## 3. Cache (category B)

What: hit/miss latency, hit ratio on fixed access traces (sequential, uniform random, Zipf 0.9/1.2), write-back vs
write-through ack latency and flush cost, eviction rate vs cache quota, read-ahead benefit on sequential traces.

Commands: _to add_ — `vnxd-io-bench --cache none|wt|wb --trace seq|uniform|zipf:<s> --cache-mib N`, same driver.

Results: _to fill_.

## 4. Scheduler / load balancer (category B; modelled backend)

What: queue wait, task latency, throughput, utilisation, memory and contention, single vs multi-worker, under the
`electronic` and `dna-sim` latency models (model inputs). P5 measured this before integration
(experiments/v14/bench/p5-balancer/); P9 re-runs it on the integrated commit.

Commands:

```
cargo build --release --offline --locked --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml
python experiments/v14/bench/p5-balancer/run.py      # vnxd-lb-bench --profile electronic|dna-sim --io-workers N --lb-workers N
```

Results: _to fill_ (`experiments/v14/bench/p9-balancer/`).

## 5. Electronic backend (category C)

What: put/get/sync latency and throughput of the P7 file-image adapter on the host disk, fsync cost, recovery time
after a SIGKILL (journal replay + orphan collection) as a function of journal length and extent count.

Commands: _to add after P7_ — `vnxd-io-bench --media file-image:<dir>`; recovery time from the E2E row R11b's
harness with timing enabled.

Results: _to fill_.

## 6. Simulator end to end (category D; SIMULATED)

What: the whole device over the P7 DNA-simulator adapter: write -> encode -> SIMULATED channel -> decode -> read,
latency per extent, throughput, recovery rate per channel model (the P6 E channel suite), peak RSS. Never compared
with the electronic backend in one figure.

Commands: _to add after P6 E and P7_ — the channel bench suite in `experiments/v14/bench/p6e-channels/` and
`vnxd-io-bench --media dna-sim --channel <model>`.

Results: _to fill_.

## 7. Block device (category B/C; after P8)

What: `fio` sequential/random read/write on `/dev/nbdN` (bs 4 KiB-1 MiB, iodepth 1-32), filesystem-level copy and
fsync latency on ext4, service RSS and CPU; reported separately for the memory, file-image and dna-sim backends.

Commands: _to add after P8_ — `fio` job files in `experiments/v14/bench/p9-blockdev/`, run against a disposable device
vnxd exported (the rules of docs/V14_STATE.md).

Results: _to fill_.

## 8. Memory, recovery, Pi

Peak RSS per configuration (all sections record it); recovery time (§5); Pi numbers only from the hardware (P10/P11).

## 9. Trade-offs and limitations

_To fill after §1-§8._
