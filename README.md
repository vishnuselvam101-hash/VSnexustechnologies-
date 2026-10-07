# VNX-DNA

**Current line: V9 (development branch `build/v9-adaptive-recovery`; not released, not tagged).** Released versions are
the git tags `v4.0.0` … `v8.0.0`. The package version string is still `6.0.0.dev0`: it is written into every archive
manifest, and archive byte identity across V6–V9 is pinned by the compatibility tests, so it changes only at a release
the founder approves. Release notes per version: [CHANGELOG.md](CHANGELOG.md) and `docs/V<N>_COMPLETION_REPORT.md`.

VNX-DNA is a CPU-only software stack for the digital side of DNA
data storage. It packs files and directories into a verifiable archive, encodes the archive as constraint-screened DNA
strands, can pass the strands through a configurable *simulated* storage and sequencing channel, and reconstructs the
archive from noisy reads. It reports success only after SHA-256 and Merkle verification, and otherwise writes nothing
(or, for PARTIAL recovery, only files that verified individually).

> **Scope: SIMULATED, NOT PHYSICALLY VALIDATED.** Every channel result in this repository comes from software strands
> through a software channel. No strand has been synthesised, stored, amplified or sequenced by VNX-DNA, and none of
> the 14 shipped channel models is fitted to a measured platform. Nothing here shows physical DNA storage, synthesis or
> sequencing yields, storage lifetime, cost, or production readiness. See [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

The current product is two interfaces over one codec:

* the **`vnx` command line** (`vnxdna.commands`), a thin layer that makes one SDK call per command and prints JSON;
* the **`vnxdna.sdk` Python API**, the stable surface (`archive`, `encode`, `decode`, `inspect`, `verify`, `extract`,
  `simulate`, `benchmark`, `conformance`, `version`, ...), returning `vnx.result/1` envelopes and raising typed errors
  with stable codes.

The formats are unchanged from VNX-DNA 4 and 5 (VNX4 container, strand frame 4, superblocks 1 and 2); V4 and V5
archives and reads keep decoding. The `vnx-dna` tool (V1-V3, format 5) is still installed and unchanged. Design:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); normative text: [docs/spec/VNX-DNA-SPEC-V6.md](docs/spec/VNX-DNA-SPEC-V6.md);
changes: [CHANGELOG.md](CHANGELOG.md).

## Install for VNX-DNA 6 (Linux, Python >= 3.12)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install .                    # builds the three native kernels when a C compiler works; '.[dev]' adds the test tools
python -m vnxdna.native          # per-kernel backend, SIMD level, ABI and any load error (JSON)
vnx version                      # software, specification and format versions
```

The aligner, FASTQ/FASTA read parser and inner Reed-Solomon decoder have optional C kernels. Without a compiler, or if
one kernel fails to build, installation still succeeds and the bit-identical NumPy references run, only slower
(`python -m vnxdna.native --require-native` exits 1 unless every kernel is native). The kernels were built and tested on x86-64 only. For an editable checkout, build the
kernels in place with the commands in [docs/NATIVE_KERNELS.md](docs/NATIVE_KERNELS.md). A decode report records the
backends that ran.

## Quick start: VNX-DNA 6

```bash
vnx archive ./dataset archive.vnx                       # files and directories to a verified VNX4 archive
vnx verify archive.vnx
vnx encode archive.vnx strands.fasta                    # DNA strands (default profile v4-balanced, 313 nt, sync markers)
vnx validate strands.fasta                              # constraint diagnostics (JSON)
vnx channel models                                      # the shipped channel models (every one SIMULATED)
vnx channel simulate strands.fasta reads.fastq --model illumina-like --coverage 10 --seed 7     # SIMULATED reads
vnx decode reads.fastq -o recovered.vnx --extract ./restored --report decode.json              # verified reconstruction
vnx inspect reads.fastq                                 # can this build read this file? (frame and layout probe)
vnx conformance                                         # run the conformance vectors; exit 0 only if CONFORMANT
```

The same from Python:

```python
from vnxdna import sdk

enc = sdk.encode("archive.vnx", "strands.fasta", dna=sdk.DNAOptions(profile="v4-balanced"))
sdk.simulate("strands.fasta", "reads.fastq", model="illumina-like", seed=7, coverage=10)       # SIMULATED
res = sdk.decode("reads.fastq", "recovered.vnx")        # status SUCCESS, PARTIAL or FAILURE; vnx.result/1 envelope
```

Opt-in decoder options (all off by default): `--indel-recovery smart`, `--soft-decoding auto`,
`--consensus-weighting quality`, `--retry-band 16`. Their measured effects, and why none is a default, are in
[docs/CLI.md](docs/CLI.md) and [docs/V6_DEFERRED.md](docs/V6_DEFERRED.md). Exit codes: 0 success, 1 verification failed,
3 invalid input, 4 key/authentication, 5 insufficient redundancy, 6 unsupported format, 7 configuration, 8 output,
9 PARTIAL, 10 provider error, 70 internal error ([docs/CLI.md](docs/CLI.md)).

## What is SIMULATED, VERIFIED, THEORETICAL

| Label | Meaning | Examples in this repository |
|---|---|---|
| **VERIFIED** | checked by a committed test or script | native kernels equal their NumPy references (golden cases and fuzzing, [NATIVE_KERNELS.md](docs/NATIVE_KERNELS.md)); the golden archives `v4_0`, `v5_0`, `v6_0` decode; 226 conformance vectors ([BENCHMARKING.md](docs/BENCHMARKING.md)) |
| **SIMULATED** | software strands through a software channel | every recovery rate, threshold and benchmark-lab number below |
| **MEASURED** | time or memory on the development host (shared, x86-64) | installed-path decode 10.8 s to 5.3 s (2.04x) after pip builds the kernels: one host, one workload ([benchmarks/v6/native_packaging/README.md](benchmarks/v6/native_packaging/README.md)) |
| **PUBLIC-DATA-DERIVED** | statistics from another group's public reads | per-read error rates of the public nanopore CNR dataset ([experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md](experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md)) |
| **THEORETICAL** | specified or computed, not run | frame 6, superblock 3, primers, the wide address class ([docs/spec/VNX-DNA-SPEC-V6.md](docs/spec/VNX-DNA-SPEC-V6.md) §3.5-§3.8) |
| **PHYSICAL** | real DNA | none |

Results (SIMULATED unless labelled; each from a committed file):

* **Outer code under strand loss.** The V6 product code (opt-in) decodes 20/20 seeds up to 16 % i.i.d. strand loss,
  against 7 % for the V5 code, at a redundant-strand overhead of 0.249 against 0.251
  ([experiments/v6/phase1/summary.md](experiments/v6/phase1/summary.md)).
* **No silent wrong output.** 0 false SUCCESS in 1,450 decodes (failure taxonomy), 1,965 decodes (quality-weighted
  consensus comparison) and 1,910 decodes (retry-band comparison), and in 157 benchmark-lab trials
  ([experiments/v6/phase4/README.md](experiments/v6/phase4/README.md),
  [experiments/v6/align-band/README.md](experiments/v6/align-band/README.md),
  [benchmarks/competitors/lab/README.md](benchmarks/competitors/lab/README.md)).
* **Benchmark lab B0.** VNX-DNA run beside DNA-RS, DNA Fountain and DNA-Aeon in the ETH `dt4dds-benchmark` harness (280
  trials; i.i.d. channel; 3 seeds per point; not the published protocol). VNX-DNA returned no wrong output with exit 0;
  DNA-RS did in 2 trials and DNA Fountain in 16. VNX-DNA was weaker at 10 % dropout, at 1 % errors near 1 bit/nt and near
  1.5 bit/nt (SIMULATED), and writes longer strands (14 bytes of header and CRC in every strand). Table and caveats:
  [benchmarks/competitors/lab/README.md](benchmarks/competitors/lab/README.md).
* **Not decoded.** The simulated nanopore-like model decoded 0/20 at coverage 3, 5 and 10 and 0/10 at coverage 15 and
  30, with or without the opt-in retry band ([experiments/v6/align-band/README.md](experiments/v6/align-band/README.md));
  the measured causes and the V7 plan are in [docs/V6_DEFERRED.md](docs/V6_DEFERRED.md) section 2.

## V6 documentation

[Architecture](docs/ARCHITECTURE.md) · [Specification](docs/spec/VNX-DNA-SPEC-V6.md) ·
[Design and migration plan](docs/V6_ARCHITECTURE.md) · [Baseline audit](docs/V6_BASELINE_AUDIT.md) ·
[Outer code](docs/V6_OUTER_CODE.md) · [Channel models](docs/CHANNEL_MODEL.md) ·
[Native kernels](docs/NATIVE_KERNELS.md) · [Interoperability](docs/INTEROPERABILITY.md) ·
[Laboratory interface](docs/LAB_INTERFACE.md) · [DDSA mapping](docs/DDSA_MAPPING.md) ·
[Security model](docs/security/V6_SECURITY_MODEL.md) · [Fuzz report](docs/security/V6_FUZZ_REPORT.md) ·
[CLI](docs/CLI.md) · [Compatibility](docs/COMPATIBILITY.md) · [Storage format](docs/STORAGE_FORMAT.md) ·
[Conformance](docs/CONFORMANCE.md) · [Benchmarking](docs/BENCHMARKING.md) · [Deferred work](docs/V6_DEFERRED.md) ·
[Implementation summary](docs/V6_IMPLEMENTATION_SUMMARY.md) · [Completion report](docs/V6_COMPLETION_REPORT.md) ·
[Competitive research](docs/research/V6_COMPETITIVE_RESEARCH.md) · [Technical research](docs/research/V6_TECHNICAL_RESEARCH.md)

## Limitations of VNX-DNA 6

- **Software only.** No wet-lab synthesis or sequencing; the channel models are stress models, not platform models.
  Provider integration exists only for a software reference simulator ([docs/INTEROPERABILITY.md](docs/INTEROPERABILITY.md));
  there are no vendor adapters, no primers and no DDSA Sector Zero/One output.
- **Nanopore-like reads are not decoded** in any tested simulated condition; the B0 benchmark is small and not the
  published protocol; most opt-in options are unproven as defaults ([docs/V6_DEFERRED.md](docs/V6_DEFERRED.md)).
- **Address space and strand length.** The default strand is 313 nt (353 nt with two 20-nt primers, over a 350-nt pool
  limit); one archive addresses about 11 TB by the 4-byte group index (THEORETICAL arithmetic in
  [docs/COMPETITIVE_GAP_ANALYSIS.md](docs/COMPETITIVE_GAP_ANALYSIS.md)).
- **Open items:** MSan and non-x86 builds were not run; open LOW findings are listed in [docs/security/V6_SECURITY_MODEL.md](docs/security/V6_SECURITY_MODEL.md).

---

# Earlier versions

The text below is the README of VNX-DNA 5, 4 and 3 and is kept as written. Commands under "V4 quick start" and the V3
sections still work as described.

---

# VNX-DNA 5

**VNX-DNA 5.0** adds an adaptive, probabilistic decoding foundation on top of the unchanged VNX-DNA 4 format
([completion report](docs/V5_COMPLETION_REPORT.md)). Every V5 result is **SIMULATED** (computational/software
validation); no DNA has been synthesised, stored or sequenced. The V4 format, codes, cryptography and integrity checks
are unchanged, and V4 decoding stays the default.

- **Native alignment kernel** (C via ctypes, optional build): bit-exact with the V4 NumPy reference on every golden
  case, 9.6–11.3× faster aligner; `VNXDNA_ALIGN_BACKEND=auto|native|reference`
  ([docs/V5_PHASE2_NATIVE_ALIGNMENT.md](docs/V5_PHASE2_NATIVE_ALIGNMENT.md)).
- **Smart indel recovery** (opt-in, `--indel-recovery smart`): median erased nucleotides per true indel 24 → 4 at
  coverage 1, fail-closed ([docs/V5_PHASE3_INDEL_RECOVERY.md](docs/V5_PHASE3_INDEL_RECOVERY.md)).
- **Soft-decision inner decoding** (opt-in, `--soft-decoding erasure|chase|auto`, `--min-quality`): GMD/Chase over the
  unchanged RS verifier ([docs/V5_PHASE4_REPORT.md](docs/V5_PHASE4_REPORT.md)).
- **Deferred per-read recovery** (`--recovery-schedule deferred`, default): smart/soft search only where a group is
  still undecodable ([docs/V5_PHASE3_OPTIMIZATION.md](docs/V5_PHASE3_OPTIMIZATION.md)).

```bash
vnx decode reads.fastq -o recovered.vnx --indel-recovery smart --soft-decoding auto --min-quality 20
```

---

# VNX-DNA 4 (format and CLI, unchanged in 5.0)

**A CPU-first, research-grade software stack for DNA data storage.** VNX-DNA 4 packs files and directories into a
verifiable archive and encodes it as constraint-screened DNA strands. Through a configurable *simulated* storage and
sequencing channel, it reconstructs the archive from noisy reads, including insertions, deletions, dropout and
uneven coverage. It reports success only after SHA-256 and Merkle verification.

> **Scope: SIMULATED, NOT PHYSICALLY VALIDATED.** All results come from computation and simulation. No sequence
> has been synthesised or sequenced. The channel is a configurable model, not fitted to any platform. Nothing here
> demonstrates physical DNA storage, synthesis or sequencing yields, storage lifetime, or production readiness.
> See [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

**New in 4.0** ([architecture](docs/V4_ARCHITECTURE.md), [format](docs/VNX4_FORMAT.md),
[completion report](docs/V4_COMPLETION_REPORT.md)):
- **VNX4 archives**: multiple files and directories, content-addressed chunks with deduplication, an RFC 6962 Merkle
  tree (`vnx verify --chunk N` checks one chunk), AES-256-GCM with key files or scrypt passphrases, sealed file
  tables, and random access (`vnx locate`, `vnx extract --file`).
- **Strand frame v4 with in-strand synchronisation markers.** A marker-template alignment turns insertions and
  deletions into erasures for the inner Reed–Solomon code. With the `v4-indel` profile, 87–100 % of single reads
  carrying 2–3 indels decoded in our simulated tests; V3 repairs one indel or one burst per read
  ([docs/INDEL_ENGINE.md](docs/INDEL_ENGINE.md), [docs/INDEL_RESEARCH.md](docs/INDEL_RESEARCH.md)).
- **Self-describing strand pools** (superblock), selective decoding of single files from reads, and PARTIAL recovery
  that extracts only individually verified files.
- **A configurable stochastic channel**: substitutions, insertions, deletions, dropout,
  Poisson/negative-binomial coverage, duplication, homopolymer-dependent errors, GC bias, bursts, N calls and reverse
  complements. It is seeded and independent of the worker count.
- **Pluggable outer codes**: Cauchy RS (default, MDS; V3's code) and an EXPERIMENTAL GF(2) fountain code, compared
  at equal redundancy ([docs/ECC_ARCHITECTURE.md](docs/ECC_ARCHITECTURE.md)).
- **Configurable constraint engine** with JSON diagnostics (`vnx validate`).
- **Benchmarks, error sweeps and reproducible experiment directories** (`vnx benchmark`, `vnx sweep`,
  `vnx experiment run|reproduce`), with recovery curves that count every failure.
- **Performance**: fast RS kernels, bit-identical to V3 and 2.5–5.2× faster, plus multi-worker encode, channel and
  decode ([docs/PERFORMANCE.md](docs/PERFORMANCE.md)).
- **V3 is unchanged** and still included (`vnx-dna`, format 5). Its 601 tests pass.

## Install (Linux, Python ≥ 3.12, CPU only)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # '.[dev]' for the tests
vnx --help                  # V4
vnx-dna --help              # V3 (format 5), unchanged
```

## V4 quick start

```bash
vnx archive ./dataset archive.vnx                     # files/directories → verified VNX4 archive
vnx inspect archive.vnx && vnx verify archive.vnx
vnx encode archive.vnx strands.fasta                  # → DNA strands (v4-balanced: 313 nt, sync markers, Cauchy RS 64+16)
vnx validate strands.fasta                            # constraint diagnostics (JSON)
vnx channel simulate strands.fasta reads.fastq --config channel.json      # SIMULATED storage + sequencing
vnx decode reads.fastq -o recovered.vnx --extract ./restored              # verified reconstruction
vnx decode reads.fastq --select dataset/one.bin --extract ./one           # random access from reads
vnx experiment run experiments/EXP-0001-substitution/config.json          # reproducible experiment
```

`channel.json` example: `{"substitution_rate": 0.003, "insertion_rate": 0.001, "deletion_rate": 0.001,
"dropout_rate": 0.03, "coverage": 5, "coverage_model": "poisson", "seed": 12345}`

Exit codes: 0 success, 1 verification failed, 3 invalid input, 4 key/authentication, 5 insufficient redundancy,
6 unsupported format, 7 configuration, 8 output, **9 PARTIAL recovery**, 70 internal error.

## V4 documentation

[Architecture](docs/V4_ARCHITECTURE.md) · [Format](docs/VNX4_FORMAT.md) · [Archive engine](docs/ARCHIVE_ENGINE.md) ·
[Channel model](docs/CHANNEL_MODEL.md) · [Indel engine](docs/INDEL_ENGINE.md) · [Indel research](docs/INDEL_RESEARCH.md) ·
[ECC architecture](docs/ECC_ARCHITECTURE.md) · [Constraint engine](docs/CONSTRAINT_ENGINE.md) ·
[Performance](docs/PERFORMANCE.md) · [Benchmarking](docs/BENCHMARKING.md) · [Reproducibility](docs/REPRODUCIBILITY.md) ·
[Security](docs/SECURITY.md) · [Limitations](docs/LIMITATIONS.md) · [Engineering log](docs/V4_ENGINEERING_LOG.md) ·
[Audit of V3](docs/V4_AUDIT.md) · [Completion report](docs/V4_COMPLETION_REPORT.md)

---

# VNX-DNA 3 (format 5, `vnx-dna`) — included unchanged

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
- **Release review.** A final review fixed 15 more issues before release, among them two data-loss paths
  (`pipeline --work-dir` and `--report` onto a key file), SIGTERM cleanup, and symlink-safe temporary files
  ([audit §4.7](docs/V3_AUDIT.md#47-found-in-the-release-review-2026-10-01)).
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
