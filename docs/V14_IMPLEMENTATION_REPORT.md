# V14 implementation report (VNX-DNA 14.0.0)

Master directive §21 (deliverable `V14_IMPLEMENTATION_REPORT`), §22 (completion criteria) and §25 (final report).
Branch `build/v14`, base `v13.0.0` (58171b8). Phases: P0-P11 and P13 complete (milestone tags `v14-p1`..`v14-p10`;
P0, P11 and P13 are documents only), P12 acceptance review done, its blockers fixed in the release-prep commits.

All channel results are SIMULATED. V14 claims evidence categories A (software codec), B (software storage device),
C (electronic-backend persistence) and D (simulated DNA channel) only; nothing is E (real DNA operations) or F
(physical device).

V14 results are computational/software validation unless otherwise explicitly identified. No physical DNA synthesis or sequencing validation is claimed by this release.

## 1. Phase index

| phase | what | report |
|---|---|---|
| P0 | audit, V13 baseline, initial docs | [V14_PHASE0_REPORT.md](V14_PHASE0_REPORT.md), [V14_BASELINE.md](V14_BASELINE.md) |
| P1 | contracts: Rust crate `vnxd`, Python `vnxdna.device` | [V14_PHASE1_REPORT.md](V14_PHASE1_REPORT.md), [V14_CONTRACTS.md](V14_CONTRACTS.md) |
| P2 | block volume, journal, VNX-IO | [V14_PHASE2_REPORT.md](V14_PHASE2_REPORT.md), [V14_VOLUME_AND_IO.md](V14_VOLUME_AND_IO.md) |
| P3 | VNX-RAM, cache, molecular-memory interface | [V14_PHASE3_REPORT.md](V14_PHASE3_REPORT.md) |
| P4 | fallback, fault, audit, authz, seal | [V14_PHASE4_REPORT.md](V14_PHASE4_REPORT.md) |
| P5 | load balancer | [V14_PHASE5_REPORT.md](V14_PHASE5_REPORT.md) |
| P6 | codec workstreams A-F | [V14_PHASE6_REPORT.md](V14_PHASE6_REPORT.md) |
| P7 | media adapters | [V14_PHASE7_REPORT.md](V14_PHASE7_REPORT.md) |
| P8 | Linux block device (`/dev/nbdN`) | [V14_PHASE8_REPORT.md](V14_PHASE8_REPORT.md) |
| P9 | integrated validation, 44-row E2E matrix | [V14_PHASE9_REPORT.md](V14_PHASE9_REPORT.md) |
| P10 | ARM64 cross-build + qemu | [V14_PHASE10_REPORT.md](V14_PHASE10_REPORT.md) |
| P11 | hardware + boot design | [V14_PHASE11_REPORT.md](V14_PHASE11_REPORT.md) |
| P12 | acceptance review | this report, §6 |
| P13 | V15 plan | [V15_FINALIZATION_PLAN.md](V15_FINALIZATION_PLAN.md) |

## 2. Completed / incomplete / hardware-dependent (§21, §25, criterion 17)

**Completed (software, this host):**
* `vnxd` control plane: block volume + mirrored journal, VNX-IO queue with reaper and backpressure, VNX-RAM, cache
  (none / write-through / write-back), fallback, fault injection, audit, authz, sealing, load balancer.
* Media adapters: file image (C), DNA software simulator, seeded fault injection, simulated lab (B/D).
* Linux software block device `vnxd nbd create|attach|serve` (`/dev/nbdN`), ext4 tests on disposable devices.
* Codec P6 A-F: linear pool decode, parallel pool pass 2, scheduler `auto`, volume reliability, channel suite,
  hardening.
* E2E/fault-injection matrix: 44 rows, 43 pass, 1 skipped for hardware (R24-hw).
* aarch64 cross-builds of the three crates and the C/C++ kernels, their tests under qemu-user, golden/equivalence
  tests through an aarch64 CPython; low-memory profile; build/deployment docs and systemd unit.
* Design documents: boot architecture, external controller (decision + Stage C spec), physical DNA device (29
  components classified), Pi profile, security model, failure/recovery model, V15 plan.
* §21 doc list: every document exists; the media-adapter specification is named `V14_MEDIA_ADAPTERS.md`.

**Incomplete (software, not hardware-blocked):**
* Benchmarks §2 logical I/O, §3 cache, §5 electronic backend, §6 simulator end to end: NOT MEASURED; §4 not re-run
  on the integrated commit.
* No clean-checkout reproducible-build evidence (criterion 2).
* O-1: the volume journal CRC-32 is unkeyed; for an encrypted volume a writer can reorder or roll back commits
  undetected (format change, deferred to V15).
* E2E rows M02/M04 run the DNA adapters on the TEST-ONLY `ToyCodec` (the real codec behind the adapter is covered by
  `src/vnxdna/native/rust/vnxd/tests/media_worker.rs`, not by the matrix).
* Filesystems other than ext4 (xfs, btrfs, f2fs) not tested; ublk not used.
* Speed targets T-A (≥ 5x) and T-B (≥ 3x over V12) missed; ~2-5 % single-archive regressions vs V13 at w4.
* aarch64 emulated runs: the slowest read/cluster parametrisations and one merged-tree run B (stopped at 84 %) were
  not completed; one 8-thread qemu run exited 101 with a lost log (per-crate reruns passed).
* `cryptography` pin `>=44,<47` vs advisories fixed in 48.0.1: founder decision, unchanged.

**Hardware-dependent (blocked, interfaces + simulators done):**
* Raspberry Pi acceptance (directive §16, Pi profile §4), Pi native build, glibc 2.36 compatibility, NEON, ext4 over
  NBD on a Pi kernel, R24-hw.
* External controller C1-C3 (board purchase), secure boot (irreversible OTP; prototype-only), key escrow.
* Physical DNA device and any category E/F evidence.

## 3. Remaining physical work

Consolidated from [V14_PHASE11_REPORT.md](V14_PHASE11_REPORT.md) ("Not done"), [V14_PHYSICAL_DNA_DEVICE.md](V14_PHYSICAL_DNA_DEVICE.md)
§7 and the P9/P10 limitations:
1. Buy a Raspberry Pi 4 Model B (or Pi 5) and a USB SSD; run the Pi acceptance, the native build, the golden subset,
   R24-hw and NEON measurements there.
2. Assemble controller C1 (buildable-now) with the boot volume and an electronic data LUN; settle the unverified
   points (Pi 5 gadget speed, ROCK 5B USB 3 device mode, `usb_f_tcm` on `dwc2`).
3. Founder decisions: secure boot only on a dedicated prototype board; key escrow for the sealing key file.
4. First physical DNA test (external-service pilot): an oligo pool from a vendor, sequencing as a service or on a
   MinION in a partner lab, decode the delivered FASTQ with the existing codec: the first category E result. Budget
   and partner are founder decisions.
5. Lab host software (MinKNOW client, basecalling step, operator UI, media contract v2), custom.
6. Only after 1-5 work end to end and are reported with evidence may any document call the device "built", and only
   for what was shown.

## 4. Tests and how to run them

Release gate at the final release-prep commit (logs committed in
`results/v14.0.0/release-gate/`):

Gated commit `4d0e3ce` (the commit after it adds only these logs and this table); summary:
[`results/v14.0.0/release-gate/SUMMARY.md`](../results/v14.0.0/release-gate/SUMMARY.md).

| check | result |
|---|---|
| full Python suite (8 workers, systemd scope) | **4,852 passed, 5 skipped, 0 failed**, 383 s (`pytest-full-suite.log`) |
| `cargo test --release --locked`, `RUSTFLAGS=-D warnings`: `vnxd` | **240 passed, 0 failed, 3 ignored** (`cargo-test.log`) |
| same: `engine` / `integrity` | **25 / 5 passed, 0 failed** |
| `ruff check src tests research` | all checks passed (`ruff.log`) |
| E2E matrix (P9, `tools/v14-e2e.sh --integration`) | 44 rows: 43 pass, 1 skipped (R24-hw); last run in P9, not re-run at the gated commit |
| sanitizers, fuzz smoke, aarch64 | last results in P9 (plan §8) and P10; not re-run at the gated commit |

Reproduction (from the repository root, lab venv on `PATH`):

```
# full Python suite (8 workers, memory-capped scope)
systemd-run --scope -p MemoryMax=24G -p TasksMax=8192 env PATH=<venv>/bin:$PATH PYTHONPATH=src OPENBLAS_NUM_THREADS=1 \
    python -m pytest -o addopts="" -q -n 8 -p no:cacheprovider -W ignore::DeprecationWarning
# Rust crates
for c in vnxd engine integrity; do
    RUSTFLAGS="-D warnings" cargo test --release --locked --manifest-path src/vnxdna/native/rust/$c/Cargo.toml
done
ruff check src tests research
tools/v14-e2e.sh --integration            # E2E matrix incl. /dev/nbdN rows (root, nbd module)
tools/cross_aarch64.sh all                # aarch64 cross-build + qemu tests
tools/sanitizers.sh                       # C kernels under ASan/UBSan
```

## 5. Benchmarks

See [V14_BENCHMARK_REPORT.md](V14_BENCHMARK_REPORT.md) (commands per section, §9 trade-offs) and the raw results in
`experiments/v14/bench/` (`p5-balancer`, `p6a-pool`, `p6b-profile`, `p6c-scheduler`, `p6e-channels/full`) and
`experiments/v14/device/p8-nbd/`. Headline: V14 equals V13 on single-archive decode (2.2-2.9x over V12, targets 5x/3x
missed); pool decode 3.3-25x vs V13 at E = 4-32; parallel pool pass 2 1.18x at w4; 600 channel runs with 0 false
successes.

## 6. P12 acceptance: the 18 completion criteria (directive §22)

| # | criterion | result | caveat / evidence |
|---|---|---|---|
| 1 | formats compatible | PASS | golden V4/V5/V6 + V13 volume fixture tests |
| 2 | reproducible build | INCOMPLETE | no clean-checkout evidence (V15 item) |
| 3 | all software modules functional | PASS | P1-P8 tests, E2E matrix |
| 4 | VNX-RAM bounded | PASS | P3 tests, R24 (SIMULATED budget) |
| 5 | VNX-IO correct incl. sync | PASS | P2 tests, E2E rows |
| 6 | cache consistency + dirty recovery | PASS | P3 tests, E2E cache rows |
| 7 | scheduling + backpressure measurable | PASS | P5 measurements; not re-run on the integrated commit |
| 8 | volume/journal recover from tested failures | PASS | P2/P6-D/P9 fault rows |
| 9 | security + integrity integrated | PASS | O-1 (unkeyed journal CRC on encrypted volumes) OPEN |
| 10 | simulator + electronic backend implement the contract | PASS | E2E DNA adapter rows M02/M04 use `ToyCodec` |
| 11 | Linux software block device passes E2E | PASS | ext4 only, this host |
| 12 | filesystem tests on disposable devices | PASS | ext4 only, this host |
| 13 | no silent corruption or fabricated success | PASS | byte oracle in every fault row; 600 channel runs, 0 false successes |
| 14 | benchmarks reproducible + separated by backend | INCOMPLETE | benchmark report §2/3/5/6 not measured |
| 15 | ARM64 tested, Pi acceptance on real hardware | INCOMPLETE | hardware-blocked; cross-build + qemu only |
| 16 | hardware + boot designs documented | PASS | P11 documents |
| 17 | remaining physical work listed | PASS | §2-§3 of this report |
| 18 | V15 plan complete | PASS | V15_FINALIZATION_PLAN.md (P12 rows in §12) |

## 7. Limitations

The trade-offs in [V14_BENCHMARK_REPORT.md](V14_BENCHMARK_REPORT.md) §9 and the incomplete items in §2 above apply.
Every number is from one x86_64 host (8 vCPU); desktop numbers are never Pi numbers. The hardware and physical DNA
device documents are designs; no such device exists.
