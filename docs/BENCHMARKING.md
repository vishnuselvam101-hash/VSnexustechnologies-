# Benchmarking methodology (V4)

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
