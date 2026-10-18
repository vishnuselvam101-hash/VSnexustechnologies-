# V14 state — RESUME FILE (last checkpoint 2026-10-18 ~10:45 IST, host expiring; next session may run on a new machine)

Governing scope: the founder's V14 MASTER DIRECTIVE (copy: `docs/V14_MASTER_DIRECTIVE_2026-10-18.md`) — the complete
storage platform (Rust control plane, real Linux block device, media adapters, Pi portability, P0–P13). The earlier
V14 brief (copy: `docs/V14_BRIEF_2026-10-18.md`) is the detailed spec for Phase 6 (codec: linear pool decode, native
pass 2, safe scheduler, bench suite, config/security) and for the volume hardening. Phase 0 (audit + baseline +
initial docs) is IN PROGRESS; nothing after Phase 0 has started. No code of the platform exists yet.

## Non-negotiable rules (founder)
- NO tag, NO merge to main, NO push by the agent (the founder transfers/pushes). Work on branch `build/v14`.
- Commit author `vishnuselvam101-hash <vishnuselvam101@gmail.com>`; no AI attribution trailers, no AI-tool names,
  no AI-tool scratch paths in committed files.
- Tests never drop, never weakened (gate: 4,454 passed / 4 skipped / 0 failed, 4,452 collected at 58171b8).
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

## What is done (Phase 0, at this checkpoint)
1. Audit of the V13 tree (findings in `docs/V14_PLAN.md` §1.2): pool-path stream decode is O(extents x pool) because
   `pipeline/stream.py::decode_extent` runs a whole-pool `decode_reads` per extent; pass 1 ignores the tag options, pass 2
   filters by tag (`recovery/outer.py::_bucket_consensus`, `recovery/superblock.py`) -> one pass 1 + per-tag spill
   partition + one pass 2 per extent is record-equivalent (design for Phase 6 workstream A, below).
2. Baseline full suite at 58171b8 on the old host: **4,454 passed / 4 skipped / 0 failed, 434.5 s** (`vnx-lab full`,
   6 workers, systemd scope; lab log `results/full-suite-58171b8-20261018-1021.txt`, not in the repo).
3. Phase 0 A/B reproduction (V12 6bf7749 vs V13 58171b8 pristine vs the build/v14 tree, same code at this point):
   `experiments/v14/bench/phase0-ab/{e2e.jsonl, e2e-summary.json, summary.txt, run.log}` — COMPLETE (45 rows, 3
   scenarios x 5 seeds x 3 trees, all SUCCESS, identical outputs, 0 false successes; old host, systemd scope, load ~1.5–2.6):
   indel-w1 v12 8.921 s / v13 3.174 / v14 3.122, v12/v14 2.863 [2.723, 2.946], v12/v13 2.811 [2.769, 2.968]
   (V13 report: 2.885 [2.732, 2.989] -> reproduced); indel-w4 2.847 / 1.659 / 1.635, v12/v14 1.741 [1.667, 2.140]
   (report 1.704 -> reproduced); clean-w4 1.301 / 0.582 / 0.586, v12/v14 2.265 [1.991, 2.412] (report 2.200 ->
   reproduced); v13/v14 = 0.994–1.012 on all three (same code: an A/A band of this host for the 58171b8 engine).
   The pool-scaling reference (`pool_bench.py`, V13 tree at E = 4, 8, 16, 3 reps) was STOPPED after its first row
   (E=4 rep 0: 4.41 s SUCCESS; V13 report: 6.06 s for the same 4 MiB/4-extent pool) when the host had to be abandoned:
   `experiments/v14/bench/phase0-pool/pool.jsonl` is PARTIAL (1 row) and must be re-run on the new machine.
4. Harness: `experiments/v14/bench/bench.py` (three trees v12/v13/v14, ratios v12/v14, v13/v14, v12/v13 with
   bootstrap CIs) and `experiments/v14/bench/pool_bench.py` (pool decode scaling with extent count E; `one`
   subcommand runs in a tree's PYTHONPATH). Both ruff-clean; `pool_bench.py` has never been executed end to end.
5. Docs written (drafts, committed): `docs/V14_PLAN.md` (Phase 6 codec plan + Phase 0 findings; the `_PENDING` cells
   must be filled from committed results, never estimated), `docs/V14_LANGUAGE_AND_RUNTIME_STRATEGY.md` (decisions
   D1 Rust control plane, D2 std-only/no crates, D3 NBD first + ublk upgrade, D4 process model with codec IPC,
   D5 portability). NOT yet written: `docs/V14_MASTER_PLAN.md`, `docs/V14_ARCHITECTURE.md`, `docs/V14_BASELINE.md`,
   `docs/V14_DEPENDENCY_GRAPH.md`; DECISION_LOG entries for D2/D3 (lab log `notes/DECISION_LOG.md` on the old host —
   if it is not handed over, recreate the two rows from the strategy doc in a repo `docs/DECISION_LOG.md`).

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

## EXACT RESUME STEPS on a new machine
```bash
git clone git@github.com:vishnuselvam101-hash/VSnexustechnologies-.git vnx && cd vnx
git checkout build/v14                      # the founder pushes this branch from the old host
git worktree add ../v14-base --detach 58171b8   # pristine V13 (A/B)
git worktree add ../v13-base --detach 6bf7749   # pristine V12 (tier denominator)
python3.12 -m venv ../.venv && ../.venv/bin/pip install -e '.[dev]'   # numpy, cryptography, zstandard, reedsolo, pydantic, typer, pytest, pytest-xdist, hypothesis, ruff, mypy
# toolchains: gcc, g++, rustc/cargo >= 1.63 (1.75 used so far), node >= 20 for web/
for t in . ../v14-base ../v13-base; do ( cd $t && for k in v5.native_alignment v6.native_reads v6.native_rs native.cluster native.strand native.orient native.integrity native.outerlist; do PYTHONPATH=src ../.venv/bin/python -m vnxdna.$k build; done ); done
for t in . ../v14-base; do ( cd $t && PYTHONPATH=src ../.venv/bin/python -m vnxdna.native.engine build ); done   # V12 has no engine
PYTHONPATH=src ../.venv/bin/python -m vnxdna.native | grep all_native      # must be true in all three trees
PYTHONPATH=src ../.venv/bin/python -m pytest -q -o addopts= -p no:cacheprovider -n 6   # expect 4454 passed / 4 skipped
../.venv/bin/ruff check src tests research
```
Then, in order:
1. Re-measure the baseline on the new host (results committed under `experiments/v14/bench/phase0-ab/` and
   `phase0-pool/`, label the host):
   `BASE_TREE=../v13-base V13_TREE=../v14-base VNX_BENCH_OUT=$PWD/experiments/v14/bench/phase0-ab/e2e.jsonl PYTHONPATH=src python experiments/v14/bench/bench.py run --scenarios indel-w1,indel-w4,clean-w4`
   then `... bench.py summary | tee experiments/v14/bench/phase0-ab/summary.txt`;
   `V13_TREE=../v14-base PYTHONPATH=src python experiments/v14/bench/pool_bench.py run --trees v13 --extents 4,8,16 --reps 3 --out $PWD/experiments/v14/bench/phase0-pool`
   (run alone on the host; record load; if a run is partial, say so in the README of the directory).
2. Write `docs/V14_BASELINE.md` (suite count, A/B table, pool scaling, host facts of the NEW machine, what was
   reproduced vs unverified), fill the `_PENDING` cells of `docs/V14_PLAN.md` §1.1 from the committed files.
3. Write `docs/V14_MASTER_PLAN.md` (P0–P13 with acceptance criteria per phase, from the master directive §24 and
   §22), `docs/V14_ARCHITECTURE.md` (data/control flow, dependencies, memory ownership, transaction boundaries,
   failure propagation, security boundaries, host interface, media boundary — ASCII diagrams; process model from the
   strategy doc D4), `docs/V14_DEPENDENCY_GRAPH.md` (Python package layers from `tests/architecture/test_layers.py`
   LAYERS + the new packages, Rust crates, system deps and licences: numpy BSD, cryptography Apache/BSD, zstandard
   BSD, reedsolo MIT/public domain (check), pydantic MIT, typer MIT; no third-party Rust crates).
4. Commit "docs(v14): Phase 0 audit, baseline and plans"; Telegram milestone "Phase 0 + plan committed".
5. P1 contracts + core storage model: new Rust crate `src/vnxdna/native/rust/vnxd` (binary + lib: block address
   types, generation ids, transaction states RECEIVED/STAGED/BACKEND_COMMITTED/VERIFIED, error enum with
   retryable/permanent classes, persisted record formats with CRC-32, media-adapter trait, management and codec IPC
   message schemas `vnx.mgmt/1`, `vnx.codec-ipc/1`), Python `vnxdna.device` package (layer 6 in LAYERS; add it to
   `tests/architecture/test_layers.py` LAYERS and the public path list), docs of every contract, unit tests; phase report.
6. P2 Volume + VNX-IO -> P3 VNX-RAM + Cache -> P4 Fallback + Secure -> P5 Load balancer -> P6 codec (workstreams A–C,
   E–F of the brief; A design above) -> P7 media adapters (A simulator via codec IPC, B image backend, C fault
   injection, D physical interface spec) -> P8 NBD block device (Stage A) + E2E with fdisk/mkfs/mount on a disposable
   image -> P9 integrated validation -> P10 ARM64 cross-build + qemu (Pi acceptance stays INCOMPLETE) -> P11 hardware +
   boot design docs -> P12 acceptance review -> P13 V15 plan. One phase report (`docs/V14_PHASE<k>_REPORT.md`) and one
   Telegram note per phase; update this file at every checkpoint (keep `docs/V14_STATE.md` in sync and committed).

## Open items / cautions
- `experiments/v14/bench/phase0-ab` rows were produced on the OLD host; if the new host differs, they are a
  cross-host record only (keep, label), not the V14 baseline.
- The old host's main checkout had someone else's uncommitted edits in `tests/v2/` (never touched; not part of V14).
- Web console node tests were never run (no `node_modules`); run `npm ci && npm test` in `web/` on the new machine
  (needs the npm registry) and record the result in Phase 6 F.
- CITATION.cff is at 13.0.0; V14 version string stays `13.0.0` until the release phase sets `14.0.0.dev0`/`14.0.0`.
