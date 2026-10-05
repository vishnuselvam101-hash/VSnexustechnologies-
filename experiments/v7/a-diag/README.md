# A-DIAG: failure taxonomy of the 6.0 decoder on the nanopore-like model (EXPERIMENTAL, SIMULATED)

**EXPERIMENTAL / SIMULATED.** Software strands, the unfitted V6 channel models of `experiments/v6/channel` (coverage
variants in `models/`, each recording its base model and SHA-256) and the 6.0 default decoder. No DNA was synthesised,
stored or sequenced; no public data and no fitted model is used. Exploration seeds 82000-82019 (protocol §7 range
82000-82099); descriptive, no pre-registration, no confirmatory claim. Protocol: `docs/V7_PROTOCOL.md` §6 (outcomes) and
§8.1 (taxonomy).

## Provenance

| item | value |
|---|---|
| code | commit `4bc5e97` (work/v7-diag from build/v7-sprint `810d35e`); header of `trials.jsonl`: `dirty_tracked: false` (only this run's own outputs untracked) |
| run | `PYTHONPATH=src nice -n 10 python experiments/v7/a-diag/adiag.py run --config experiments/v7/a-diag/config.json --jobs 4` (41.1 s wall) |
| summary | `PYTHONPATH=src python experiments/v7/a-diag/adiag.py summarise --config experiments/v7/a-diag/config.json` |
| files | `config.json` (SHA-256 `f660f141…`), `trials.jsonl` (header, 120 trial records, every one kept, end record), `summary.json`, `log.txt` |
| backends | align native, reads native, RS native (header of `trials.jsonl`, with `environment()`) |
| input | 20,000 random bytes (data seed 6201), uncompressed archive, profile v4-balanced: 691 strands of 313 nt (679 data in 9 rows of K = 64 + M = 16, the last shorter; 12 superblock strands, 3 needed) |
| decoder | `DecodeOptions()` (6.0 defaults) plus `stage_counters=True` (observability only), 1 worker; every trial decoded again with 4 workers and 512-read batches |

## Method

A trial is (cell, seed). The model is simulated with the trial seed; the read file is decoded; the claim is classified
by `vnxdna.benchmark.outcome` (protocol §6). Ground truth is the simulator's: `simulate_batch(..., truth=True)` regenerates
every read with its source strand and orientation, and the harness refuses the trial unless the regenerated reads equal
the file base for base. The decoder's report carries the stage counters (`report["stage_counters"]`, or the error's
details for NO_SUPERBLOCK); the harness adds what needs ground truth (`adiag.py` docstring):

* a per-read trace of the 1-worker decode (the decoder's pass-1 worker output, passed on unchanged);
* pass-2 placement and consensus per address, recomputed with the decoder's own functions on the traced records. Where
  the decoder reached pass 2 (deletion-heavy, illumina-like) the recomputed recoveries equal the decoder's in 40/40
  trials. Where it stopped at the superblock (all nanopore-like trials), the data-strand and data-row rows below are
  what pass 2 *would* have done with the true geometry;
* each lost strand's stage, in the order `coverage` (0 or 1 read in the pool), `read_parsing`, `orientation`,
  `alignment`, `address`, then the frame stage of the reads placed at the address (`indel_placement` when columns with
  no read base exceed r = 16 bytes, `inner_ecc`, `crc`, `consensus`); clustering is not a 6.0 stage;
* the decode's **first failing stage**: the first stage after which a unit the decoder could not decode (the superblock
  for NO_SUPERBLOCK, else each failed row) keeps fewer strands than it needs (3 of 12; k of k + 16).

## Results

Baseline reproduction: nanopore-like 0/20 at coverage 3, 5 and 10 (V6 AB-EXP-01: 0/20 each, seeds 80000-80019):
**reproduced** (`summary.json` `baseline_reproduction.reproduced: true`). FALSE SUCCESS 0 of 120 decodes (rule of three:
95 % upper bound 3/120 = 0.025 overall, 0.15 per cell); CRASH 0; outcome and every counter identical with 4 workers in
120/120 trials.

| cell | EXACT (Wilson 95 %) | FALSE SUCCESS | PARTIAL | EXPLICIT FAILURE | CRASH | first failing stage | decoder terminal stage | 1 vs 4 workers identical | median decode s |
|---|---|---|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 0/20 [0.00, 0.16] | 0 | 0 | 20 | 0 | address 20 | superblock 20 | 20/20 | 0.15 |
| nanopore-like/cov5 | 0/20 [0.00, 0.16] | 0 | 0 | 20 | 0 | address 20 | superblock 20 | 20/20 | 0.23 |
| nanopore-like/cov10 | 0/20 [0.00, 0.16] | 0 | 0 | 20 | 0 | address 16, indel_placement 4 | superblock 20 | 20/20 | 0.38 |
| nanopore-like/cov15 | 0/20 [0.00, 0.16] | 0 | 0 | 20 | 0 | address 13, indel_placement 7 | superblock 20 | 20/20 | 0.52 |
| deletion-heavy/cov10 | 0/20 [0.00, 0.16] | 0 | 0 | 20 | 0 | consensus 16, crc 1, indel_placement 3 | outer_ecc 20 | 20/20 | 0.37 |
| illumina-like/cov10 | 20/20 [0.84, 1.00] | 0 | 0 | 0 | 0 | — | — | 20/20 | 0.17 |

Reads (mean per trial):

| cell | reads | oriented correctly | oriented and aligned | verified in pass 1 | pending kept (header readable) | placed at the true address |
|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 2060 | 1441 | 734 | 0.1 | 382 | 65 |
| nanopore-like/cov5 | 3437 | 2407 | 1239 | 0.1 | 642 | 110 |
| nanopore-like/cov10 | 6887 | 4819 | 2481 | 0.5 | 1304 | 228 |
| nanopore-like/cov15 | 10341 | 7213 | 3705 | 0.4 | 1936 | 338 |
| deletion-heavy/cov10 | 6883 | 6689 | 4440 | 668.5 | 3284 | 1326 |
| illumina-like/cov10 | 6893 | 6893 | 6893 | 6890.9 | 2 | 6891 |

Data strands (679 per trial), mean surviving after each stage:

| cell | start | coverage | read_parsing | orientation | alignment | address | indel_placement | inner_ecc | crc | consensus | recovered |
|---|---|---|---|---|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 679 | 479 | 479 | 462.55 | 367.95 | 57.25 | 6.05 | 3.5 | 2.05 | 0.1 | 0.1 |
| nanopore-like/cov5 | 679 | 593.3 | 593.3 | 582 | 502.2 | 97.65 | 11.45 | 7.9 | 6.15 | 0.15 | 0.15 |
| nanopore-like/cov10 | 679 | 662.6 | 662.6 | 659 | 622.45 | 184.8 | 33.45 | 25.8 | 21.4 | 0.65 | 0.65 |
| nanopore-like/cov15 | 679 | 673.25 | 673.25 | 671.8 | 653.15 | 250.8 | 58.35 | 49.05 | 41.9 | 0.9 | 0.9 |
| deletion-heavy/cov10 | 679 | 678.6 | 678.6 | 678.6 | 677.3 | 657 | 595.4 | 592.5 | 589.05 | 545.55 | 545.55 |
| illumina-like/cov10 | 679 | 679 | 679 | 679 | 679 | 679 | 679 | 679 | 679 | 679 | 679 |

Superblock strands (12 per trial; 3 needed), mean surviving after each stage:

| cell | start | coverage | read_parsing | orientation | alignment | address | indel_placement | inner_ecc | crc | consensus | recovered |
|---|---|---|---|---|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 12 | 8.45 | 8.45 | 8.25 | 6.6 | 0.65 | 0.05 | 0 | 0 | 0 | 0 |
| nanopore-like/cov5 | 12 | 10.9 | 10.9 | 10.7 | 9.2 | 0.6 | 0 | 0 | 0 | 0 | 0 |
| nanopore-like/cov10 | 12 | 11.8 | 11.8 | 11.65 | 11 | 1.55 | 0.35 | 0.25 | 0.25 | 0 | 0 |
| nanopore-like/cov15 | 12 | 11.9 | 11.9 | 11.85 | 11.6 | 2.35 | 0.35 | 0.2 | 0.15 | 0 | 0 |
| deletion-heavy/cov10 | 12 | 12 | 12 | 12 | 12 | 11 | 9.3 | 9.2 | 9.1 | 8.45 | 8.45 |
| illumina-like/cov10 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 |

Data rows (9 per trial) with at least k strands still alive, mean surviving after each stage:

| cell | start | coverage | read_parsing | orientation | alignment | address | indel_placement | inner_ecc | crc | consensus |
|---|---|---|---|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 9 | 1.3 | 1.3 | 1.1 | 0.4 | 0 | 0 | 0 | 0 | 0 |
| nanopore-like/cov5 | 9 | 8.85 | 8.85 | 8.6 | 1.9 | 0 | 0 | 0 | 0 | 0 |
| nanopore-like/cov10 | 9 | 9 | 9 | 9 | 9 | 0 | 0 | 0 | 0 | 0 |
| nanopore-like/cov15 | 9 | 9 | 9 | 9 | 9 | 0 | 0 | 0 | 0 | 0 |
| deletion-heavy/cov10 | 9 | 9 | 9 | 9 | 9 | 9 | 8.85 | 8.85 | 8.75 | 6 |
| illumina-like/cov10 | 9 | 9 | 9 | 9 | 9 | 9 | 9 | 9 | 9 | 9 |

Stage at which each lost strand was lost (strand-trials, data + superblock):

| cell | coverage | read_parsing | orientation | alignment | address | indel_placement | inner_ecc | crc | consensus | recovered |
|---|---|---|---|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 4071 | 0 | 333 | 1925 | 6333 | 1036 | 52 | 29 | 39 | 2 |
| nanopore-like/cov5 | 1736 | 0 | 230 | 1626 | 8263 | 1736 | 71 | 35 | 120 | 3 |
| nanopore-like/cov10 | 332 | 0 | 75 | 744 | 8942 | 3051 | 155 | 88 | 420 | 13 |
| nanopore-like/cov15 | 117 | 0 | 30 | 378 | 8232 | 3889 | 189 | 144 | 823 | 18 |
| deletion-heavy/cov10 | 8 | 0 | 0 | 26 | 426 | 1266 | 60 | 71 | 883 | 11080 |
| illumina-like/cov10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 13820 |

Address losses, by read (aligned, correctly oriented reads of strands lost at `address`):

| cell | header unreadable (dropped in pass 1) | scrambler variant byte wrong | other header bytes wrong |
|---|---|---|---|
| nanopore-like/cov3 | 5831 (53%) | 666 (6%) | 4548 (41%) |
| nanopore-like/cov5 | 9572 (53%) | 1040 (6%) | 7409 (41%) |
| nanopore-like/cov10 | 15796 (52%) | 1880 (6%) | 12663 (42%) |
| nanopore-like/cov15 | 19780 (52%) | 2399 (6%) | 15584 (41%) |
| deletion-heavy/cov10 | 317 (24%) | 33 (2%) | 986 (74%) |
| illumina-like/cov10 | 0 (0%) | 0 (0%) | 0 (0%) |

First pass-1 failure of every read (decoder counters, all trials):

| cell | verified | drift_beyond_band | path_beyond_band | erasures_exceed_parity | inner_ecc | crc | invalid_version_or_kind |
|---|---|---|---|---|---|---|---|
| nanopore-like/cov3 | 0.000 | 0.643 | 0.000 | 0.341 | 0.011 | 0.005 | 0.000 |
| nanopore-like/cov5 | 0.000 | 0.638 | 0.000 | 0.346 | 0.012 | 0.005 | 0.000 |
| nanopore-like/cov10 | 0.000 | 0.638 | 0.000 | 0.344 | 0.012 | 0.005 | 0.000 |
| nanopore-like/cov15 | 0.000 | 0.641 | 0.000 | 0.342 | 0.012 | 0.005 | 0.000 |
| deletion-heavy/cov10 | 0.097 | 0.355 | 0.000 | 0.509 | 0.026 | 0.013 | 0.000 |
| illumina-like/cov10 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

Data rows, first failing stage had the superblock decoded (would-be pass 2, true geometry; row-trials):

- nanopore-like/cov3: address 8, alignment 14, coverage 154, orientation 4
- nanopore-like/cov5: address 38, alignment 134, coverage 3, orientation 5
- nanopore-like/cov10: address 180
- nanopore-like/cov15: address 180
- deletion-heavy/cov10: consensus 55, crc 2, decodable 120, indel_placement 3
- illumina-like/cov10: decodable 180


## Interpretation (descriptive)

1. **The nanopore-like decode stops at the superblock in every trial, and the strands are lost at the address step.**
   Pass 1 verifies essentially no read (≤ 0.5 per trial of 2,060-10,341): 64 % of reads drift beyond the band of 6
   and 34 % align with indel erasures beyond the inner parity. Of the strands that still have an aligned, correctly
   oriented read, 62-84 % (data; falling with coverage) and 80-93 % (superblock) have no read that reaches the true
   address: about half of
   those reads are dropped in pass 1 because neither header reading has a valid version nibble, 6 % carry a wrong
   scrambler variant byte, and the rest carry other header bytes too corrupted for the one-byte snap. This is the
   AB-DIAG "address step" (V7_ARCHITECTURE §1, cause 1) measured with exact ground truth.
2. **Behind the address step, segment erasure.** Strands whose reads do reach their address fail mostly because the
   columns no read covers (indel segment erasures) exceed r: `indel_placement` is the second-largest loss at coverage 10
   and 15 and decides 4/20 (cov 10) and 7/20 (cov 15) decodes once enough superblock strands pass the address step (cause 2).
3. **Coverage and alignment matter only at low coverage.** At coverage 3, 29.5 % of data strands have at most one read
   (THEORETICAL 29 %, architecture §4); data rows would fail first at `coverage` (154 of 180 row-trials) even with a
   superblock. At coverage 5 they would fail at `alignment` (134/180); from coverage 10 on every row would fail at
   `address` (180/180). Orientation loses few strands (2.4 % of strand-trials at cov 3, less above), although 30 % of
   reads end in the wrong orientation.
4. **Controls.** illumina-like decodes 20/20 with every read verified in pass 1. deletion-heavy fails in pass 2 (3 rows
   per trial on average): `consensus` decides 16/20, `indel_placement` 3/20, `crc` 1/20; its addresses mostly survive
   (24 % of misplaced reads unreadable against 52 % for nanopore-like).

## Deviations and scope

1. Protocol §8.1 asks for A-DIAG "on the V6 nanopore-like cells and on F". This run covers the nanopore-like cells and
   two controls only; F (fitted models) and public data are out of scope of this step by instruction and are not run.
2. The determinism rerun uses 4 workers **and** 512-read batches (a trial's ~2,000-10,000 reads fit in one default
   8,192-read batch, which would leave 3 workers idle). Identical counters therefore also show batch-size independence.
3. For the nanopore-like cells (no superblock), data-strand and data-row figures are the would-be pass 2 computed by the
   harness with the decoder's own functions and the true geometry; the decoder itself never ran pass 2 there.

## Limitations

Synthetic, i.i.d. channel models (apart from their explicit homopolymer and burst terms), unfitted to any platform. 20
seeds per cell: a 0/20 cell has a Wilson 95 % interval of [0, 0.16]. One archive (20,000 B, one data seed). The strand
stage ordering and the frame-stage rules (`adiag.py` docstring) are conventions of this diagnostic; a strand counts
once, at its first loss. The `scrambler_variant_wrong` / other split compares only header byte 0 of both readings with
the truth. Stage attribution uses ground truth the decoder never has.
