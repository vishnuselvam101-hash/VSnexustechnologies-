# V14 state — RESUME FILE (last checkpoint 2026-10-19: P0-P11 + P13 DONE; P12 acceptance review DONE; v14.0.0 release prep IN PROGRESS)

**Current state (2026-10-19, supersedes the phase-by-phase notes below):** Phases P0-P11 and P13 are complete and
milestone-tagged `v14-p1`..`v14-p10` (P0 and P11/P13 are docs-only and untagged; P11 = docs/V14_PHASE11_REPORT.md,
P13 = docs/V15_FINALIZATION_PLAN.md). The P12 acceptance review is done; its blockers (state file, version bump to
14.0.0, implementation report, benchmark trade-offs, V15 follow-ups, release gate at the final commit) are fixed in the
release-prep commits on `build/v14` (docs/V14_IMPLEMENTATION_REPORT.md has the 18-criterion table and the gate
evidence). **Next:** the founder's explicit go for the exact release SHA (merge to main + tag `v14.0.0`; nothing is
pushed/merged/tagged before that), then V15 per `docs/V15_FINALIZATION_PLAN.md`.

Governing scope: the founder's V14 MASTER DIRECTIVE (copy: `docs/V14_MASTER_DIRECTIVE_2026-10-18.md`) — the complete
storage platform (Rust control plane, real Linux block device, media adapters, Pi portability, P0–P13). The earlier
V14 brief (copy: `docs/V14_BRIEF_2026-10-18.md`) is the detailed spec for Phase 6 (codec: linear pool decode, native
pass 2, safe scheduler, bench suite, config/security) and for the volume hardening. Phase 0 (audit + baseline +
initial docs) and Phase 1 (contracts: Rust crate `vnxd`, Python `vnxdna.device`, docs/V14_CONTRACTS.md) are COMPLETE
(docs/V14_PHASE0_REPORT.md, docs/V14_PHASE1_REPORT.md), and Phase 2 (block volume + VNX-IO in `vnxd`:
journal, volume, io; docs/V14_VOLUME_AND_IO.md, docs/V14_PHASE2_REPORT.md) and Phase 3 (VNX-RAM + cache in `vnxd`,
molecular-memory interface in Python; docs/V14_RAM_AND_CACHE.md, docs/V14_MOLECULAR_MEMORY.md,
docs/V14_PHASE3_REPORT.md) are COMPLETE. The documented P2/P3 limits were removed (c200ea8: mirrored journal,
atomic multi-extent writes, no generation reuse, cache lock work) and Phase 4 (fallback + security: fallback,
fault, audit, authz, seal modules; Python seal + worker; docs/V14_SECURITY_MODEL.md,
docs/V14_FAILURE_AND_RECOVERY_MODEL.md, docs/V14_PHASE4_REPORT.md) and Phase 5 (load balancer: balance.rs, IoQueue reaper,
measured defaults; docs/V14_LOAD_BALANCER.md, docs/V14_PHASE5_REPORT.md, experiments/v14/bench/p5-balancer/) are
COMPLETE; P6 is next.

## Non-negotiable rules (founder)
- Founder 2026-10-18: push `build/v14` and a milestone tag `v14-p<k>` after each phase (done for p1..p5). Still NO
  merge to main and NO release tag `v14.0.0` before the P12 acceptance review.
- Commit author `vishnuselvam101-hash <vishnuselvam101@gmail.com>`; no AI attribution trailers, no AI-tool names,
  no AI-tool scratch paths in committed files.
- Tests never drop, never weakened (gate: 4,570 passed / 4 skipped / 0 failed after P4; 4,454 at 58171b8).
- Every channel number is SIMULATED and comes from a committed results file; evidence categories A–F in every report
  (A codec, B software device, C electronic backend, D simulated channel, E real DNA ops, F physical device; V14 claims A–D only).
- Never claim a tier (2x/5x/10x) unless the CI lower bound clears it. The pre-fix V13 scheduler (5.23x run) never returns.
- Never run heavy jobs ungoverned on the lab host (`VNX_LAB_GOVERN=systemd`); never `pkill -f`/`pgrep -f`.
- Block device work: only `/dev/nbd*`/`/dev/ublkb*` devices created by our own code over disposable image files
  under a lab scratch dir; never mkfs/dd/mount/partition any other device; always tear down; if modprobe/device
  creation is denied by the permission system, record the blocker and the exact command for the founder, no workaround.
- Telegram milestones only (one short note per phase completion):
  `openclaw message send --channel telegram --target 7890262154 --message "..."` (attachments: copy into /root/.openclaw/media first).
  On a new machine without openclaw: put the note in the phase report and tell the coordinator.

## Fixed facts
- Repo: GitHub `vishnuselvam101-hash/VSnexustechnologies-` (private). `origin/main` = tag `v13.0.0` = **58171b8**
  (V13 code f7586b9, docs 9e2e0ac, CITATION bump). V12 baseline = `build/v12` **6bf7749** (tier denominator).
- Branch `build/v14` from 58171b8 (local only at this checkpoint; HEAD = the WIP commit listed in `git log`).
- Old lab layout (the expiring host): worktrees `/root/vnx-dna-lab/v14` (build/v14), `/root/vnx-dna-lab/v14-base`
  (detached 58171b8, pristine V13 for A/B), `/root/vnx-dna-lab/v13-base` (detached 6bf7749, pristine V12);
  venv `/root/vnx-dna-lab/.venv`; `tools/vnx-lab` wrapper (governed runs); results in `/root/vnx-dna-lab/results/`.
- Host facts of the old machine are in `docs/V14_LANGUAGE_AND_RUNTIME_STRATEGY.md` §2 (x86_64, 8 vCPU, 31 GiB,
  Linux 6.8 with nbd.ko + ublk_drv.ko modules, rustc/cargo 1.75 x86_64 only, no ARM tooling, no Pi hardware).
  A NEW machine must be re-audited the same way (kernel modules, toolchains, CPU/RAM) and V14_BASELINE re-measured
  before any V14 number is compared with the old ones.

## What is done (Phase 0 — COMPLETE)
1. Audit of the V13 tree (findings in `docs/V14_PLAN.md` §1.2): pool-path stream decode is O(extents x pool) because
   `pipeline/stream.py::decode_extent` runs a whole-pool `decode_reads` per extent; pass 1 ignores the tag options, pass 2
   filters by tag (`recovery/outer.py::_bucket_consensus`, `recovery/superblock.py`) -> one pass 1 + per-tag spill
   partition + one pass 2 per extent is record-equivalent (Phase 6 workstream A design, below).
2. Baseline suite at 58171b8: 4,454 passed / 4 skipped / 0 failed, 434.5 s (lab log `results/full-suite-58171b8-20261018-1021.txt`).
3. A/B V12 vs V13 (`experiments/v14/bench/phase0-ab/`): reproduces the V13 report on all three scenarios (CIs overlap).
4. Pool scaling (`experiments/v14/bench/phase0-pool/`, complete, 9 rows): V13 4.45 / 16.83 / 63.69 s at E = 4 / 8 / 16,
   x3.78 per doubling. `pool_bench.py` fixed (relative tree paths are resolved).
5. Docs: V14_MASTER_PLAN, V14_ARCHITECTURE, V14_BASELINE, V14_DEPENDENCY_GRAPH, V14_LANGUAGE_AND_RUNTIME_STRATEGY,
   V14_PLAN (all cells filled), V14_PHASE0_REPORT. D2/D3 rows added to the lab `notes/DECISION_LOG.md`.
6. A parallel web session also did Phase 0 (commits 92bb028, 629d1a4, 2b1cdc1, never pushed; it was stopped by the
   founder on 2026-10-18). This branch does not contain them; if they ever reach GitHub, they are docs/results only and
   are reconciled by hand, not merged blindly.

## Phase 6 workstream A design (ready to implement; nothing coded)
- Add to `pipeline/decode.py` a pool mode: `decode_reads(..., output=None)` with a "pool plan" object; `_decode_reads`
  runs layout probe + pass 1 once (engine rung through the existing ladder, or the Python pass 1), then instead of
  one `_pass2` the plan partitions the spill by tag (per bucket: stable sort of `acc` by `tag` and of `pend` by raw `tag`,
  slices written to a per-tag `Spill` directory with the same bucket count B; orphans only exist with smart/soft
  options, which keep the legacy path) and runs `recovery.outer._pass2` per extent with
  `replace(opt, expect_archive_id=rec.archive_id, archive_tag=tag)` and a `Counter(stats)` copy; each extent's output is
  verified against the manifest (factor `decode_extent`'s verification into a helper) and written in order; a
  per-extent failure falls back to the existing whole-pool `decode_extent` (ladder preserved); the manifest's
  duplicate-tag refusal (`ARCHIVE_TAG_AMBIGUOUS`) stays. Equivalence argument: pass 1 does not depend on the tag
  options; pass 2 uses `acc["tag"] == tag`; consensus groups pending reads by the raw (kind, tag, group, symbol) key
  and `alt` is only used by `snap_addresses` against the missing set of the SAME tag -> partition pend by raw tag
  (`pend["tag"] == tag`) is exact. Only `evidence_refusal(sb, spill.records())` sees fewer records (per-tag count): note it.
- Tests to write (tests/v13 or tests/v14): random shuffles, E in {1,2,4,8}, missing/duplicated/corrupted records,
  ambiguous tags (refusal), several archive IDs incl. foreign archives, empty stream, partial final extent,
  equivalence of output bytes and per-extent reports with the legacy per-extent path, determinism across workers.
- Then measure with `pool_bench.py --trees v13,v14 --extents 4,8,16,32`.

## EXACT RESUME STEPS
Lab host (this machine): worktrees `/root/vnx-dna-lab/v14` (build/v14), `v14-base` (58171b8), `v13-base` (6bf7749),
venv `/root/vnx-dna-lab/.venv`, every kernel + engine built (`python -m vnxdna.native` -> all_native true).
On a new machine, follow the clone/build block of git history (this file at 9bd0149) and re-measure the baseline first.
Phases 1-5 DONE (cargo test 141 + seal_worker via pytest, in src/vnxdna/native/rust/vnxd). Next:
1. P6 codec: workstream A (linear pool decode, design below) first, then B, C, E, F (brief §4-§9).
   Founder open item from P4: `cryptography` pin >=44,<47 vs advisories fixed in 48.0.1 (decision, not changed).
2. P6 codec (workstreams A–C,
   E–F; A design above) -> P7 media adapters -> P8 NBD block device (this host has nbd + ublk_drv, root) -> P9
   integrated validation -> P10 ARM64 cross-build + qemu (Pi acceptance stays INCOMPLETE) -> P11 hardware + boot
   docs -> P12 acceptance review -> P13 V15 plan. One phase report + one Telegram note per phase; keep this file current.

## Inputs for P13 (V15 plan), decided 2026-10-18
- Indel benchmark: measure VNX-DNA's indel path (markers -> erasures, multi-read alignment, single-read realignment)
  against HEDGES-class and marker/watermark codes on the SAME simulated indel channel (rate at equal recovery, overhead,
  decode time); no "indel-immune" wording unless measured. SIMULATED only.
- Physical random access: primer/address design so one file can be PCR-amplified out of a pool (the software index
  `.vxidx` already exists; the primer side does not). Needs the bioinformatics specialist and literature review.
- PCR/chimera and decay-aware channel models in the simulator.

## P6 progress (2026-10-18)
- Workstream A (linear-time shuffled-pool decode) DONE + reviewed: local commits 2dc694c + b69e70f on build/v14
  (PUSHED, not tagged). PoolDecoder in pipeline/decode.py; decode_stream pool path partitions one pass-1 spill by
  archive tag, per-extent fallback to legacy; Spill.view in recovery/spill.py; tests/v14/test_pool_linear.py (14
  differential tests). Bench experiments/v14/bench/p6a-pool/: v13-vs-v14 pool decode 3.3/6.6/13/25x at E=4/8/16/32,
  RSS ~100 MiB flat (SIMULATED). Suite 4584/4/0. Review: no blocking; fixed (wall-time budget -> legacy path;
  quality-weighting test). Diagnostic-only divergence documented: linear path's consensus/snap counters are an upper
  bound; recovered BYTES are identical (fail-closed, verified).
- Remaining P6: B (native pass 2 - running), C (safe recovery scheduler, correctness-sensitive), E (channel bench
  suite), F (config/SDK/CLI/web-console-node hardening). Tag v14-p6 only when the whole phase is done.

## Open items / cautions
- The old host's main checkout had someone else's uncommitted edits in `tests/v2/` (never touched; not part of V14).
- Web console node tests were never run (no `node_modules`); run `npm ci && npm test` in `web/` on the new machine
  (needs the npm registry) and record the result in Phase 6 F.
- Version: set to 14.0.0 (src/vnxdna/_version.py, CITATION.cff) in the release prep; Rust crate versions unchanged.
