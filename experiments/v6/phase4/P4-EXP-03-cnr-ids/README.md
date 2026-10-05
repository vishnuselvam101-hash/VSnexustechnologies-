# P4-EXP-03: CNR per-read IDS statistics (PUBLIC-DATA-DERIVED)

**PUBLIC-DATA-DERIVED.** Numbers come from the public Microsoft Clustered Nanopore Reads dataset
(github.com/microsoft/clustered-nanopore-reads-dataset, MIT licence; Srinivasavaradhan et al., "Trellis BMA", ISIT 2021,
arXiv:2107.06440). VNX-DNA produced none of these reads and **cannot decode them**: they are not VNX frames. One row is an
i.i.d. **extrapolation** and is labelled as such. No comparison with BMA or Trellis BMA is made here.

Known caveat (dataset README, note of 2024-08-12): the 10,000 centres were not generated uniformly at random (long-range
dependencies), so the clustering may have produced malformed clusters. The dataset has no quality scores, so it **cannot**
test quality-weighted consensus.

## Method

`cnr_ids.py`: each of the 269,709 reads is aligned to its cluster's 110-nt centre with unit-cost global edit distance (one
optimal alignment; ties diagonal, then deletion, then insertion). Reads with more than 33 edits would be counted as
likely mis-clustered (none were). File SHA-256s are in `results.json`.

    python experiments/v6/phase4/cnr_ids.py --data <dataset dir> --out experiments/v6/phase4/P4-EXP-03-cnr-ids

## Results (`results.json`)

| quantity | value |
|---|---|
| clusters / empty / median size (p10-p90) | 10,000 / 16 / 21 (9-54) |
| per-base substitution / insertion / deletion | 2.16 % / 1.66 % / 1.95 % (total 5.77 %) |
| per-read error, median (p90) | 5.45 % (10.0 %) |
| reads with no error / with no indel | 1.85 % / 4.34 % |
| read length − 110: mean, 5th-95th percentile | −0.32, −4 … +4 |
| share with abs drift ≤ 0 / 3 / 6 at 110 nt | 17.7 % / 87.8 % / 100.0 % |
| **EXTRAPOLATION** (i.i.d. at these rates) to a 313-nt v4-balanced strand: share with abs drift ≤ 6 (the default band) | 94.1 % (≤ 3: 69.5 %) |
| position-wise vote over the exactly-110-nt reads of each cluster (what a marker-free frame could use): clusters recovered exactly | 1,288 / 10,000 (12.9 %); per-base error of the vote 18.1 % |

The measured per-base rates agree with the values the Trellis BMA paper reports for clusters 1-2000 (insertion about 1.7 %,
deletion about 2.0 %, substitution about 2.2 %; see `docs/ALGORITHM_COMPARISON.md`), which is a check of this aligner, not
new information.

Positional profile (11 bins along the centre, `positional_profile_11_bins`): insertions are about 1.8-2 times more
frequent in the first and last bins than in the middle; substitutions rise about 25 % in the last bin. Errors are therefore
not i.i.d. along the strand, which the extrapolation row ignores.

## What it says about the VNX-DNA decoder (interpretation)

* At this platform's error level the band of 6 is not the binding limit for 110-nt reads, and by i.i.d. extrapolation about
  6 % of 313-nt reads would fall outside it. The simulated `nanopore-like` model (P4-EXP-01) is much harsher (deletion
  3.8 %, insertion 1.3 %, mean drift about −8 nt at 313 nt), which is why every read there fell outside the band.
* Only 4.3 % of reads carry no indel, so a layout without markers (`s184` in the B0 benchmark) has almost nothing to vote
  with at nanopore rates: the exact-length position-wise vote recovers 12.9 % of clusters. Markers (or an indel-aware
  consensus) are a precondition at such rates, not an option.
