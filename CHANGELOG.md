# Changelog

## 1.0.0 — 2026-09-29

First stable research-grade release. Computational only: no wet-lab validation.

### Fixed (V0.1 defects; see docs/V0.1_BASELINE.md)
- **Erasure code was not MDS.** V0.1 claimed any 4 of 12 shards recoverable (8+4), but 10 of 495 four-loss patterns,
  including {4,5,7,11}, could not be recovered. It is replaced by a systematic Cauchy Reed–Solomon code with a proof
  and exhaustive verification.
- `vnx-dna simulate` recursed infinitely. The simulator is rewritten and reports the events that actually happened.
- A corrupt first duplicate hid a valid copy. Every copy is now validated, and conflicts resolve by majority or become
  an erasure.
- `verify` printed the *stored* hash as the "recovered" hash. It now recomputes the SHA-256 of the recovered bytes.
- Malformed manifests raised `KeyError`/`TypeError`. They now raise structured errors with stable exit codes; the
  CLI used to exit 2 for every failure.
- With encryption, the manifest leaked the file name, size and plaintext SHA-256, and it was unauthenticated. These
  fields are now sealed, and the manifest is HMAC-authenticated.
- Strand addresses lived only in FASTA headers. They are now inside the DNA.
- Version labels disagreed (1.0.0 / 0.1.0). There is now one source, `src/vnxdna/_version.py`.

### Added
- The `.vxdna` self-describing container (archive format 4), a strict canonical manifest, and `required_features`.
- AES-256-GCM per chunk with HKDF-SHA256 subkeys, HMAC-SHA256 manifest authentication, and wrong-key detection.
- Two-level coding: per-strand CRC-32 + inner RS, and an outer Cauchy RS K+M with shortening. Metadata strands make a
  FASTA pool a complete archive.
- 2bit / rotation3 / codebook8 mappings, a constraint layer (GC, windowed GC, homopolymers, motifs) and scrambler
  screening that fails loudly when constraints are unsatisfiable.
- A seeded channel simulator (dropout, coverage, substitutions, indels, bursts, reverse complement, shuffle) with an
  event log.
- Experimental RS-assisted indel realignment (`--experimental-indel-repair`).
- Random access by chunk or byte range (`extract`).
- CLI: `store/pack, encode, simulate, decode, restore, recover, verify, info, extract, pipeline, keygen, benchmark,
  version, legacy`.
- Explicit read-only V0.1 compatibility (`vnx-dna legacy`).
- Test suite: unit, integration, property (Hypothesis), fuzz/adversarial, CLI, the clean-room acceptance test, and a
  README-executes test. Also a benchmark suite, an experiment runner and an extended fuzz campaign.

### Removed
- The V0.1 HTTP API and its web and plotting dependencies (fastapi, uvicorn, pandas, matplotlib). The V0.1 code,
  tests, docs and results are preserved under `research/legacy/`.

## 0.1 (baseline) — tag `v0.1-baseline` (cf7d1f5)

The R&D prototype. See docs/V0.1_BASELINE.md for its forensic record.
