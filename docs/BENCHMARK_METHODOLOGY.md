# Benchmark methodology

Applies to `vnxdna benchmark`, `vnxdna experiment` and the release gates (`ops/vnxops/benchmarks.py`,
`experiments.py`, `release.py`). The research runners under `research/` keep their own methodology
([BENCHMARKS.md](BENCHMARKS.md)).

## DNA benchmark (`vnxdna benchmark --dna --suite smoke|standard|large`)

* Input: `vnx-dna benchmark generate --size N --seed <suite seed> --pattern mixed` (exact size, reproducible).
* Stages run as **separate CLI processes**: store → encode → decode → restore → extract (random access through the
  DNA index, 1000 bytes at the middle). Wall time per stage; **peak RSS and CPU seconds from `wait4()`** of each stage
  process; `--workers` from the suite, capped at `SAFE_CPU_THREADS`.
* The CLI under test is this checkout's `src/` (`python -m vnxdna`), or another install via `VNXDNA_CLI`
  (the V3 baseline is measured with the pristine `v3.0.0` venv).
* Sizes: 1 KB … 1 GB (decimal). A size above `MAX_BENCHMARK_INPUT`, or whose working set (≈ 8 × input) exceeds the
  disk budget, is reported `SKIPPED` with the reason; it is never attempted.
* Small sizes are dominated by process start-up (about 0.5 s per stage on this host — see the 1 KB rows in
  `docs/releases/V3_BASELINE.md`), so MB/s at 1–100 KB measures start-up, not codec speed. Regression gates compare
  throughput only for inputs ≥ 100 KB.

| Column | Definition |
|---|---|
| compression ratio | input bytes / stored bytes (from the encode report) |
| nt per input byte | DNA bases (all strands incl. ECC and metadata) / input bytes |
| input bits per nt | 8 × input bytes / bases. Counts *original-file* bits after compression, so compressible input can exceed 2; it is not a physical density |
| ECC overhead | parity strands / data strands |
| random access s | wall time of the `extract` stage |
| peak RSS | largest single stage process |

* Recovery rate / failure rate: seeded channel trials (suite `recovery:` block), outcomes as below.
* Results: `/opt/vnx-dna/reports/benchmarks/<suite>/<run>/result.{json,md}` and `latest.json`. Tables are rendered by
  the tool; never edit them by hand.

## Experiment outcomes

| Outcome | Meaning |
|---|---|
| RECOVERED | output SHA-256 equals input SHA-256 |
| FAILED_CLEAN | a stage refused (non-zero exit): the loss was detected |
| WRONG_OUTPUT | restore succeeded but bytes differ: undetected corruption — must be 0; the work directory is kept |

## System benchmark (`vnxdna benchmark --system`, `vnxdna profile`)

The hardware profile: sysbench CPU (1 thread, all threads), memory bandwidth, dd sequential read/write (direct I/O),
sysbench random 16 KiB I/O, gcc/g++/cargo compile times, Python loop, SHA-256, zstd, Cauchy RS and inner RS, a CLI DNA
round trip and container streaming. All niced. Derived limits (with their formulas) are written to
`/opt/vnx-dna/config/hardware-capability.yaml` and drive the governor and benchmark scaling.

## Regression thresholds

From `config/laya/release-gates.yaml`, against `docs/releases/v3-baseline.json`: throughput −15 % max (≥ 100 KB),
peak RSS +25 % max (and > 32 MiB), net bits per base may not drop, recovery rate inside the guarantee 1.0, undetected
corruption 0.

## Model benchmarks (`vnxdna models bench`)

Per local model: load + first-token time, RSS of the Ollama runner, generation and prompt tokens/s (Ollama counters),
5 small coding tasks executed against asserts (network-less, governed, timeout), one tool-call check. Remote tier: one
coding probe with latency and reported cost. Results: `/opt/vnx-dna/reports/model-benchmarks.{json,md}`.
