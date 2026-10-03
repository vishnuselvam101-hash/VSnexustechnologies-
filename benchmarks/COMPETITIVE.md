# Competitive benchmarking framework

Comparisons with other DNA-storage systems follow four strict categories. Every number in a comparison table carries
exactly one of them:

| Category | Meaning | Allowed source |
|---|---|---|
| **MEASURED** | Run on this machine with the same input, seed and channel, by `vnxdna benchmark` | a reproducible harness committed under `benchmarks/competitors/<name>/` (pinned commit + SHA-256 of the code) |
| **PUBLISHED** | Reported by the authors | citation (DOI, table/figure); reproduced verbatim, never re-scaled |
| **ESTIMATED** | Derived by us from published parameters | formula and inputs shown next to the value |
| **UNKNOWN** | Not available | — |

Rules:

1. A system whose code or data is not available is listed as **NOT REPRODUCIBLE LOCALLY**; only PUBLISHED values may
   appear for it, labelled as such.
2. MEASURED and PUBLISHED values are never mixed in one ranking. Density (bits/nt), overhead and recovery depend on
   the channel model; numbers measured under different channels are not comparable.
3. No "beats X" statement without a MEASURED comparison under an identical channel, and the comparison must name the
   channel, coverage, seeds and sizes.
4. Third-party code is inspected and pinned before it runs, and runs inside the governor (`vnxdna run --class bench`).

Status: no competitor harness is implemented yet. Candidate open implementations to evaluate (licence and
reproducibility not yet checked — UNVERIFIED): DNA Fountain (Erlich & Zielinski, Science 2017), the Microsoft/UW
DNA storage tooling, and the Grass et al. RS-based scheme (Angew. Chem. 2015). Until a harness exists, every
competitor row is NOT REPRODUCIBLE LOCALLY.
