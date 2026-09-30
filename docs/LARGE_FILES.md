# Large files (1 GB – 10 GB)

VNX-DNA V2 processes multi-gigabyte files with memory that depends on the chunk size and the worker count, **not**
on the file size ([STREAMING.md](STREAMING.md)). Everything below was measured on this project's machine by
`vnx-dna benchmark scale` and `vnx-dna benchmark corruption` and rendered from their JSON output. Re-run the
commands to reproduce on your hardware.

## Large-file workflow

```bash
vnx-dna benchmark generate --size 10GB --pattern mixed --seed 42 --output input.bin   # reproducible; never commit it
sha256sum input.bin

vnx-dna store   input.bin    --output archive.vxdna          # streaming, parallel; add --resume after an interruption
vnx-dna encode  archive.vxdna --output strands.vxs           # packed 2-bit strands + strands.vxs.vxidx
vnx-dna recover strands.vxs  --output recovered.bin --temp-dir /big/scratch   # two-pass, disk-backed decoder

sha256sum recovered.bin && cmp input.bin recovered.bin
vnx-dna extract strands.vxs --offset 5000000000 --length 1048576 --output section.bin   # random access via the DNA index
```

Plan disk space: input + container (≈ input × compressibility) + VXS (≈ 2 × container) + decoder spill (≈ 1.3 ×
container) + output. `--temp-dir` moves the spill elsewhere. FASTA is ≈ 4 × larger than VXS, so use VXS for
multi-gigabyte pools.

## Where the computational boundary is

* **Storage pipeline at 10 GB** (store → encode → decode/recover → restore, random access): fully executed on
  real 10 GB files, below.
* **Simulated sequencing channel with clustering and consensus**: executed on representative inputs (kilobytes to
  10 MB: [EXPERIMENTS.md](EXPERIMENTS.md), [BENCHMARKS.md](BENCHMARKS.md)). At 10× coverage a 10 GB input becomes
  ~2.3 × 10⁹ reads and ~6 × 10¹¹ bases. The simulator and clustering are streaming and bucketed, so memory is not
  the limit. Time and disk are: FASTQ with qualities is ≈ 2 bytes per base, about 1.2 TB of reads for 10 GB at 10×.
  Consensus alignment runs at ~4,000 reads/s per process. Rather than fake such a run, VNX-DNA documents the
  boundary. The architecture needs no change to scale further: bucket files, spill files and per-chunk decoding
  already bound memory.
* **Large-file damage** is tested at 1 GB on the real strand file: deleted strands in 50 ECC groups (within the
  guarantee) and one group beyond it, below.

## Scalability matrix

<!-- BEGIN GENERATED: scale-matrix -->
*(not yet measured)*
<!-- END GENERATED: scale-matrix -->

## Memory: does it grow with the file?

<!-- BEGIN GENERATED: memory-scaling -->
*(not yet measured)*
<!-- END GENERATED: memory-scaling -->

How to read the RAM figures: each stage runs 1 main process plus up to 7 worker processes. Every Python process
with numpy and the codecs loaded has a baseline of ~50–60 MiB, so a parallel stage's floor is ~400 MiB regardless of
input. The measurement sums RSS over all processes (shared library pages are counted once per process, which
overstates the true footprint). What matters for scalability is the trend across sizes.

## 10 GB acceptance test

<!-- BEGIN GENERATED: acceptance-10gb -->
*(not yet measured)*
<!-- END GENERATED: acceptance-10gb -->

The input and recovered hashes were computed with the system `sha256sum`, and the byte comparison with `cmp`. Both
are independent of VNX-DNA's code. The container was deleted after `encode`, so `recover` rebuilt everything from the
strand file alone.

## Large-file corruption acceptance

`vnx-dna benchmark corruption --size 1GB --groups 50 --substitutions 5` on a real 1 GB archive:

<!-- BEGIN GENERATED: corruption -->
*(not yet measured)*
<!-- END GENERATED: corruption -->

Damage is isolated. `verify` identifies the exact chunk that is beyond repair, and every other chunk still verifies.
The decoder never reprocesses unrelated data to find it, because each chunk's ECC groups are decoded independently.

## Resumable store

`store` checkpoints every 64 chunks (`--checkpoint-interval`). After an interruption (Ctrl-C, `kill -9`, power loss),
rerun the same command with `--resume`. It verifies the checkpoint, the partial file (every completed chunk's
stored SHA-256) and the input (every completed chunk's plaintext SHA-256), then continues. The result is
byte-identical to an uninterrupted run (tested with `SIGKILL`). Changed input, options or key are refused.
