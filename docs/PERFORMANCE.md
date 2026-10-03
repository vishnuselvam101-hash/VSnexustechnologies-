# Performance engineering (V4)

Method (mission principle 5): **profile → identify the hotspot → optimise → benchmark again → verify
correctness.** Every optimisation below was preceded by a cProfile run and followed by an equivalence check: byte-
identical output, or word-for-word identical decoder results. Machine: 8 logical CPUs, 31 GiB RAM, CPU only
(`experiments/*/environment.json`). All data is simulated.

Measured tables (generated from `results.json`, never typed): [V4_RESULTS.md](V4_RESULTS.md), sections EXP-0015
(stages), EXP-0011 (workers), EXP-0012 (memory). The V3 baseline is in `benchmarks/baseline/v3/` and
[V4_AUDIT.md](V4_AUDIT.md).

## 1. Optimisation record

| step | profile finding | change | before → after | correctness check |
|---|---|---|---|---|
| encode | 1,174 per-group Cauchy parity calls (0.51 s of 2.2 s); per-strand ASCII conversion (0.35 s); label formatting | one vectorised parity pass per task; one table lookup + one join for FASTA text | 1.55 s → 1.12 s (3 MB container, 93,838 strands) | strand file byte-identical |
| clean decode | RS syndromes for every read (0.92 s); V3 line-by-line FASTA parser (1.4 s); per-read orientation loop; per-key duplicate resolution | CRC-first acceptance; block-based reader (`v4/reads.py`); vectorised orientation; vectorised uniform-group detection | 2.00 s → 1.05 s (93,838 reads) | identical container, all tests |
| orientation | reverse-complement reads went through the forward DP before the RC retry | marker-agreement pre-pass (fwd vs RC) before the sync path | 33.0 s → 17.2 s (308,592 reads, 50 % RC, 2 workers) | same SUCCESS, same container |
| noisy decode | inner RS 15.3 s, DP 13.1 s, traceback 4.8 s (35.7 s profiled) | skip the DP for exact-length reads with intact markers; no errors-only retry on sync erasures; cumulative-minimum insertion pass | 31.5 s → 28.7 s | sync test set: identical decode results |
| inner RS | Horner syndromes + Chien/Forney via 2-D GF(256) gathers ≈ 75 % of RS time | `rs_fast`: same algorithm, table-gather kernels (V3 decoder kept as reference) | 2.5–5.2× on the decoder; noisy decode 28.7 s → 19.7 s | word-for-word identical to V3 on 18,000 noisy words; permanent test |
| parallel decode | — | process pool over read batches, ordered results | 19.7 s (1 worker) → 6.0 s (4 workers) | identical result (order-independent voting) |

Rejected or deferred: a compiled (C/Rust) DP kernel. The DP and traceback remain the largest noisy-decode cost.
A native kernel would need a build dependency and a fallback; the mission allows one only once the Python/NumPy
version is measured and the gain justifies it. It is a V5 candidate (§5).

## 2. Where the time goes now

Single-core stage throughput (EXP-0015, 8 MiB mixed input):

* **fast:** encryption (~700 MB/s), decompression (~530 MB/s), chunk hashing (~400 MB/s), DNA validation
  (~380 Mbases/s), compression (~230 MB/s), clean frame checking (~1.2 M frames/s), Merkle (~0.86 M leaves/s);
* **moderate:** archive build (~79 MB/s), inner RS parity (~71 MB/s), outer Cauchy encode (~28 MB/s), noisy inner RS
  decode (~67 k frames/s);
* **slow (the real limits):** DNA encoding incl. constraint screening (~4.3 MB/s of payload, ~108 k strands/s),
  channel simulation (~11 Mbases/s), **marker alignment (~9.5 k reads/s)**, and outer Cauchy decoding at maximum
  erasures (~3.5 MB/s, one matrix inverse per distinct erasure pattern; typical decodes see few erasures).

The system figure of merit is **recoverable MB/s** (bytes of input recovered *and verified* per second of decoding).
For a clean pool, decoding is dominated by parsing and CRC checks. Under indels, every read that needs the sync path
costs ~0.1 ms per core. Recoverable MB/s therefore falls with the indel rate, and the sweep tables report decode time
for every channel point.

## 3. Parallelism

Configurable with `--workers N` or a performance profile:

| profile | workers | decode batch | encode groups/task | extra checks |
|---|---|---|---|---|
| `safe` | 1 | 4,096 | 32 | decodes the strands after encoding (`verified_by_decoding`) |
| `balanced` | min(4, CPUs) | 8,192 | 64 | — |
| `maximum-throughput` | all CPUs | 16,384 | 128 | — |

No profile disables integrity verification: the container SHA-256, Merkle validation and per-file verification
always run.

* **Archive:** threads (zstd, hashing and AES release the GIL); the parent writes in order, so output is identical.
* **Encode:** processes over group ranges; ordered writes; identical strand files for any worker count.
* **Channel:** processes over fixed 1024-strand batches; seeded per batch; identical reads for any worker count.
* **Decode pass 1:** processes over read batches; ordered spill writes. Pass 2 (outer decoding) is single-process.
* **Sweeps:** trials in parallel processes, each decoding with one worker.

Measured scaling (EXP-0011) is in [V4_RESULTS.md](V4_RESULTS.md#worker-scaling-exp-0011). Parallelism is skipped
where it costs more than it helps: one worker runs everything in-process without pools.

## 4. Memory

* **Archive:** at most 2 × workers chunks in flight (1 MiB each by default) plus 84 B per unique chunk.
* **Encode:** one task (64 groups ≈ 160 KB of container) per worker, plus the strands being serialised.
* **Channel:** one 1024-strand batch per worker (reads × length × a few bytes of temporaries).
* **Decode:** a 8,192-read batch per worker in pass 1. Verified symbols and pending reads are spilled to bucket files
  (≈ (15 + P) bytes per verified read, (9 + frame_nt) bytes per pending read). Pass 2 loads one bucket at a time
  (buckets ≈ reads / 200,000). Both the container and the extracted output are written to disk, never held in RAM.
* Inputs and outputs are hashed incrementally (streamed SHA-256).

Measured peak RSS from 1 MiB to 1 GiB (EXP-0012): [V4_RESULTS.md](V4_RESULTS.md#memory-and-large-inputs-exp-0012).

## 5. Next performance steps (measured candidates)

1. A native (C via cffi, or Rust via PyO3) marker DP + traceback with the NumPy version as fallback and an
   equivalence test. It is the largest remaining noisy-decode cost.
2. A parallel pass 2 (outer decoding per bucket in worker processes).
3. Vectorising outer decoding across erasure patterns (shared with V3's roadmap).
4. Faster screening: test only the strands whose variant-0 candidate fails, in larger batches (already partially
   done); or constrained coding, which removes screening entirely.
