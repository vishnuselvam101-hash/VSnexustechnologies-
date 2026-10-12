# VNX-DNA V11 — completion report (production-oriented release)

Release: tag `v11.0.0` on the main merge commit of `build/v11` (the tag records the exact SHA). Base: v10.0.0. Plan and baseline: docs/V11_BASELINE.md.
Evidence label: **SIMULATED** throughout — software channel models only; no DNA was synthesised, stored or sequenced.
Every number below comes from a committed results file or from the release-commit runs named in docs/V11_RELEASE_GATES.md.

## Release status
- Version 11.0.0 (`vnxdna._version`, CHANGELOG head, `vnx-dna --version`, `vnx --version`; tested).
- Formats unchanged from v10.0.0: archive format 5, container v2, strand frame 5; the manifest format stamp stays
  `6.0.0.dev0`, so archive ids and golden fixtures are byte-identical. Native align library ABI 3 (loader rejects older).
- Release gates: docs/V11_RELEASE_GATES.md. Full suite at the release commit: 4047 passed, 7 skipped, 0 failed (docs/TESTING.md).

## Implementation
- **WP1 — SIMD aligner levels (native ABI 3):** portable 4-lane, AVX2 8-lane and AVX-512 16-lane DP/traceback chosen at
  run time, refused before execution when the CPU/OS cannot run it; byte-identical output at every level. Merges
  9b6d8cf, 10d40de, 5f70316; level tests `tests/v5/test_native_align_levels*.py` (102 tests).
- **WP2 — adaptive dispatcher (`vnxdna.native.dispatch`):** capability probe, deterministic path choice, reference
  fallback, `path_selection` in every encode/decode report, opt-in `VNXDNA_DIFFERENTIAL` self-check (quarantine or
  raise). Merge b2354e7.
- **WP3 — memory and cleanup:** spill files and orphan map released on a stopped decode; clustering store not kept
  mapped after a stop; peak-RSS-vs-input regression test; measurements in `experiments/v11/mem/`. Merge 7a9d0b7.
- **WP4 — VNX-Secure gate and release hygiene:** opt-in gate on encode/decode/restore/extract and the legacy paths
  (merge 7b2beb2); version 11.0.0, `--version` on both CLIs, CHANGELOG, TESTING, SECURITY triage, release gates (merge 3a6e91c).
- **Red-team review and hybrid-boundary hardening (job #90):** nine findings fixed (1 HIGH, 2 MEDIUM, 6 LOW; no
  CRITICAL), each with a regression test that failed before the fix; native loaders refuse relative library overrides;
  C++ outer-list wrappers check buffer lengths. docs/security/V11_SECURITY_REVIEW.md. Merge 0666308.

## Validation
- Full suite at the release commit: **4047 passed, 7 skipped, 0 failed** (`pytest -n 6 tests`, 359.98 s, run at
  `d4c0f47` after both merges; later commits change only docs, release metadata (CITATION.cff, workflow default ref) and the align sanitizer script). Baseline v10.0.0: 3634 passed, 3 skipped, 0 failed.
- Native equivalence and golden reads per SIMD level; dispatcher fault injection; gate bypass tests per entry point;
  version tests. Collected per suite: `tests/v11` 101 (63 red-team), `tests/dispatch` 25, aligner level tests 102.
- Sanitizers and fuzz: docs/V11_RELEASE_GATES.md (reads parser 1M execs clean). On the merged code: `align` harness
  (ABI 3) 300 s, 143 786 runs, 0 crashes (time-boxed smoke, below 1M); ruff and mypy clean; wheel built from
  `git archive`, clean venv: both CLIs print 11.0.0, all 7 packaged native libraries load (align ABI 3, avx512),
  `vnx-dna pipeline` round trip of 200 000 random bytes byte-identical.
- Security: secret scan (0 unreviewed findings), pip-audit (5 known, triaged, unreachable) — docs/SECURITY.md; red-team
  review — docs/security/V11_SECURITY_REVIEW.md (open: V6-SEC-10 LOW TOCTOU narrowed not closed; V11-RT-10 INFO scrypt
  defaults for a founder decision).

## Performance
Benchmarks: see docs/V11_BENCHMARK_REPORT.md (V10 vs V11, same host, alternating order, medians with dispersion).
Headline numbers copied from that report (SIMULATED, one shared host, n = 5 paired runs, ratio = V10 s / V11 s,
> 1 means V11 faster; with n = 5 the 95 % CI is the min–max of the five ratios):

| scenario | V10 median s | V11 median s | ratio [95 % CI] |
|---|---|---|---|
| decode indel 4 MiB w4 | 4.592 | 3.920 | 1.174 [1.136, 1.233] |
| decode indel 4 MiB w1 | 13.597 | 10.598 | 1.283 [1.244, 1.321] |
| decode clean 4 MiB w4 | 1.450 | 1.440 | 0.981 [0.966, 1.038] |
| decode substitution 4 MiB w4 | 2.468 | 2.443 | 0.999 [0.938, 1.066] |
| encode 4 MiB w1 | 1.332 | 1.285 | 0.999 [0.992, 1.059] |
| encode 4 MiB w4 | 0.421 | 0.432 | 1.003 [0.942, 1.041] |

All 85 measured runs succeeded with output SHA-256 identical between V10 and V11; 0 false success. Tree peak RSS within
−8 % to +1.1 % of V10. Outside the indel path there is no speed-up (all CIs contain 1.0).

**WP1 §9 verdict: NOT MET.** The w4 indel target (≥ 1.25x) was not reached: 1.174x, whole CI below 1.25. The w1 target
was met (1.283x). The cProfile before/after artefact (w1) was added after the release benchmark: aligner time −45 %
(6.14 → 3.40 s), profiled decode 1.20x; the non-aligner part (about 8.6–8.9 s) is unchanged (`experiments/v11/wp1/profile-summary.json`).

## Limitations
- **Nanopore-like 10 MB run deferred.** The 10 MB nanopore-like case (seed 93100) ends in **EXPLICIT_FAILURE**: the
  clustering stage stops on its candidate-pair budget and recovers nothing; no wrong data is returned (jobs #82, #86,
  blocked). It is not fixed in V11.
- **Preset F is not in V11.** The nanopore preset F work lives on branch `work/nanopore-f2` and is not merged into V11.
- **WP1 §9 not met:** 4 MiB indel decode at w4 is 1.174x, below the 1.25x target. The w1 profile shows the remaining
  time is outside the aligner; no w4 (per-worker) profile was taken.
- The RS-decoder and align fuzz harnesses ran below 1M execs (time-boxed smokes).
- Benchmarks: one shared host under moderate background load, n = 5, not reproduced on a quiet or second machine; two
  medians fall just below 1.0 (clean decode w4 0.981, encode 1 MiB w4 0.995) with CIs containing 1.0 — no regression
  shown, none ruled out.
- All results are SIMULATED on one host (4 cores / 8 threads, 31 GB).

## Next
- Nanopore-like 10 MB: candidate-pair headroom or graceful degradation, then re-run (jobs #86, #82).
- Decide on preset F from `work/nanopore-f2` with its held-out evaluations.
- Lift the `cryptography<47` pin to clear the triaged advisories (founder decision).
- Longer RS/align fuzz campaigns on the merged code.
