# Benchmarking methodology

The first part is the V4 methodology, unchanged and still used by `vnx benchmark`, `vnx sweep` and `vnx experiment`
(now in `vnxdna.benchmark`; the `vnxdna.v4.*` module paths still import). V6 adds a benchmark lab, conformance vectors,
installed-path and kernel benchmarks, and pre-registered paired comparisons; they are described after the V4 sections
below. Labels: SIMULATED (software strands, software channel), MEASURED (time or memory on this host),
PUBLIC-DATA-DERIVED (statistics from another group's public reads). No result here comes from synthesised or
sequenced DNA.

## V4 methodology

All V4 numbers are produced by `vnxdna.v4.bench`, `vnxdna.v4.sweep`, `vnxdna.v4.compare` and
`vnxdna.v4.experiment`, and stored as JSON together with the environment and the resolved configuration. Every
number is a **software measurement on simulated data**.

## Commands

```bash
vnx benchmark --profile safe|balanced|maximum-throughput [--size 1MB --size 10MB] [--output bench.json] [--human]
vnx sweep sweep.json [-o result.json]                  # recovery curve table
vnx experiment run experiments/EXP-0001-substitution/config.json
vnx experiment reproduce experiments/EXP-0001-substitution
vnx experimental codec-compare --trials 200
```

## Metrics

| metric | definition |
|---|---|
| `encode_mb_s` | input MB / (archive + DNA encode seconds) |
| `decode_mb_s` | input MB / decode seconds (reads → verified container) |
| **`recoverable_mb_s`** | input MB / decode seconds **if and only if the decode SUCCEEDED** (verified container + extracted SHA-256 equal to the input), else 0 |
| `recoverable_tb_per_day` | `recoverable_mb_s` × 86,400 / 10⁶ |
| `reads_per_second` | reads in the file / decode seconds |
| stage throughputs | MB/s (compression, encryption, hashing, ECC, DNA encoding/mapping), Mbases/s (validation, simulation), reads/s or frames/s (sync, RS) |
| `nt_per_input_byte` | all nucleotides written (superblock, parity, headers, markers) / input bytes |
| `peak_rss_mb` | max(`ru_maxrss` of the benchmark process, of its reaped worker processes): measured in a fresh process per case (`bench.isolated`, spawn), so earlier cases do not inflate it. The worker figure is the largest single worker, not a sum |

Bases per second alone is not the figure of merit. Recoverable bytes per second is: a fast decoder that fails
contributes 0.

## Outcomes (never filtered)

`SUCCESS` (verified), `PARTIAL` (some files individually verified), `DECODER_FAILURE` (insufficient information),
`INTEGRITY_FAILURE` (verification failed; must stay 0), `ADDRESS_FAILURE`, `ERROR`. Every trial is stored in
`results.json`, including failures.

## Fairness rules for comparisons

* Same input bytes (generated from the same pattern, size and seed).
* Same hardware, run sequentially (no concurrent heavy jobs while timing).
* Same channel implementation (`vnxdna.v4.channel`), parameters and per-trial seeds for every system compared.
* Same success definition (output SHA-256 equal to the input SHA-256).
* Same redundancy budget where meaningful (EXP-0007: 25 % for every outer code). Where budgets differ (V3 vs V4
  default profiles in EXP-0010), the nucleotide cost per byte is reported next to every success rate.
* No per-system tuning to a channel: V3 runs with its defaults and, at coverage 1, also with its best documented
  repair options; V4 runs with its default profile.
* Harness errors abort the run instead of being counted against a system (lesson L9 in the engineering log).

## Sizes and resources

Sweeps use a 256 KiB random input (about 8,000 strands for v4-balanced) unless stated, with 5–10 trials per point.
Larger inputs are used for scaling (16 MiB) and memory (1 MiB – 1 GiB). Trials run in parallel processes, each
decoding with one worker. Scaling runs measure one system at a time with 1/2/4/8 workers.

## Cost model (theoretical; not part of the measurements)

Physical cost per recoverable byte would be `nt_per_input_byte × price per synthesised nt + sequencing cost per read ×
reads per strand / bytes per strand`. V4 reports the software factors (nt per byte, reads per strand needed for a
target success rate). It does **not** assume any synthesis or sequencing price; any such figure would be an assumption,
not a measurement.

## V6 additions

### Benchmark lab (external codecs, SIMULATED)

`benchmarks/competitors/lab/` runs VNX-DNA as an external codec inside the ETH `dt4dds-benchmark` harness next to
public codecs (DNA-RS, DNA Fountain, DNA-Aeon), on identical input files, the same error generator, the same seeds and
the same success check. Stage B0 is complete: 280 trials, every one stored and none filtered
(`benchmarks/competitors/lab/results/b0/trials_*.jsonl`, aggregates `aggregate_*.md`, provenance `provenance.json`).
Participants, pinned upstream commits and licences, exclusions, VNX-DNA strand profiles, caveats, tables and the
reproduction commands are in [benchmarks/competitors/lab/README.md](../benchmarks/competitors/lab/README.md); the plan
is [VNX_BENCHMARK_PLAN.md](VNX_BENCHMARK_PLAN.md).

Rules the lab adds to the fairness rules above:

* Third-party tools live outside this repository, at pinned SHAs, as separate processes. No third-party code is copied
  into or imported by VNX-DNA, and a test asserts it (`tests/bench_lab/test_lab.py`). Proprietary, non-commercial and
  unlicensed systems are excluded.
* Success is byte-exact: output SHA-256 equals input SHA-256. The harness's own prefix check is also recorded, because it
  ignores trailing bytes. A **false SUCCESS** is a run whose steps all exit 0 and which writes a different output;
  it is counted per codec and never filtered.
* VNX-DNA strand profiles are chosen for matched rate (about 1.0 and 1.5 bit/nt); the achieved rate is reported next to
  every success rate, and where VNX-DNA cannot be matched (5 kB inputs, because of the fixed container cost) the
  table says so.
* Timing is reported with the host load average; B0 timings were taken above the plan's load limit and are indicative to
  within a factor of about 2.

Limits of B0, stated in the lab README: 3 seeds per point (wide Wilson intervals); the harness's i.i.d. channel (53/45/2 %
substitution/deletion/insertion composition), not a platform model; not the published ETH protocol (30 trials, logistic
threshold, 1 h limit), so results are not entered in `benchmarks/competitors/records.json`; VNX-DNA receives raw reads
while the other codecs receive the harness's default read set. Stage B1 (HEDGES, YYC, the published protocol, the
Aeon demo limit, stronger clusterers) has not been run.

### Conformance vectors (`vnx conformance`)

`tests/conformance/` holds 222 vectors (129 positive, 93 negative) listed in `tests/conformance/index.json`
(schema `vnx.conformance-index/1`, evidence class `SYNTHETIC SOFTWARE TEST`): GF(256) and Reed-Solomon, CRC and scrambler,
mapping and markers, frame 4, superblocks 1 and 2, the outer code, strand order, the container, AEAD, end-to-end
decodes of the stored `v4_0`, `v5_0` and `v6_0` fixtures, and the stable error codes. A packaged subset of 22 vectors
ships inside the wheel (`src/vnxdna/conformance/vectors/`).

```bash
vnx conformance                      # full set in a source checkout, packaged subset otherwise
vnx conformance --backend native     # native kernels; also: reference, auto
vnx conformance --select frame4.build.balanced.001
```

Exit 0 means every selected vector gave exactly its expected output or its expected error code and exit code; exit 1
means at least one did not. Conformance proves that a build reproduces the specified bytes and codes. It says nothing
about channel performance. Layout: spec §6. The experiment manifest (`vnx.experiment/1`) and `docs/CONFORMANCE.md` of
the plan are not implemented ([V6_DEFERRED.md](V6_DEFERRED.md) section 5).

### Kernel and installed-path benchmarks (MEASURED)

* `benchmarks/v6/native_packaging/`: a plain `pip install` before and after the install built all three native
  kernels, same reads, one worker. Decode of a 4 MiB noisy input: 10.8 s to 5.3 s (2.04x, rounds 2 and 3; host
  shared with other work; one machine and one workload) - `benchmarks/v6/native_packaging/README.md`.
* `benchmarks/v6/native_reads/`, `benchmarks/v6/native_rs/`: per-kernel benchmarks, differential stress tests and
  sanitizer runs. The AVX-512 path measured 0.94 times AVX2 (`benchmarks/v6/native_rs/results/bench.json`), so AVX2 is
  selected automatically.
* Peak memory: a child process records its own `VmHWM`. `wait4` `ru_maxrss` of an exec'd child inherits the parent's
  high-water mark on Linux and must not be used for a child that is smaller than the harness
  (`experiments/v6/align-band/README.md`, "Deviations from the pre-registration"). The V4 fresh-process method above
  (spawn per case) is not affected.

### Pre-registered paired comparisons (SIMULATED)

Phase 4 and the retry-band job compare a changed decoder with the stock decoder on the same reads. The design, criteria and
analysis code are committed before the run (`experiments/v6/phase4/PREREG.md`, `experiments/v6/align-band/PREREG.md`).
Each run stores every trial, uses seeds disjoint from the diagnostic seeds, reports Wilson 95 % intervals per arm and
Newcombe intervals for paired differences, and applies a written decision rule. Both V6 comparisons ended in
"keep opt-in" (quality-weighted consensus: efficacy criterion REJECT; retry band: efficacy and memory criteria REJECT).
Deviations from a pre-registration are listed in the experiment README, not applied silently.

| Experiment | Trials / decodes | False SUCCESS | Where |
|---|---|---|---|
| P4-EXP-01 failure taxonomy | 770 trials, 1,450 decodes | 0 | `experiments/v6/phase4/P4-EXP-01-failure-taxonomy/` |
| P4-EXP-02 quality-weighted consensus | 975 paired trials, 1,965 decodes | 0 | `experiments/v6/phase4/P4-EXP-02-qw-consensus/` |
| AB-EXP-01 retry band | 935 trials, 1,910 decodes | 0 | `experiments/v6/align-band/` |
| Benchmark lab B0 (VNX-DNA only) | 157 trials | 0 | `benchmarks/competitors/lab/results/b0/` |

`experiments/v6/phase4/P4-EXP-03-cnr-ids/` is PUBLIC-DATA-DERIVED (per-read error statistics of a public nanopore
dataset), not a decoder benchmark.
