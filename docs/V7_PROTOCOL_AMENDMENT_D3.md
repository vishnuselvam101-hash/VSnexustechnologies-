# V7 protocol amendment D3: two concatemeric nanopore datasets (D13, CAS9) — pre-registration

- Status: **PRE-REGISTRATION, 2026-10-06.** Committed before any error, segment, coverage or CAS9 read statistic is
  computed. State of knowledge at this commit, stated in full:
  - All files were downloaded and checked against the source's checksums (D13 FASTQ: Git LFS SHA-256; CAS9 reads and
    small files: git blob SHA-1). The D13 reference files and the CAS9 address file were read for their format, and one
    read header of run 16 was read for its format.
  - **Disclosure.** An earlier, interrupted session ran an uncommitted draft of the manifest scan over all five D13 FASTQ
    files on 2026-10-05, *including the held-out run 13*, before this amendment existed. It computed per-file read-level
    summaries only (record count, read-length min/median/p95/max, quality-value histogram summary, header run IDs); no read
    was aligned or split and no error, segment or coverage statistic was computed. The output stayed in a scratch file and
    is not committed; while resuming, its run-13 summary line was seen (about 131k reads, median length about 4.6 kb). No
    rule, constant or threshold below depends on it, and the committed manifest records no read-level statistic for run 13
    (PR-2). CAS9 reads have not been parsed.
- Extends [V7_PROTOCOL.md](V7_PROTOCOL.md) (sections 3, 4, 5.4, 7). Everything there applies unless this document says
  otherwise. Section numbers below are cited as PR-n (PR-4.2 = section 4.2 of this document).
- Evidence classes: every number from these reads is **PUBLIC-DATA-DERIVED**; reads generated from a channel model for
  comparison are **SIMULATED**; synthetic concatemers in the unit tests are **SYNTHETIC**. Nothing here is PHYSICAL:
  VNX-DNA has synthesised, stored and sequenced nothing, and these data were produced by other groups.
- Scope update 2026-10-06 (founder directive, root-cause phase 8), recorded here before any data were processed: **no
  model is fitted in this item.** The data enter the nanopore debugging loop as measurements to compare with the
  simulator's `nanopore-like` model (PR-7).

## 1. Datasets and why they are added

| ID | Source | Content | Licence | Use |
|---|---|---|---|---|
| D13 | github.com/uwmisl/data-ncomms19-nanopore, commit `4bf31ff40e0d280322d1009bad0971c25fc4ee90` (Lopez et al., Nat Commun 10:2933, 2019) | 5 MinION runs (R9.4, 1D² reads, Git LFS FASTQ) of four files of 150-nt oligos assembled into long concatemers; one reference list per file | none in the repository | real nanopore reads with ground-truth references |
| CAS9 | github.com/uwmisl/cas9-random-access, commit `03f029cac35896d6abf0ab339ec89b031830b72c` (Imburgia et al., Nat Commun 16:6388, 2025) | 288 basecalled FASTQ chunks of one run (2020-07-15; R9.4.1, Guppy 3.2.2, Q ≥ 9); rolling-circle concatemers of circularised 165-nt strands; files 2, 13 and 24 of 25 were enriched by Cas9; address list `splint_all.fasta` | none in the repository | real nanopore reads with repeated units of one molecule, no published references |

Neither repository has a licence file. Both are used internally only (founder approval 2026-10-05): reads, reference
sequences and per-read derived data (segments, alignments, per-reference counts) are never committed or published; only
aggregate statistics (rates, histograms, counts), attributed to the articles. Data live under
`/root/vnx-dna-lab/data/public/{d13,cas9}`. CAS9 is limited to the basecalled reads plus `README.md`,
`splint_all.fasta` and `20200715_triple_file_access.py` (as documentation of the authors' pipeline; never run, never
copied); the preprocessing and consensus outputs and the bundled third-party software (C3POa, BLAT) are not downloaded.

## 2. Manifest and access to held-out data

The manifest (`experiments/v7/datasets/MANIFEST.json`, rebuilt by `build_manifest.py`) gets one entry per dataset: URLs,
commit SHA, per-file SHA-256, size, cross-check against the source (D13 FASTQ: Git LFS pointer SHA-256; small files and
CAS9 reads: git blob SHA-1 from the GitHub API listing of that commit), download timestamps (UTC), licence status and
usage constraints, and reference-file summaries (count, distinct, length, overlap between D13 files).

Read-level manifest statistics (record count, malformed records, read length distribution, quality histogram, header
fields) are computed **for FIT/DEV material only**:

- D13: for runs 15, 16, 18, 20. The held-out run (PR-3.1) gets SHA-256, size, LFS cross-check and a gzip integrity
  check only (no record count, no length or quality statistic).
- CAS9: the held-out part is a demultiplexed subset of every chunk (PR-3.2), so it cannot be excluded at file level.
  The manifest records per-chunk hashes and the git blob check only; read-level statistics of CAS9 are computed by the
  characterisation (PR-6) on FIT/DEV reads after address classification.

Every script that opens held-out material before a PREREG commit records it in `experiments/v7/datasets/ACCESS_LOG.jsonl`
(protocol 4.2). In this item that is limited to (a) hashing and (b) CAS9 address classification of every read, needed to
know which reads are held out; for held-out CAS9 reads only their count is kept, and D13 segments of held-out-bucket
references (PR-3.1) are counted and discarded.

## 3. Split (protocol 4.1 rule, applied unchanged where possible)

### 3.1 D13

- Units with disjoint reference sets are the four **files** (365-dishes, apollo, space_shuttle, vitruvian), not the five
  runs: runs 15 and 18 are both apollo. Disjointness is checked in the manifest (canonical-sequence overlap between
  reference files); if two files overlap, the overlapping references go to HELD-OUT in both.
- Held-out file: `heldout_unit("D13", files)` = units sorted by name, index `int(SHA-256("VNX-V7-HELDOUT/D13"), 16) mod
  4` = **space_shuttle**, i.e. **run 13 is held out whole**.
- Runs 15, 16, 18 and 20: every reference gets the canonical-sequence bucket `b = int(SHA-256(c), 16) mod 10`
  (protocol 4.1.2): FIT = 0-5, DEV = 6-7, HELD-OUT = 8-9. Duplicate reference lines (identical sequences) share a bucket.
  A read segment (PR-4) belongs to the split of its reference. Runs 15 and 18 share references, so apollo is split by
  reference only (the weaker kind, protocol 4.3).
- Read-level statistics (read length, segments per read, read quality) are split by **read ID**:
  `int(SHA-256(read ID), 16) mod 10` with the same 0-5 / 6-7 / 8-9 mapping, the read ID being the first header token
  without `@`. A read in ID buckets 8-9 still contributes its FIT/DEV segments to segment-level statistics, never to
  read-level ones.

### 3.2 CAS9

- One run; no reference list. Units with disjoint designs are the three **accessed files** (addresses g02, g13, g24).
  Held-out file: `heldout_unit("CAS9", ["g02", "g13", "g24"])` = **g13**: every read classified as address g13 is held out.
- All other reads (g02, g24, off-target addresses, unclassified) are split by read ID as in PR-3.1: FIT 0-5, DEV 6-7,
  HELD-OUT 8-9. This split is weak: reads of the same designed strand can fall in FIT and DEV (protocol 4.3 applies, and
  the completion report says so).

## 4. Concatemer splitting, D13

### 4.1 Method

A k-mer index of every reference of the read's file (all buckets, so that held-out-bucket oligos are recognised and
discarded rather than mistaken for others) keeps only k-mers that occur at most `max_occ` times over the file's
references (shared primers and repeated motifs drop out). For each read and both strands, k-mer hits are grouped by
(reference, diagonal) with tolerance `diag_tol`; a group with at least `min_hits` hits is a candidate. Each candidate is
aligned semi-globally (edlib HW: the whole reference against the read window around the diagonal, padded by `pad`); it
is kept if its edit distance is at most `max_err` × reference length. Candidates are accepted in order of edit distance;
a candidate overlapping an accepted segment by more than `overlap` of the shorter is dropped, and if it is a different
reference within `margin` edits of the accepted one, the accepted segment is marked **ambiguous** and discarded. A read
base is never assigned to the nearest reference when the evidence is ambiguous.

### 4.2 Frozen constants

`k = 16, max_occ = 8, min_hits = 3, diag_tol = 24, pad = 30, max_err = 0.30, margin = 5, overlap = 0.5` (each read
handled independently, so results do not depend on worker count). These may be changed only on the basis of the
SYNTHETIC unit tests (PR-4.3) and before the first real read is split; any later change is a new amendment, and both
results are reported.

### 4.3 Splitter acceptance (SYNTHETIC unit tests, run before any real data)

On synthetic concatemers built from random 150-nt oligos with shared 20-nt primers, both orientations, junction linkers,
and i.i.d. substitutions/insertions/deletions at a total of 8 % per base:

- recall of embedded oligos ≥ 0.95, and 0 segments assigned to a wrong reference;
- two references differing by fewer than `margin` edits in one read position: never assigned (ambiguous);
- exact behaviour on error-free reads (every oligo found, edit distance 0, coordinates exact), empty reads, reads with
  N, reads shorter than k.

### 4.4 Real-data adequacy (reported per run, decides whether a run is used)

A run is **adequate** for segment-level statistics if: ≥ 2,000 distinct FIT references have at least one segment
(protocol 4.3), ≥ 50 % of FIT/DEV-ID reads have at least one segment, and ambiguous segments are ≤ 1 % of accepted
segments. An inadequate run is reported with the failing criterion and excluded from pooled statistics.

## 5. Repeat-unit splitting, CAS9

- **Address hits.** Every occurrence of the 24-nt address motif with its 10 variable bases as N (the 14-nt common prefix
  `TCGCAGAGGTGGCG` plus 10 × N) within ≤ 3 edits is located on both strands (edlib HW, best hit masked and repeated). The
  strand with more hits is the read's orientation (ties: forward).
- **Address class.** Each hit window is classified among the 26 addresses of `splint_all.fasta` (≤ 3 edits, best two at
  least 1 edit apart). The read's address is the class of a strict majority of its classified hits; otherwise the read is
  **unclassified**.
- **Units.** A repeat unit runs from one hit start to the next. Its admissible length range is `[0.8 U, 1.2 U]`, with U
  the modal unit length over FIT reads (estimated on FIT only, rounded, recorded). Units outside the range are dropped.
- **Pseudo-reference.** For reads with ≥ 4 admissible units (at most the first 12 are used), each unit is compared with
  the leave-one-out **star consensus** of the other units (medoid backbone, majority vote, majority insertions) by global
  alignment. Rates measured this way have basis **inferred**: they include errors of the consensus, miss every error
  shared by all units (synthesis, and errors of the circularised template), and describe one molecule's repeated reads,
  not reads of independent molecules. They are reported as such and never presented as per-read error rates of D13 kind.

## 6. Characterisation (FIT and DEV only, PUBLIC-DATA-DERIVED)

Reported per run (D13) and for the run (CAS9), FIT and DEV separately, with bootstrap 95 % CIs over references (D13) or
reads (CAS9), 200 resamples:

1. Reads: count, length distribution (min/median/p95/max, histogram), mean read quality (ONT convention), per-base
   quality histogram; segments (units) per read, share of read bases inside segments, orientation share.
2. Errors per reference site: substitution, deletion, insertion events and inserted bases; substitution matrix; deletion
   and insertion run-length histograms (1-16+).
3. Homopolymer behaviour: deletion, substitution and extension-insertion rates by homopolymer run length 1-8+.
4. Position profile: rates in 15 equal-width relative-position bins.
5. Length- and quality-dependent error: segment error rate by read-length decile and by read mean-quality bin.
6. Burstiness: P(event at i+1 | event at i) versus P(event); per-segment edit count mean, variance and variance/mean.
7. Quality calibration: empirical error by reported Phred value (reported for reference only; no quality model is fitted).
8. Strand survival / dropout (D13 only): per-reference segment-count distribution for FIT references, zero fraction
   (an upper bound on dropout: it also counts splitting failures), coefficient of variation.
9. Split check: FIT and DEV rates compared; a difference beyond 3 bootstrap SE is reported as a split weakness.

Metric definitions reuse plan 3.4 / protocol 5.4 where they exist: M1 (rates), M2 (edit-distance distribution, KS D and
TV), M3 (length drift P(|drift| ≤ 0/3/6)), M4 (position profile, ± 10 % per bin), M5 (deletion run lengths, TV), M6
(homopolymer-conditioned indel rate, 10 % relative), M7 (coverage counts, D13 only), M9 (quality calibration). M8 and
M10 need a fitted model and are not computed in this item.

## 7. Comparison with the simulator (SIMULATED vs PUBLIC-DATA-DERIVED)

For each D13 run, reads are simulated from the shipped `nanopore-like` model (unfitted, `vnx.channel-model/1`) for a
deterministic sample of the **same FIT references** (seed 82000 + run number; references sampled by canonical bucket
order, at most 20,000), one read per reference draw at the model's own coverage. Simulated reads go through the same
semi-global alignment and the same tally as real segments, so alignment conventions cancel. Every statistic of PR-6
(items 2-6 and 8; for 8 the simulated coverage is rescaled to the real mean) is put side by side, with the M1-M7
thresholds as the criterion for "reproduced". Where the simulator fails a threshold, the report names the effect
(e.g. homopolymer-dependent indels, position profile, read-length tail, bursty errors, read-level heterogeneity) and the
`/1` field that cannot express it, if any. No parameter of the model is changed in this item; the conclusion is a list
of simulator defects to fix before the simulator is used for robustness claims.

## 8. What the data will be used for

- **Now (this item):** characterisation (PR-6) and the simulator comparison (PR-7), on FIT/DEV only.
- **Later, after the fitter is merged (not in this item):** fits of nanopore models F on FIT, validated on DEV with
  M1-M10 and the gating set M2, M3, M8 (protocol 5.4); E and transcript replay R from the held-out run after a PREREG.
- **Item A (real-read tests, after a PREREG):** D13 segments as real clusters with known references for header-
  independent clustering (purity, false assignment) and consensus error versus cluster size; CAS9 units as real
  same-molecule read sets. These reads are not VNX-encoded, so no VNX archive is decoded from them; VNX decode tests on
  real error transcripts go through replay R.

## 9. Outputs and provenance

`experiments/v7/nanodata/`: code (`nanolib.py`, drivers), `config.json`, `results/*.json` (aggregate statistics only),
`README.md`, each with commit SHA and dirty flag, environment (Python, NumPy, edlib versions, CPU), dataset SHA-256s
from the manifest, seeds, wall time, and the label **PUBLIC-DATA-DERIVED** (simulated columns: **SIMULATED**). Heavy jobs
stay under 45 minutes; if a run would exceed that, a deterministic read subsample by read-ID hash (first N reads in
hash order) is used and its size is stated.
