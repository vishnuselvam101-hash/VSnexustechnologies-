# VNX-DNA V15 — completion report (long-term readability, archive platform, decode speed; release candidate)

Version 15.0.0 on branch `build/v15` at the gated commit `5e44b201b1646ae6bdd10eb701e3ea0bec22389e` (release
candidate: the tag, the merge into `main` and the push are the founder's). The commit that follows the gated commit
adds this report and the release-gate logs of that commit; it changes no source and no test file. Every channel
result in this report is
SIMULATED (reads from a software channel model); **no DNA was synthesised, stored, amplified or sequenced**, and the
evidence claims of this release are categories A-D only (software codec, software storage device, electronic-backend
persistence, simulated DNA channel) — nothing is category E (physical synthesis) or F (physical sequencing). Every
number below comes from a committed file named next to it.

## Release status

- **Scope delivered** (governing scope: `docs/V15_FINALIZATION_PLAN.md` with the scope lock of
  `docs/V15_M0_DECISIONS.md`): the long-term-readability headline LR-1 and LR-3 (a frozen format with a golden corpus
  and a normative compatibility rule, plus an independent standard-library-only reference decoder), the hardware and
  OS design documents (H1 carrier board, H3 FPGA decoder, H5 DNA I/O), the archive platform AP-1 (write-once
  retention and snapshots), AP-6 (Prometheus observability and alert rules), AP-8 (synthesis-cost planner), AP-14
  (partial byte-range retrieval), AP-16 (offline security monitor), AP-17 (offline-only build) and DA-14 (drive
  digital twin, SIMULATED), the NBD no-space bound and the NBD attach-readiness fix, the decode-speed work CC-1, the
  nanopore branch CC-3, the hardening rows O-2..O-9 and the eight security-review findings, and the evidence and
  hygiene scan.
- **Both registered speed targets are MISSED, and the numbers are not rounded up.** Against 14.0.0 the clean 4 MiB
  workload (4 workers) is **1.236x [1.216, 1.274]** and the indel 4 MiB workload (1 worker) is **1.042x
  [1.035, 1.046]** (n = 18 seeds each). Measured against the v12 tier denominator the same runs give 2.774x and
  2.956x, against registered targets of ≥ 3x and ≥ 5x. The output is byte-identical to 14.0.0 on every row and there
  are 0 false successes. (`experiments/v15/cc1/final/primary-summary.json`, `experiments/v15/cc1/PREREG.md` with its
  two amendments, `experiments/v15/cc1/diff/`, `experiments/v15/cc1/README.md`.)
- **No physical validation of any kind.** The H1/H3/H5 documents are designs and specifications: no board, no FPGA
  and no instrument exists, no bitstream was generated and no synthesis, placement, routing or timing run was made.
- **The plan's own release acceptance is not met in full, and only the founder can waive it.** `docs/V15_FINALIZATION_PLAN.md`
  §1 accepts V15.0.0 only when LR-1 is done, LR-2 is implemented (or explicitly waived by the founder), LR-3 is
  bit-exact on the whole golden corpus, LR-4 is published, LR-5's internal dry run has passed and the external run is
  reported, LR-6's table is complete, plus the QA gates of §7. Of these, **LR-1, LR-3 and the QA-1 gate** are met;
  **LR-2, LR-4, LR-5, LR-6 and the QA-2..QA-6 gates are not** in this tree. Q6 of the scope lock answers the LR-5
  question — ship with LR-5 marked pending if no outside party has run it — but the LR-2 waiver and the acceptance
  decision itself are the founder's, not the engineer's.
- **Not in this release** (the rest of the plan's item list, stated so that nothing is implied to be done): LR-2, LR-4,
  LR-5, LR-6; FZ-1..FZ-4 (the breaking-change freeze, the media contract v2, the volume-journal MAC); CC-2 (the
  indel comparison against HEDGES-class codes), CC-4 (channel-adaptive density), CC-5 (primer-based physical random
  access); RS-3, RS-4; the frontier items F1 (self-describing bootstrap), F2, F3, F4, F5, F6, F7 and F8 of plan §5 (the
  frontier F1..F8 are the plan's own items and are distinct from the security review's findings of the same letters,
  which *are* closed); AP-2, AP-3, AP-4, AP-5, AP-7, AP-9, AP-10, AP-11, AP-12, AP-13, AP-15, AP-18; DA-1..DA-13; the
  PL items; and QA-2..QA-6.
- The archive, container, strand, stream (`vnx.stream/1`) and volume (`vnx.volume/1`) formats are **unchanged from
  14.0.0**; only the Python package version and `CITATION.cff` move to 15.0.0. The Rust crate versions (`vnxd`,
  `vnx_engine`, `vnx_integrity`) are deliberately left unchanged, as in V14. The V4 and V5 archives still decode and
  the V13 volume fixture still opens, reads, scrubs and accepts new commits.

## Implementation

| item | commits | where |
|---|---|---|
| V15 plan, M0 scope lock, hardware (H1-H5) and VNX-OS tracks | cfd6ee0 | docs/V15_FINALIZATION_PLAN.md, docs/V15_M0_DECISIONS.md |
| Reference decoder and golden corpus, compatibility rule, release-process rule (LR-1, LR-3) | c826c14, c57554c, 57d45d8, 348fad0 | reference/, docs/V15_REFERENCE_DECODER.md, docs/RELEASE_PROCESS.md |
| Hardware and OS designs: H1 carrier, H5 DNA I/O, H3 GF(256) RS decoder in RTL and its differential check | 26eebc7, 96d8f9d, 0017e01, 24302da | hardware/h1-carrier, hardware/h3-fpga, hardware/h5-dna-io |
| The reference decoder's runtime held to a measurement; the hardware documents and the golden corpus held to each other | e4020a9, ad55dd2 | tests/v15/test_hardware_lane.py |
| CC-1 decode speed: pass 2 no longer builds the unused known-address set | 37f4502 | src/vnxdna/recovery/ |
| CC-1: engine tables with fixed-distance prefetch and 2 MiB pages | c7906ea | src/vnxdna/native/rust/engine/ |
| CC-1: layout probe keeps the sample flat, computes the frame heads on demand, shares the exact-length matrices | d944783 | src/vnxdna/ |
| CC-1: scan-ahead on a helper thread — measured, **not kept**, reverted | 7ddd8a7, 22bfd71, 8efce8d | experiments/v15/cc1/ |
| CC-1: review fixes (checked table sizes, prefetch warm-up, probe typing, refusal-path probe tests) | e612067 | src/vnxdna/, tests/v15/ |
| CC-1 protocol, A/B driver, amendments, differential rows and the final measurement | aa57986, 8862c84, 8ac8af2, b7b36f0, 855f5da, c1e1fc3, 3570a07, e9dc09c, f21e903, c1b057d, f7facdc | experiments/v15/cc1/ |
| CC-3: opt-in `nanopore` decoder preset, the outer-list fix, the hypothesis selection of the short-row recovery | 0412030, 1e0321d, bf6fa15 | src/vnxdna/recovery/cluster/ |
| CC-3: over the candidate budget lower the bucket cap instead of dropping the stage; one sort for the cluster members; `bucket_cap_floor` default 2 | 815f603, f17ea8a, b0e44d3 | src/vnxdna/recovery/cluster/, src/vnxdna/native/c/cluster.c |
| CC-3: harness, pre-registration and amendment, the 10 MB and column-parity runs, the results, the review | c416349, 074f6dd, 4e32e18, c1503ff, 233d97f, 9b58553, 16fd35b | experiments/v15/cc3/, docs/V15_CC3_REVIEW.md |
| AP-1: snapshot and retention records (D1), the enforcement on every write path (D2), the WORM superblock flag | 1538be3, 1ddc936, 7a57c72, de7cc74 | src/vnxdna/native/rust/vnxd/src/volume.rs |
| AP-6: Prometheus text exporter, the metrics on the management socket, the alert rules | 8cd32cb, d8e95ef, c21e464 | src/vnxdna/native/rust/vnxd/src/metrics.rs, deploy/monitoring/ |
| AP-8: planner core, vendor price profiles, the CLI, the strand count that matches the encoder | 7db473f, afcc0e4, ed07e8f, e4db866 | src/vnxdna/pipeline/plan.py |
| AP-14: partial byte-range retrieval with a per-extent SHA-256 | a5b0c42 | src/vnxdna/pipeline/stream.py |
| AP-16: offline security monitor with signed manifests and tamper-evident audit log | ba3209c | src/vnxdna/secure/monitor.py, docs/V15_SECURITY_MONITOR.md |
| AP-17: the offline-only check (network-namespace runner and the runtime guard) | b978de8, 569c3cc | tools/offline-check.sh, tests/v15/test_ap17_offline.py |
| DA-14: the drive digital twin, six pools with per-pool fault injection (SIMULATED) | 735fa8c, d9a9271 | src/vnxdna/native/rust/vnxd/src/twin.rs, docs/V15_DIGITAL_TWIN.md |
| The NBD no-space bound, the attach-readiness fix and its boundary tests | aee28a7, f5a29df, ffd490e, c3bda1e, 15bd312 | src/vnxdna/native/rust/vnxd/src/nbdserve.rs, nbdsys.rs, nbdctl.rs |
| O-2..O-9: the CRC-32 row validator, the align overflow and geometry checks, the cluster allocation guard, the thread-count configuration error, the manifest type checks, the native loader's integer bounds, the result-cache key race | 02b5ad9, 6ce93f7, a1bce31, 1e4d26f, 5e898a0, 0affa93, 9d89592, 0641e06, 95a4609, 5229395, ee7ccf9, 05f6757, bc31ce6 | src/vnxdna/native/, src/vnxdna/recovery/, tests/v15/ |
| The eight security-review findings: private writes for the control plane and the FASTA output, the V1 parser bounds, the legacy count caps, the constant-time MAC and key checks | 8c3849f, d04c236, 7be6423, 3d64de7, 46c7369, fe4bcaf | src/vnxdna/, src/vnxdna/native/rust/vnxd/ |
| A stale packaged extension must not shadow a newer in-place build | ec1d141 | src/vnxdna/_native_build.py |
| The E2E matrix registry holds the real-codec M02/M04 variants | 02f842c, bfbbd7f | tests/v14/ |
| The evidence and hygiene scan of the committed tree | 57421f4 | tools/v15-evidence-scan.py, docs/V15_EVIDENCE_AUDIT.md |
| The version, the changelog, the release notes and the release-gate results | 009b7da, d2a23ff, 6c7bbb2 | src/vnxdna/_version.py, CHANGELOG.md, results/v15.0.0/ |
| The V2 end-to-end property test moved onto the shipped ECC geometry, and the two tests that keep the small geometry | b5141a0 | tests/v2/test_properties_adversarial_v2.py |

## Validation

- **The release gate is green on the gated commit** `5e44b201b1646ae6bdd10eb701e3ea0bec22389e`
  (`results/v15.0.0/release-gate/`): the full Python suite **5,177 passed, 7 skipped, 0 failed** in 205.31 s on 8
  workers, with the nine native kernels built first so the ctypes bindings load this tree's libraries and not a stale
  one; `cargo test --release --locked` with `RUSTFLAGS=-D warnings` gives `vnxd` **295 passed, 0 failed, 5 ignored**,
  `engine` **26 passed** and `integrity` **5 passed**, every crate exiting 0; `ruff check src tests research` and
  `ruff check .` both report "All checks passed!".
- **The test count does not drop against V14.** The V14 gate recorded 4,852 passed / 5 skipped; this release records
  5,177 passed / 7 skipped. No test was weakened, skipped or deleted in V15, and the V4/V5 archives still decode
  (`tests/compat/`).
- **The 7 skips are the same kind as V14's 5**: three `tests/cli/test_readme.py` tests (they need the installed
  `vnx-dna` on PATH and bash), three `tests/v6/native/test_install.py` tests (the install test is opt-in:
  `VNXDNA_INSTALL_TEST=1`) and one `tests/v5/test_native_align_levels.py` test (this CPU and OS support every
  alignment level; the refusal path is covered by `test_masked_library_refuses_missing_levels`). The two
  `/dev/nbdN` files are deselected, not skipped, and are therefore not counted.
- **One test in this release is sensitive to the load of the host.**
  `tests/v15/test_hardware_lane.py::test_the_documented_runtime_is_still_the_measured_runtime` compares the measured
  reference-decoder runtime with the table of `docs/V15_REFERENCE_DECODER.md` and requires the measurement to stay
  within a factor of two of the stated number. An earlier run on a loaded host measured 25.1 ms for `encrypted-2bit`
  against a stated 12 ms and failed on that bound; the same four archives on an idle host give 10.86 / 31.44 / 44.14 /
  12.69 ms against a stated 11 / 31 / 44 / 12 ms, so the table and the code agree and the earlier failure was host
  contention, not a drift of the code. **The test was not changed**, and the run in `pytest-full-suite.log` is the one
  on the quiet host.
- **One test fixture was corrected, and no assertion was relaxed.** The V2 end-to-end property test
  (`tests/v2/test_properties_adversarial_v2.py`) drove the full chain through the `FAST` fixture, a shrunken 8+4
  group (12 strands per group, erasure tolerance 4) that **no shipped profile uses**, with a channel that drops 2 % of
  the strands outright and makes about 11 % of the reads unreadable. Such a channel loses 2-3 strands in a group of
  80 but can lose 5 or more in a group of 12, and the outer code of a group of 12 recovers at most 4, so the decoder
  refused a group that had lost 5 strands (seed 788 at coverage 6) and the test failed on about 1 % of suite runs.
  The refusal is the codec's documented behaviour (`docs/CHANNEL_MODEL.md`), and the same failure reproduces
  identically on the released 14.0.0 tag — V15 touched nothing under `src/vnxdna/v2/` or `src/vnxdna/ecc/`, so this
  is not a V15 regression. The property now runs on the **shipped `balanced` profile** (64 data + 16 parity shards per
  group, 8 KiB chunks), the geometry of the committed coverage table of `docs/CHANNEL_MODEL.md`; the same seed and the
  same channel lose 2 strands in the worst group there, so the property holds for every seed. The assertion is
  unchanged, and the 8+4 geometry is kept by **two new deterministic tests**: the same chain with a channel inside its
  4-strand tolerance, and a channel that loses more strands than the outer code can recover, which must be refused
  with the typed `InsufficientRedundancyError` and must leave no output file behind. The file went from 6 to 8 tests
  and the suite from 5,175 to 5,177.
- **The evidence and hygiene scan** (`tools/v15-evidence-scan.py`, `docs/V15_EVIDENCE_AUDIT.md`) checks every committed
  file and the whole commit history for attribution trailers and AI-tool names, claims that DNA was synthesised or
  stored or sequenced, lifetime promises and superlatives, unlabelled measurements, credentials and scratch paths.
  The scan reports, it does not gate; the findings were judged by hand.
- **Not part of this gate:** the E2E integration runner with `/dev/nbdN` (`tools/v14-e2e.sh --integration`), the
  sanitizers, the fuzz campaigns, aarch64, and the clean-checkout reproducible build (QA-1).

## Performance (SIMULATED; `experiments/v15/cc1/`, `experiments/v15/cc3/`)

Median ratio of seconds, paired per seed, 95 % CI, from the committed A/B rows (n = 18 seeds per primary workload):

| workload | vs 14.0.0 | vs the v12 tier denominator | registered target | verdict |
|---|---|---|---|---|
| clean 4 MiB, 4 workers | **1.236 [1.216, 1.274]** | 2.774 [2.713, 2.880] | ≥ 3x | **MISSED** |
| indel 4 MiB, 1 worker | **1.042 [1.035, 1.046]** | 2.956 [2.899, 3.024] | ≥ 5x | **MISSED** |

Secondary workloads (n = 3): indel 16 MiB w4 1.056 [1.047, 1.144], substitution 64 MiB w4 1.141 [1.133, 1.189], mixed
4 MiB w4 1.005 [0.999, 1.016]. Every row is byte-identical to 14.0.0 in output and report, and the 374 committed
differential rows against 14.0.0 hold 0 false successes (`experiments/v15/cc1/diff/`). The A/A control of the final
run is inside its band (0.995 [0.977, 1.006] on the clean workload, 1.002 [0.983, 1.012] on the indel workload), so
the ratios above are not noise.

**CC-3, the nanopore branch, evaluated on held-out seeds (SIMULATED):** 271 rows over 35 cells, **0 false
successes**, 165 exact decodes (`experiments/v15/cc3/results/summary.json`). The `nanopore` preset (F) decodes
34/40 of the held-out 1 MiB d13f1 rows where the default preset decodes 0/10 and the previous branch (E) 1/20. With
the opt-in column parity the same held-out seeds give 20/20 for E and 20/20 for F while the default preset stays at
0/20. The 10 MB runs stay at EXACT_FAILURE for the base tree and are still the open item of the review (see
Limitations).

**The reference decoder** (LR-3) is 1,341 lines of standard-library-only Python and decodes the four-archive golden
corpus in 0.099 s from strands (753 strands) and 0.0002 s from containers; it is two to three orders of magnitude
slower than the production decoder, which is expected and accepted.

## Decision: the CC-1 change set, the cluster budget and the V2 geometry

- **CC-1.** Three changes were kept — pass 2 no longer builds the unused known-address set, the engine tables use a
  fixed-distance prefetch with 2 MiB pages, and the layout probe keeps the sample flat and shares the exact-length
  matrices. A fourth (a scan-ahead on a helper thread) was measured and **reverted**: its lower bound
  (1.104 [1.040, 1.294] on the clean workload) did not clear the A/A band, so keeping it would have meant shipping a
  change whose gain could not be told apart from noise. The kept set is worth 1.236x and 1.042x, and the targets of
  3x and 5x are missed.
- **CC-3's cluster budget.** Over the candidate-pair budget the stage now lowers the bucket cap to the largest cap
  whose slot pairs fit instead of stopping, with a floor of 2. The review found one reachable edge case in the
  silent-drop path; the cap-selection rule and the report key were corrected afterwards, and the committed E/F
  numbers are unaffected.
- **The V2 property test.** The failing case was a *fixture* whose geometry is not a shipped one, not a codec defect.
  The engineering decision was to keep every assertion as strong as before, to raise the test count, and to remove
  the flakiness: the property moved to the shipped geometry, and the small geometry was kept by two deterministic tests
  that say what it is for. The alternative — a looser channel, a lower coverage or a raised tolerance on the fixture
  — would have hidden a real property.

## Limitations and open risks

- **Both registered speed targets are missed** (above). The remaining levers are structural: a native pass 2 and a
  native layout probe, and grouping inside the thread pool.
- **The release acceptance of `docs/V15_FINALIZATION_PLAN.md` §1 is not met in full**: LR-2 (the self-describing
  bootstrap layer), LR-4 (the physical preservation guidance), LR-5 (the external validation run) and LR-6 (the
  standards-alignment table) are not in this tree, and the QA-2..QA-6 gates are not met. Only the founder can waive
  LR-2 and decide the acceptance.
- **No physical validation of any kind.** Every channel result is SIMULATED and every read set in the corpus is
  software-produced. The H1/H3/H5 documents are designs; the H3 decoder has never been through synthesis, placement,
  routing or timing, and the GF(256) Reed-Solomon decoder it contains is recorded with an explicit Yosys limitation.
- **CC-3's open item:** the 10 MB d13f1 runs remain EXACT_FAILURE for the base tree, and the cap-selection rule
  after the budget fix is the subject of the review's should-fix note. The 1 MiB held-out evaluation and the
  column-parity runs are the evidence that the branch works; the 10 MB case is not closed.
- **The strand retry rung still roughly doubles the time to report FAILURE** on undecodable inputs: the retry ladder
  spends its budget before the failure is declared. Correct, but slow.
- **The reference decoder is slow by design** and covers the format-4 read path only; it is not a drop-in for the
  newer formats, and its runtime numbers are a property of the golden corpus, not a claim about any pool size.
- **The digital twin is a model.** It checks one property of the model — the device never answers a read with bytes
  other than the bytes of the last completed write of that extent — and its counters are SIMULATED counts of a
  software model, not measurements of a medium.
- **The security monitor's signer is a placeholder** (HMAC-SHA256) until AP-4; the journal MAC of FZ-1 is not in
  this tree.
- **The web console is not part of this gate.** Its type check, build and node contract tests run in CI, not in the lab
  release gate. That contract test found and fixed a drift between the TypeScript bindings and the normative JSON
  schema (the schema had gained `stream-read-range` and `plan` in V15 while the TypeScript list had not), so the
  bindings now match the schema in both directions.

## Next

1. The founder's decision on the release: tag `v15.0.0` at the gated SHA, or waive the unmet acceptance items
   explicitly, or ask for the missing scope (LR-2, LR-4, LR-6) first.
2. The CC-1 floor: a native pass 2 and a native layout probe, and grouping inside the thread pool, are the structural
   levers for the missed targets.
3. Close CC-3's 10 MB case and the review's cap-selection note.
4. The website (`vsnexus-web`) carried the V13 wording and needed the V14 and V15 entries. The founder ordered that
   update on 2026-10-22, and it is under way in the site repository; it is outward-facing, so it lands through the
   site's own review and deploy timer.
