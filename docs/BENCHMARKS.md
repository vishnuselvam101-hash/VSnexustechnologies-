# Benchmarks (V2)

Every number on this page was measured on this project's machine and rendered from JSON by
`research/v2/render_v2_tables.py`. Nothing is hard-coded. Absolute numbers depend on the hardware. Re-run:

```bash
vnx-dna benchmark stages --sizes 100KB,1MB,10MB --output stages.json            # A–H, in-process
vnx-dna benchmark scale  --sizes 1MB,10MB,100MB,500MB,1GB,2GB,5GB,10GB --work-dir /big/scratch --output scale.json
vnx-dna benchmark corruption --size 1GB --work-dir /big/scratch --output corruption.json
python research/v2/run_v2_research.py --out research/results/v2                 # profiles + channel experiments
python research/v2/render_v2_tables.py                                           # refresh these tables
```

The benchmark categories required by the V2 plan map as follows:

| category | where |
|---|---|
| A. storage pipeline | scale matrix (`store`, `restore`), stages A |
| B. DNA encoding | scale matrix (`encode → VXS`), stages B |
| C. DNA decoding | scale matrix (`recover from DNA`), stages C |
| D. simulated sequencing | stages D |
| E. clustering | stages E |
| F. consensus | stages F |
| G. ECC | stages G (outer code alone, encode and decode with M erasures in every group) |
| H. full end to end | stages H; the canonical CLI workflow in [README](../README.md); [EXPERIMENTS.md](EXPERIMENTS.md) |

## Scalability matrix (1 MB – 10 GB, storage pipeline with the real CLI)

<!-- BEGIN GENERATED: scale-matrix -->
*(not yet measured)*
<!-- END GENERATED: scale-matrix -->

Peak RAM, CPU utilisation, swap and disk per stage are in [LARGE_FILES.md](LARGE_FILES.md#memory-does-it-grow-with-the-file).
Random-access measurements are in [RANDOM_ACCESS.md](RANDOM_ACCESS.md#measured).

## Stage benchmark (A–H)

<!-- BEGIN GENERATED: stages -->
*(not yet measured)*
<!-- END GENERATED: stages -->

## Chunk size selection

The format accepts any chunk size from 1 byte to 64 MiB. The profiles use 256 KiB – 4 MiB, based on this
measurement. Larger chunks amortise per-chunk overhead (fewer index entries, fewer shortened ECC groups) but raise
peak memory (proportional to chunk size × workers) and the cost of random access, which decodes whole chunks.

<!-- BEGIN GENERATED: chunks -->
*(not yet measured)*
<!-- END GENERATED: chunks -->

## Storage profiles

No profile is universally best. Density, speed and robustness trade off, and the measurements show by how much.

<!-- BEGIN GENERATED: profiles -->
*(not yet measured)*
<!-- END GENERATED: profiles -->

## Optimisation log (measured baseline first, then each change)

Measured during development on this machine (8 logical CPUs; ad-hoc timing runs, single process unless stated),
on 33,000 strands ≈ one 1 MiB chunk of incompressible stored data at the balanced geometry:

| step | before | after | change |
|---|---|---|---|
| constraint check (GC, homopolymer), 33,000 × 252 nt | 0.188 s (V1 integer cumulative sums) | 0.029 s | runs found with shifted boolean ANDs; identical results (tested against V1) |
| inner RS parity, 33,000 frames | 0.074 s (k·r scalar table passes) | 0.032 s | one (256 × r) table gather per message column; bit-identical to `reedsolo` |
| strand building incl. screening | 0.854 s | 0.296 s → **0.181 s** | table RS, then *linear screening*: parity and the 2bit mapping are XOR-linear, so each scrambler variant is `codes(v=0) XOR mask_v`; byte-identical to direct screening (tested) |
| outer Cauchy parity, 410 stripes × 64 × 40 B | 0.110 s (generic GF(2⁸) product) | 0.031 s | one (M × 256) gather per data column; bit-identical |
| `encode` 20 MB random, 1 worker / 7 workers | 2.3 / 12.5 MB/s | 4.4 / **24.0** MB/s | the above; output byte-identical before and after |
| frame CRC validation | per-strand `zlib.crc32` loop (V1) | vectorised over batches | column-wise table CRC; equal to `zlib.crc32` (tested) |
| channel batch (8,192 strands × 10×, all error classes) | 6.2 s | 1.29 s | Bernoulli events via geometric gaps; vectorised ragged gathers |

Bottlenecks that remain (measured, not fixed): the inner-RS *decoder* is pure Python (`reedsolo`) and runs per
damaged read, so heavily damaged read sets decode at a few thousand reads per second per process. Consensus alignment
runs at ~4,000 reads/s per process. Clustering is dominated by per-read Python work in the address scan.

## V1 benchmarks

The V1 (format 4) stage benchmark still exists (`vnx-dna benchmark v1`). Its 1.0.0 results are in
`research/results/benchmarks.json` and in this file at tag `v1.0.0`.
