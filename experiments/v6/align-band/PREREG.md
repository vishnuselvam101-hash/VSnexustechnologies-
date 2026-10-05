# Job #80 (ALIGN-BAND) pre-registration: opt-in retry band for reads beyond the alignment band (SIMULATED)

**SIMULATED.** Every channel here is a software model (`experiments/v6/channel`: `vnxdna.v6.loss` + `vnxdna.v4.channel`).
No DNA was synthesised, stored or sequenced. Nothing here is biological validation.

Written and committed **before** AB-EXP-01 and AB-EXP-02 were run. The analysis code (`verdict.py`), the cost script
(`cost.py`), the fuzz script (`fuzz_retry.py`) and the comparison configuration (`AB-EXP-01-retry-band/config.json`) are
in the same commit. Nothing below changes after the runs; any deviation is listed in the results README under
"Deviations".

## 1. Question

The failure taxonomy (P4-EXP-01) found that on nanopore-like reads 64 % of reads drift beyond the band of 6 (mean
drift −8.0 nt) and the superblock never decodes (0/20 at every coverage). Does aligning the reads beyond the band with
a wider band, leaving every in-band read untouched, recover archives that the stock decoder refuses, without any false
SUCCESS, without harm on any other channel model, and within a time and memory budget?

## 2. Candidate and baseline (no format change)

| arm | `DecodeOptions` | what changes |
|---|---|---|
| `default` (baseline) | `{}` | band 6; reads with \|len − 313\| > 6 are not aligned (`unaligned`) |
| `retry16` (candidate, opt-in) | `{"retry_band": 16}` | reads with 6 < \|len − T\| ≤ 16 are aligned by a second `TemplateAligner` of band 16 (same DP, same native kernel); reads inside band 6 are not touched |

Implementation: commit `2135461` (`src/vnxdna/sync/template.py`, `sync/smart/path.py`, `recovery/pass1.py`,
`pipeline/decode.py`, `recovery/consensus.py`). No C change: the V5 native kernel already implements the alignment
contract for every band ≤ 64. Verification is unchanged: every frame passes inner RS + CRC-32, consensus frames must
decode to their group's address, the container SHA-256 decides SUCCESS.

**Why 16, chosen before the comparison.** On the diagnostic seed 41000 (P4 seed set, not used below) the share of reads
with \|drift\| ≤ R was, for nanopore-like, 0.36 / 0.87 / 0.98 / 1.00 at R = 6 / 12 / 16 / 24, and for deletion-heavy
0.65 / 0.99 / 1.00 / 1.00. R = 16 covers ≥ 98 % of both at 33 band cells per row against 13 (≈ 2.5× DP work for the
reads that need it, none for the others).

**What was seen before this registration (exploratory, diagnostic seed 41000 only).** nanopore-like at its own
coverage 15: no superblock with band 6, 12, 16 or 24 applied to every read, with or without smart + soft;
deletion-heavy coverage 10: stock decoder FAILURE, `retry_band=16` SUCCESS. A ground-truth funnel on the same seed
(aligned share, header-address accuracy, oracle per-strand consensus) suggested that on nanopore-like the band is
necessary but not sufficient: only 3.8 % of aligned reads carry their exact address in the header. That funnel is
re-run on fresh seeds as AB-DIAG (§7, descriptive only, not a criterion). The expectation for C2 is therefore weak; it
is registered unchanged because it is the job's goal.

## 3. Design

Paired: per (cell, seed) the reads are simulated once and decoded by every arm of the cell (decode workers 1, arms in
the fixed order of the cell). 20 kB random payload (19,456 B for `s184`), data seed 6201, v4-balanced unless stated.
Fresh trial seeds **80000-80019** (never the P4 diagnostic seeds 41000+ or the P4-EXP-02 seeds 52000+). Harness:
`experiments/v6/phase4/phase4.py run` unchanged (outcomes, stage classification, ground-truth attribution).

| panel | cells | seeds | arms | purpose |
|---|---|---|---|---|
| grid (G) | the 14 named models (burst-loss, clean, deletion-heavy, dropout-5/10/20, illumina-like, insertion-heavy, mixed-harsh, mixed-mild, nanopore-like, quality-degradation, substitution-heavy, uneven-coverage), each at coverage 3, 5 and 10 (other parameters unchanged), plus B0-like on `s184` (no markers; 1 %, 53/45/2 sub/del/ins, fixed coverage, no dropout, constant qualities) at coverage 3, 5, 10: **45 cells** | 20 | default, retry16 | efficacy and harm |
| determinism (D) | deletion-heavy cov 5, nanopore-like cov 10, mixed-mild cov 5 | 5 (80000-80004) | retry16, retry16-w4 (4 decode workers) | identical output for 1 and 4 workers |
| exploratory (X) | nanopore-like at coverage 15 (its own) and 30 | 10 | default, retry16, smart-auto, smart-auto-retry16 | descriptive only, no criterion |

Total 935 (cell, seed) trials. Cost (AB-EXP-02, `cost.py`): 1 MiB payload; mixed-mild, illumina-like, deletion-heavy,
nanopore-like at their own parameters; seeds 81000-81002; every decode in a fresh child process (peak RSS by
`wait4`), arm order alternating by seed. Bit-exactness (AB-FUZZ, `fuzz_retry.py --rounds 400 --reads 256`, seed
80080): 102,400 reads.

## 4. Statistics

* Success = SUCCESS with the original container SHA-256 (`exact`); Wilson 95 % intervals per arm and cell.
* Paired difference p(retry16) − p(default): Newcombe method 10 (hybrid score, no continuity correction) on the paired
  outcomes, per cell and pooled over the stated cells' (cell, seed) pairs.
* Per-cell intervals are not corrected for multiplicity (45 cells); C3 is per cell on purpose (any sign of harm counts).
* Time: per pair, `decode_seconds(retry16) / decode_seconds(default)` (in-process, same worker, shared machine); median
  per cell.

## 5. Criteria (each reported ACCEPT or REJECT by `verdict.py`)

| id | criterion | ACCEPT iff |
|---|---|---|
| C1 | no false SUCCESS | 0 false SUCCESS over every decode of every arm and panel |
| C2 | nanopore-like efficacy | pooled over nanopore-like cov 3/5/10 (60 pairs): Newcombe 95 % lower bound of the gain > 0 |
| C3 | no harm | in every grid cell the Newcombe 95 % upper bound ≥ 0 (the interval includes 0 or is positive), and the same for the pool of every grid cell except nanopore-like |
| C4 | time budget | in every grid cell where `default` already decodes (≥ 10/20 exact), the median paired time ratio ≤ **1.5** |
| C5 | memory budget | AB-EXP-02: fresh-process peak-RSS ratio ≤ **1.15** for every (model, seed) pair, and 0 false SUCCESS |
| C6 | determinism | D panel: status, decoded SHA-256 and the full `reads` statistics identical for 1 and 4 workers in every pair |
| C7 | bit-exact native = reference | AB-FUZZ: ≥ 100,000 reads, 0 mismatches (native vs reference with the retry band, path kernel, in-band identity with the plain band-6 aligner, window reads equal to the plain band-R aligner) |
| S1 | secondary efficacy | pooled over the grid cells of the high-indel models (deletion-heavy, insertion-heavy, mixed-harsh, nanopore-like; 240 pairs): Newcombe 95 % lower bound > 0 |

## 6. Decision rule

* All of C1-C7 ACCEPT → propose `retry_band = 16` as the default (the lead decides).
* C2 REJECT, but S1 and C1, C3-C7 ACCEPT → report "CONDITIONAL": propose the default change for the high-indel gain only
  as an option for the lead, state plainly that nanopore-like remains undecodable, and give the measured next bottleneck.
* Anything else → keep it opt-in.

Every cell is reported, including the ones where nothing changes. The default-arm results must reproduce the stock
decoder (same code path, option off).

## 7. Descriptive diagnostics (not criteria)

AB-DIAG (`funnel.py`, ground truth, seeds 80000-80004): per band 6 / 12 / 16 / 24, on nanopore-like cov 10 and 15 and
deletion-heavy cov 5 — share of reads aligned, erased bytes per aligned read, share of aligned reads whose header (either
reading) gives the exact address or one within one byte, and the share of strands an oracle count vote (reads grouped by
their true strand) would decode. It locates the next bottleneck; it changes no criterion.
