# V4 engineering log

Major decisions, in order. Each entry records the problem, the options considered, the decision and its reason, the
measurement behind it, the result and the remaining limitation. All measurements are software and simulation on the
machine described in `benchmarks/baseline/v3/environment.json` (8 logical CPUs, 31 GiB RAM, Python 3.12, CPU only).

---

### 2026-10-03 · L1 · V4 as a new package and format, not a V3 rewrite
- **Problem.** V4 needs multi-file archives, a Merkle tree, sync markers and pluggable outer codes. Every one of
  them changes the on-disk formats.
- **Options.** (a) extend format 5 in place; (b) new format and package, reusing V3 primitives by import.
- **Decision.** (b): `vnxdna.v4`, VNX4 container, frame version 4, CLI `vnx`.
- **Reason.** V3 is the known-good baseline. In-place changes would risk V3 compatibility and its 601 tests. Reusing
  V3's Cauchy RS, RS parity, CRC and bounded decompression avoids re-implementing proven code.
- **Result.** V3 source unchanged; 601/601 V3 tests pass at every V4 commit (see the completion report).
- **Remaining limitation.** Two formats now coexist. Converting needs extract + re-archive, with no in-place
  migration.

### 2026-10-03 · L2 · Systematic LT (robust soliton) fails; dense GF(2) fountain adopted as the EXPERIMENTAL code
- **Problem.** A fountain/rateless outer code was requested as an alternative to RS for strand dropout.
- **Options.** Classical LT (robust soliton, peeling); LT + Gaussian elimination; dense random linear fountain
  over GF(2); Raptor (pre-code + LT).
- **Benchmark.** k = 256, 25 % repair, 30 trials per point, receiving 1.00 / 1.02 / 1.06 / 1.15 × k symbols:
  robust soliton (systematic) 0, 0, 0, 0 successes; dense GF(2) 16, 29, 30, 30.
- **Decision.** Keep both distributions behind the codec interface; dense is the default for `lt-fountain`, and
  the whole fountain code stays EXPERIMENTAL.
- **Reason.** In the systematic setting the missing symbols are few and random. Low-degree soliton droplets rarely
  touch them, so the residual system is rank-deficient. The dense code behaves like a random linear code
  (failure ≈ 2^−extra).
- **Remaining limitation.** The dense code's encode/decode cost grows as k² (fine for k ≤ 1024). Raptor-style
  pre-coding is not implemented.

### 2026-10-03 · L3 · One RS codeword per frame (address included) instead of a separately protected address
- **Problem.** Addresses must survive errors, and a corrupted address must not silently misplace data.
- **Options.** (a) a separate short RS codeword for the address (+6–8 bytes per strand); (b) the address inside the
  frame's RS codeword, plus CRC-32 over address + payload.
- **Decision.** (b), plus tentative-address grouping for reads that fail alone, and orphan counting.
- **Reason.** (a) costs 9–11 % more nucleotides. With markers, an indel hitting the address region erases those
  bytes, and the frame RS restores them along with the payload. A miscorrected address fails the CRC.
- **Remaining limitation.** At high error rates many failing reads have unreadable headers ("orphans": 31 % of reads
  at 1 % sub + 0.4 % ins + 0.4 % del, coverage 3) and cannot join consensus. Orphan rescue by partial-header
  matching is a V5 candidate.

### 2026-10-03 · L4 · Indel blame rules in the marker DP
- **Problem.** First measurement: one insertion erased 14.6 bytes on average, about two segments instead of one.
- **Root cause.** Frame positions cost 0, so with diagonal-first traceback an indel lands right next to a marker,
  and the boundary rule blamed both neighbours. A deletion of a marker base costs exactly as much as deleting the
  adjacent frame base.
- **Fix.** Insertions blame only the frame side of an unshifted marker. A deletion at a marker base costs +1, an
  exact tie-break that prefers the frame base.
- **Benchmark (500 strands, v4-balanced).** Erased bytes per read: insertion 14.6 → 9.8; deletion 14.8 → 8.2.
  Single reads with 1 ins + 1 del decoded: 54.8 % → 75.6 %.
- **Remaining limitation.** Indel position inside a segment unknown → whole segment erased (§7 of
  INDEL_ENGINE.md).

### 2026-10-03 · L5 · Profile-driven optimisation of the hot paths
- **Method.** cProfile on a 3 MB container (93,838 strands), clean and noisy, before any optimisation.
- **Encode hotspots.** Per-group Cauchy parity calls; per-strand ASCII conversion; per-row label formatting. Fix:
  one vectorised parity pass per task, vectorised FASTA serialisation. **1.55 s → 1.12 s, strand file
  byte-identical to the pre-optimisation output.**
- **Decode hotspots (clean).** RS syndromes on every read, the V3 line-by-line FASTA parser, per-read orientation
  loop, per-key duplicate resolution. Fix: CRC-first acceptance (clean frames skip RS, as in V3), a block-based
  reader, vectorised orientation, vectorised uniform-group detection. **2.00 s → 1.05 s.**
- **Decode hotspots (noisy, 308,611 reads, RC + indels).** Inner RS 15.3 s, DP 13.1 s, traceback 4.8 s. Fixes:
  skip the DP for exact-length reads whose markers all match; no errors-only retry on sync erasures; vectorised
  insertion pass in the DP (cumulative minimum); **fast RS kernels** (L6). **31.5 s → 19.7 s single worker;
  6.0 s with 4 workers.**
- **Orientation pre-pass** (marker agreement forward vs reverse complement) before the sync path: 33.0 s → 17.2 s
  on a 50 % reverse-complement read set (2 workers).
- **Remaining limitation.** The DP and traceback remain the most expensive noisy-decode stage in Python/NumPy. A
  compiled kernel is justified only if the measured gain is worth a build dependency (not done).

### 2026-10-03 · L6 · Fast RS kernels with V3 as reference
- **Problem.** The V3 batch RS decoder's Horner syndromes and Chien/Forney evaluation dominated inner decoding.
- **Benchmark.** Table-gather syndromes on 4096 × 70-byte words: 31.4 ms → 4.9 ms, bit-identical.
- **Decision.** `vnxdna.v4.rs_fast`: the same algorithm with table-gather kernels. V3's decoder is untouched and
  remains the reference (`VNX_RS_REFERENCE=1`).
- **Verification.** Word-for-word identical outputs (codeword, ok flag, errata count) to V3 on 18,000 noisy words
  across six (n, r) geometries, plus a permanent test.
- **Result.** 2.5–5.2× faster inner decoding (1.6× for the tiny r = 2 code).

### 2026-10-03 · L7 · Security finding: zstd `max_output_size` is not a bound
- **Problem.** A fuzz test of decompression bombs found that python-zstandard ignores `max_output_size` when the frame
  header declares a content size. A hostile unencrypted chunk declaring 64 MiB was fully decompressed.
- **Fix.** V4 decompresses through V3's bounded `stream_reader` helper (V3 was already safe), with a regression test.
- **Remaining limitation.** None known for this path.

### 2026-10-03 · L8 · Partial recovery and the container header
- **Problem.** The first PARTIAL test failed: when group 0 was lost, the 16-byte container header was lost with it,
  and the surviving index could not be opened.
- **Fix.** For partial recovery only, the header is restored from its format-4.0 constant (magic, version, zero
  flags). Every recovered file is still verified chunk by chunk. Selective decoding decodes group 0 together with the
  index groups.
- **Result.** `test_partial_recovery_extracts_only_verified_files`: lost files are reported, and recovered files
  match byte for byte.

### 2026-10-03 · L9 · Fair V3 comparison: a wrapper bug that would have under-reported V3
- **Problem.** The first V3-vs-V4 run showed V3 failing every case. The cause was the comparison wrapper, not V3.
  `python -m vnxdna._entry` silently did nothing (no `__main__` guard), and two flags were misspelled
  (`--experimental-indel` vs `--experimental-indel-repair`; `consensus` has no `-j`).
- **Fix.** Invoke V3's entry point explicitly, use V3's exact flags, and abort on any V3 usage error (exit 2) instead
  of counting it as a V3 outcome.
- **Lesson.** A comparison harness must fail loudly on its own errors. Recorded as a permanent guard in
  `compare._v3_trial`.

### 2026-10-03 · L10 · Benchmark isolation could hang
- **Problem.** `bench.isolated()` waited forever on its result queue when a spawned child died (seen when a child
  could not re-import a `<stdin>` main module).
- **Fix.** Poll the queue and the child's liveness; a dead child is reported as an `ERROR` result.

### 2026-10-03 · L11 · Channel configuration type checks
- **Problem.** The configuration fuzz test found that wrong types (e.g. a string for `homopolymer_min_run`) raised a
  bare `TypeError`.
- **Fix.** Explicit type validation; every invalid value is a `VNXConfigurationError` (exit 7).

### 2026-10-03 · L12 · Round-1 experiments change the default marker layout
- **Problem.** EXP-0008/0009 (round 1, commit 2cd82b3; results in git at eb8d225): at 0.2 % ins + 0.2 % del,
  coverage 1, the default v4-balanced layout (2-nt markers every 32 nt) recovered 2/10 trials; without markers,
  even 0.05 % indels failed 10/10.
- **Options (measured, same P = 40, r = 16, same outer code).**

  | period / length | nt/B | success at 0.2 % + 0.2 % |
  |---|---|---|
  | none | 8.81 | 0/10 |
  | 48 / 2 | 9.12 | 0/10 |
  | 32 / 2 (round-1 default) | 9.31 | 2/10 |
  | 24 / 2 | 9.50 | 9/10 |
  | 32 / 3 | 9.56 | 5/10 |
  | 24 / 3 | 9.85 | 10/10 |
  | 16 / 2 | 9.88 | 10/10 |
- **Decision.** v4-balanced = P 40, r 16, 3-nt markers every 24 nt (313 nt).
- **Reason.** The cheapest layout with 10/10, tied with 16/2 at a lower cost. 3-nt markers make chance matches
  rarer (1/64 vs 1/16).
- **Consequence.** v4-balanced, v4-indel and v4-archival now share a strand length (313 nt), so layout
  auto-detection verifies frames on a read sample.
- **Remaining limitation.** +5.8 % nucleotides versus round 1. Only 10 trials per point.

### 2026-10-03 · L13 · Address snapping (grouping, not consensus, was the bottleneck)
- **Problem.** EXP-0005 (round 1): at 1 % sub + 0.4 % ins + 0.4 % del, coverage 5 succeeded 1/5. In isolation,
  consensus over 5 reads recovered 99.3 % of symbols. In the pipeline it recovered ~29 of ~450 attempted addresses.
- **Root cause (measured).** Of failed reads with an intact header, only 56 % had the exact address (24 % were
  one byte off). Of failed reads with an *erased* header (45 % of failures), only 10 % were within one byte, because
  the DP places an indel at the left edge of its segment and shifts the projected header.
- **Fix.** Pass 2 knows the missing addresses (from the superblock). Each failed read carries two readings of its
  header (projection and raw unshifted prefix). It is snapped to the unique missing address within one byte, where a
  byte matches if either reading has it. Consensus runs only for missing addresses.
- **Benchmark (same reads, coverage 3).** Failed groups 26 → 22 (single reading) → 16 (two readings). Consensus
  recoveries 78 → 156 → 228.
- **Safety.** A wrong snap only adds a wrong vote. Recovered frames must pass RS + CRC and decode to the group's own
  address, and the container SHA-256 still decides.
- **Remaining limitation.** Headers with ≥ 2 wrong bytes in both readings are not rescued. Snapping is per decode
  bucket (a read with a corrupted group field cannot reach another bucket when inputs exceed ~200,000 reads).

### 2026-10-03 · L14 · Constraint-study baseline flaw
- **Problem.** The first EXP-0014 run reported that 100 % of strands violated homopolymer ≤ 3 before screening.
- **Root cause.** The "before" strands used scrambler variant 0. The variant byte is sent unscrambled, and 0x00 maps
  to AAAA at the strand start (so do 0x55, 0xAA and 0xFF).
- **Fix.** The baseline uses one random variant per strand. Re-measured: 55 % violate homopolymer ≤ 4 (theory
  ≈ 58 %), and 96.5 % violate ≤ 3.
- **Remaining limitation (design).** Those four variant values always start the strand with a 4-base homopolymer.
  Screening skips them when the rules forbid it, at no correctness cost. A future frame version could map the variant
  byte through a run-free table.

### 2026-10-03 · L15 · V3 vs V4 under the identical channel (round 1)
- **Result.** At coverage 1, V3 with its best documented repair options matched V4 at every point except
  0.2 % + 0.2 % indels (V3 1/6, V4 2/6). V3 defaults fail from 0.1 % deletions. At coverage 5 with 1 % sub +
  0.4 % ins + 0.4 % del + 2 % dropout, V4 5/6 vs V3 0/6. V4 decodes 4–15× faster and spends 9.38 vs 8.08 nt per
  input byte (round-1 layout).
- **Interpretation.** V4's gain is at higher indel rates and noisy multi-read data, plus speed. It costs about 16 %
  more nucleotides (more with the round-2 layout). Neither system was tuned to the channel.

### 2026-10-03 · L16 · Release 4.0.0
- The package version becomes 4.0.0. V3 behaviour is unchanged, except that new format-5 archives record encoder
  version 4.0.0 in their manifest. Round-2 experiments (all fifteen runs, with the L12/L13 changes) supersede round 1
  in the experiment directories. Round-1 results stay in git history (commit eb8d225).
