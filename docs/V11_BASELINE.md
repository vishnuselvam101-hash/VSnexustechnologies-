# V11 baseline (Phase A) - verified state of V10

Written 2026-10-11 on build/v11 (from tag v10.0.0). No code changed. All performance numbers are SIMULATED channels,
one shared host (Xeon Gold 6240, 4C/8T-class, 31 GB). Evidence label: no physical DNA.

## 1. Repository facts
- Release: `main` on origin = `c364264` (merge of PR #12, build/v10), tag `v10.0.0` (annotated, object 0a2637b) -> c364264.
  Release worktree `/root/vnx-dna-lab/v10-rel` is detached at c364264, clean.
- The primary checkout `/root/VSnexustechnologies-` is NOT on main: it is on `vnx-dna/v2` (fb468a1) with 2 modified test
  files, and local `main` is stale (0d1c285, V5). Do not build from it; use `origin/main`/tags/worktrees.
- V11 worktree `/root/vnx-dna-lab/v11`, branch `build/v11` from v10.0.0. Work branches work/v10-fix, v10-opt, build/v10 are
  all ancestors of the tag. `work/nanopore-f2` (9318aa6, 6 commits ahead, other agent) is NOT in V10; not touched.
- Toolchains: Python 3.12.3 (venv `/root/vnx-dna-lab/.venv`; its editable install points at the v6 worktree, so use
  `PYTHONPATH=<tree>/src`), gcc 13.3, clang 18.1, rustc 1.75 (cargo), C++20 via g++. All 7 kernels build in-place
  (`python -m vnxdna.<kernel> build`, see docs/NATIVE_KERNELS.md); .so files are gitignored.
- Size: 42,091 lines Python (src), 3,866 lines native (C 2,621 incl. cluster.c 1,574; C++ 511; Rust 234), 164 test files.
- Bug confirmed: `vnxdna.__version__` and `python -m vnxdna.native` report `6.0.0.dev0` (`src/vnxdna/_version.py`; pyproject
  reads it). CHANGELOG still has stale "Unreleased" sections for V9 and PERF-DROP above/below the 10.0.0 entry.

## 2. Tests (recorded, not rerun)
- Release commit message (28b3f12): **3634 passed, 3 skipped, 0 failed**, 6 workers, 339 s (junit `/root/vnx-dna-lab/tmp/rel10/junit.xml`,
  log suite3.log); ruff clean; install test 3/3; TypeScript 15/15 + tsc strict; Rust 5/5. (Full suite is ~6 min, not 25.)
- Earlier: V9 3337 passed/0 failed/6 skipped (385 s). Skips are environment skips (CLI-on-PATH, opt-in install).
- Commands: `PYTHONPATH=src pytest -n 6` ; `ruff check src tests research`; install test `VNXDNA_INSTALL_TEST=1`;
  sanitizers `tools/sanitizers.sh`, `tools/msan.sh`; fuzz `fuzz/run.sh`; CI `.github/workflows/ci.yml`.
- Targeted smoke in this phase (tests/arch, tests/v10, tests/secure, -n 2): started but the pass count was not captured
  (output filter lost the summary; second attempt hit my time box). **Smoke result: INCONCLUSIVE, not claimed.** The
  three profile runs below did complete and decode correctly.
- docs/TESTING.md is stale (describes V1-V3 suites only).

## 3. Benchmarks and recorded outputs
- Framework: `experiments/arch/compare.py` (paired, alternating order, subprocess per tree, JSONL rows with env, commit,
  native backends, RSS), `experiments/v10/opt/bench.py`, `experiments/v10/{w1,w2,w3,mem}`, `benchmarks/{arch,v7}`, `tools/check_benchmark_json.py`.
- **V10 comparison verified complete**: `experiments/arch/results/compare.jsonl` 82 rows over 8 scenarios, `nanopore.jsonl`
  6 rows, plus summaries; report `docs/ARCH_COMPARISON_REPORT.md`. Run 2026-10-11 06:49-08:05, load 1-3.8.
  Results: encode 1.15x (w1) / 1.23x (w4) vs v9; decode 1.02-1.14x; nanopore-like 1 MiB preset E 1,017 s -> 140 s
  (both EXPLICIT_FAILURE; 7.2x); byte-identical FASTA 10/10, identical outcomes 31/31, 0 false success.
  Caveats: one run per (scenario, seed); background load 1-3.8; the optional v10-base configuration was not run.
- Not in the release tree: the v9-vs-v10-opt compare/final/mem/opt result files exist only untracked in
  `/root/vnx-dna-lab/v10-gate/experiments/v10/*/results` (e.g. nanopore 1 MiB decode 341.98 -> 202.52 s on 3 seeds,
  load rule ok). Commit or re-run them in V11 before citing.
- Preset F (opt-in, EXPERIMENTAL) held-out: 7/10, 15/20, 15/20 exact vs E 2/10, 1/20, 2/20; 0 false success; code changed
  between rounds so not pooled (`experiments/arch/outer_list_eval/`).

## 4. Bottlenecks (evidence)
Fresh cProfile (this phase): 1 MiB random container, encode + decode, v4 channel proxy for the illumina-like case
(sub 1 %, ins/del 0.2 %, coverage 5, seed 96006), **1 worker**, v10.0.0 native libs, load ~1-3 from a shared host.
Script was run from the scratchpad (not committed). Single run, not a benchmark.
- Encode 0.36 s; decode 3.78 s, SUCCESS, output byte-identical; RSS peak 145 MB.
- Decode self time: `native/align.py:align_usable` (native aligner call incl. marshalling) 1.43 s = **38 %**;
  `recovery/pass1.py:_process` Python glue 0.35 s (9 %) and `_try` 0.21 s; numpy reduce 0.22 s; RS (`native/rs.py:_run`)
  0.21 s (6 %); argsort 0.11 s; reads parse 0.09 s. Pass 1 inclusive 3.03 of 3.78 s (80 %).
- Encode: native strand builder 0.16 s of 0.36; `_serialize`/FASTA records 0.07 s; Cauchy parity 0.03 s.
- Existing profiles: nanopore-like decode (V8 py-spy, `experiments/v8/perf/results/profile.json`, pre-V10): consensus 82 %,
  clustering 14 %, all else <1 %. V10 moved polish native and consensus to processes (1 MiB nanopore-like 342 -> 202 s, untracked above).
- Scaling (compare.jsonl, indel 4 MiB): 1 / 2 / 4 workers = 14.16 / 7.81 / 4.67 s (1.8x, 3.0x).
- Large inputs: 10 MB nanopore-like is EXPLICIT_FAILURE under E and F (candidate-pair budget; chunked kernel + scaled budget are in the tag but untuned, TENTATIVE).
- Mid-size encode is cheap; decode noisy path and Python glue around native calls dominate on standard channels.

## 5. Native/Python boundary
ctypes, C ABI, one loader contract (docs/HYBRID_ARCHITECTURE.md s3): ABI versions (align 2, cluster 5, strand 1, integrity 1,
outerlist 1; reads/rs per `python -m vnxdna.native`), caller-owned NumPy buffers, range checks returning -1/-2, Rust
catches panics. Backend env vars `VNXDNA_<K>_BACKEND=auto|native|reference` (rs also avx2/avx512/scalar), `_LIB` overrides.
Selection is per kernel and static; decode reports record `native_backends`. There is no unified capability/workload
dispatcher (V11 objective B): no workload validation, no per-run path-selection report beyond backends, no automatic
differential check. Per-call marshalling (e.g. 42 `align_usable` calls) is visible in the profile.

## 6. Formats and public APIs (must stay compatible)
Archive format 5 / container v2 / frame format 5 / VXS 1; V1 format 4; V0.1 legacy read-only; V4-V6 golden fixtures
(`tests/fixtures/v4_0, v5_0, v6_0`) decode and re-encode byte-identically (docs/COMPATIBILITY.md, docs/spec/VNX-DNA-SPEC-V6.md).
V10 wrote byte-identical FASTA to V9. Python API `vnxdna.api` (`store, encode, decode, restore, recover, verify, info,
extract, simulate, pipeline, detect_input`, `StoreOptions`, `DecodeOptions`, `ChannelConfig`), `vnxdna.sdk`, `vnxdna.v4.{encoder,decoder}`;
CLI `vnx` / `vnx-dna` (docs/CLI.md) with typed exit codes and `vnx.result/1, vnx.event/1, vnx.error/1` JSON envelopes.
Any V11 format change must be versioned and opt-in (skill vnx-dna-format-compat).

## 7. Security controls
- Container: AES-GCM with scrypt, nonce/epoch rules, manifest MAC, `--expect-sha256/--expect-archive-id`, key-file decode
  checks, `--max-container-bytes` 4 GiB cap, bounded manifest depth, RS cache bounds (docs/SECURITY.md; V6 audit).
- Open LOW/INFO findings: V6-SEC-05, 06, 09 (implicit native buffer sizes), 10 (extract TOCTOU), 11, 13, 15, 21;
  accepted V6-SEC-07 (scrypt default awaits founder), 08 (env library paths), 16/17/20; V6-SEC-12 CI gitleaks/pip-audit pending.
- Known limits: unencrypted archives = accident integrity only; rollback-restore resume nonce risk; no key escrow.
- **VNX-Secure** (`src/vnxdna/secure/`, 14 modules; docs/VNX_SECURE_*.md): gate, policy, audit, sessions, detect, contain,
  sandbox. It is wired only to the `vnx security` CLI/`sdk/security.py`; **encode/decode/restore do not pass through the
  gate** (VNX_SECURE_LIMITATIONS 1: gate enforces policy only for access that goes through it). Network isolation is a stub;
  PQC planned; not claimed unhackable. V11(E) integration is therefore real work.

## 8. Known failures and debt
- 10 MB nanopore-like archives EXPLICIT_FAILURE; 5/20 evaluation-3 seeds fail under F (too few correct bytes per failed frame).
- Default strands 313 nt exceed common 300 nt oligo limits (docs/V10_COMPLETION_REPORT.md).
- Inner decoder is hard-decision (+erasures); no cross-group code by default (column parity is opt-in V6); indel
  guarantees are statistical (docs/LIMITATIONS.md). No HTTP service. x86-64-only SIMD, portable C elsewhere (unmeasured).
- No performance claim exists for the Rust integrity kernel, strand builder or TS layer at the 4 MiB+ scale beyond encode numbers.
- Version string bug (s1); stale CHANGELOG sections; stale docs/TESTING.md; ~80 TODO-pattern hits in src are mostly false positives (variable names).
- Local main stale and primary checkout on a wrong branch (s1).

## 9. V11 plan - four independent work packages (priority order)
Each package: own branch `work/v11-<x>` from build/v11, bit-exact/format-neutral unless stated, full suite before/after
(expect >= 3634 passed, 0 failed), paired V10-vs-V11 bench with alternating order, warm-up, >= 5 repetitions, dispersion.

### WP1 Decode efficiency (objective A/C) - highest value
Files: `src/vnxdna/recovery/pass1.py`, `src/vnxdna/native/align.py`, `native/rs.py`, `v5/native/align.c`, `recovery/cluster/*`, `core/concurrency.py`.
Work: batch ctypes calls and remove per-read Python glue in `pass1._process/_try` (9-15 % self); reduce align marshalling
(`align_usable` 38 %); re-profile with 2 and 4 workers; improve 2->4 worker scaling.
Accept: 4 MiB indel decode (v10: 4.67 s w4, 14.16 s w1) >= 1.25x faster, medians of 5 paired runs, CI excludes 1.0;
output SHA-256 identical to V10 on all seeds; peak RSS not above V10 +10 %; native vs reference equivalence suites unchanged and green; ASan/UBSan clean for touched C.
Tests: existing equivalence/golden tests, new boundary tests for batched calls (empty batch, one read, max length), cProfile before/after committed under `experiments/v11/`.

### WP2 Adaptive dispatcher + reliability (objectives B, C)
Files: new `src/vnxdna/native/dispatch.py`, `native/_loader.py`, `pipeline/decode.py`, report schema in `core/schemas`, `tests/v11/`.
Work: one capability probe (cpu, ABI, library age), workload validation, deterministic path choice, safe fallback to the
reference, selected path in every decode/encode report (additive JSON field), opt-in differential self-check mode that compares native vs reference on a sample and refuses on mismatch;
cancellation and crash-safe output (temp + fsync + rename) audit; explicit partial-recovery status.
Accept: path reported in 100 % of runs; forced-failure injection (missing lib, ABI mismatch, mismatched kernel output) never yields SUCCESS with wrong bytes
(property test, >= 1000 cases); output identical across paths; dispatcher overhead < 1 % of a 1 MiB decode; no format change.
Tests: fault-injection unit tests, hypothesis equivalence, truncated/corrupt/zero-byte/1-byte inputs, interrupted-write test, determinism across 1/2/4 workers.

### WP3 Memory, scale and nanopore-large (objective D + V10 debt)
Files: `recovery/cluster/stage.py`, `native/c/cluster.c` (candidates), `recovery/spill.py`, `pipeline/decode.py`, `experiments/v10/mem`.
Work: commit the untracked V10 result files; measure peak RSS and copies vs input size (1, 4, 16, 64 MiB clean/indel; capped at 12 GB RSS, `ulimit -v` guard);
bounded queues/backpressure audit; tune and validate the TENTATIVE candidate-pair budget; attempt 10 MB nanopore-like with pre-registered seeds.
Accept: RSS slope vs input documented and <= V10; no run > 12 GB; 10 MB nanopore result reported either way (EXPLICIT_FAILURE counts as honest); 0 false success; large-input tests run under the job tool with timeouts.
Tests: memory regression test (extends the existing bounded-memory marker test), spill cleanup after cancel, load rule in harness.

### WP4 Security integration, release hygiene, docs (objective E, H, I)
Files: `src/vnxdna/pipeline/*`, `sdk/security.py`, `secure/gate.py`, `_version.py`, `CHANGELOG.md`, `docs/*` (TESTING, V11_*), `tests/secure`, CI.
Work: optional gate hook on encode/decode/restore/extract (opt-in, default behaviour unchanged) so no alternate path bypasses policy when VNX-Secure is on;
close V6-SEC-09/10/13/21 where cheap; check no key/passphrase in logs; fix version string to the real release (`10.x` or `11.0.0`, with a test that tag == version);
clean CHANGELOG; refresh TESTING.md; wheel install check; secret scan, pip-audit.
Accept: every public entry point either passes the gate or is listed as exempt with a test; wrong key/tampered archive never reports SUCCESS; `vnx --version` == `_version.py` == tag; ruff, mypy, install test pass; fuzz smoke (>= 1M execs per native parser) clean.
Tests: bypass tests per entry point, log-scrubbing test, wrong-key/rollback tests, docs link checker `tools/check_doc_links.py`.

## 10. Rules carried into V11
No tag/push to main without founder go on the exact SHA; never touch v10.0.0; SIMULATED labels everywhere; report regressions and skips; keep reference implementations for differential tests.
