# VNX-DNA Project State

_Last updated: 2026-09-29_

**CURRENT VERSION:** V0.1 research prototype. The package metadata says `1.0.0`, but that label is unsupported; see `V0.1_BASELINE.md` §1.
**CURRENT MILESTONE:** Gate 0 (V0.1 forensic baseline) is **complete**. V0.2 (architecture hardening) is **not started**.
**LAST VERIFIED COMMIT:** `cf7d1f5`, tagged `v0.1-baseline`. Work continues on branch `vnx-dna/v0.2`.

## TEST STATUS
* `pytest`: 36 passed, 0 failed, 0 skipped, 0 warnings (Python 3.12.3)
* `scripts/acceptance_test.py`, `scripts/ecc_acceptance_matrix.py`, `scripts/phase3_acceptance_matrix.py`: all exit 0
* Coverage gaps: no CLI tests, no API tests, RS tested only at K=4/M=2, non-seeded random inputs

## BENCHMARK STATUS
* Single-run baseline timings up to 1 MB are in `research/v0_1_forensics/baseline_perf.json`.
* There is no reproducible benchmark framework yet. 10 MB has not been measured.
* The committed `results/` E001–E008 have invalid provenance. Do not cite them (`V0.1_BASELINE.md` §4.3).

## KNOWN FAILURES (open defects)
1. **CRITICAL.** The RS shard codec is not MDS. Some patterns with ≤ M erasures fail for every probed K,M except 4/2. Failure is safe (it raises).
2. `vnx-dna simulate` crashes with infinite recursion.
3. Committed experiment results are not reproducible, and E004 dropped 0 chunks.
4. The first duplicate observation wins, even when it is corrupt.
5. Malformed manifests raise `KeyError`/`TypeError` instead of typed errors.
6. When encrypted, the manifest leaks the plaintext SHA-256, name, and size. The manifest is unauthenticated.
7. The CLI uses exit code 2 for every error, and `verify` displays the manifest hash as the "recovered" hash.

## KNOWN LIMITATIONS
* Addressing and checksums are out-of-band in FASTA headers, and the channel never corrupts them.
* There is no nucleotide-level substitution correction and no indel correction. Both are detected and then treated as erasures.
* Processing is in memory only. Throughput is ≈0.85 MB/s (no ECC) and ≈0.27 MB/s (RS 8+4) on one core.
* No random access exists in the shipped pipeline.

## NEXT ACTION
Start V0.2 in this order:
1. A new MDS generator (Cauchy) in a new format version, plus a legacy decoder and exhaustive erasure tests.
2. CLI fixes and CLI tests.
3. A schema-validated, versioned, deterministic container. Update `docs/V0.2_ARCHITECTURE.md` and `docs/FORMAT_SPECIFICATION.md`.
4. Retire or quarantine the legacy archive pipeline.

Owner decisions still pending:
* Whether to reset the package version from 1.0.0 to 0.2.0.
* Whether the legacy `VNXDNA 0.1` JSON archive needs to stay supported.
* Whether to push `v0.1-baseline` and the branch to GitHub.

## ARCHITECTURAL RISKS
* In-band addressing (index inside the DNA) changes the strand format and density. Every recovery result so far assumes a perfect index channel.
* Two parallel pipelines split tests and docs, and the experiments test the pipeline that is not shipped.
* A new RS generator breaks bit-compatibility of parity. It needs strict version dispatch, or old datasets will fail to decode (safely) or be misread.
