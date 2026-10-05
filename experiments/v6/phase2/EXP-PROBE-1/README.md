# EXP-PROBE-1: version probe and dispatch (SIMULATED)

**Evidence class: SIMULATED.** Software-generated strands and software channel models; no DNA was synthesised, stored or
sequenced. Question (V6_ARCHITECTURE §9): does the version probe of spec §3.10 (`vnxdna.recovery.probe`) ever refuse
a supported pool, or accept an unsupported one?

- Code: commit `fd07d37` (clean tree; `results.json` → `git`), vnxdna 6.0.0.dev0, native kernels. The probe code is
  unchanged in later commits except for typing-only edits (487c62e).
- Command: `PYTHONPATH=src:tests python experiments/v6/phase2/EXP-PROBE-1/run.py --workers 6` (config: `config.json`;
  1,182 s wall on the shared development VPS, 6 workers; log: `log.txt`).
- Grid: 12 pools × 6 channel models (`experiments/v6/channel/models`: clean, illumina-like, nanopore-like, mixed-harsh,
  deletion-heavy, dropout-20) × coverage {1, 3, 10} × 20 seeds (7200–7219) × sample sizes {64, 1,000, 20,000} =
  12,960 probe decisions. Pools: VNX4 frame 4 for v4-balanced, v4-dense, v4-indel and v4-archival, each with
  superblock 1 and superblock 2 (stripe depth 8, column parity 2); V1 frame format 4; V2/V3 frame format 5; frame-4
  strands with version nibble 7 (valid in every other respect); uniformly random sequences of the v4-balanced strand
  length. A cell's sample is the first n reads of its simulated read file.

## Results (`results.json`)

| Criterion | Result |
|---|---|
| refusals of supported pools | **60** of 8,640 supported decisions, all **nanopore-like at n = 64**; 0 at n = 1,000 and 20,000 |
| a verified frame and the wrong layout | 0 |
| acceptances of unsupported pools | **0** of 4,320 |
| unsupported pools identified with the right code, n ≥ 1,000 | **2,880 / 2,880** |
| verdict (`pass`) | **false**, because of the 60 refusals |

Confusion by class and sample size (decisions): supported n = 64: 2,820 accepted, 60 `LAYOUT_UNDETECTED`; n = 1,000
and 20,000: 2,880 accepted each. nibble 7: `FRAME_VERSION_UNSUPPORTED` 360/360 at n ≥ 1,000; at n = 64 68
`FRAME_VERSION_UNSUPPORTED` and 292 `LAYOUT_UNDETECTED`. V1 and V3 pools: `LEGACY_FORMAT` 360/360 at n ≥ 1,000; at
n = 64 66 `LEGACY_FORMAT` and 294 `LAYOUT_UNDETECTED`. Random: `LAYOUT_UNDETECTED` 1,079/1,080, one
`FRAME_VERSION_UNSUPPORTED` at n = 64 (nanopore-like, coverage 10). Probe time: median 0.023 s, p99 0.61 s, max 1.55 s.

1,327 supported decisions accepted a layout without any verified frame (1,242 nanopore-like, 72 deletion-heavy,
13 mixed-harsh); 769 of them chose the pool's layout. Equal-length layouts (v4-balanced and v4-indel/v4-archival both
give 313-nt strands) cannot be told apart without a verified frame. Such nanopore-like pools did not decode with any
layout or schedule in a spot check (v4-indel pool, coverage 3 and 10, segment and smart recovery: `NO_SUPERBLOCK` with
the right and with the wrong profile).

## Conflict with the pre-registered criterion

The criterion "0 refusals of supported pools" cannot be met together with "0 acceptances of unsupported pools" at
n = 64 when the 64 reads are coverage copies of a few strands and no frame verifies: a follow-up run at n = 64
(probe metrics only, not committed) found random-sequence samples at coverage 10 whose version-nibble statistics
(share up to 0.59 for nibble 4) overlap those of the refused nanopore-like samples (share ≥ 0.39). The implementation
keeps the conservative side: it never accepts an unsupported pool, and a refused n = 64 nanopore-like sample is one that
no layout decodes. The criterion is met at n ≥ 1,000. Changing it needs a decision (founder).
