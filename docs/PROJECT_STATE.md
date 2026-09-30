# VNX-DNA project state

_Last updated: 2026-09-30_

**Current version:** 2.0.0. The only version source is `src/vnxdna/_version.py`.
**Release status:** v2.0.0 is a streaming, bounded-memory, research-grade *software* release. There is **no wet-lab
validation**. V1 archives stay readable (`vnx-dna v1 …`, `vnx-dna migrate`).
**Branch:** `vnx-dna/v2`. V1 is tagged `v1.0.0` on `vnx-dna/v0.2`; the V0.1 baseline is tagged `v0.1-baseline` (`cf7d1f5`).
**Measurements:** every result in `research/results/v2/` was produced from a fresh clone and a fresh venv at the
release-candidate commit `9f25598` (2.0.0rc4). The release commit differs from it only in version and documentation
(`git diff 9f25598 -- src` changes only `_version.py`).

## Architecture

store (streaming container, format 5) → encode (outer Cauchy RS + frame format 5 + inner RS + DNA mapping, chunk
parallel) → simulate / sequence → reads → cluster → consensus → decode (two-pass, disk-backed) → restore / verify.
See [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md) and [V2_FORMAT.md](V2_FORMAT.md). The V1 implementation is kept unchanged
and mounted as `vnx-dna v1`.

## V2 acceptance criteria

| criterion | result | evidence |
|---|---|---|
| clean install, `vnx-dna --help` works | met | release step 1 at `9f25598` |
| full test suite | met: **406 passed, 0 failed** | `pytest` at `9f25598`, fresh venv |
| 1 MB → 10 GB scale matrix: SHA-256, byte comparison and random access from container and from DNA | met: **PASS at 1 MB, 10 MB, 100 MB, 500 MB, 1 GB, 2 GB, 5 GB, 10 GB** | `scale-matrix.json`, [LARGE_FILES.md](LARGE_FILES.md) |
| 10 GB without 10 GB of RAM | met: peak RAM stays below 0.6 GiB at every size; from 100 MB to 10 GB it grows ×1.05–×1.27 per stage while the input grows ×100 | memory-scaling table in [LARGE_FILES.md](LARGE_FILES.md) |
| random access at ≈5 GB offset, 1 MB, independently compared | met (container and DNA) | `scale-matrix.json` |
| 1 GB corruption: damage within the guarantee recovered exactly; one strand more than a group can lose fails clearly with no output | met: **PASS** | `corruption.json`, [LARGE_FILES.md](LARGE_FILES.md) |
| experiments: 0 undetected corruption, 0 internal errors | met | `montecarlo.json`, `coverage.json`, `errors.json`, `abundance.json`, [EXPERIMENTS.md](EXPERIMENTS.md) |
| no measured number typed by hand | met | `research/v2/render_v2_tables.py` |

## Supported commands

`store`, `restore`, `verify`, `info`, `extract`, `encode`, `decode`, `recover`, `simulate`, `sequence`, `reads`,
`cluster`, `consensus`, `pipeline`, `migrate`, `keygen`, `experiment run`, `benchmark generate|scale|corruption|stages`,
`version`, `legacy` (read-only V0.1 archives) and `v1 …` (the V1 CLI). Exit codes are listed in [CLI.md](CLI.md).

## Configuration and guarantees

| aspect | state |
|---|---|
| container | `.vxdna` v2 (format 5): body first, canonical manifest, binary chunk index, plaintext index, SHA-256 trailer; written to `.partial`, fsynced, renamed atomically |
| chunking | fixed plaintext chunk size (64 KiB … 64 MiB); memory ≤ 2 × workers chunks in flight |
| compression | zstd per chunk, kept only if smaller |
| encryption | chunked AES-256-GCM; chunk count and `VNX-DNA/5` in the associated data; HKDF-SHA256 subkeys; per-chunk AEAD epoch so a resumed store never reuses a nonce |
| resume | `store --resume` with validated checkpoints, byte-identical to an uninterrupted run |
| outer ECC | Cauchy Reed–Solomon (MDS) per group: any M of K+M strands may be lost |
| inner ECC | RS per strand + CRC-32, re-checked after correction |
| DNA | frame format 5 (32-bit stripe index), 2bit / rotation3 / codebook8, GC / homopolymer / tandem-repeat / motif screening; FASTA or packed VXS + DNA index |
| channel | sequencing simulator: coverage models incl. log-normal abundance, synthesis and sequencing errors, duplicates, truncation, N, junk, contamination, quality scores |
| reads | filtering, clustering (address + minimizer index), consensus with quality-weighted voting and honest `N`s |
| decoder | two-pass, disk-backed; memory independent of the read-set size |
| profiles | compact, balanced, resilient, archival ([BENCHMARKS.md](BENCHMARKS.md)) |
| compatibility | V1 archives read and migrated with full verification ([COMPATIBILITY.md](COMPATIBILITY.md)) |

## Security review (at release)

- **Secret scan** of all tracked files (AWS/GitHub/Slack/API-key-style tokens, private keys, password/api_key/secret
  assignments; `gitleaks` not installed): no findings. No `.env` or key files are tracked; test keys are derived from public strings.
- **Unsafe code:** no `eval`, `exec`, `pickle`, `shell=True` or `os.system` in `src/`.
- **Files:** outputs are atomic and renamed into place only after verification. Stored names are never used as paths.
- **Generated data:** no generated test inputs, archives or strand files are tracked (only the 36 KB of V0.1
  compatibility fixtures under `tests/fixtures/v0_1/`).

## Known limitations

See the [README](../README.md#limitations). In short: software only, simulated channel not fitted to a platform,
guarantees are per ECC group, indels need coverage > 1 or the single-read realignment, the full sequencing →
clustering → consensus chain is measured on representative inputs rather than at 10 GB, and throughput is CPU-bound.

## Future research

See [ROADMAP.md](ROADMAP.md).

## Git / release

- Tag `v2.0.0` marks the release commit on `vnx-dna/v2`.
- Publishing (pushing the branch and tag without force, see [RELEASE_PROCESS.md](RELEASE_PROCESS.md)) happens after
  tagging, so this tagged tree cannot record its outcome. No credentials are stored in the repository.
