# VNX-DNA V11 — completion report (production-oriented release)

Release: tag `v11.0.0` (RELEASE-SHA-PENDING) on `build/v11`. Base: v10.0.0. Plan and baseline: docs/V11_BASELINE.md.
Evidence label: **SIMULATED** throughout — software channel models only; no DNA was synthesised, stored or sequenced.
Every number below must come from a committed results file; sections marked PENDING are filled at the release commit.

## Release status
- Version 11.0.0 (`vnxdna._version`, CHANGELOG head, `vnx-dna --version`, `vnx --version`; tested).
- Formats unchanged from v10.0.0: archive format 5, container v2, strand frame 5; the manifest format stamp stays
  `6.0.0.dev0`, so archive ids and golden fixtures are byte-identical. Native align library ABI 3 (loader rejects older).
- Release gates: docs/V11_RELEASE_GATES.md. Full suite at the release commit: PENDING (counts also in docs/TESTING.md).

## Implementation
- **WP1 — SIMD aligner levels (native ABI 3):** portable 4-lane, AVX2 8-lane and AVX-512 16-lane DP/traceback chosen at
  run time, refused before execution when the CPU/OS cannot run it; byte-identical output at every level. PENDING:
  merge commit, tests added.
- **WP2 — adaptive dispatcher (`vnxdna.native.dispatch`):** capability probe, deterministic path choice, reference
  fallback, `path_selection` in every encode/decode report, opt-in `VNXDNA_DIFFERENTIAL` self-check (quarantine or
  raise). Merge b2354e7.
- **WP3 — memory and cleanup:** spill files and orphan map released on a stopped decode; clustering store not kept
  mapped after a stop; peak-RSS-vs-input regression test; measurements in `experiments/v11/mem/`. PENDING: merge commit.
- **WP4 — VNX-Secure gate and release hygiene:** opt-in gate on encode/decode/restore/extract and the legacy paths
  (merge 7b2beb2); version 11.0.0, `--version` on both CLIs, CHANGELOG, TESTING, SECURITY triage, release gates.

## Validation
- Full suite at the release commit: PENDING (baseline v10.0.0: 3634 passed, 3 skipped, 0 failed).
- Native equivalence and golden reads per SIMD level; dispatcher fault injection; gate bypass tests per entry point;
  version tests. PENDING: counts per suite.
- Sanitizers and fuzz: docs/V11_RELEASE_GATES.md (reads parser 1M execs clean; align/rs re-run after the WP1 merge).
- Security: secret scan (0 unreviewed findings), pip-audit (5 known, triaged, unreachable) — docs/SECURITY.md.

## Performance
Benchmarks: see docs/V11_BENCHMARK_REPORT.md (V10 vs V11, same host, alternating order, medians with dispersion).
PENDING: headline numbers copied from that report only.

## Limitations
- **Nanopore-like 10 MB run deferred.** The 10 MB nanopore-like case (seed 93100) ends in **EXPLICIT_FAILURE**: the
  clustering stage stops on its candidate-pair budget and recovers nothing; no wrong data is returned (jobs #82, #86,
  blocked). It is not fixed in V11.
- **Preset F is not in V11.** The nanopore preset F work lives on branch `work/nanopore-f2` and is not merged into V11.
- The RS-decoder fuzz harness ran below 1M execs (time-boxed; not a parser). PENDING: any further limits from the
  benchmark report (regressions are disclosed there).
- All results are SIMULATED on one host (4 cores / 8 threads, 31 GB).

## Next
- Nanopore-like 10 MB: candidate-pair headroom or graceful degradation, then re-run (jobs #86, #82).
- Decide on preset F from `work/nanopore-f2` with its held-out evaluations.
- Lift the `cryptography<47` pin to clear the triaged advisories (founder decision).
- Longer RS/align fuzz campaigns on the merged code.
