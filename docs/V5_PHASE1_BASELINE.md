# VNX-DNA V5 — Phase 1: V4 freeze, baseline and first bottleneck

All results are **SIMULATED**: software-generated strands passed through the V4 channel simulator. No DNA was
synthesised or sequenced. Raw numbers are in `benchmarks/v5/baseline-v4/*.json`, produced by
`benchmarks/v5/phase1_baseline.py`. Each file carries a full provenance block (`benchmarks/v5/provenance.py`).

## 1. Freeze

| check | result |
|---|---|
| V4 release tag | `v4.0.0`, annotated, tagger vishnuselvam101-hash |
| release commit | `a358ae8` (merge of PR #5); equal to `origin/main` on 2026-10-03 |
| V5 branch | `feature/vnx-dna-v5`, created from `v4.0.0`; V4 history is not rewritten |
| full V4 suite | **723 collected, 723 passed** (4 min 55 s, 8 logical CPUs) |
| V3 regression | the 601 V3 tests are part of the 723 and pass; `src/` and `tests/` are byte-identical to `v4.0.0` |
| V4 artefacts | `experiments/EXP-0001` … `EXP-0016`, `docs/V4_*` and `benchmarks/baseline/v3/` are unchanged |

**Control group.** Every Phase 1 measurement ran the unmodified V4 code: commit under test `a358ae8`, which is
also the `v4_control_commit`, with a clean worktree.

**Environment.** Intel Xeon Gold 6240 (8 logical CPUs, AVX2 and AVX-512), 31 GiB RAM, Linux 6.8, CPU only.
Python 3.12.3, numpy 2.5.3, gcc 13.3.0, rustc 1.75.0.

## 2. Where the decode time goes

Single-worker noisy decode under cProfile. Input: 4 MiB random (seed 42), profile v4-balanced, EXP-0011 channel
(0.2 % sub, 0.05 % ins, 0.05 % del, 2 % dropout, Poisson coverage 3, channel seed 1011). The result was SUCCESS.
The output container SHA-256 equals the input's.

| stage (cumulative) | seconds | share of decode |
|---|---|---|
| **marker alignment `TemplateAligner.project`** | **20.25** | **58 %** |
| of which: DP fill | 12.3 | 35 % |
| of which: traceback | 7.8 | 22 % |
| inner RS decode (`rs_fast`) | 4.8 | 14 % |
| read parsing (`reads.iter_reads`) | 5.2 | 15 % |
| pass 2 (consensus, snapping, outer decode, verify) | 1.7 | 5 % |
| total (profiled) | 34.9 | 100 % |

Only **26 % of reads** (100,484 of 386,490) take the sync path, yet they account for **58 % of decode time**. The
other 74 % are accepted on the fast path (exact length, CRC first).

**First bottleneck: the marker-template DP and traceback** (`src/vnxdna/v4/sync.py`). This confirms V4 §17.1. The
DP is vectorised over reads, but its Python loop runs over all 313 template positions with about 15 NumPy
temporaries per step. The traceback is a per-step Python loop over the active reads.

**Amdahl bound (a projection, not a result).** Removing the alignment cost entirely would cut single-worker noisy
decode by about 58 %, at most about 2.4×. Read parsing (15 %) and inner RS (14 %) are next in line.

## 3. Aligner micro-benchmark (the golden reference)

The micro-benchmark used 4,096 real V4 strands (random payloads, default constraints) at coverage 1 and band 6.
Times are the best of 3 runs on 1 core.

| layout | channel | reads/s | µs/read | Mbases/s | traceback share | peak memory / 2,048 reads |
|---|---|---|---|---|---|---|
| 178 nt | clean | 16,334 | 61 | 2.91 | 29 % | 10.1 MB |
| 178 nt | 0.5 % ins + 0.5 % del | 14,897 | 67 | 2.65 | 32 % | 10.0 MB |
| **313 nt (default)** | clean | 9,284 | 108 | 2.91 | 29 % | 16.8 MB |
| 313 nt | 0.5 % sub | 9,357 | 107 | 2.93 | 28 % | 16.8 MB |
| 313 nt | 0.1 % + 0.1 % | 8,871 | 113 | 2.78 | 31 % | 16.7 MB |
| 313 nt | 0.5 % + 0.5 % | 8,503 | 118 | 2.66 | 33 % | 16.7 MB |
| 313 nt | 1 % + 1 % | 7,152 | 140 | 2.24 | 37 % | 16.6 MB |
| 313 nt | mixed L2 (0.5 % sub, 0.2 % + 0.2 %) | 7,607 | 131 | 2.38 | 35 % | 16.7 MB |
| 494 nt | clean | 4,768 | 210 | 2.36 | 30 % | 25.9 MB |
| 494 nt | 0.5 % + 0.5 % | 4,489 | 223 | 2.22 | 36 % | 25.6 MB |

* **Read length:** cost grows linearly (about 2.4–2.9 Mbases/s throughout).
* **Error rate:** the traceback grows with the indel rate, because each indel adds a step and a segment mark.
* **Golden output:** `align.json` stores a `projection_sha256` for every case (bases, erasures, ok, indel counts,
  marker mismatches, cost). A V5 native kernel must reproduce these hashes bit for bit before any speed is reported.

**Worker scaling** (`align_workers.json`): 32,768 mixed-L2 reads in 2,048-read chunks, process pool, pool start-up
included.

| workers | 1 | 2 | 4 | 8 |
|---|---|---|---|---|
| reads/s | 6,809 | 13,191 | 26,186 | 39,107 |
| speed-up | 1.0 | 1.94 | 3.85 | 5.74 |

Results are identical for every worker count.

## 4. End-to-end baseline (`e2e.json`)

4 MiB random input, v4-balanced, each run in a fresh process.

| run | status | encode s | decode s | recoverable MB/s | peak RSS |
|---|---|---|---|---|---|
| clean, coverage 1, 1 worker | SUCCESS | 1.50 | 1.65 | 2.55 | 143 MB |
| EXP-0011 channel, 1 worker | SUCCESS | 1.52 | 24.96 | 0.168 | 153 MB |
| EXP-0011 channel, 2 workers | SUCCESS | 1.21 | 13.45 | 0.312 | 156 MB |
| EXP-0011 channel, 4 workers | SUCCESS | 0.93 | 7.78 | 0.539 | 173 MB |
| EXP-0011 channel, 8 workers | SUCCESS | 0.91 | 5.44 | 0.770 | 190 MB |

Every output SHA-256 equals the input's (`d8658297…`). Noisy decode scales 4.6× on 8 workers at 4 MiB. V4
measured 5.4× at 16 MiB (EXP-0011); the smaller input leaves less to parallelise.

## 5. Nucleotide cost (`overhead.json`)

v4-balanced: 313-nt strand, 40 payload bytes, outer RS 64 + 16. Measured on 4 MiB: **9.787 nt per input byte**.
The analytic cost per payload byte is 9.781 nt (0.818 payload bits/nt, against 2 raw bits/nt); the difference is
container framing and superblock strands.

| component | nt per payload byte | share |
|---|---|---|
| payload | 4.00 | 40.9 % |
| outer parity (16/64) | 1.96 | 20.0 % |
| inner RS (16 B) | 1.60 | 16.4 % |
| address header (10 B incl. scrambler variant) | 1.00 | 10.2 % |
| sync markers (11 × 3 nt) | 0.83 | 8.4 % |
| CRC-32 | 0.40 | 4.1 % |
| constraint screening | 0 | 0 % (uses the in-frame variant byte) |

The other profiles: v4-dense 7.955, v4-indel 11.593, v4-archival 17.389 nt/byte.

## 6. RQ1 baseline: information lost per indel

From `align.json`, 313-nt layout:

| channel | true indels | indels detected by DP | frame nt erased | erased nt per true indel | erased share of frame |
|---|---|---|---|---|---|
| 0.1 % + 0.1 % | 2,611 | 2,431 | 68,178 | **26.1** | 5.9 % |
| mixed L2 | 5,168 | 4,512 | 124,408 | 24.1 | 10.8 % |
| 0.5 % + 0.5 % | 12,831 | 9,405 | 250,008 | 19.5 | 21.8 % |
| 1 % + 1 % | 25,693 | 14,621 | 371,471 | 14.5 | 32.7 % |

At low rates, each indel costs a whole 24-nt segment (6 bytes of inner-RS erasure budget) plus boundary spill. At
higher rates, indels share segments, and some cancel inside one segment and go undetected; those leave substitution
errors for the inner RS. This is the number Phase 3 (smart indel recovery) has to beat.

## 7. Phase 2 recommendation

Build a native implementation of `TemplateAligner._align` + `_traceback` with identical semantics:

* **Language:** C (gcc 13.3, through `ctypes` or `cffi`). It needs no new build dependency. The installed rustc is 1.75
  (December 2023), so PyO3/maturin version compatibility would have to be checked first.
* **Order of work:** equivalence first. Match the `projection_sha256` of every `align.json` case, then add fuzz
  equivalence (random reads, every edit type, reverse complements, malformed and boundary lengths, every band) against
  the NumPy reference, which stays as the fallback.
* **Benchmarks:** report reads/s, bases/s, memory, read-length and error-rate scaling, and worker scaling with this
  same harness against the V4 numbers above. Then re-profile to confirm that parsing and inner RS become the next
  bottlenecks.
