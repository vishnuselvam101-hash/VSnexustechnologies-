# VNX-DNA V5.0.0 — completion report

**V5 results are computational/software validation unless otherwise explicitly identified. No physical DNA synthesis
or sequencing validation is claimed by this release.**

Every channel result below is **SIMULATED**: software-generated strands passed through the V4 channel simulator,
harness-injected edits, or synthetic quality models. Numbers are taken from the phase reports and their committed
`results.json` files; the release verification in §7–§11 was re-run on the release candidate.

## 1. Overview

VNX-DNA 5.0 is an adaptive, probabilistic decoding foundation on top of the unchanged VNX-DNA 4 format. It makes the
existing decoder faster (native alignment), recovers information around insertions and deletions instead of erasing
whole segments (smart indel recovery), uses read qualities and posteriors to decode beyond the hard RS bound when
the evidence is informative (soft-decision inner decoding), and runs the expensive per-read searches only where they
can still change the result (deferred recovery). Everything new is opt-in except the deferred schedule and the
automatic native backend, both of which produce results identical to the previous behaviour.

## 2. What changed from V4

| area | V4 (4.0.0) | V5 (5.0.0) |
|---|---|---|
| marker-template alignment | NumPy DP + traceback | native C kernel (ctypes, optional build), bit-exact; NumPy reference kept |
| indel handling | erase the whole marker segment around an indel | opt-in `indel_recovery="smart"`: bounded local search, consensus realignment |
| inner decoding | hard RS(70, 54) with erasures | opt-in `soft_decoding=erasure|chase|auto` over the unchanged RS + CRC verifier |
| per-read recovery schedule | — | `recovery_schedule="deferred"` (default) or `"eager"` |
| format, codes, crypto, integrity | VNX4 4.0, frame 4, Cauchy RS, AES-256-GCM, SHA-256/Merkle | **unchanged** |
| package version | 4.0.0 | 5.0.0 (new archives record encoder 5.0.0) |

V4 decoding is the default; V4 archives and strand pools decode exactly as before. V3 (`vnx-dna`, format 5) is still
included and unchanged.

## 3. Phase summary

| phase | commit | report | result (SIMULATED) |
|---|---|---|---|
| 1 — baseline | `f3a6e75` | [V5_PHASE1_BASELINE.md](V5_PHASE1_BASELINE.md) | V4 frozen and profiled; 723/723 V4 tests; alignment = 58 % of single-worker noisy decode; golden projection hashes recorded |
| 2 — native alignment | `b19cb19` | [V5_PHASE2_NATIVE_ALIGNMENT.md](V5_PHASE2_NATIVE_ALIGNMENT.md) | bit-exact on 10/10 golden cases; aligner 9.6–11.3×; noisy 4 MiB decode 24.98 → 10.33 s (1 worker) |
| 3 — smart indel recovery | `3633dfb` | [V5_PHASE3_INDEL_RECOVERY.md](V5_PHASE3_INDEL_RECOVERY.md) | median erased nt per true indel 24 → 4; coverage-1 threshold 0.2–0.3 % → 0.4–0.5 % indels; 0 false acceptances, 0 false SUCCESS (202 decodes) |
| 4 — soft-decision decoding | `c5b68d6` | [V5_PHASE4_REPORT.md](V5_PHASE4_REPORT.md) | 0.5 % + 0.5 % indels + 1 % substitutions at coverage 1: 3,156 reads (soft auto + min-Q 20) vs 3,055 best hard, 2,778 Phase 3, 1,499 V4; 0 false acceptances, 0 false SUCCESS (186 decodes) |
| Gate A — provenance | `ff9d860` | [V5_PHASE4_PROVENANCE_VALIDATION.md](V5_PHASE4_PROVENANCE_VALIDATION.md) | Phase 4 experiments re-run on clean `c5b68d6`: 0 outcome differences |
| Gate B — deferred recovery | `12c0ddf`, `cab9233`, `d41530d`, `7124cd5` | [V5_PHASE3_OPTIMIZATION.md](V5_PHASE3_OPTIMIZATION.md) | verified archives/hour V5-hard 155 → 581, soft-auto 147 → 669; 0 outcome differences in 89 comparisons; 0 false SUCCESS (160 decodes) |

## 4. Architecture

```
reads ─▶ parse/batch ─▶ align (native C | NumPy reference) ─▶ segment erasures (V4)
                                   │                                  │
                                   ▼                                  ▼
                       smart indel recovery (opt-in)        inner RS(70,54) + CRC-32 ── accept / reject
                       soft symbols → GMD/Chase (opt-in) ──▶ candidates checked by the same verifier
                                   │
           deferred schedule: only reads of still-undecodable groups enter smart/soft search
                                   ▼
                consensus ─▶ address snapping ─▶ outer Cauchy RS ─▶ SHA-256 / Merkle ─▶ publish (fail-closed)
```

New code lives in `src/vnxdna/v5/` (`native_alignment.py`, `native/align.c`, `indel/`, `soft/`); the decoder
hooks are in `src/vnxdna/v4/decoder.py` behind `DecodeOptions`. The CLI options are `vnx decode --indel-recovery`,
`--soft-decoding`, `--min-quality` and `--recovery-schedule`.

## 5. Native alignment

A C11 kernel (`src/vnxdna/v5/native/align.c`) implements the V4 marker-template DP and traceback, including the
Phase 3 path variant `vnx_align_batch_path`. It is loaded through ctypes; the package build compiles it as an optional
setuptools extension, and a missing compiler falls back to the NumPy reference. `VNXDNA_ALIGN_BACKEND=auto|native|reference`
selects the backend (`auto` prefers native when available); `VNXDNA_NATIVE_LIB` points at an alternative build. The
contract ([V5_NATIVE_ALIGNMENT_CONTRACT.md](V5_NATIVE_ALIGNMENT_CONTRACT.md)) requires every Projection field to be
identical to the reference; invalid arguments return defined error codes.

## 6. Soft-decision decoding

Reads with qualities (or consensus posteriors) become soft symbols. GMD erasure ordering and bounded Chase search
generate candidate received words; every candidate goes through the **unchanged** RS(70, 54) decoder and the CRC-32
and metadata checks, so soft decoding can only choose which words the verifier sees. Ambiguous results (two different
verified frames) are rejected. The hard decoder stays exactly at 2e + f ≤ 16; with informative posteriors GMD recovers
500/500 frames at 2e + f = 17, 18 and 20. The CRC/metadata checks rejected 1,871 RS-valid wrong frames.

## 7. Test results (release verification)

Run on the release candidate (`7124cd5` + the release changes in this commit), Intel Xeon Gold 6240, 8 CPUs.

| check | command | result |
|---|---|---|
| full suite (unit, integration, CLI, property/fuzz, golden, native/reference equivalence, regression V1–V4, V5 phases, benchmark smoke) | `PATH=.venv/bin:$PATH .venv/bin/python -m pytest -p no:cacheprovider -o addopts="" -q` | **1013 passed, 0 failed, 0 skipped** (703 s) |
| lint (CI scope + benchmarks) | `ruff check src tests research benchmarks` | clean |
| byte-compile | `python -m compileall -q src` | clean |
| package build + install | `pip wheel --no-deps .` → fresh venv `pip install vnx_dna-5.0.0-*.whl` | wheel builds with the native extension; `vnx version` → `"vnx": "5.0.0"`, `"alignment_backend": "native"`; `vnx-dna version` → 5.0.0 |
| Phase 1 golden projection hashes | `tests/v5/test_native_alignment_equivalence.py::test_phase1_golden_fingerprints` (in the suite) | pass (10/10 cases) |
| decode identical with either backend | `tests/v5/test_native_alignment.py::test_decode_identical_with_either_backend` (in the suite) | pass |

Phase totals: 723 V4 tests + 116 Phase 2 + 77 Phase 3 + 81 Phase 4 + 16 Gate B = 1013.

## 8. Fuzz results

| run | build | rounds × reads | mismatches |
|---|---|---|---|
| release: `stress_fuzz.py --rounds 1000 --seed 5005` | production | 1,000 × 250 = 250,000 | **0** |
| release: sanitizer builds (below), seed 20261003 | gcc ASan+UBSan / clang UBSan-trap | 2 × 100,000 | **0** |
| Phase 2: seed 99 | production | 500,000 | 0 |

In-suite fuzz and property tests (archives, manifests, frames, read files, configurations, aligner) pass as part of
the 1013. Every fuzz input is SYNTHETIC SOFTWARE TEST data.

## 9. Sanitizer results

`benchmarks/v5/native_alignment/sanitizers.sh` on the release candidate, kernel source SHA-256
`7005c242a2faf1dc91a1c6341fe6a2de652ae030c6b79efa3d7eb44ce8450fb2` (gcc 13.3.0, clang 18.1.3):

| check | result |
|---|---|
| gcc `-fsanitize=address,undefined -fno-sanitize-recover=all`: all 290 `tests/v5` tests + 100,000-read fuzz | clean (290 passed, 0 mismatches) |
| clang `-fsanitize=undefined,integer,bounds,nullability -fsanitize-trap=all`: 290 tests + 100,000-read fuzz | clean (290 passed, 0 mismatches) |
| ASan canary (deliberate ABI misuse) | fired as expected — instrumentation live |
| `-Wall -Wextra -Werror` | clean |
| clang ASan | not run: the clang ASan runtime is not installed on this machine |

## 10. Benchmark results

All SIMULATED, one machine (Intel Xeon Gold 6240, 8 CPUs), from the committed results; not re-run for the release
(they take hours) — the release verification re-ran the benchmark smoke tests in the suite and the golden equivalence.

| measurement | V4 | V5 | source |
|---|---|---|---|
| aligner throughput (178–494 nt, 1 core) | 1× | 9.6–11.3× | Phase 2 §8 |
| noisy 4 MiB decode, 1 worker (median of 3) | 24.98 s | 10.33 s | Phase 2 §10 |
| noisy 4 MiB decode, 8 workers | 5.58 s | 4.01 s | Phase 2 §10 |
| erased nt per true indel (median, cov 1) | 24 | 4 | Phase 3 §summary, §10 |
| reads decoded, 0.5 % ins + 0.5 % del + 1 % sub, cov 1, informative qualities (of 4,096) | 1,499 | 3,156 (smart + soft auto + min-Q 20) | Phase 4 §12.3 (P4-EXP-03) |
| verified archives/hour, V5-hard (eager → deferred) | — | 155 → 581 | Gate B P3O-EXP-01 |
| verified archives/hour, V5-soft-auto (eager → deferred) | — | 147 → 669 | Gate B P3O-EXP-01 |

Costs, stated in the phase reports: soft decoding on adds +13–27 % on low-noise workloads and up to 2.5× on
substitution-heavy coverage-1 channels; smart recovery on noisy channels was 25–35× V4 before Gate B; deferred is
slower than a well-balanced eager schedule when every read must be searched.

## 11. Reproducibility

Release verification environment: Linux 6.8.0 x86-64 (glibc 2.39), Intel Xeon Gold 6240 (8 vCPUs), Python 3.12.3,
NumPy 2.5.3, gcc 13.3.0, clang 18.1.3. Suite run inside a cgroup slice with an 8 GiB memory limit.

To reproduce from a clean checkout of the tag:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
PATH=.venv/bin:$PATH .venv/bin/python -m pytest -p no:cacheprovider -o addopts="" -q
.venv/bin/python benchmarks/v5/native_alignment/stress_fuzz.py --rounds 1000 --seed 5005
bash benchmarks/v5/native_alignment/sanitizers.sh /tmp/vnx-san
```

Expensive experiment sweeps (Phase 3/4 experiments, Gate B P3O-EXP-01) were **not** re-run for the release; their
committed results and the Gate A clean-commit reproduction stand.

Every experiment directory under `experiments/v5/` contains its `config.json`, script, `results.json` (with a
provenance block: commit, dirty flag, CPU, compiler, Python, NumPy, library SHA-256) and logs. Phase 4 results were
reproduced from a clean checkout of `c5b68d6` (Gate A) with 0 differences. Scratch-directory paths in a few
historical logs are redacted to `<scratch>/` in this release; the numbers are unchanged.

## 12. Known limitations

* SIMULATED only; the channel model is not fitted to any synthesis or sequencing platform, and no posterior is
  calibrated against a real sequencer.
* One machine; small archives (32 KiB–4 MiB) for most experiments.
* Soft decoding costs time when enabled; `min_quality` reduces it.
* Deferred recovery gives no speed-up on archives where every read must be searched.
* Pass-2 smart/soft consensus still runs for groups that are already decodable (the largest remaining cost in some
  cells, Gate B report §10).
* The native kernel targets Linux x86-64 with a C compiler; elsewhere the NumPy reference is used (same results,
  slower).

## 13. Scientific evidence classification

| result | class |
|---|---|
| bit-exact native vs reference alignment, golden hashes, fuzz | SYNTHETIC SOFTWARE TEST |
| erased nt per indel, reads decoded, archive thresholds, false-acceptance counts | SIMULATED |
| throughput, timings, memory | SIMULATED workload, measured on one machine |
| RS bound 2e + f ≤ 16 | THEORETICAL (verified by tests) |
| Amdahl projections, cost per byte | ENGINEERING ESTIMATE |
| physical write → store → read cycle | FUTURE TARGET |
| real biological results | **none** |

## 14. Physical-validation status

None. No sequence has been synthesised, stored or sequenced for V5. No claim about synthesis or sequencing yield,
storage lifetime, or performance on a real platform is made.

## 15. Security status

* Format, cryptography (AES-256-GCM, scrypt), integrity checks (CRC-32, SHA-256, Merkle) and the fail-closed
  publish rule are unchanged from V4. Soft and smart decoding cannot bypass the verifier: every candidate passes the
  same RS + CRC + metadata checks, ambiguous candidates are rejected (0 false acceptances, 0 false SUCCESS in all
  V5 experiments).
* The native kernel validates every argument and returns defined error codes; ASan/UBSan clean (§9).
* Secret scan (`gitleaks`): no findings in the V5 commits (`a358ae8..release`). A scan of the whole tree reports 3
  generic-key pattern hits in pre-V5 test fixtures (`tests/fixtures/v0_1/`, `tests/fixtures/v2_0/SHA256SUMS.json`):
  deterministic test keys and SHA-256 sums, not credentials.
* No new runtime dependencies. Dependency vulnerability scanning is not configured in this repository.

## 16. Release commit

The release commit is `release: finalize VNX-DNA v5.0.0` on `feature/vnx-dna-v5`, the parent of the merge into
`main`. The V5 history (`f3a6e75` … `7124cd5`) is preserved unsquashed; the merge commit is the commit tagged `v5.0.0`.

## 17. Release tag

`v5.0.0` (annotated): "VNX-DNA V5.0.0 — Adaptive and probabilistic decoding foundation", on the merge of
`feature/vnx-dna-v5` into `main`.

## 18. Next milestone: V6

V6 is not started by this release. Candidate scope, from the phase reports: outer-code resilience (the planned
V5 Phase 5 work), removing the remaining pass-2 work, larger-archive measurements, and a first physical
write → store → read cycle with a laboratory partner.
