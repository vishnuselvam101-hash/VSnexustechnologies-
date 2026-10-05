# VNX-DNA 6 architecture

VNX-DNA is a CPU-only software implementation of the **digital side** of DNA data storage. It turns files and
directories into a verifiable archive, encodes the archive as constraint-screened DNA strands, can pass the strands
through a *simulated* storage and sequencing channel, and reconstructs the archive from noisy reads. A result is
reported as SUCCESS only after SHA-256 and Merkle verification.

> **Scope.** Everything here is software and simulation. No strand has been synthesised or sequenced by VNX-DNA.
> The channel models are configurable stress models, none fitted to a measured platform. "Recovery" means recovery
> from software-generated DNA through a simulated channel (**SIMULATED**). Labels used in this repository:
> VERIFIED (a committed test or script checks it), SIMULATED, THEORETICAL (specified or computed, not run),
> PHYSICAL (real DNA; there is none). See [LIMITATIONS.md](LIMITATIONS.md).

This page describes the code at `6.0.0.dev0` (`src/vnxdna/_version.py`). The normative text is the specification,
[spec/VNX-DNA-SPEC-V6.md](spec/VNX-DNA-SPEC-V6.md); the design record and migration plan are
[V6_ARCHITECTURE.md](V6_ARCHITECTURE.md), and the audit it started from is [V6_BASELINE_AUDIT.md](V6_BASELINE_AUDIT.md).
Where the code is behind the plan, [V6_DEFERRED.md](V6_DEFERRED.md) says so.

## Pipeline

The canonical stage graphs are specified in spec §5 (encode E0-E15, decode D0-D14) and implemented as two
orchestrators in `vnxdna.pipeline`.

```
FILES / DIRECTORIES
  E0-E6   collect, chunk, identify (SHA-256 or HMAC), dedup, compress (zstd, kept if smaller), seal (AES-256-GCM),
          container (.vnx: canonical manifest, Merkle root, trailer)
  E7-E9   plan geometry, outer code (Cauchy Reed-Solomon rows, optional column parity over stripes), superblock
  E10-E14 frame (address, payload, CRC-32), scramble + inner Reed-Solomon, 2-bit map with sync markers,
          constraint screening (GC, homopolymer, repeat, motif; up to 256 scrambler variants), ordered strand file
  E15     export package for a laboratory (vnx.export-package/1)           -- never executed by a laboratory
SIMULATED CHANNEL (vnxdna.simulation, vnxdna.providers.ReferenceSimulatorProvider)
  strand loss, coverage, synthesis / storage / amplification / sequencing stages, bursts, reverse complements
  D0-D4   ingest reads, probe the frame version and layout, orient, marker-template alignment (indel -> erasures)
  D5-D7   inner Reed-Solomon + CRC acceptance, spill to disk, opt-in recovery rounds (smart indel, soft decoding,
          retry band)
  D8-D11  superblock selection, consensus per address, outer erasure decoding, stripe (column) decoding
  D12-D14 verify against the superblock SHA-256, atomic publish (SUCCESS, or PARTIAL with verified files only),
          extract with per-chunk and per-file verification
```

Failure is fail-closed: a decode that cannot verify ends as FAILURE (or PARTIAL, listing the verified files) and writes
no unverified bytes (spec failure contract FC-1 to FC-5). Three committed experiment sets record 0 false SUCCESS for
VNX-DNA: 1,450 decodes in P4-EXP-01 (`experiments/v6/phase4/README.md`), 1,910 in AB-EXP-01
(`experiments/v6/align-band/README.md`) and 157 trials in the benchmark lab
(`benchmarks/competitors/lab/README.md`). All SIMULATED.

## Packages and layers

Every package is under `src/vnxdna/`. A module may import only from its own package or a lower layer; function-level
imports count (V6_ARCHITECTURE §3).

| Layer | Package | Responsibility |
|---|---|---|
| 0 | `vnxdna.core` | stable error codes, version registry, canonical JSON, hashing, atomic output, CRC-32, JSON Schemas (`core/schemas`), events and report builders |
| 1 | `vnxdna.native` | loader and backend registry for the three C kernels, ABI and source-hash checks, `native_status` |
| 2 | `vnxdna.archive` | VNX4 container, chunking, dedup, bounded zstd, AEAD and KDF, Merkle tree, extract, list, locate, verify |
| 2 | `vnxdna.codec` | GF(256), inner Reed-Solomon (native, NumPy and reference backends), outer Cauchy Reed-Solomon, redundancy profiles |
| 3 | `vnxdna.dnaenc` | strand layouts, frame 4, superblocks 1 and 2, scrambler, 2-bit mapping, markers, constraints, strand and read file I/O |
| 3 | `vnxdna.sync` | marker-template alignment, native aligner use, smart indel recovery |
| 4 | `vnxdna.recovery` | probe and dispatch, pass 1, spill, consensus (count vote; opt-in quality-weighted vote), soft decoding, recovery schedule and budgets, stripes, publish |
| 5 | `vnxdna.pipeline` | the two orchestrators, stage timing, events |
| 5 | `vnxdna.simulation` | `vnx.channel-model/1` models, staged simulator, Monte Carlo and sweeps |
| 5 | `vnxdna.physical` | physical-record schemas and validator (no physical record exists) |
| 6 | `vnxdna.providers` | `DNAWriter`, `DNAReader`, `DNAProvider` protocols; `ReferenceSimulatorProvider` (the only provider); export and import packages |
| 6 | `vnxdna.benchmark` | benchmark and experiment harness, data generator, sweeps |
| 7 | `vnxdna.sdk` | the stable Python API |
| 7 | `vnxdna.conformance` | conformance vector runner and a packaged vector subset |
| 8 | `vnxdna.commands` | the `vnx` CLI: argument parsing, one SDK call, JSON output |
| - | `vnxdna.v2`, `v3`, `v4`, `v5`, `v6`, `api`, `cli`, `legacy`, ... | compatibility paths and frozen older code; whole-module moves are aliases of the same module object, split modules are facades |

Rules R1-R6 (downward imports only; codec layers do not import simulation, providers, SDK or CLI; no import cycles;
legacy isolation; only `vnxdna.native` calls `ctypes`; the CLI imports only the SDK and core) are checked by
`tests/architecture/test_layers.py`, with an allow-list for the compatibility shims that may only shrink
(`tests/architecture/layer_allowlist.json`). `tests/architecture/test_public_paths.py` checks that every `vnxdna.*`
path used outside `src/` still imports.

The C sources and the in-place library names remain in `vnxdna/v5/native` and `vnxdna/v6/native`; only the bindings
moved to `vnxdna.native`. `experiments/v6/channel` and `experiments/v6/physical` still carry their own copies of the code
that `vnxdna.simulation` and `vnxdna.physical` now provide, and `V0.1-V3` code has not been moved into `vnxdna.legacy`
(except `v0_1`). Both are listed in [V6_DEFERRED.md](V6_DEFERRED.md) section 5.

## Public API and command line

`vnxdna.sdk` is the stable API. Functions: `archive`, `encode`, `decode`, `inspect`, `verify`, `extract`,
`list_entries`, `locate`, `simulate`, `channel_models` / `channel_model` / `channel_convert` / `channel_sweep`,
`benchmark`, `sweep`, `experiment_run` / `experiment_reproduce`, `conformance`, `native`, `version`, `keygen`. Results
are frozen dataclasses with `to_json()`, carried in a `vnx.result/1` envelope (software version, spec version, backends
with ABI and library SHA-256, input and output SHA-256, formats, timings, resources). Anticipated failures raise
`VNXError` subclasses with a stable `code` (spec §10), rendered as `vnx.error/1`.

`vnx` (entry point `vnxdna.commands:main`) is a thin layer over the SDK. Reference: [CLI.md](CLI.md). The V1-V3
`vnx-dna` CLI is unchanged and documented there.

## Formats and version axes

VNX-DNA versions nine things independently (spec §1, §4): software, specification, container format, strand frame,
superblock, codec identifier, channel model, provider adapter, and report/event/result schemas. At this commit:

| Axis | Value | Source |
|---|---|---|
| software | `6.0.0.dev0` | `src/vnxdna/_version.py` |
| specification | 6.0 (draft) | `docs/spec/VNX-DNA-SPEC-V6.md` |
| container | VNX4 4.0, writer provenance in `extensions.vnx` by default | [VNX4_FORMAT.md](VNX4_FORMAT.md); [STORAGE_FORMAT.md](STORAGE_FORMAT.md) |
| frame | 4 (frame 6 specified, not implemented: THEORETICAL, V7) | spec §3.5 |
| superblock | 1 and 2 (3 specified, not implemented: V7) | [V6_OUTER_CODE.md](V6_OUTER_CODE.md), spec §3.8 |
| channel model | `vnx.channel-model/1`; `/0` files and V4 channel configs are read and converted | [CHANNEL_MODEL.md](CHANNEL_MODEL.md) |
| providers | `ReferenceSimulatorProvider` only; vendor adapters are V11 | [INTEROPERABILITY.md](INTEROPERABILITY.md), [LAB_INTERFACE.md](LAB_INTERFACE.md) |

A reader that meets an unknown frame version refuses with `FRAME_VERSION_UNSUPPORTED` (exit 6); V1 and V3 pools are
refused with `LEGACY_FORMAT`; reads it cannot place are refused with `LAYOUT_UNDETECTED` (exit 3). V4 and V5 archives
keep decoding: golden archives `v4_0`, `v5_0` and `v6_0` are decoded by the test suite
([COMPATIBILITY.md](COMPATIBILITY.md)).

## Native kernels

Three optional C kernels (marker aligner, FASTQ/FASTA read parser, inner Reed-Solomon decoder with run-time AVX2 and
AVX-512BW selection) accelerate NumPy references that stay normative. `pip install .` builds all three; without a
compiler the references run, with the same results. Every decode report records the backend that ran
(`native_backends`). Installation, selection and diagnostics: [NATIVE_KERNELS.md](NATIVE_KERNELS.md).

## Security model

Trust boundaries, findings and fuzz campaigns: [security/V6_SECURITY_MODEL.md](security/V6_SECURITY_MODEL.md) and
[security/V6_FUZZ_REPORT.md](security/V6_FUZZ_REPORT.md). Container reads cap the size a forged superblock may claim
(`--max-container-bytes`); `--key-file` is checked on a full decode; `--expect-archive-id` and `--expect-sha256` bind a
decode to a known archive. No finding rated CRITICAL or HIGH is recorded in the model.

## Research, benchmarks and conformance

- Benchmark methodology, the benchmark lab and conformance vectors: [BENCHMARKING.md](BENCHMARKING.md).
- Research syntheses: [research/V6_COMPETITIVE_RESEARCH.md](research/V6_COMPETITIVE_RESEARCH.md),
  [research/V6_TECHNICAL_RESEARCH.md](research/V6_TECHNICAL_RESEARCH.md).
- What V6 does not contain: [V6_DEFERRED.md](V6_DEFERRED.md).
- What each V6 phase built: [V6_IMPLEMENTATION_SUMMARY.md](V6_IMPLEMENTATION_SUMMARY.md); results, acceptance table and
  evidence classes: [V6_COMPLETION_REPORT.md](V6_COMPLETION_REPORT.md).

## History

Earlier architecture documents describe earlier code and are not rewritten:
[V3_ARCHITECTURE.md](V3_ARCHITECTURE.md) (the previous content of this page, format 5, `vnxdna.v2` / `vnxdna.v3`),
[V4_ARCHITECTURE.md](V4_ARCHITECTURE.md), [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md),
[V1_ARCHITECTURE.md](V1_ARCHITECTURE.md), [V5_COMPLETION_REPORT.md](V5_COMPLETION_REPORT.md).
