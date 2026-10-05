# V7 Phase D: channel models F fitted to public sequencing data

**PUBLIC-DATA-DERIVED** (fitted parameters, measured statistics) and **SIMULATED** (every read simulated from a fitted model,
and every validation metric that uses such reads). VNX-DNA did not synthesise, store or sequence any DNA. The datasets were
produced by other groups. A fitted model describes that group's synthesis, library, instrument and basecaller at the time of
their experiment. It does not predict how VNX strands would behave in a future wet-lab round.

Governing documents: [docs/V7_PROTOCOL.md](../../../docs/V7_PROTOCOL.md) (sections 4, 5, and amendment 2 in 5.5),
[docs/research/V7_CHANNEL_FITTING_PLAN.md](../../../docs/research/V7_CHANNEL_FITTING_PLAN.md) (estimators 3.3, metrics M1-M10 in 3.4).
The model register is [docs/V7_CHANNEL_MODELS.md](../../../docs/V7_CHANNEL_MODELS.md).

## Method

- Split (protocol 4.1, committed in `experiments/v7/split`, manifest SHA-256 `0c90197f…c95`): FIT = reference buckets 0-5
  of the non-held-out runs, DEV = buckets 6-7. HELD-OUT (D03 `file-1` and buckets 8-9) was **not opened**:
  `experiments/v7/datasets/ACCESS_LOG.jsonl` is empty. Reads are read only through `experiments/v7/split/guard.py`.
- Fit (plan 3.3): edlib unit-cost alignment (NW for nanopore reads, HW for D02 reads with flanks) and leftmost indel
  normalisation; count estimators; 200 bootstrap resamples over references; simulation calibration against FIT statistics
  (4,000 references, coverage 10, 5 damped iterations). Seed 20261005. Code: `vnxdna.simulation.fit`, drivers `run.py`
  and `run_d02.py` here.
- Validation (plan 3.4) on DEV, seed 20261006: 5 seeds x 6 simulated reads for up to 4,000 DEV references, same alignment
  pipeline; M8 on up to 1,500 real DEV clusters; M10 = simulate, refit, compare. **Gating (protocol 5.4): M2, M3, M8.**
  A model failing any of them is INADEQUATE: it is not used to choose decoder parameters and may only be run as a labelled
  stress condition.
- **Round 1** (commit 1c0b889; D02 at a343252): `fit-<dataset>/{models,results,log.txt}`.
- **Round 2** (commit df46fa4; protocol amendment 2, committed as c2da583 before any refit): `fit-<dataset>/a2/`. Two changes,
  both estimated or fixed from FIT only: (A2.1) a per-read gamma rate multiplier `read_heterogeneity`, (A2.2) simulated reads
  pass through the dataset's read-length selection (CNR [-4, +5], D03 [-15, +15], the range of all FIT reads; D02 none)
  wherever they are compared with real reads. M3 is also reported without the selection (`M3_unselected`, not gating).
  Round 2 is a second look at DEV (DEV showed the round-1 failures; nothing was estimated on it).

Reproduce (data under `/root/vnx-dna-lab/data/public`, see the dataset manifest):

```bash
PYTHONPATH=src python experiments/v7/fit/run.py config
PYTHONPATH=src python experiments/v7/fit/run.py fit <job> --workers 8 --bootstrap 200
PYTHONPATH=src python experiments/v7/fit/run.py validate <job> --workers 8
PYTHONPATH=src python experiments/v7/fit/run.py orientation hac|fast --workers 8
```

Jobs: `cnr`, `cnr-p4tie`, `d03-hac-fwd`, `d03-hac-bwd`, `d03-fast-fwd`, `d03-fast-bwd`, `d02-twist`. Results do not
depend on the worker count. Each results file records the git commit, dirty flag, versions and the model SHA-256.

## Results

### Adequacy per dataset and metric (DEV; P = pass, F = fail, - = not applicable)

| Job (dataset) | Round | M1 | **M2** | **M3** | M4 | M5 | M6 | M7 | **M8** | M9 | M10 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cnr (D04 CNR, ONT) | 1 | P | F | F | P | P | P | P | P | - | P | INADEQUATE |
| cnr | 2 | P | F | P | P | P | P | P | P | - | P | **INADEQUATE** (M2) |
| cnr-p4tie (CNR, P4-EXP-03 tie-break) | 1 | P | F | F | P | P | P | P | P | - | P | INADEQUATE |
| cnr-p4tie | 2 | P | F | F | P | P | P | P | P | - | F | **INADEQUATE** (M2, M3) |
| d03-hac-fwd (D03 guppy HAC pass, forward) | 1 | P | F | F | F | F | P | P | P | - | F | INADEQUATE |
| d03-hac-fwd | 2 | P | P | F | F | F | P | P | P | - | F | **INADEQUATE** (M3) |
| d03-hac-bwd | 1 | F | F | F | F | P | P | P | P | - | P | INADEQUATE |
| d03-hac-bwd | 2 | F | P | F | F | P | P | P | P | - | P | **INADEQUATE** (M3) |
| d03-fast-fwd | 1 | P | F | F | F | P | P | F | P | - | F | INADEQUATE |
| d03-fast-fwd | 2 | P | P | F | F | P | P | F | P | - | F | **INADEQUATE** (M3) |
| d03-fast-bwd | 1 | F | F | F | F | P | P | F | P | - | F | INADEQUATE |
| d03-fast-bwd | 2 | F | P | F | F | P | P | F | P | - | F | **INADEQUATE** (M3) |
| d02-twist (D02 DT4DDS Twist, Illumina iSeq) | 1 | F | P | F | F | F | F | P | P | F | P | INADEQUATE |
| d02-twist | 2 | F | P | P | F | F | F | P | P | F | P | **ADEQUATE on M2/M3/M8** (non-gating M1, M4, M5, M6, M9 fail) |

### The gating metrics in numbers (DEV, real vs SIMULATED)

M2: per-read edit distance, KS D <= 0.03, TV <= 0.05, p90/p99 within 10 %. M3: P(|length - L| <= 0, 3, 6) within 1
percentage point (pp); listed as simulated minus real.

| Job | Round | M2 KS D | M2 TV | p99 real / sim | M3 diff pp (<=0, <=3, <=6) | M3 unselected (<=0, <=3, <=6) |
|---|---|---|---|---|---|---|
| cnr | 1 | 0.054 | 0.103 | 14 / 14 | -1.67, -6.88, -2.59 | |
| cnr | 2 | 0.038 | 0.065 | 14 / 16 | -0.05, -0.58, 0.00 | -2.16, -11.10, -4.25 |
| cnr-p4tie | 2 | 0.037 | 0.066 | 14 / 16 | +1.44, +0.29, 0.00 | -0.63, -9.27, -3.91 |
| d03-hac-fwd | 1 | 0.186 | 0.295 | 22 / 14 | -10.77, -0.30, +1.97 | |
| d03-hac-fwd | 2 | 0.014 | 0.017 | 22 / 22 | -2.85, +0.14, +1.00 | -2.86, +0.12, +0.99 |
| d03-hac-bwd | 2 | 0.020 | 0.031 | 22 / 22 | -2.98, -0.59, +0.53 | -2.99, -0.61, +0.51 |
| d03-fast-fwd | 1 | 0.101 | 0.188 | 29 / 24 | -1.04, -2.63, +0.63 | |
| d03-fast-fwd | 2 | 0.016 | 0.018 | 29 / 29 | -0.30, -1.23, +0.24 | -0.33, -1.42, -0.05 |
| d03-fast-bwd | 2 | 0.024 | 0.027 | 30 / 30 | -0.41, -2.15, -1.43 | -0.44, -2.36, -1.76 |
| d02-twist | 1 | 0.025 | 0.029 | 4 / 4 | -1.17, -0.05, +0.20 | |
| d02-twist | 2 | 0.021 | 0.025 | 4 / 4 | -0.91, -0.08, +0.19 | (as selected: D02 has no selection) |

M8 (consensus error vs cluster size k = 1, 2, 5, 10, 20 and the exact-length vote) passes for every model in both rounds.

### Fitted read heterogeneity (round 2, shape k of Gamma(k, 1/k); Var(m) = 1/k; 95 % CI)

CNR 12.1 [11.6, 12.7] (p4tie 12.0); D03 HAC forward 1.96 [1.94, 1.98], backward 1.98 [1.96, 2.00]; D03 fast forward
9.97 [9.86, 10.07], backward 10.10 [9.98, 10.21]; D02 0.565 (sequencing stage only; see limitations). The CIs are the
bootstrap of the moment estimator scaled by the calibration factor; they do not include calibration noise.

### Orientation (D03)

Forward and backward reads were fitted separately. Their substitution matrices differ beyond the 95 % intervals (largest
off-diagonal difference 0.428 HAC, 0.483 fast, in the reference frame; 0.122 HAC and 0.091 fast in the reads' native frame), and the
per-base rates differ too. No merged model was produced (`fit-d03/*/results/orientation-*.json`).

## Diagnosis of M2 and M3

**What they measure.** M2 compares the whole distribution of per-read edit distances, so it tests whether the model gets
read-to-read variation right, not only the mean rate. M3 compares the distribution of read length minus reference length
(net insertions minus deletions per read), which decides how often a decoder's alignment band (|drift| <= 6 by default)
holds a read.

**Round-1 causes, found on FIT only:**

1. *Model class too simple.* The independent-site model has the right mean edit distance but too little variance:
   variance/mean 4.00 real vs 1.21 simulated (D03 HAC), 2.23 vs 1.06 (D03 fast), 1.71 vs 1.30 (CNR). Real reads are a mix
   of clean and very noisy reads. This also caused most of the D03 HAC M3 gap at drift 0 (clean reads keep their length).
2. *Split/selection artefact, not an estimator bug.* CNR reads are length-selected (every read has drift in [-4, +5]; this
   comes from the dataset's clustering, it is not documented), and D03 segments are censored at +-15 nt. Simulated reads
   were compared without that selection, so M3 compared two different populations. In round 2 the CNR M3 gap closed
   (-6.88 to -0.58 pp at <= 3) once the selection was matched; unselected it is -11.1 pp.
3. No estimator bug was found: M1 (per-base rates) passes for most jobs, the calibration residuals are within 2.6 % for every
   round-2 fit, and the round-trip test (M10) of CNR and D03 HAC backward passes.

**Round 2, what still fails and why (hypotheses, not tested):**

- *D03, all four conditions: M3 at drift 0 (and <= 3 for fast).* Real reads keep their exact length more often than the
  model predicts (HAC forward: 29.3 % real vs 26.5 % simulated; backward 28.5 % vs 25.5 %). One gamma factor scales insertions and deletions together; a
  basecaller that makes length-compensating errors (an insertion near a deletion), or separate per-read variation of indels
  and substitutions, would produce this. Neither is in the model class, and amendment 2 does not allow further changes on
  the basis of these DEV results. **Verdict: INADEQUATE.**
- *CNR: M2.* The gamma multiplier gives the right variance but a heavier tail than the length-selected real reads
  (p99 16 vs 14; TV 0.065 vs limit 0.05). The length selection also truncates the real edit-distance tail, which a
  read-level multiplier fitted to the variance overshoots. **Verdict: INADEQUATE.** `cnr-p4tie` also misses M3 at drift 0
  (+1.44 pp), because its tie-break changes where indels are counted.
- *D02: passes M2, M3, M8* but M3 at drift 0 passes by 0.09 pp, M1 (deletion and insertion rates 7 % and 10 % high), M5
  (deletion run-length TV 0.28: real deletions are mostly single bases plus a tail of long ones, which one geometric run
  length cannot follow; the plan's burst estimator is not implemented), M6 (homopolymer indels) and M9 (the Q11 bin:
  real error Phred 12.8 vs simulated 3.0, because the binned iSeq qualities do not fit the Gaussian quality model) fail.
  The fitted heterogeneity (shape 0.56) is large and acts on the sequencing stage only; it partly stands in for the
  deletion-length mixture. ADEQUATE here means only that the gating metrics pass on DEV. It is the weakest kind of
  ADEQUATE: second look at DEV, split by reference only (protocol 4.3), with several non-gating misfits.

## Held-out evaluation (protocol 4.2, 5.4)

**Not run.** The once-only validation of frozen F against HELD-OUT requires the PREREG commit of protocol 7, which does
not exist yet (no `experiments/v7/**/PREREG*`). The guard refuses held-out access without it, and the access log is empty.
It runs after the PREREG commit, exactly once per model, and is reported without being acted on.

## Limitations

- All fits are of other groups' public data (CNR: MIT; D03 Zenodo 10943282: CC BY 4.0; D02 ENA ERR12033806/10/50).
  Nanopore quality is assumed (no qualities in CNR or D03); D02 qualities are binned to three values.
- CNR and D02 are split by reference only, so their DEV references share runs with FIT (protocol 4.3). D03 DEV shares
  files 0 and 2 with FIT; only the held-out run `file-1` is a separate run.
- Round 2 is a second look at DEV. The read-length windows and the heterogeneity were estimated on FIT, but the decision to
  add them came from round-1 DEV failures.
- The CNR and D03 models describe length-selected reads. Simulating them on 313-nt VNX strands applies the fitted error
  process without the selection and extrapolates the relative position profile.
- Read heterogeneity absorbs every source of read-to-read variation (hard references, partly mis-assigned reads); it is
  not a claim about one mechanism.
- Forward/backward asymmetry, error correlation beyond the per-read factor, indel contexts and deletion bursts are not
  modelled (misfit notes in each model's `fit_report`).
- No model here is ADEQUATE for nanopore data. Under protocol 5.4 the CNR and D03 models may be used only as labelled
  stress conditions, not to choose decoder parameters.
