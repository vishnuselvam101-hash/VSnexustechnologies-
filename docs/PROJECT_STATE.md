# VNX-DNA project state

_Last updated: 2026-09-30_

**Current version:** 3.0.0. The only version source is `src/vnxdna/_version.py`.
**Release status:** v3.0.0 is a research-grade *software* release on archive format 5. It follows a complete audit of
2.0.0 ([V3_AUDIT.md](V3_AUDIT.md)). There is **no wet-lab validation**. V2 and V1 archives stay readable.
**Branch:** `feature/vnx-dna-v3` (from `vnx-dna/v2`). V2 is tagged `v2.0.0`, V1 `v1.0.0`, and the V0.1 baseline
`v0.1-baseline`.
**Measurements:** everything in `research/results/v3/` was produced by `research/v3/run_v3_research.py` on this
machine, with VNX-DNA 2.0.0 (tag `v2.0.0`) as the baseline. Each file records its commit and dirty flag. The 2.0.0
results in `research/results/v2/` are kept as the historical record.

## Architecture

store (streaming container, format 5) → encode (outer Cauchy RS + frame format 5 + inner RS + DNA mapping) →
simulate / sequence (now with bursts) / simulate-errors → reads → cluster → consensus → decode (vectorised inner RS,
optional indel or burst resynchronisation, two-pass, disk-backed) → restore / verify. See
[ARCHITECTURE.md](ARCHITECTURE.md) and [STORAGE_FORMAT.md](STORAGE_FORMAT.md).

## V3 acceptance

| criterion | result | evidence |
|---|---|---|
| complete V2 audit, every finding reproduced | met: 48 code and test defects and 13 documentation claims found and fixed; 5 low-impact items remain, none of which can produce wrong output | [V3_AUDIT.md](V3_AUDIT.md) §4 |
| every fix has a regression test that fails on 2.0.0 | met, checked against a `v2.0.0` checkout | [TESTING.md](TESTING.md) |
| full test suite | met | V3_AUDIT §6 (generated) |
| V2 compatibility | met: V3 reads all 2.0.0 fixtures; V2 reads V3 output except resumed encrypted stores (clean exit 6) | [COMPATIBILITY.md](COMPATIBILITY.md) |
| V2 baseline and V3 measured on the same machine | met | [BENCHMARKS.md](BENCHMARKS.md) |
| no undetected corruption under any simulated error type | met: 0 in every sweep | [ERROR_MODEL.md](ERROR_MODEL.md) |
| no measured number typed by hand | met | `research/v3/render_v3_tables.py`, `research/v2/render_v2_tables.py` |

## Supported commands

`store`, `restore`, `verify`, `info`, `extract`, `encode`, `decode`, `recover`, `simulate`, `sequence`,
`simulate-errors` (new), `reads`, `cluster`, `consensus`, `pipeline`, `migrate`, `keygen`, `experiment run`,
`benchmark generate|scale|corruption|stages|v1`, `version`, `legacy` and `v1 …`. Exit codes are in [CLI.md](CLI.md).

## Configuration and guarantees

| aspect | state |
|---|---|
| container | `.vxdna` v2 (format 5); body length must equal `stored_size`; atomic, no-clobber publish |
| encryption | chunked AES-256-GCM, HKDF-SHA256, HMAC-SHA256; per-resume AEAD epochs persisted before use; HMAC-bound checkpoints; `final-seal-epoch-v3` for resumed stores |
| outer ECC | Cauchy RS (MDS): any M of K+M strands per group |
| inner ECC | RS per strand + CRC-32, vectorised bounded-distance decoder (`2e + f ≤ r`) |
| resynchronisation | consensus (coverage > 1); single-read indel repair and burst repair (coverage 1, opt-in) |
| channel | coverage models, substitutions, indels, bursts, dropout, duplicates, truncation, N, junk, contamination, reverse complements, reordering |
| ECC interface | `vnxdna.ecc.engine` (registry keyed by manifest names) |

## Security review (V3)

- Secret scan of all tracked files (key/token/password patterns, private keys): no secrets. The only key material
  in the repository is test keys derived from public strings (`tests/v1_support.py`, `tests/v2_support.py`,
  `tests/fixtures/v2_0/key.hex`), each documented as protecting nothing.
- No `eval`, `exec`, `pickle`, `shell=True` or `os.system` in `src/`.
- The crypto fixes are listed in [SECURITY.md](SECURITY.md#v3-changes).

## Known limitations

[LIMITATIONS.md](LIMITATIONS.md). Future work: [ROADMAP.md](ROADMAP.md).
