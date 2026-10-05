# V7 item D: plan for fitting channel models to public DNA-storage sequencing data

Status: plan only, 2026-10-05. No code, model, format or CLI behaviour changes with this document. No fitted model exists yet.

**Classification.** VNX-DNA has not synthesised, stored, amplified or sequenced any DNA and has no wet-lab results. Every
dataset below was produced by another group. Parameter files fitted to them are `data_source: "LABORATORY"`,
`evidence_class: "PUBLIC-DATA-DERIVED"`. Reads simulated from a fitted model, and every decoder result obtained on such
reads, stay **SIMULATED**. A fitted model describes the source lab's synthesis, PCR, instrument and basecaller at the time
of that experiment. It does not predict how VNX strands would behave in a future wet-lab round.

Inputs read for this plan: `docs/CHANNEL_MODEL.md` (`vnx.channel-model/1`), `src/vnxdna/core/schemas/channel-model.schema.json`,
the 14 models in `experiments/v6/channel/models` (all SIMULATED stress settings; `illumina-like` and `nanopore-like` are
qualitative only), `docs/DNA_STORAGE_DATASET_REGISTRY.md` (D01–D38) and `experiments/v6/phase4/P4-EXP-03-cnr-ids`
(the only PUBLIC-DATA-DERIVED result so far).

Source-strength labels used below: **peer-reviewed** (journal or IEEE conference), **preprint**, **vendor**,
**repository metadata** (Zenodo/GitHub/ENA API, checked today), **this plan** (an engineering proposal, not a measurement).

---

## 1. Reachability and metadata check (2026-10-05, from this host)

Only API calls, HEAD requests and byte-range reads of a few kB were made (zip central directory, first lines of text
files). Nothing large was downloaded; nothing was written into the repository except this file.

| What | URL checked | Result |
|---|---|---|
| CNR Centers.txt / Clusters.txt | raw.githubusercontent.com/microsoft/clustered-nanopore-reads-dataset/main/… | 200; 1,120,000 B / 30,452,518 B; GitHub API licence MIT |
| D03 Zenodo 10943282 | zenodo.org/api/records/10943282 (+ HEAD on each file) | 200; CC-BY-4.0; `clustered_read_segments.tar.gz` 138,068,568 B, `oligos.fasta` 14,671,450 B, `primers_synthesis.fasta` 303 B; first tar member `file-0_acc-false_passQ-false.tar.gz` |
| D02 DT4DDS reads | ENA portal filereport PRJEB65931; HEAD on ftp.sra.ebi.ac.uk/vol1/fastq/ERR120/008/ERR12033808/ERR12033808_1.fastq.gz over HTTPS | 200; 161 runs, 10.68 GB FASTQ.gz in total |
| D02 design files | raw.githubusercontent.com/fml-ethz/dt4dds_notebooks/master/data/Aging/0a_Twist_GCfix/design_files.fasta | 200; 1,416,000 B (12,000 × 108 nt per registry) |
| D01 ETH benchmark reads | ENA filereport PRJEB90546 | 10 runs, iSeq 100, paired, 1.51 GB FASTQ.gz |
| D07 Technion binned (Grass/Erlich/CNR) | zenodo.org/api/records/14296588 | 200; CC-BY-4.0; Grass.txt 309,941,369 B; Erlich.txt 1,977,247,465 B; Srinivasavaradhan.txt 19,826,466 B |
| D06 DNAformer | zenodo.org/api/records/13896773, 17473983 | 200; CC-BY-4.0; `raw_reads_pilot_nanopore.zip` (883,232,999 B) holds 1,111 `.fastq` files (zip central directory read by range request) |
| D05 Organick id20 refs | media.githubusercontent.com/media/uwmisl/data-nbt17/master/id20.refs.txt.gz | 200; 18,284,141 B (Git LFS); repo has no licence |
| D28 BBS subsets | zenodo.org/api/records/16959565 | 200; CC-BY-4.0; CNR with empty clusters removed (29.9 MB) |
| Tools | pypi.org/simple/edlib, /parasail; github.com/lh3/minimap2/releases | 200 (installable) |

Local tooling today: the lab venv has NumPy only (no SciPy, edlib, parasail, mappy, pysam); `minimap2`, `samtools`,
`cutadapt`, `NGmerge`, `bbmap` are absent; 8 cores, 31 GB RAM, 37 GB free disk. All fitting below fits in < 2 GB of downloads.

**Registry correction found today.** D03 is no longer only a preprint: Crossref lists Welter L, Sokolovskii R, Heinis T,
Wachter-Zeh A, Rosnes E, Graell i Amat A, "An End-to-End Coding Scheme for DNA-Based Data Storage With Nanopore-Sequenced
Reads", IEEE J. Sel. Areas Inf. Theory 7:17–32 (2026), doi:10.1109/JSAIT.2026.3655592 (peer-reviewed; metadata verified
via api.crossref.org, full text not opened; arXiv:2406.12955 read). `docs/DNA_STORAGE_DATASET_REGISTRY.md` (D03, §6) should
be updated in a separate docs commit.

---

## 2. Candidate datasets

"Ref" = designed (reference) strands are published, so per-position substitution/insertion/deletion, dropout and
coverage can be measured against ground truth. "Q" = per-base FASTQ qualities exist. Schema fields refer to
`vnx.channel-model/1` paths.

### 2.1 Fit first

| | D04 CNR | D03 Nanopore end-to-end (Zenodo 10943282) | D02 DT4DDS (PRJEB65931) |
|---|---|---|---|
| URL | github.com/microsoft/clustered-nanopore-reads-dataset | doi.org/10.5281/zenodo.10943282 | ebi.ac.uk/ena/browser/view/PRJEB65931; github.com/fml-ethz/dt4dds_notebooks |
| Paper | Srinivasavaradhan et al., ISIT 2021, arXiv:2107.06440 (peer-reviewed conf.) | Welter, Sokolovskii et al., IEEE JSAIT 7:17–32 (2026) (peer-reviewed) | Gimpel et al., Nat Commun 14:6026 (2023), doi:10.1038/s41467-023-41729-1 (peer-reviewed) |
| Licence (verified) | MIT (GitHub API). Reuse: anything, keep copyright + licence notice; a small fixture may be committed with the notice | CC BY 4.0 (Zenodo API). Reuse incl. commercial; attribution required; a fixture may be committed with attribution | Reads: INSDC, no explicit licence; INSDC policy places no restriction on use. Design FASTAs: `dt4dds_notebooks` has **no licence** (GitHub API: none) → download for analysis, do not commit or redistribute; `dt4dds` code GPL-3.0 → may be run as an external tool, must not be copied into the MIT VNX tree |
| Size / cost | 31.6 MB; seconds | 153 MB; ~1 min. Unpacked ≈ 5.4 M read segments (≈ 0.9 GB text) | 2 baseline runs + 1 PhiX run ≈ 0.31 GB (ERR12033806 130 MB, ERR12033810 143 MB, ERR12033850 38 MB); full project 10.68 GB not needed |
| Platform | Twist synthesis, PCR, ONT MinION (LSK109); basecaller not stated | GenScript synthesis, PCR, ONT MinION (+ one flongle file), guppy fast vs high-accuracy, pass (Q ≥ 8) vs fail | Twist and GenScript synthesis, KAPA PCR, Illumina iSeq 100 2×150 |
| Ref | yes, 10,000 × 110 nt (Centers.txt) | yes, 91,766 × 150 nt (110 nt payload + 2 × 20 nt primers); TX files per group | yes, 12,000 × 108 nt data region per pool (design_files.fasta); reads also contain the priming regions |
| Q | no | no (segments are text) | yes (iSeq FASTQ) |
| Pre-clustered | yes (Rashtchian 2017 clustering) | yes (BLAST primer search, segment 150 ± 15 nt, nearest strand by Levenshtein) | no: raw reads, mapping needed |
| Selection biases | clustering drops outlier reads; centres not uniformly random (README note 2024-08-12); 16 empty clusters = dropout **or** clustering failure | reads with \|drift\| > 15 nt or missing primers are excluded, so edit-distance tail and truncation are under-observed; file3 is a subsample of 42 M reads (Table I note c) | dt4dds pipeline thresholds (similarity 0.7/0.85) if reused; otherwise none beyond our own filters |
| Published numbers for cross-check | sub 2.2 %, ins 1.7 %, del 2.0 % (Trellis BMA paper, clusters 1–2000); VNX P4-EXP-03 measured 2.16 / 1.66 / 1.95 % | Table II (file3, "similar across all files"): acc-BC pass p_I 0.009, p_D 0.014, p_S 0.020; acc-BC fail 0.037/0.052/0.094; fast-BC pass 0.014/0.038/0.043; fast-BC fail 0.023/0.062/0.080 (read from arXiv PDF today) | 40 datasets: del 6.7 ± 6.9, sub 7.9 ± 2.0, ins < 0.3 ± 0.2 per 1000 nt; deletion runs mean 2.6 nt; electrochemical 5′ deletion > 5 %/nt; PCR 1.09e-4 sub/nt/cycle (61 % A>G/T>C); iSeq PhiX sub 1.8 ± 0.8e-3, indels < 1e-4; coverage lognormal σ 0.58 (GC-constrained) vs 1.30 (unconstrained) (quoted from the registry, which checked the Europe PMC full text) |
| Schema fields it can fill | `sequencing.substitution` (rate, matrix, from_multipliers), `insertion` (rate, base_weights), `deletion` (rate, run_length), `position_profile` (absolute, 110), `homopolymer`, `bursts`, `coverage` (lognormal/NB σ or k), `synthesis.dropout_rate` (upper bound only). Not: quality, reverse_complement_rate (reads are pre-oriented), synthesis/sequencing split | as CNR, plus `reverse_complement_rate` (forward vs backward counts: e.g. file3 acc-BC pass 432,501 fwd / 470,052 bwd → 0.52), `synthesis.dropout_rate` (empty clusters per TX list), primer-region error profile (positions 1–20, 131–150), fast vs HAC and pass vs fail as separate models. Not: quality | all four stages: `sequencing.*` incl. `quality` (calibration) and absolute `position_profile` (cycle-dependent), `synthesis.substitution/deletion/position_profile/truncation` (Aging_0 minus PhiX), `amplification.substitution_per_cycle` (PCR series 15–90 cycles, optional), `storage.damage` (Aging 2/4/7 d, optional), `sequencing.coverage` lognormal σ and `amplification.gc_bias` (GCall pools), `synthesis.dropout_rate` |

### 2.2 Later candidates

| ID | Dataset (URL) | Licence (verified today?) | Size | Platform | Ref | Q | Adds |
|---|---|---|---|---|---|---|---|
| D01 | ETH codec benchmark, ENA PRJEB90546 + github.com/fml-ethz/dt4dds-benchmark_notebooks | reads INSDC; notebooks GPL-3.0 (GitHub API, yes) | 1.51 GB reads; repo 0.54 GB | material-deposition vs electrochemical synthesis, iSeq 100 | yes (design_files.fasta per experiment) | yes | best/worst-case synthesis at ~129 nt; same group and pipeline as D02, so a cross-check of the D02 fit; competitor-decoder runs (separate item) |
| D06 | DNAformer, Zenodo 13896773 / 17473983 | CC BY 4.0 (yes) | binned 74–468 MB each; raw nanopore FASTQ pilot 0.88 GB | Twist, Illumina MiSeq + ONT (+ raw FAST5) | yes (bin headers; original files in 17473983) | raw FASTQ yes; binned no | **nanopore quality calibration** (only candidate found with ONT FASTQ + refs at modest size); Illumina and ONT on one pool |
| D07 | Technion binned Grass 2015 / Erlich 2017 / CNR, Zenodo 14296588 | CC BY 4.0 on the record (yes); underlying 2015/2017 data terms not stated | 0.31 / 1.98 / 0.02 GB | CustomArray (Grass, not re-verified), Twist (Erlich); Illumina | yes (bin header) | no | older electrochemical chemistry (Grass); reads were primer-trimmed and filtered by the binning script, so tails are biased |
| D05 | Organick 2018 id20, github.com/uwmisl/data-nbt17 (LFS) | **no licence** (GitHub API: none) → reuse terms unclear; fit internally, publish only derived statistics, ask the founder before shipping a model | 1.38 GB | Twist, Illumina (instrument not verified) | yes (id20.refs.txt.gz) | yes | large-pool Illumina; keep at priority 3 because of the licence |
| D12 | Chandak 2020, github.com/shubhamchandak94/nanopore_dna_storage_data | data repo: no licence (GitHub API) → unclear | 1.9 GB repo | ONT MinION, guppy + bonito, FAST5 | yes (oligo files) | yes | basecaller-version comparison; licence blocks shipping |
| D28 | BBS subsets, Zenodo 16959565 | CC BY 4.0 (yes) | 30 MB | as CNR/DNAformer | yes | no | CNR minus empty clusters: a second view of the CNR dropout ambiguity |
| D20 / D21 | HEDGES nanopore set (Zenodo 11985455), radiation damage (Zenodo 12713629) | CC BY 4.0 (yes) | 7.3 GB / 6.9 GB | ONT / Illumina | D20 unclear, D21 yes | yes | `storage.damage` from radiation (D21); too large for the first pass |

ETH/Grass/Organick, as asked: the **ETH** group's data are public (D01, D02, both via ENA + GitHub); **Grass 2015** reads are
public only through the Technion binned derivative (D07); **Organick 2018** id20 is reachable but unlicensed (D05).

---

## 3. Method

### 3.1 Pipeline (proposed path `experiments/v7/fit/`, nothing exists yet)

```
fetch.py (datasets.lock.json: URL, size, md5 from source, sha256 computed locally)
  → ingest (format adapters: CNR clusters, D03 TX/RX, binned text, FASTQ.gz + design FASTA)
  → assign read → reference (pre-clustered: given; raw: k-mer index, then verify)
  → realign globally to the full reference, left-normalise indels
  → event tables (by reference position, base, context, homopolymer run, read position, quality)
  → estimators → canonical vnx.channel-model/1 + sidecar (calibration tables, misfit report)
  → hold-out validation with the VNX simulator (§3.4)
```

Data stay outside the repository (`$VNX_DATA_DIR`, default under `/root/vnx-dna-lab/data/public/`); only
`datasets.lock.json`, fitted models, results JSON and (for MIT/CC BY sources) a ≤ 50-cluster fixture with the licence
notice are committed. Dependencies go in an optional extra (`[fit]`: edlib, optionally scipy); no codec layer imports
the fitter (layer rule R2 stays).

### 3.2 Alignment

| Step | Tool and parameters | Why / source |
|---|---|---|
| Adapter trim (raw Illumina) | cutadapt `-a AGATCGGAAGAGC -m 50 -e 0.1` on R1 (and `-A` on R2) | Martin, EMBnet.journal 17:10 (2011), doi:10.14806/ej.17.1.200 (peer-reviewed). dt4dds' own default adapter is `AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT` (standard.yaml, read today) |
| Pair handling | **Fit on R1 alone; R2 separately as a replicate.** Do not merge for rate estimation | Merging (NGmerge, Gaspar, BMC Bioinformatics 19:536 (2018)) replaces overlapping bases by the higher-quality call and so hides sequencing errors; dt4dds also defaults to `paired = False` (config.py) |
| Read → reference (raw reads) | exact k-mer index (k = 12, 3 seeds per reference, at 5′, middle, 3′ of the data region) → candidate set → edlib distance; accept if best ≤ 0.3·L and best < second-best − 3; else unassigned (counted). Optional cross-check: minimap2 `-x sr` (Illumina) / `-x map-ont -k 13 -w 5` (ONT) `--secondary=no` | Li, Bioinformatics 34:3094 (2018), doi:10.1093/bioinformatics/bty191; edlib: Šošić & Šikić, Bioinformatics 33:1394 (2017), doi:10.1093/bioinformatics/btw753 (both peer-reviewed). References are random 108–150-nt sequences, so collisions are rare; the margin rule guards against mis-assignment |
| Per-read alignment for event counting | edlib, unit costs, `task="path"`. Mode **HW** (reference global, read infix) when reads carry primers/adapters (D02, D01, raw D06); **NW** for pre-trimmed segments (CNR, D03, binned). Do **not** count events on minimap2 output: local alignment soft-clips the ends, which removes exactly the end effects the position profile must capture | P4-EXP-03 used unit-cost NW already; D03 authors used Wagner–Fischer with random tie-breaking (arXiv:2406.12955 §V-A1) |
| Indel normalisation | shift every indel to its leftmost equivalent position (inside homopolymers and short repeats) before counting; record whether an indel lies in a run ≥ 2/3/4 | unit-cost alignments are not unique; without normalisation position profiles and homopolymer multipliers depend on the tie-break (P4-EXP-03 used diagonal > deletion > insertion) |
| Sensitivity check | on a 20 k-read subset: (a) rightmost normalisation, (b) random tie-break, (c) a pair-HMM posterior (one EM pass) — report the change of every fitted rate; accept the counting fit if all change < 5 % relative | the D03 authors report no gain from Baum–Welch over counting for their KMER model (§V-A1) — their observation, not tested here |
| Orientation | D03 forward and backward segment sets fitted **separately** (backward reverse-complemented to reference orientation), then compared; merge only if the substitution matrices agree within CI | D03 §V warns that merging erases asymmetric substitution and directional memory effects |
| PhiX (sequencing-only errors, D02) | map PhiX-run R1 to NC_001422.1 (5,386 bp) with minimap2 `-x sr`, then edlib HW realignment of each read against its mapped window ±10 nt | the PhiX spike-in has no synthesis errors, so its rates estimate the instrument stage alone (Gimpel 2023 used it the same way) |

The native VNX aligner (`src/vnxdna/v5/native/align.c`) can be used as a second implementation on a subset to
cross-check edit distances (must be identical), not as the primary counter (it is tuned for the decoder's band).

### 3.3 Statistics and estimators (all per dataset and condition)

Notation: n_b = aligned reference bases; counts are summed over all assigned reads. The /1 simulator applies, per
position, one uniform draw that chooses deletion, insertion or substitution (CHANNEL_MODEL.md), so per-position event
probabilities are estimated as exclusive events per reference base.

| Statistic | Estimator | /1 field |
|---|---|---|
| Substitution rate, matrix | rate = subs / n_b; matrix[i][j] = subs(i→j)/subs(i→·); from_multipliers[i] = (subs from i / bases i) / rate | `*.substitution` |
| Insertion rate, base weights | rate = insertion events / n_b; base_weights = inserted-base frequencies. Also measure the share of insertion **runs** > 1 at one gap: /1 inserts at most one base per position, so this share is reported as model misfit | `*.insertion` |
| Deletion events, run length | rate = deletion runs / n_b; geometric MLE mean = mean run length; goodness of fit of geometric vs observed run-length histogram (1…8+) by χ²; if the tail is heavier, fit the excess as `bursts` (rate per read, max_length = p99 of excess) | `*.deletion`, `sequencing.bursts` |
| Position profile | per reference position: rate(pos)/overall rate, for sub/ins/del; `absolute` basis with L entries. Smooth only if a position has < 200 events (merge into 5-nt bins). For Illumina, also by **read cycle** (absolute basis is right for the instrument stage) | `*.position_profile` |
| Homopolymer | scan min_run ∈ {2,3,4,5}; multipliers = rate inside runs ≥ min_run / rate outside, separately for indels and subs; pick min_run by log-likelihood | `sequencing.homopolymer` |
| Coverage incl. dropout | per-reference read counts n_i. Fit Poisson, NB (k), Poisson–lognormal (σ; Gauss–Hermite quadrature, 40 nodes, NumPy only), each with and without a zero-inflation π; select by AIC. π → `synthesis.dropout_rate`, σ → `coverage.sigma`. The mean is a property of sequencing depth, so ship it as a default and let users override it | `sequencing.coverage`, `synthesis.dropout_rate` |
| GC bias | Poisson–lognormal regression of n_i on strand GC with the /1 weight exp(−s((gc − opt)/0.1)²); only on GC-unconstrained pools (D02 GCall); LR test against s = 0 | `amplification.gc_bias` |
| Truncation / read length | Illumina HW alignments that end before the reference end while the read continues into adapter → truncated molecules (synthesis); ONT: distribution of read length − L (D03 is censored at ±15, report as such) | `synthesis.truncation`, `sequencing.read_length` |
| Stage split (D02 only) | sequencing = PhiX rates (same run); amplification = slope of sub rate vs PCR cycles (optional runs); synthesis = Aging_0 rates − PhiX rates (floor at 0, CI by bootstrap over references); ONT datasets: everything goes to `sequencing`, synthesis left at a labelled prior or 0 | stage objects |
| Molecule-shared errors | within a cluster, an event at the same reference position in ≥ 30 % of ≥ 10 reads is a molecule-level (synthesis/PCR) or reference error. Report its frequency; with ONT error levels it is only an upper bound | `synthesis.*`, `molecules_per_strand` (only if clearly identifiable) |
| Quality calibration (FASTQ only) | per reported Q: empirical error = (mismatches + inserted read bases)/(read bases at that Q); output a calibration table (sidecar). Map to /1: `correct` = median Q on correct bases, `error` = median Q on erroneous bases, `informative` = max(0, P(Q ≤ τ\|error) − P(Q ≤ τ\|correct)) with τ the midpoint, `sd` = pooled SD, `position_slope` = least-squares slope of mean Q on correct bases vs read cycle. First check whether the instrument reports binned qualities (count distinct Q values) | `sequencing.quality` |
| Error co-occurrence | P(event at i+1 \| event at i) vs marginal (burstiness beyond deletion runs); k-mer (k = 3) conditional rates as in the D03 KMER model | not expressible in /1; reported as misfit, input to a future opt-in schema |

Confidence intervals: bootstrap over **references** (clusters), 200 resamples, never over reads (reads of one reference
are not independent). Rates of ~1e-4 from ~2 M reads × 108 nt have relative SE ≈ 1 %; CNR rates of ~2 % from
269,709 × 110 positions have a relative SE well under 1 % in aggregate and about 1.5–3 % per position (≈ 5,000 events per position; clustering of reads by reference widens this, hence the bootstrap).

### 3.4 Fit-quality metrics (hold-out)

Split references deterministically: SHA-256(reference sequence) mod 5 == 0 → test (20 %), the rest → fit. Simulate the
test references with `vnx channel simulate … --model <fitted> --seed s` for 5 seeds; for per-read metrics condition on the
observed read count per reference (override coverage to the empirical counts, or subsample simulated reads to the
real counts) so error fit and coverage fit are judged separately. Report every metric also for the shipped
`illumina-like`/`nanopore-like` models as the baseline.

| # | Metric | Proposed acceptance (this plan, tunable) |
|---|---|---|
| M1 | per-base sub/ins/del rates, simulated vs held-out real | within 5 % relative or 3 bootstrap SE, whichever is larger |
| M2 | per-read edit distance distribution (unit cost, NW) | two-sample KS statistic D ≤ 0.03 and total-variation distance ≤ 0.05; p90 and p99 within 10 % relative. With ~10⁵ reads a KS **p-value** rejects any tiny difference, so the effect size D is the criterion, not p |
| M3 | length drift (read length − L) | P(\|drift\| ≤ 0, ≤ 3, ≤ 6) within 1 percentage point; ≤ 6 is the VNX default band (P4-EXP-03) |
| M4 | position profile (11 bins and per position) | per-bin ratio within ± 10 %; χ² reported |
| M5 | deletion run-length histogram (1…8+) | TV ≤ 0.05 |
| M6 | homopolymer-conditioned indel rate | within 10 % relative |
| M7 | coverage: per-reference counts | KS D ≤ 0.05; zero fraction inside the bootstrap 95 % CI; p10/p90 within 10 % |
| M8 | functional: exact-length positional vote (P4-EXP-03 metric) and the VNX consensus per-base error vs cluster size k ∈ {1, 2, 5, 10, 20}, on real held-out clusters vs simulated clusters | curves inside each other's Wilson 95 % CI or within 2 percentage points |
| M9 | quality calibration (FASTQ datasets) | expected calibration error of simulated vs real within 1 Phred unit per occupied bin |
| M10 | round trip: simulate from the fitted model, refit, compare | every parameter within 5 σ (existing tolerance pattern in `tests/v6/channel`) |

A model that fails M2, M3 or M8 is not shipped; the misfit report says which /1 limitation causes it (typically
k-mer context, insertion runs, or forward/backward asymmetry).

### 3.5 Tests (before any real data)

1. Round trip on SIMULATED data from each of the 14 shipped models (M10), including models with position profiles,
   geometric deletion runs and lognormal coverage.
2. Determinism: identical fitted JSON (byte for byte, canonical form) for 1, 3 and 8 workers.
3. A ≤ 50-cluster CNR fixture (MIT notice kept) and a ≤ 50-cluster D03 fixture (CC BY attribution kept) with golden
   fitted rates.
4. Provenance: a fitted model without `provenance.datasets[*].sha256` or with `data_source` other than LABORATORY is refused.

---

## 4. Recommendation: what to fit first

Day 0 (smoke test, not a shipped model): **CNR (D04)**. The data and an aligner exist (P4-EXP-03); it exercises
the fitter end to end and the result must reproduce P4-EXP-03 (2.16 / 1.66 / 1.95 %).

| Order | Dataset | Proposed model names | Why |
|---|---|---|---|
| 1 (Illumina-like) | **D02 DT4DDS**, Twist_GCfix Aging_0a + 0b (ERR12033806, ERR12033810) + PhiX (ERR12033850); then GenScript_GCfix (ERR12033804, ERR12033808, PhiX ERR12033846) | `illumina-iseq-twist-fit`, `illumina-iseq-genscript-fit` | peer-reviewed, raw FASTQ with qualities, ground truth, a PhiX control for the stage split, published reference numbers to check against; ~0.6 GB for both vendors |
| 2 (nanopore) | **D03**, acc-BC pass and fast-BC pass (forward and backward fitted separately) | `ont-guppy-hac-pass-fit`, `ont-guppy-fast-pass-fit` (fail groups as optional stress variants) | peer-reviewed (JSAIT 2026), CC BY 4.0, 153 MB, ~5.4 M segments, primers present, published Table II rates to check against, dropout visible as empty clusters |

Nanopore quality calibration is not possible with D03 or CNR (no qualities); the follow-up for that is D06's raw
pilot nanopore FASTQ (0.88 GB, CC BY 4.0) with references from `BinnedPilotNanopore.txt`.

### Effort estimate (engineering, this plan)

| Work | Engineer time | Compute (8 cores) |
|---|---|---|
| Fitter core: ingest adapters, edlib wrapper, left-normalisation, estimators, /1 writer with provenance, tests §3.5 | 3 days | — |
| CNR smoke test + hold-out metrics harness (M1–M8, M10) | 1 day | ~5 min |
| D03: two shipped models (+ fwd/bwd comparison, fail variants) | 1–1.5 days | ~20 min (≈ 5.4 M alignments of 150 nt) |
| D02: read assignment, PhiX, stage split, quality calibration, coverage/GC (GC on GCall run ERR12033805, optional) | 2 days | ~30–40 min (2–3 runs × ~2 M reads) |
| Docs (CHANNEL_MODEL.md "fitted models" section, registry update), evidence audit | 0.5–1 day | — |
| **Total** | **≈ 7.5–8.5 engineer-days** | **< 2 h**, < 1 GB download |

Risks: (1) /1 cannot express k-mer context, insertion runs or forward/backward asymmetry, so nanopore fits may fail M2/M8
at the tails (D03 found context effects worth modelling); (2) dropout in pre-clustered sets mixes molecular loss with
clustering/segmentation loss, so `dropout_rate` from CNR/D03 is reported as an upper bound; (3) all source strands are
108–150 nt while VNX strands are ~313 nt: applying a fitted model to 313 nt is an **extrapolation** (relative-basis
synthesis profiles, absolute-basis sequencing profiles, and no Illumina data beyond cycle 150) and must be labelled so;
(4) the D02 design files have no licence, so they cannot be committed — the lock file records their SHA-256 instead.

---

## 5. Labels for anything produced from this plan

- Fitted parameter files: `data_source: "LABORATORY"`, `evidence_class: "PUBLIC-DATA-DERIVED"`, `provenance.datasets`
  with accession (DOI/ENA run IDs) and the locally computed SHA-256 of each file used, `references` with the paper DOI,
  `fitter` with the VNX commit and tool versions.
- Results that compare fitted rates with published numbers: PUBLIC-DATA-DERIVED.
- Decoder recovery measured on reads simulated from a fitted model: SIMULATED (parameters PUBLIC-DATA-DERIVED).
- No statement that VNX strands were synthesised or sequenced; no claim that a fitted model predicts VNX wet-lab results.
