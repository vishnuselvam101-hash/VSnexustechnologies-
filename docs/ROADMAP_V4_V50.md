# Roadmap V4 → V50 → VNX-DNA ULTIMATE

**Status: PLAN, not commitment.** Only V4 is concrete; everything after it is a direction that will be re-planned as
results come in. A version exists when `vnxdna release check --version VN` reports ACCEPTED, not when code compiles.
Version counts are not a goal: a range may finish in fewer versions, or be merged with another.

Common to every version:

* **Entry:** previous version accepted; `vnxdna doctor` has no FAIL; work on `development` / `feature/*`.
* **Exit:** BUILD, TEST, INTEGRATION, REGRESSION, SECURITY, PERFORMANCE, REPRODUCIBILITY, DOCUMENTATION, LINT
  (`config/laya/release-gates.yaml`), plus SCIENTIFIC_VALIDATION for research versions (V6–V16, V37–V40) and
  COMMERCIAL_READINESS_REVIEW for commercial versions (V41–V47, V50) — both human reviews.
* **Always out of scope:** claims about physical DNA (synthesis, storage lifetime, sequencing accuracy, wet-lab
  feasibility) unless a real laboratory experiment is done and published; "beats X" claims without a MEASURED
  comparison ([benchmarks/COMPETITIVE.md](../benchmarks/COMPETITIVE.md)); revenue or market claims.
* V1/V2/V3 archives keep decoding in every version.

## V4–V5 — architecture hardening

Grounded in the V3 state ([LIMITATIONS.md](LIMITATIONS.md), [V3_AUDIT.md](V3_AUDIT.md)):

* Goals: make the ECC engine (`vnxdna.ecc.engine`) a real plug-in boundary (outer/inner codes registered with
  feature flags and compatibility tests); add resume for `encode`/`decode` (they restart today); bounded memory for
  one very large consensus cluster; V3 rerun of the 10 GB scale matrix (V3 was measured up to 5 GB); clarify the
  library API surface used by `ops/` (V1 in-memory `api.store` vs the V2 streaming path).
* Exit: the gates above; no format change without a format document and compatibility fixtures.
* Risks: breaking format compatibility; refactors that slow the hot paths (caught by REGRESSION).

## V6–V8 — advanced DNA encoding (research)

* Goals: secondary-structure / melting-temperature screening, synthesis-cost model, alternative mappings evaluated
  under the same channel; density reported as input-bits per nucleotide with ECC and metadata included.
* Out of scope: physical density (bytes/gram).
* Risks: density gains that vanish under a realistic channel.

## V9–V12 — advanced error correction (research)

* Goals: fountain/LT outer codes and cross-chunk interleaving for correlated dropout (not implemented in V3);
  measured overhead vs recovery curves against the V3 Cauchy RS baseline.
* Risks: probabilistic codes without a clear guarantee; must keep "fail detectably, never wrongly" (WRONG_OUTPUT = 0).

## V13–V16 — indel/dropout resilience (research)

* Goals: in-strand synchronisation markers or watermark/VT codes for indels at coverage 1 beyond one indel or one
  burst per read (V3 limit); cancelling indels (+1 −1); several bursts per read; channel models fitted to published
  data (position-, context- and GC-dependent errors).
* Risks: fitted channels mistaken for physical validation (they are not).

## V17–V20 — scaling and random access

* Goals: larger pools; molecular-addressing simulation (primer design per partition and a simulated selection step —
  V3 random access is a software DNA index); faster clustering and consensus (slowest stages in V3).
* Risks: simulated selection presented as physical random access.

## V21–V24 — performance optimisation

* Goals: profile-driven optimisation of clustering, consensus and the decoder; optional native extensions (Rust/C)
  behind the pure-Python reference; GPU only as an optional accelerator if profiling shows the need.
* Risks: native code diverging from the reference (differential tests required).

## V25–V28 — security / cryptographic architecture

* Goals: password KDF, size padding, random per-run nonce component, key-management guidance, external review of the
  container format (V3 leaks chunk count, compressibility and ECC parameters).
* Risks: cryptographic misuse; needs an independent reviewer.

## V29–V32 — distributed archival

* Goals: archives split across storage sites/pools, multi-archive pools (`--archive-tag` exists in V3), integrity
  auditing over time.

## V33–V36 — synthesis/sequencing interfaces

* Goals: import/export adapters for vendor file formats (order sheets, FASTQ/BAM ingestion) and validators.
* Out of scope: claims that an order would synthesise correctly.

## V37–V40 — simulation, experimentation, benchmarking (research)

* Goals: richer channel simulators, experiment campaigns with confidence intervals, competitor harnesses where code
  is available (MEASURED rows), published-result tables (PUBLISHED rows).

## V41–V44 — enterprise SDK/API (commercial)

* Goals: stable versioned Python API, service API, SDK documentation, licence review of dependencies (SBOM).

## V45–V47 — deployment and commercialisation infrastructure (commercial)

* Goals: packaging, deployment guides, PoC and evaluation kits ([COMMERCIAL_READINESS.md](COMMERCIAL_READINESS.md)).

## V48 — full integration · V49 — adversarial audit · V50 — release candidate

* V48: every subsystem integrated and gated together. V49: red-team and external security audit; every finding a
  test. V50: commercial release candidate.

## VNX-DNA ULTIMATE

VNX-DNA ULTIMATE RELEASE CANDIDATE, then VNX-DNA ULTIMATE, exist only when all of these hold:

1. V3 baseline preserved (`v3.0.0` unchanged; `docs/releases/V3_BASELINE.md`).
2. V4–V50 completed as applicable, each with an ACCEPTED acceptance report.
3. All acceptance gates pass on the candidate; regressions resolved.
4. Security audit completed.
5. Benchmark suite completed and published from measurements.
6. Reproducibility verified.
7. Documentation completed.
8. Commercial SDK validated.
9. Known limitations documented.
