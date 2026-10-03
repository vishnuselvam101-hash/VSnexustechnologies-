# VNX-DNA V4 completion report

Version **4.0.0**, branch `feature/vnx-dna-v4` from V3 (tag `v3.0.0`, 9b5123c), dated 2026-10-03. Every result is
**SIMULATED** (software-generated DNA, the configurable V4 channel). **Nothing is physically validated**: no DNA
was synthesised, stored or sequenced. Numbers come from `experiments/*/results.json` and are rendered in
[V4_RESULTS.md](V4_RESULTS.md). Experiments ran in two rounds (§13).

## 1. Executive summary

V4 adds a complete, measured software stack on top of the unchanged V3:
- a multi-file archive format with deduplication and a Merkle tree;
- a strand format with in-strand synchronisation markers that converts insertions and deletions into recoverable
  erasures;
- a configurable channel simulator;
- a reconstruction pipeline with consensus, address snapping, PARTIAL recovery and random access from reads;
- pluggable outer codes;
- a constraint engine;
- benchmarks, error sweeps and sixteen reproducible experiment directories.

**Regression:** 723 tests pass (601 V3 + 122 V4), and there were **zero integrity failures in all experiment
trials**. Every failure was reported as a failure; none was published as data.

Headline measurements (round 2, default profile, coverage 1 unless stated):
- **Indel tolerance:** recovers 0.2 % ins + 0.2 % del per base at coverage 1 (10/10). Without markers, 0.05 % already
  fails. At coverage 5, recovers 1 % insertions or 1 % deletions.
- **Substitutions:** up to 1 % at coverage 1, 2 % at coverage 5.
- **V3 comparison** (identical channel and seeds): V4 6/6 vs V3-best 1/6 at 0.2 % + 0.2 % indels; 6/6 vs 0/6 on
  heavy noisy coverage-5 data. V4 decodes 4–15× faster, but uses **23 % more nucleotides per byte**.
- **Scale:** 1 GiB full DNA round trip (33.6 M strands) at **326 MB peak RSS**, flat from 100 MB to 1 GiB.
  Decode scales 5.4× on 8 workers.
- **Inner RS kernels:** 2.5–5.2× faster, bit-identical to V3.

## 2. V3 baseline

See [V4_AUDIT.md](V4_AUDIT.md) and `benchmarks/baseline/v3/`. At baseline: 601/601 tests (4 min 19 s); 8 inputs × 3
repeats, all SHA-256 identical; 8.08 nt per byte on random data (round-1 comparison input); a noisy coverage-10
pipeline in 20.2 s, with clustering and consensus taking 82 % of the time.

## 3. Architecture and format

[V4_ARCHITECTURE.md](V4_ARCHITECTURE.md) · [VNX4_FORMAT.md](VNX4_FORMAT.md) (normative: container 4.0, frame 4,
superblock 1). It is a new package `vnxdna.v4` (6,031 lines) with CLI `vnx`. V3 primitives are reused by import:
Cauchy RS, RS parity, CRC, bounded decompression and the strand writer. V3 source is unchanged.

## 4. Channel model

[CHANNEL_MODEL.md](CHANNEL_MODEL.md). It models substitutions, insertions, deletions, dropout,
fixed/Poisson/negative-binomial coverage, duplication, homopolymer-dependent errors, GC-dependent coverage, bursts,
N calls, reverse complements and qualities. Output is seeded and worker-count independent (tested byte for byte).

## 5. Indel strategy

[INDEL_ENGINE.md](INDEL_ENGINE.md) · [INDEL_RESEARCH.md](INDEL_RESEARCH.md). A marker-template banded DP, vectorised
over reads, converts each indel into erasure of one segment (6 bytes with the default 24-nt period); the inner RS then
corrects `2e + f ≤ r`. Consensus works over pending reads, which are grouped by address snapping. RS alone is never
credited with indel correction.

## 6. Dropout strategy and ECC architecture

[ECC_ARCHITECTURE.md](ECC_ARCHITECTURE.md). Default outer code: Cauchy RS 64+16 per group (MDS; V3 code).
EXPERIMENTAL: a GF(2) fountain code. At equal 25 % redundancy (EXP-0007, EXP-0016), block length dominates: the dense
fountain over 256 symbols and RS over 250 symbols perform the same, and both beat RS over 80 symbols. RS 64+16
recovers 10 % dropout 9/10 and 12 % 2/10, whereas RS 200+50 and the fountain hold 12 % at 10/10 and 14 % at 9/10.
Classical robust-soliton LT fails in the systematic setting (a documented negative result).

## 7. Constraint engine

[CONSTRAINT_ENGINE.md](CONSTRAINT_ENGINE.md). Configurable GC, windowed GC, homopolymer, tandem-repeat, motif and
length rules, with JSON diagnostics (`vnx validate`). Screening by scrambler variant costs no extra nucleotides.
EXP-0014 (20,000 frames):

| rule set | violating before screening | after | mean / max variant |
|---|---|---|---|
| default (GC 40–60 %, homopolymer ≤ 4) | ≈ 55 % | 0 | ≈ 1.0 / ≤ 20 |
| relaxed (GC 30–70 %, homopolymer ≤ 6) | ≈ 4 % | 0 | ≈ 0.04 |
| strict (GC 45–55 %, homopolymer ≤ 3) | ≈ 97 % | — | **UNSATISFIABLE** (explicit failure) |

Exact values are in [V4_RESULTS.md](V4_RESULTS.md#constraint-screening-exp-0014). Strict rules need constrained
coding (V5).

## 8. Reconstruction, integrity and random access

* **Reconstruction:** a fast path (CRC first), orientation detection, the sync path, disk-spilled buckets, superblock
  decoding, duplicate majority, consensus with posteriors (the soft-information interface), address snapping, and
  outer decoding per group.
* **Integrity:** file SHA-256 → chunk ID + stored SHA-256 → Merkle root → manifest (SHA-256 or HMAC) → trailer
  SHA-256 → the container SHA-256 in the superblock. A decode is SUCCESS only if the container matches the
  superblock SHA-256 and validates.
* **PARTIAL:** extracts only individually verified files (exit 9).
* **Random access:**
  - digital: `vnx locate` (binary search, ~0.4 ms) and `vnx extract --file`, which reads only that file's chunks;
  - from reads: `vnx decode --select` decodes the index plus the needed groups (30 % of groups for a small file in the
    CLI test);
  - strand records per file: `vnx locate --dna-profile`.

## 9. Performance and memory

[PERFORMANCE.md](PERFORMANCE.md). Profile-driven optimisations: encode 1.55 → 1.12 s with byte-identical output;
clean decode 2.00 → 1.05 s; noisy decode 31.5 → 19.7 s single worker. RS kernels are 2.5–5.2× faster.

Round 2 measurements:

| measurement | value |
|---|---|
| stage throughput (1 core) | encryption ~700 MB/s, compression ~230 MB/s, hashing ~400 MB/s, DNA validation ~380 Mbases/s, DNA encoding ~4.2 MB/s, marker alignment ~8.7 k reads/s |
| worker scaling, 16 MiB, cov 3, mixed errors (EXP-0011) | decode 100.2 s → 51.6 / 28.6 / 18.4 s on 2/4/8 workers (1.94× / 3.50× / 5.44×); encode 6.3 → 1.6 s; channel 40.1 → 8.0 s |
| recoverable throughput, noisy (EXP-0011) | 0.17 MB/s (1 worker) → 0.91 MB/s (8 workers) at coverage 3 with errors |
| recoverable throughput, clean (EXP-0012, 4 workers) | 4.0–4.5 MB/s for 10 MiB – 1 GiB (≈ 0.38 TB/day) |
| memory (EXP-0012, clean, 4 workers) | peak RSS 102 / 273 / 302 / 326 MB for 1 MiB / 10 MiB / 100 MiB / 1 GiB (largest worker ≈ 124 MB) |
| 1 GiB round trip | SUCCESS: 33,557,314 strands, encode 96.8 s, decode 244.4 s, 9.78 nt per byte |

## 10. Error-recovery results (round 2)

| channel | coverage 1 | coverage 5 |
|---|---|---|
| substitution | ≤ 1 %: 10/10; 2 %: 9/10 | ≤ 2 %: 10/10 |
| insertion | ≤ 0.1 %: 10/10; 0.5 %: 0/10 | ≤ 1 %: 10/10 |
| deletion | ≤ 0.1 %: 10/10; 0.5 %: 5/10; 1 %: 0/10 | ≤ 1 %: 10/10 |
| ins + del (equal) | ≤ 0.2 % each: 10/10; 0.3 %: 0/10 (v4-indel: 6/10) | — |
| dropout (20 % parity) | ≤ 5 %: 10/10; 10 %: 8/10; ≥ 15 %: 0/10 | — |
| dropout (50 % parity) | ≤ 30 %: 10/10; 40 %: 0/10 | — |
| mixed L1 / L2 / L3 / L4 / L5 | 10/10, 10/10, 9/10 | 10/10, 10/10, 10/10, 2/10, 0/10 |
| heavy errors (1 % sub, 0.4 % ins, 0.4 % del), Poisson | 1×: 0/5, 2×: 0/5 | 5×–100×: 5/5 |

## 11. Security

[SECURITY.md](SECURITY.md). Audit table covering parsing, path traversal, decompression bombs, metadata, malformed
reads, forged strands, resource limits, deserialisation, temporary files, permissions and cryptographic
configuration. One real finding was fixed: zstd's `max_output_size` is ignored when a frame declares its content
size. The fuzz tests also found and fixed three configuration type-validation bugs.

## 12. Reproducibility

[REPRODUCIBILITY.md](REPRODUCIBILITY.md). Each experiment directory holds config, environment, seeds, input hash and
all trials. `vnx experiment reproduce` compares deterministic fields. Evidence: EXP-0009 reproduced identically
across rounds 1 and 2 (12 layouts × 10 trials), despite intervening decoder changes that do not affect coverage 1. A
permanent test runs `reproduce` on a small experiment.

## 13. Experiment rounds

* **Round 1** (commit 2cd82b3, results preserved in git at eb8d225) used v4-balanced with 2-nt markers every 32 nt
  and no address snapping. Its weak points drove two changes: EXP-0009 (marker layout) and EXP-0005 (grouping).
* **Round 2** (commit a387b34) uses the new layout and snapping; all fifteen runs were repeated. EXP-0007 (pure codec
  comparison; codec code unchanged) was not repeated.

## 14. Known failures (measured, controlled)

At coverage 1, insertion or deletion ≥ 0.5 %, 0.3 % ins + 0.3 % del, and 2 % substitution (1/10) fail. At coverage
5, mixed L4/L5 fail. Dropout ≥ 15 % fails with 20 % parity. Negative-binomial coverage with k ≤ 1 at mean 3 fails, as
do homopolymer ×20 indel multipliers and strict homopolymer ≤ 3 constraints (explicit encode failure). Single-file
archives cannot show PARTIAL, because the file and the index are lost together.

## 15. Known limitations

[LIMITATIONS.md](LIMITATIONS.md). In short:
- **Not physically validated.**
- The channel is not fitted to any platform.
- Each indel costs a whole segment.
- Snapping fixes only one header byte and works per decode bucket.
- There is no cross-group interleaving.
- The fountain code is experimental.
- Decoding is hard-decision, plus erasures.
- The marker DP is the throughput limit (~8.7 k reads/s per core).
- Screening cannot satisfy strict constraints.
- V4 uses 23 % more nucleotides than V3 for its indel robustness.
- BAM input is not supported.

## 16. Unimplemented research

Soft-decision inner decoding, LDPC or Raptor codes, VT/watermark codes, indel position search inside segments,
consensus realignment, constrained coding, cross-group interleaving, adaptive outer group sizes, primer design and
molecular random access, synthesis/sequencing provider adapters, platform-fitted channel models, a native DP kernel,
GPU acceleration.

## 17. V5 recommendations (in priority order)

1. **Native marker-DP and traceback kernel** (C or Rust, with the NumPy fallback and an equivalence test). It is the
   dominant noisy-decode cost.
2. **Indel position search + consensus realignment** to shrink erasures per indel and lower the nucleotide cost.
3. **Adaptive or larger outer groups** (RS up to 256 symbols, or a large-block fountain) and cross-group
   interleaving, for dropout and uneven coverage (EXP-0004/0013/0016).
4. **Constrained coding** for strict homopolymer/GC rules, and a run-free mapping for the variant byte.
5. **Soft-decision decoding** from the consensus posterior (the interface already exists).
6. **Global address snapping** across decode buckets, and two-byte header recovery.
7. **Physical pathway:** primers and adapters in the layout, provider adapters, channel models fitted to published
   data, and a **wet-lab pilot**, the only way to replace "SIMULATED" with evidence.

## 18. Files

New: `src/vnxdna/v4/` (24 modules), `tests/v4/` (6 files, 122 tests), `experiments/` (16 directories),
`research/v4/` (results and README generators), `benchmarks/baseline/v3/`, and these docs: V4_AUDIT,
V4_ARCHITECTURE, VNX4_FORMAT, ARCHIVE_ENGINE, INDEL_ENGINE, INDEL_RESEARCH, ECC_ARCHITECTURE, CONSTRAINT_ENGINE,
PERFORMANCE, BENCHMARKING, V4_ENGINEERING_LOG, V4_RESULTS, V4_COMPLETION_REPORT. Extended with V4 sections:
README, CHANNEL_MODEL, SECURITY, LIMITATIONS, REPRODUCIBILITY, CHANGELOG. Modified V3-adjacent files:
`pyproject.toml` (`vnx` entry point), `src/vnxdna/_version.py` (4.0.0), `.gitignore`. V3 source and tests are
unchanged.
