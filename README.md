# VNX-DNA 3

**Scalable computational DNA data storage, end to end, on a CPU.** Give VNX-DNA a real file, including a
multi-gigabyte one. It processes the file in bounded memory, turns it into a structured DNA-storage representation,
can pass that through a reproducible simulated storage and sequencing channel, recovers the data, and proves
with SHA-256 that the result is byte-for-byte identical to the original.

**New in 3.0** ([CHANGELOG](CHANGELOG.md), [audit report](docs/V3_AUDIT.md)):
- **Audit fixes.** A full audit of 2.0.0 fixed 48 defects. Two of them were AES-GCM nonce-reuse paths on resumed
  stores. Each fix has a regression test that fails on 2.0.0.
- **Vectorised inner Reed–Solomon decoder.** It is strictly bounded-distance and sits behind an ECC engine interface,
  and noisy reads decode several times faster.
- **Single-read burst resynchronisation** (`--burst-repair`). A contiguous run of lost or extra bases is recovered
  even at coverage 1.
- **Burst errors in the simulator**, and `vnx-dna simulate-errors`: seeded error sweeps with recovery statistics.
- **Pools holding several archives** can be decoded (`--archive-tag`).
- **Same format as 2.0 (format 5).** V2 archives read in V3 and vice versa, with one documented exception.

- streaming store: per-chunk zstd, **AES-256-GCM chunked authenticated encryption**, footer-indexed container, resumable;
- ECC: **Cauchy Reed–Solomon** (provably MDS) across strands, inner Reed–Solomon + CRC-32 per strand;
- constraint-screened DNA strands (GC, homopolymers, tandem repeats, motifs), FASTA or packed 2-bit VXS;
- simulated sequencing (coverage 1×–50×+, uneven abundance, substitutions, indels, duplicates, N, junk,
  contamination, quality scores), **clustering**, **consensus** with honest `N`s, and **synchronization** for indels;
- two-pass disk-backed decoder, random access through a DNA index, experiments with Monte Carlo statistics;
- tested from 1 MB to **10 GB** (2.0) and to 5 GB again with V3, with measured peak RAM
  ([docs/LARGE_FILES.md](docs/LARGE_FILES.md)).

> **Scope.** This is software. Every result in this repository comes from computation and **simulation**. No
> sequence has been synthesised or sequenced. Software-generated DNA is not synthesised DNA, and simulated
> sequencing is not real sequencing. Nothing here demonstrates physical DNA storage or commercial archival
> readiness. See [Limitations](#limitations).

## Installation (Linux, Python ≥ 3.12, CPU only)

```bash
git clone https://github.com/vishnuselvam101-hash/VSnexustechnologies-.git
cd VSnexustechnologies-
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # add '.[dev]' to run the tests
vnx-dna --help
```

## Basic example

```bash
echo "Hello VNX-DNA" > input.txt
vnx-dna store input.txt --output hello.vxdna          # streaming container (format 5)
vnx-dna encode hello.vxdna --output hello.fasta       # DNA strands (+ hello.fasta.vxidx)
vnx-dna recover hello.fasta --output recovered.txt    # DNA -> verified original
cmp input.txt recovered.txt && echo identical
```

## Complete DNA workflow

Every stage is its own command with its own verifiable output file:

```bash
vnx-dna benchmark generate --size 200KB --pattern mixed --seed 42 --output input.bin
vnx-dna store input.bin --output archive.v2.vxdna
vnx-dna encode archive.v2.vxdna --output strands.fasta
vnx-dna sequence strands.fasta --coverage 10 --substitution-rate 0.001 --insertion-rate 0.0001 --deletion-rate 0.0001 --dropout-rate 0.02 --seed 42 --output reads.fastq
vnx-dna cluster reads.fastq --output clusters.jsonl
vnx-dna consensus clusters.jsonl --output consensus.fasta
vnx-dna decode consensus.fasta --output recovered.vxdna
vnx-dna restore recovered.vxdna --output recovered.bin
vnx-dna verify recovered.vxdna --file recovered.bin
cmp input.bin recovered.bin && sha256sum input.bin recovered.bin
cmp archive.v2.vxdna recovered.vxdna && echo "container rebuilt byte for byte"
```

The same in one command, with a machine-readable report:

```bash
vnx-dna pipeline input.bin --output recovered2.bin --coverage 10 --substitution-rate 0.001 --insertion-rate 0.0001 --deletion-rate 0.0001 --dropout-rate 0.02 --seed 42 --report pipeline.json
```

## Large files

Memory depends on the chunk size and the worker count, not on the file size ([docs/STREAMING.md](docs/STREAMING.md)).
Use the packed `.vxs` strand format and put the decoder's spill on a large disk:

```bash
# 1 GB example
vnx-dna benchmark generate --size 1GB --pattern mixed --seed 42 --output big.bin
vnx-dna store big.bin --output big.vxdna
vnx-dna encode big.vxdna --output big.vxs
vnx-dna recover big.vxs --output big.out --temp-dir /path/to/scratch
cmp big.bin big.out

# 10 GB scalability run (the real CLI, measuring time, CPU, peak RAM, swap and disk per stage)
vnx-dna benchmark scale --sizes 1GB,2GB,5GB,10GB --work-dir /path/to/scratch --output scale.json
```

An interrupted `store` continues with `--resume` and produces a byte-identical archive.

<!-- BEGIN GENERATED: readme-results -->
*(generated by `research/v2/render_v2_tables.py` from `research/results/v2/*.json`)*

Measured on this project's machine (8 logical CPUs; details and reproduction in the linked docs):

| run | result | wall time (all stages) | peak RAM (process tree) | independent check |
|---|---|---|---|---|
| 1 GB: store → restore → encode (VXS) → random access → recover from DNA | PASS | 0.8 min | 612 MiB | `sha256sum` match: True, `cmp`: identical |
| 2 GB: store → restore → encode (VXS) → random access → recover from DNA | PASS | 1.6 min | 576 MiB | `sha256sum` match: True, `cmp`: identical |
| 5 GB: store → restore → encode (VXS) → random access → recover from DNA | PASS | 3.9 min | 582 MiB | `sha256sum` match: True, `cmp`: identical |
| 10 GB: store → restore → encode (VXS) → random access → recover from DNA | PASS | 7.7 min | 599 MiB | `sha256sum` match: True, `cmp`: identical |
| 1 GB corruption: 50 ECC groups damaged inside the guarantee, then one beyond | PASS | – | – | inside: `cmp` identical; beyond: exit 5, no output |
| Monte Carlo, canonical channel (10×, 0.1 % sub, 0.01 % ins/del, 2 % dropout), consensus | 1000/1000 exact (95 % CI [0.9962, 1.0000]) | – | – | undetected corruption: 0 |

See [docs/LARGE_FILES.md](docs/LARGE_FILES.md), [docs/BENCHMARKS.md](docs/BENCHMARKS.md) and [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md). All figures are software measurements; the channel is simulated.
<!-- END GENERATED: readme-results -->

VNX-DNA 3 measurements (V2 baseline on the same machine):

<!-- BEGIN GENERATED: readme-v3-results -->
*(generated by `research/v3/render_v3_tables.py` from `research/results/v3/*.json`)*

Measured on this project's machine (8 logical CPUs, Linux-6.8.0-139-generic-x86_64-with-glibc2.39); software simulation only. Versions: vnx-dna 3.0.0 (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1) vs vnx-dna 2.0.0 (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1).

| measurement | result | source (`research/results/v3/`) |
|---|---|---|
| 1 GB: store → restore → encode (VXS) → random access → recover from DNA (V3) | PASS; all stages 0.8 min, peak RAM 559 MiB; SHA-256, `cmp` and random access equal: True (V2: 0.8 min, 630 MiB) | `scale-v3.json, scale-v2.json` |
| 5 GB: store → restore → encode (VXS) → random access → recover from DNA (V3) | PASS; all stages 3.7 min, peak RAM 609 MiB; SHA-256, `cmp` and random access equal: True | `scale-v3.json, scale-v2.json` |
| restore 10MB from coverage-1 reads with 0.006 substitutions per base | V3 2.2 s vs V2 16.0 s (7.37× faster), both exact | `noisy-decode.json` |
| coverage 1, lost/extra-base bursts (all burst-deletion and burst-insertion points) | exact 20/100 with V2 decoding options → 100/100 with V3 burst + indel repair | `sweep-cov1-*.json` |
| error sweeps, every error type (coverage 1 and 5) | 1,310 trials: undetected corruption 0, internal errors 0 | `sweep-*.json` |
| single-read repair of 2 indels | 40/40 correct in 145.69 ms per read (V2: 40/40 in 422.25 ms) | `indel-*.json` |
| VNX-DNA 2.0.0 reading V3 output | plain.vxdna: exit 0 (identical), encrypted.vxdna: exit 0 (identical), resumed.vxdna: exit 6, plain.fasta: exit 0 (identical) | `v2-reads-v3.json` |
<!-- END GENERATED: readme-v3-results -->

## V3: error sweeps and coverage-1 repair

```bash
vnx-dna benchmark generate --size 40KB --pattern mixed --seed 3 --output sweep-in.bin
vnx-dna simulate-errors sweep-in.bin --output sweep --trials 2 --sweep substitution=0,0.004 --sweep burst-deletion=0.3 --burst-repair 24
cat sweep/sweep.md
vnx-dna store sweep-in.bin --output s.vxdna && vnx-dna encode s.vxdna --output s.fasta
vnx-dna simulate s.fasta --output s-reads.fasta --burst-rate 0.3 --burst-kind deletion --seed 5
vnx-dna recover s-reads.fasta --output s-out.bin --burst-repair 24 --experimental-indel-repair
cmp sweep-in.bin s-out.bin && echo identical
```

Each sweep point runs seeded trials through the whole decoder and classifies every outcome by SHA-256: exact,
detected failure, or undetected corruption. The last would be a bug; it has been 0 in every run
([docs/ERROR_MODEL.md](docs/ERROR_MODEL.md)).

## Random access

```bash
vnx-dna extract big.vxdna --offset 500000000 --length 1048576 --output section.bin   # container: reads ~1 chunk
vnx-dna extract big.vxs   --offset 500000000 --length 1048576 --output section.bin   # DNA: via big.vxs.vxidx
```

## Damage simulation, recovery and verification

```bash
# damage: 5 % strand dropout, 0.5 % substitutions, indels, duplicates, reverse complements, uneven abundance
vnx-dna sequence strands.fasta --coverage 8 --coverage-model lognormal --abundance-sigma 0.5 --dropout-rate 0.05 --substitution-rate 0.005 --insertion-rate 0.0005 --deletion-rate 0.0005 --duplication-rate 0.05 --reverse-complement-rate 0.5 --seed 7 --output damaged.fastq

# recovery: through consensus, or directly from the reads
vnx-dna cluster damaged.fastq --output damaged.jsonl
vnx-dna consensus damaged.jsonl --output damaged.cons.fasta
vnx-dna recover damaged.cons.fasta --output from-consensus.bin

# verification: every layer, and a recovered file against the archive
vnx-dna verify damaged.cons.fasta
vnx-dna verify archive.v2.vxdna --file from-consensus.bin
```

If damage exceeds what the ECC guarantees (more than M lost strands in one ECC group), VNX-DNA exits with code 5,
names the damaged chunk, and writes nothing. It never outputs corrupted data.

### Encrypted archives

```bash
vnx-dna keygen --output key.txt                         # 256-bit key, mode 0600; keep it safe
vnx-dna store input.bin --output secret.vxdna --key-file key.txt
vnx-dna encode secret.vxdna --output secret.vxs         # no key needed to encode or decode DNA
vnx-dna recover secret.vxs --output secret.bin --key-file key.txt
cmp input.bin secret.bin && echo identical
```

### V1 archives

V1 (format 4) containers and reads are read by every command. `vnx-dna migrate old.vxdna -o new.vxdna` converts
them with verification before and after. `vnx-dna v1 …` is the unchanged V1 CLI.

## Documentation

| topic | document |
|---|---|
| **V3 architecture**, components, design decisions, guarantees | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| **V3 audit and release report** (issues found, fixed, remaining; tests; benchmarks) | [docs/V3_AUDIT.md](docs/V3_AUDIT.md) |
| storage format (V3 decisions) · byte layouts (format 5) | [docs/STORAGE_FORMAT.md](docs/STORAGE_FORMAT.md) · [docs/V2_FORMAT.md](docs/V2_FORMAT.md) |
| encoding (bytes → DNA → bytes) | [docs/ENCODING.md](docs/ENCODING.md) |
| ECC layers, the vectorised decoder, the ECC engine, guarantees | [docs/ECC.md](docs/ECC.md) |
| error model, correction guarantees per error type, error sweeps | [docs/ERROR_MODEL.md](docs/ERROR_MODEL.md) |
| synchronization (indels, bursts) | [docs/SYNCHRONIZATION.md](docs/SYNCHRONIZATION.md) |
| random access | [docs/RANDOM_ACCESS.md](docs/RANDOM_ACCESS.md) |
| benchmarks (V3 vs V2) | [docs/BENCHMARKS.md](docs/BENCHMARKS.md) |
| large files, memory scaling, corruption at scale | [docs/LARGE_FILES.md](docs/LARGE_FILES.md) |
| security (what is encrypted, key handling, V3 fixes) | [docs/SECURITY.md](docs/SECURITY.md) |
| limitations | [docs/LIMITATIONS.md](docs/LIMITATIONS.md) |
| roadmap | [docs/ROADMAP.md](docs/ROADMAP.md) |
| reproducibility | [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) |
| compatibility and migration | [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) |
| channel model, V2 measurements · clustering and consensus · experiments | [docs/CHANNEL_MODEL.md](docs/CHANNEL_MODEL.md) · [docs/CONSENSUS.md](docs/CONSENSUS.md) · [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) |
| streaming model and Python API | [docs/STREAMING.md](docs/STREAMING.md) |
| CLI reference and exit codes · tests | [docs/CLI.md](docs/CLI.md) · [docs/TESTING.md](docs/TESTING.md) |
| project state | [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) |
| V2 architecture · V1 architecture and format · V0.1 forensics | [docs/V2_ARCHITECTURE.md](docs/V2_ARCHITECTURE.md) · [docs/V1_ARCHITECTURE.md](docs/V1_ARCHITECTURE.md), [docs/V1_FORMAT.md](docs/V1_FORMAT.md) · [docs/V0.1_BASELINE.md](docs/V0.1_BASELINE.md) |
| illustrated 2.0 overview (PDF; describes 2.0.0) | [docs/VNX-DNA-v2-overview.pdf](docs/VNX-DNA-v2-overview.pdf) |

## Limitations

- **Software only.** No wet-lab synthesis or sequencing. The channel is a stress model, not a platform model.
  Constraint rules are common heuristics, not a synthesis vendor's specification. Secondary structure and primer design
  are not modelled.
- **Guarantees are per ECC group.** Any M of K+M strands per group may be lost. Beyond that, success under random
  damage is a measured probability, not a promise.
- **Indels** need coverage > 1 (consensus alignment), or one of the opt-in single-read repairs: one indel per read
  (up to three of the same net direction within a search budget), or one contiguous burst per read (V3). Frame
  format 5 has no in-strand sync markers. Details: [docs/ERROR_MODEL.md](docs/ERROR_MODEL.md).
- **Scale of the sequencing chain.** Store, encode, decode, restore and random access are tested at 10 GB. The full
  simulated sequencing → clustering → consensus chain is measured on representative inputs up to 10 MB, because
  10× coverage of 10 GB means ~3.5 × 10¹¹ sequenced bases ([docs/LARGE_FILES.md](docs/LARGE_FILES.md#where-the-computational-boundary-is)).
- **Throughput** is CPU-bound Python/NumPy (no GPU); nothing here is RAM-class storage. Clustering and consensus are
  the slowest stages. V3 vectorised the inner-RS decoder, which was V2's bottleneck on noisy reads.
- **Encrypted archives reveal** the approximate size and per-chunk compressibility ([docs/SECURITY.md](docs/SECURITY.md)).
- **Random access** reads only the needed records of a strand *file*. It is not molecular (PCR-based) random access.
- **Compatibility:** VNX-DNA 2.0 cannot read encrypted archives whose store was resumed by V3 (it refuses them with
  exit 6). Full list: [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## License

See [LICENSE](LICENSE).
