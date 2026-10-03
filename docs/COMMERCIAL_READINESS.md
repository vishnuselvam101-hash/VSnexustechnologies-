# Commercial readiness

**Status:** VNX-DNA is a software platform for computational DNA data storage, validated only in simulation
([LIMITATIONS.md](LIMITATIONS.md)). This document lists the infrastructure for future commercial use. It makes no
revenue, market-size or customer claims.

## What the environment can produce today

| Deliverable | Source | Command |
|---|---|---|
| Technical baseline report | `docs/releases/V3_BASELINE.md` (+ `v3-baseline.json`) | `vnxdna baseline [--measure]` |
| Benchmark report | `/opt/vnx-dna/reports/benchmarks/<suite>/<run>/result.{json,md}` | `vnxdna benchmark --dna --suite standard` |
| Experiment report | `/opt/vnx-dna/experiments/<suite>/<run>/summary.md` | `vnxdna experiment error-models` |
| Acceptance report | `release/<VERSION>_ACCEPTANCE_REPORT.md` | `vnxdna release check --version VN` |
| SBOM (CycloneDX JSON) | `/opt/vnx-dna/reports/security/sbom.cdx.json` | `vnxdna security` |
| Security summary | `/opt/vnx-dna/reports/security/summary.json` (gitleaks, pip-audit, permissions) | `vnxdna security` |
| Architecture / format docs | `docs/ARCHITECTURE.md`, `docs/STORAGE_FORMAT.md`, `docs/V2_FORMAT.md`, `docs/ENVIRONMENT.md` | — |
| CLI / API docs | `docs/CLI.md`, module docstrings | — |
| Environment report | `/opt/vnx-dna/reports/ENVIRONMENT_READY.md` | `vnxdna report` |
| Customer demo (simulated) | end-to-end run with SHA-256 check | `vnxdna e2e`, `vnx-dna pipeline` |

## Areas and what is missing

| Area | Exists | Missing |
|---|---|---|
| SDK licensing | MIT-licensed library; SBOM of the dependency tree | licence review of every dependency, chosen commercial licence model, stable versioned API (V41–V44) |
| Enterprise deployment | Dockerfile; governed execution | deployment guide, service packaging, monitoring for customer sites |
| API licensing | CLI and Python API | network service API, authentication, rate limits |
| OEM integration | file formats documented with compatibility tests | integration guide, ABI/API stability policy |
| Technical PoC | e2e and pipeline commands, benchmarks | PoC kit with a customer's own data and acceptance criteria |
| Benchmark package | reproducible suites, methodology ([BENCHMARK_METHODOLOGY.md](BENCHMARK_METHODOLOGY.md)) | competitor harnesses (all NOT REPRODUCIBLE LOCALLY today) |
| Customer evaluation | acceptance reports | evaluation licence, support process |
| Audit package | V3_AUDIT.md, acceptance reports, SBOM, security summary | independent security/cryptography review (V25–V28, V49) |

Any customer-facing material must carry the simulation-only status and the limitations, and use only measured or
cited numbers.
