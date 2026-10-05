# 40 - Public DNA-data-storage sequencing datasets for VNX-DNA (registry, 2026-10-05)

**Classification: PUBLIC-DATA-DERIVED registry (metadata only).** VNX-DNA has not synthesised, stored or sequenced any DNA and has no
wet-lab results. Every dataset below was produced by another group. Results obtained from these data must be labelled
PUBLIC-DATA-DERIVED; channel results generated from fitted models stay SIMULATED.

Machine-readable registry: `/root/vnx-dna-lab/research-2026-10-05/raw/40-datasets.json` (38 profiled datasets plus 28 further
candidate accessions listed but not profiled).

## How entries were verified

- Accessions found by EBI project search, then each checked against NCBI SRA runinfo (run count, spots, SRA-normalised size,
  instrument), the ENA project XML, the Zenodo/figshare/GitHub APIs, and the data-availability statement of the paper (Europe PMC full
  text) where one exists. No accession here was taken from memory.
- Only small files were downloaded, to count strands (ETH design FASTA 1.6 MB, CNR Centers.txt 1.1 MB, one Chandak oligo file).
- "not verified" means the value was not checked against a primary source in this pass; do not use those values in code
  until someone checks them.
- Source strength: **peer-reviewed** = journal or IEEE conference paper; **preprint** = arXiv or bioRxiv; **unverified** = SRA/ENA
  project with no publication found; **vendor** = a manufacturer document.
- ENA and SRA give no explicit licence. The INSDC policy places no restrictions on use, so the registry says "unclear" and the policy.

## Registry

Priority: 1 = use first; 5 = little use to VNX. "Ground truth" means the reference strands (and ideally the encoded file) are
published. VNX cannot decode any of these datasets because they use other encodings. They are good for **fitting channel models**
and for **running competitor decoders on real reads**.

| ID | Dataset | Accession(s) | Sequencing | Synthesis | Strand length | Ground truth | Size | Licence | Paper status | Main VNX use | P |
|---|---|---|---|---|---|---|---|---|---|---|---|
| D01 | ETH codec benchmark pool experiments (6 literature codecs, best/worst-… | ENA PRJEB90546 | ILLUMINA Illumina iSeq 100 | two scenarios: material-deposition synthesis … | design_files.fasta (bestcase/Cov10): 11,… | YES | ENA ~1.0 GB; GitHub notebooks repo … | reads: INSDC open access | peer-reviewed | fit channel models (Illumina iSeq, two synthesis fidelities,… | 1 |
| D02 | DT4DDS error and bias characterisation (synthesis, PCR, aging, Illumin… | ENA PRJEB65931 | ILLUMINA Illumina iSeq 100 | Twist Bioscience (material deposition) and Ge… | 4 pools of 12,000-12,472 sequences, 143-… | YES | ENA ~10.6 GB (SRA-normalised) | reads: INSDC open access | peer-reviewed | fit channel models stage-by-stage (synthesis vendor, PCR cyc… | 1 |
| D03 | Nanopore end-to-end channel dataset (GenScript pool, MinION, guppy fas… | Zenodo 10.5281/zenodo.10943282 | OXFORD_NANOPORE MinION (pore/flow cell not stated in record)… | GenScript; 91,766 oligos | 150 nt = 110-nt pseudo-random payload + … | YES | ~153 MB | CC BY 4.0 | preprint (arXiv); … | fit nanopore channel models (fast vs HAC, pass vs fail) with… | 1 |
| D04 | Clustered Nanopore Reads (CNR) dataset | GitHub microsoft/clustered-nanopore-reads-dataset | OXFORD_NANOPORE MinION (ligation adapters; basecaller not st… | Twist Bioscience, PCR amplified | 110 nt (Centers.txt, verified) | YES | 31 MB | MIT | peer-reviewed conf… | fit per-read IDS nanopore model quickly; smoke test of fitti… | 1 |
| D05 | Organick et al. 2018 random-access dataset (file id20 subset) | GitHub uwmisl/data-nbt17 (Git LFS) | Illumina (instrument for id20 not verified) | Twist Bioscience for most pools per paper (no… | 150 nt incl. primers per paper (not re-v… | YES | ~1.38 GB | no licence file (unclear) | peer-reviewed | fit Illumina channel (Twist + NextSeq-era chemistry) | 2 |
| D06 | DNAformer datasets (Technion; Twist; Illumina MiSeq + ONT incl. raw si… | Zenodo 10.5281/zenodo.13896773<br>Zenodo 10.5281/zenodo.17473983<br>Zenodo 10.5281/zenodo.17399364 | ILLUMINA MiSeq paired-end (PEAR-merged); OXFORD_NANOPORE (pi… | Twist Bioscience | not verified here | YES | 13896773: ~38.5 GB total (binned fi… | CC BY 4.0 (data) | peer-reviewed | fit Illumina and nanopore models on the same Twist pool | 2 |
| D07 | Technion binned benchmark: Grass 2015, Erlich 2017, Srinivasavaradhan … | Zenodo 10.5281/zenodo.14296588 | Illumina (Grass, Erlich); ONT MinION (CNR) | Grass 2015: CustomArray (per Heckel 2019 cont… | Erlich 152-nt data region (Heckel et al.… | YES | Grass.txt 310 MB, Erlich.txt 1.98 G… | CC BY 4.0 (record) | peer-reviewed sour… | fit models across three chemistries quickly (pre-clustered) | 2 |
| D08 | DNA Fountain raw reads (master pool + dilution/deep-copy experiments) | ENA PRJEB19305<br>ENA PRJEB19307 | ILLUMINA Illumina MiSeq | Twist Bioscience (72,000 oligos) | 152-nt data region (Heckel et al. 2019 T… | PARTIAL | ~68.7 GB SRA-normalised (PRJEB19307… | reads: INSDC open access | peer-reviewed | fit Illumina MiSeq + Twist model; dilution series for dropou… | 2 |
| D09 | Goldman et al. 2013 (EBI) - 5 files in 153,335 features | ENA PRJEB1186 | ILLUMINA Illumina HiSeq 2000 | Agilent OLS | 117 nt data features (Heckel 2019); feat… | YES | ~18.3 GB SRA-normalised | reads: INSDC open access | peer-reviewed | historic Agilent/HiSeq channel fit | 3 |
| D10 | HEDGES in vitro test reads | SRA PRJNA631961 (SAMN14897329-35) | ILLUMINA Illumina MiSeq | not verified here | not verified here | NOT FOUND as a strand list | ~1.4 GB | reads: INSDC open access | peer-reviewed | fit MiSeq 2x300 model only if references can be regenerated | 3 |
| D11 | Antkowiak et al. 2020 photolithographic synthesis reads | Figshare collection 10.6084/m9.figshare.c.5128901.v1 | Illumina (R1 FASTQ files only in figshare; instrument not re… | photolithographic in-house synthesis (low cos… | not verified here | UNCLEAR | ~4.1 GB | CC BY 4.0 (figshare) | peer-reviewed | stress-test VNX model fitting on a high-error synthesis chan… | 3 |
| D12 | Chandak et al. nanopore convolutional-code dataset (raw FAST5 + FASTQ … | GitHub shubhamchandak94/nanopore_dna_storage_data (branches master, bonito) | OXFORD_NANOPORE MinION (flow cell not verified); guppy and b… | not verified here | merged.fa: 12,301 oligos; first oligo 15… | YES | GitHub repo ~1.9 GB plus externally… | data repo: none stated (unclea… | peer-reviewed conf… | fit nanopore model incl. positional error profile | 2 |
| D13 | Lopez et al. 2019 nanopore assembly readout | GitHub uwmisl/data-ncomms19-nanopore (Git LFS) | OXFORD_NANOPORE MinION | not verified here | assembled long constructs (not re-verifi… | YES | several GB | none stated (unclear) | peer-reviewed | long-read nanopore channel (assembly) - low relevance to VNX… | 4 |
| D14 | Chen et al. 2020 molecular bias coverage data | GitHub uwmisl/storage-biasing-ncomms20 | Illumina (coverage arrays derived by BWA) | Twist (biased vs non-biased chip; per data_re… | n/a | n/a | ~36 MB | none stated (unclear) | peer-reviewed | fit VNX coverage model (negative binomial vs lognormal) and … | 2 |
| D15 | Lee et al. 2019 template-independent enzymatic (TdT) synthesis | SRA PRJNA521561 / SRP185459 | ILLUMINA Illumina MiSeq; OXFORD_NANOPORE MinION | enzymatic (TdT, terminator-free; homopolymer-… | short enzymatic strands (see paper) | processed data + code in GitHub | ~1.2 GB | reads: INSDC open access | peer-reviewed | enzymatic-synthesis channel exploration only (different enco… | 4 |
| D16 | Lee et al. 2020 photon-directed multiplexed enzymatic synthesis | SRA PRJNA633646 / SRP262219 | ILLUMINA Illumina MiniSeq; OXFORD_NANOPORE MinION | enzymatic, photon-directed | see paper | MATLAB scripts in GitHub | ~32 MB | reads: INSDC open access | peer-reviewed | enzymatic channel exploration only | 5 |
| D17 | DNA-Aeon evaluation data | SRA PRJNA855029 (SRR19954693-97) | ILLUMINA Illumina MiSeq | not verified here | not verified here | UNCLEAR | ~3.1 GB | reads: INSDC open access | peer-reviewed | Illumina channel fit if references obtained | 3 |
| D18 | DNA StairLoop experiments | SRA PRJNA1306341<br>Figshare 10.6084/m9.figshare.28902032.v2<br>Figshare 10.6084/m9.figshare.26212682 (encoded oligos) | ILLUMINA Illumina MiSeq | not verified here | not verified here | YES per DAS | ~6.1 GB SRA | reads: INSDC open access | peer-reviewed | Illumina channel fit with published oligos | 3 |
| D19 | Magnetic DNA RAM with nanopore readout (Stanford) | SRA PRJNA758230 | ILLUMINA Illumina iSeq 100; OXFORD_NANOPORE MinION; OXFORD_N… | not verified here | see paper | oligo generation code in bonito branch | ~9.2 GB | reads: INSDC open access | peer-reviewed | nanopore (MinION + PromethION) channel fit | 3 |
| D20 | HEDGES nanopore nominal FAST5/FASTQ evaluation set (NCSU/JHU) | Zenodo 10.5281/zenodo.11985455 | OXFORD_NANOPORE (FAST5 + FASTQ) | not verified here | not verified here | UNCLEAR | ~7.3 GB | CC BY 4.0 | peer-reviewed | nanopore channel with HEDGES-encoded strands; soft-decoder c… | 3 |
| D21 | Takahashi et al. 2024 particle-radiation damage reads (Microsoft/UW) | Zenodo 10.5281/zenodo.12713629 | Illumina (not verified) | not verified here | see sequences files | YES | ~6.9 GB | CC BY 4.0 | peer-reviewed | fit a damage/aging stage (dropout + substitution increase) f… | 3 |
| D22 | Joint index-payload codes: large electrochemical + inkjet pools (Tianj… | SRA PRJNA1439609<br>Zenodo 10.5281/zenodo.19203651 (encoded sequences) | ILLUMINA Illumina NovaSeq X Plus; OXFORD_NANOPORE MinION; OX… | electrochemical synthesis (ECS-Pool1 999,998 … | 170 nt and 248 nt | YES | ~1.9 GB SRA + 346 MB | reads: INSDC open access | unverified | fit ~1M-strand electrochemical channel on Illumina + ONT (sc… | 2 |
| D23 | Direct nanopore readout with UEP oligos (Tianjin), 300-nt strands | SRA PRJNA1505625 | ILLUMINA Illumina HiSeq 2000; OXFORD_NANOPORE PromethION; ON… | not stated in project; 179,928 oligos x 300 n… | 300 nt | NOT FOUND | ~0.6 GB | reads: INSDC open access | unverified | fit fast/HAC/SUP nanopore models at VNX-like strand length (… | 3 |
| D24 | Composite ranging codes as indices (Tianjin) - Illumina + MinION + Pro… | SRA PRJNA1371011<br>SRA PRJNA1276869 | ILLUMINA Illumina NovaSeq 6000; OXFORD_NANOPORE MinION; OXFO… | not verified here | not verified | UNCLEAR | ~0.8 GB | reads: INSDC open access | peer-reviewed | cross-platform channel fit if references available | 4 |
| D25 | High-density low-coverage coding (SWJTU), 2 x 30,000 oligos | SRA PRJNA1436205 | ILLUMINA Illumina NovaSeq 6000 | phosphoramidite (vendor not verified) | not verified | NOT FOUND | ~3.7 GB | reads: INSDC open access | preprint (arXiv) | low-coverage channel fit if references obtained | 4 |
| D26 | DNA-of-Things (Stanford bunny, multi-generation) | ENA PRJEB35217 | ILLUMINA Illumina MiSeq | Twist (DNA Fountain encoding; not re-verified… | not verified | design files in fml-ethz/dt4dds scripts/bunny_generations/design_files.fasta | ~1.5 GB | reads: INSDC open access | peer-reviewed | coverage drift over repeated PCR generations | 3 |
| D27 | Yin-Yang codec experimental data (BGI) | CNGB CNSA CNP0001650 | not verified (CNGB record not opened) | not verified here | not verified | UNCLEAR | unknown | unclear (CNGB terms) | peer-reviewed | YYC competitor (better via D01, which includes YYC with grou… | 4 |
| D28 | Bidirectional Beam Search post-processed benchmark subsets (CNR, DNAfo… | Zenodo record 16959565 | ONT (all three sources) | as sources | 110 nt (CNR), others per source | YES | ~81 MB | CC BY 4.0 | preprint | small, ready-made trace-reconstruction benchmark for VNX con… | 3 |
| D29 | SeqFormer datasets (Illumina + ONT, preprocessed) | Zenodo 10.5281/zenodo.21377476 | ILLUMINA; OXFORD_NANOPORE | not stated | not stated | UNCLEAR | illumina_data.zip 11 MB, nanopore_d… | CC BY 4.0 | unverified | small consensus benchmark; provenance must be checked before… | 4 |
| D30 | DNA-DISK enzymatic synthesis on digital microfluidics | SRA PRJNA1143195 | ILLUMINA Illumina NovaSeq 6000 | enzymatic TdT single-nucleotide on DMF | short | UNCLEAR | ~0.45 GB | reads: INSDC open access | peer-reviewed | enzymatic channel exploration | 5 |
| D31 | PCRobot robotic PCR amplification for DNA data storage (2026, nanopore… | ENA PRJEB111897 | OXFORD_NANOPORE MinION | not stated | ~280-290 nt mean read length (SRA avgLen… | NOT FOUND | ~0.8 GB | reads: INSDC open access | unverified | PCR-stage error modelling if references obtained | 4 |
| D32 | LANL 'DNA storage error model' reads | SRA PRJNA978627 | ILLUMINA NextSeq 500 | not stated | not stated | NOT FOUND | ~2.9 GB | reads: INSDC open access | unverified | only after references are obtained from authors | 4 |
| D33 | MPHAC-DIS massively parallel homogeneous amplification of chip-scale D… | ENA PRJEB82436 | ILLUMINA Illumina NovaSeq 6000 | chip-scale array synthesis | not verified | decoding demo on figshare | ~0.5 GB | reads: INSDC open access | peer-reviewed | coverage/amplification-bias model | 4 |
| D34 | 'Error profile of the DNA storage channel' (Wellcome Sanger Institute) | ENA PRJEB32885 | ILLUMINA Illumina MiSeq | not stated | not stated | NOT FOUND | ~10.4 GB | reads: INSDC open access | unverified | potentially valuable error-profile data; needs designs from … | 4 |
| D35 | Davos Bitcoin DNA challenge (Goldman encoding), solved 2018 | ENA PRJEB41462 | ILLUMINA Illumina MiSeq | Agilent (not verified) | Goldman scheme | NO | ~1.4 GB | reads: INSDC open access | peer-reviewed meth… | low value | 5 |
| D36 | Composite DNA letters (Anavy et al.) | ENA PRJEB32427 | ILLUMINA Illumina HiSeq 2500 | composite-letter synthesis | see paper | UNCLEAR | ~32.8 GB | reads: INSDC open access | peer-reviewed | not applicable to VNX (composite alphabet) | 5 |
| D37 | Helixworks motif-based storage runs (ONT R10.4.1 FAST5) | Zenodo 10.5281/zenodo.15839178 (+15839390, 15848607, 15849818, 16032156) | OXFORD_NANOPORE R10.4.1 / FLO-MIN114 (FAST5) | enzymatic ligation of motifs (Helixworks) | motif assemblies | YES | ~28-35 GB per record | CC BY-SA 4.0 | vendor/company dat… | R10.4.1 signal data only; motif channel not applicable to VN… | 5 |
| D38 | Single-molecule assembly-free readout from medium-length encoded DNA (… | SRA PRJNA1235219<br>Zenodo 10.5281/zenodo.16883332 | OXFORD_NANOPORE MinION (POD5 + FASTQ) | plasmid-cloned medium-length DNA | kb-scale plasmid inserts | YES | ~150 MB | reads: INSDC open access | peer-reviewed | not aligned with VNX short-oligo design | 5 |

Searched for but not registered:
- Church, Gao, Kosuri 2012 (Science 337:1628) raw reads: no SRA/ENA project found by EBI project search; not registered
- Grass et al. 2015 raw reads: no repository accession found; only the Technion binned derivative (D07)
- Heckel/Mikutis/Grass 2019 'High/Low PR' CustomArray datasets: no accession stated in the Sci Rep full text
- Sabary SOLQC standalone dataset: none found; Technion data are D06/D07; Zenodo 7370899 'Deep DNA - First Pilot' (CC BY 4.0, 760 MB FASTQ) has no description of platform or references
- Wukong / Storage-D (Tianjin) experimental reads: no dataset accession verified
- Catalog / Biomemory public sequencing data: none found
- Kaggle 'DNA data storage' datasets: none found
- NOREC4DNA / MESA: software tools; related reads are DNA-Aeon PRJNA855029 (D17)
- Zenodo 7995806 (cited by SynDe): E. coli genomic nanopore data, not DNA storage; excluded

## Recommended first three

1. **D01: ETH codec benchmark (ENA PRJEB90546 + github.com/fml-ethz/dt4dds-benchmark_notebooks).** Gimpel et al., Nat Commun
   2026 (peer-reviewed). Six literature codecs (DNA-Aeon, DNA Fountain, Goldman, HEDGES, DNA-RS, Yin-Yang) were encoded into one
   pool and read on Illumina iSeq 100 in a high-fidelity (material deposition) and a low-fidelity (electrochemical) synthesis
   scenario, at several coverages. Per-codec `encoded.txt`, per-experiment `design_files.fasta` (11,293 x 129 nt) and demultiplexed
   reads are in GitHub; the raw FASTQ (~1.0 GB) is on ENA. This is the only dataset found that offers both (a) real reads with ground
   truth for channel fitting and (b) competitor decoders run on the same reads under a published protocol (bbmap/NGmerge/seqtk
   versions are pinned). Any VNX comparison must use matched code rate (0.5/1.0/1.5 bit/nt) and say SIMULATED for VNX, because VNX
   strands are not in that pool.
2. **D03: Sokolovskii/Welter nanopore dataset (Zenodo 10.5281/zenodo.10943282, CC BY 4.0, 153 MB).** 91,766 GenScript oligos of
   150 nt (110-nt payload + 20-nt primers), MinION, guppy fast vs high-accuracy, pass vs fail reads, and the primer FASTA. It is
   small and has primers in the reads, so it tests the primer-trimming and orientation steps that VNX lacks. Source: arXiv preprint
   (not peer-reviewed as checked). Start with the **CNR** dataset (D04, MIT, 31 MB, 110-nt, Twist + MinION, ISIT 2021) as a day-0
   smoke test of the fitting code. Note its README caveat (2024): the centers are not uniformly random.
3. **D02: DT4DDS (ENA PRJEB65931 + github.com/fml-ethz/dt4dds_notebooks).** Gimpel et al., Nat Commun 2023 (peer-reviewed). 161
   iSeq 100 runs (~10.6 GB) covering two vendors (Twist and GenScript/CustomArray), GC-constrained and unconstrained pools, PCR
   cycles, and accelerated aging. Design FASTAs are in GitHub. This is the best single source for fitting the *per-stage* parameters
   of the VNX channel. The published numbers in the next section can serve as priors before any download.

Next: D05 Organick id20 (1.4 GB, refs included), D12 Chandak (raw FAST5 + oligos), D22 Tianjin ~1M-strand electrochemical pools
(Illumina + ONT, encoded sequences on Zenodo), D06 DNAformer (Twist, Illumina + ONT + raw signal, original files published).

## Published parameters usable as priors now (with source strength)

| Quantity | Value | Source | Strength |
|---|---|---|---|
| Overall rates over 40 datasets (Illumina, Twist + GenScript) | del 6.7±6.9, sub 7.9±2.0, ins <0.3±0.2 per 1000 nt | Gimpel 2023 Nat Commun (D02) | peer-reviewed, measured |
| Deletion clustering | runs of consecutive deletions, mean length 2.6 nt; substitutions approximately independent | Gimpel 2023 | peer-reviewed |
| Electrochemical synthesis | deletion rate rises toward 5' end, >5 %/nt at the end | Gimpel 2023 | peer-reviewed |
| Material-deposition synthesis | < 1 deletion per 2000 nt, no strong positional dependence | Gimpel 2023 | peer-reviewed |
| PCR (KAPA SYBR FAST, Taq-based) | 1.09e-4 sub /nt /cycle; 61 % A>G/T>C | Gimpel 2023 | peer-reviewed |
| Aging (accelerated) | 1.64e-4 sub /nt per half-life; 77 % C>T/G>A | Gimpel 2023 | peer-reviewed |
| Illumina iSeq 100 (PhiX) | sub 1.8±0.8e-3 /nt; ins and del < 0.1e-3 /nt; minimum near cycle 20, rising after 25 | Gimpel 2023 | peer-reviewed |
| Coverage distribution | lognormal; sigma 0.58 (GC-constrained) vs 1.30 (unconstrained) for the synthesis shown in Fig. 2b | Gimpel 2023 | peer-reviewed |
| Illumina reading errors | sub ~4e-4 to 1.5e-3 /nt; indels ~1e-6 | Heckel et al. 2019 Sci Rep (citing a prior study) | peer-reviewed, secondary |
| Homopolymers | sub and del rise for runs > 6 | Heckel 2019 (citing ref. 24) | peer-reviewed, secondary |
| Twist Oligo Pools | up to 350 nt; error "up to 1:3,000 nt"; >90 % of oligos within <2x of mean; >0.2 fmol/oligo | Twist product sheet DOC-001054 REV12 (2026) | vendor spec |
| IDT oPools | 40-350 nt; "error rates below one per 2 kb"; scales 1/10/50 pmol/oligo | IDT launch material (Lab Manager article) | vendor spec, secondary |
| Codec benchmark error composition | 53 % sub / 45 % del / 2 % ins (used as a realistic mix) | Gimpel 2026 Nat Commun (D01) | peer-reviewed |

## What the VNX repository has today (committed state, worktree /root/vnx-dna-lab/v6, HEAD 081697b)

- `docs/V6_PHYSICAL_VALIDATION_INTERFACE.md` and `experiments/v6/physical/`: a record schema (synthesis, sample, storage, sequencing,
  decode, result, attestations), `validate.py` (VALID/INVALID/INCOMPLETE), and a SYNTHETIC SOFTWARE TEST example (66 strands of
  313 nt, profile v4-balanced, illumina-like model). There are three evidence classes: REAL PHYSICAL RESULT, SIMULATED RESULT and
  SYNTHETIC SOFTWARE TEST. **There is no PUBLIC-DATA-DERIVED class.**
- `experiments/v6/channel/models/*.json`: 14 models, all labelled SIMULATED, "not fitted to any measured platform". Parameters: i.i.d.
  sub/ins/del, dropout, coverage fixed/Poisson/negative-binomial with dispersion, duplication, homopolymer multipliers (min run 3), GC
  bias (strength/optimum), per-read deletion bursts, N rate, reverse-complement rate, quality scores (correct/error/informative). The
  README states the limit: errors are i.i.d. per base apart from the homopolymer, burst and GC terms.
- No ingestion path for real reads: no primer trimming, no paired-end merge, no read-to-reference assignment, no fitting code, no
  primer design and no order-export tool. VNX strands carry **no primers**. The example strand of 313 nt plus two 20-nt primers comes to
  353 nt, **over the 350-nt maximum** of both Twist and IDT pools (vendor specs above).

## Plan (a): fit VNX channel models to public data (implementable and testable)

1. **Model schema v2, opt-in.** Add optional fields to the channel JSON; the 14 v1 models stay valid: `classification`
   ("SIMULATED" or "PUBLIC-DATA-DERIVED-FIT"), `provenance` (accessions, run IDs, SHA-256 of each downloaded file, fitter commit),
   `position_profile` (per-position sub/ins/del multipliers, length L or binned), `sub_matrix` (4x4), `deletion_run_length`
   (geometric mean, for the 2.6-nt clustering), `coverage_model: "lognormal"` with `sigma`, and a `synthesis` vs `sequencing` split.
   Synthesis errors are shared by all reads from one molecule; sequencing errors are independent per read. This split matters for
   consensus decoding. Simulated output from a fitted model is still SIMULATED. Only the parameter file is PUBLIC-DATA-DERIVED.
2. **Fitter `experiments/v7/fit/fit_channel.py`.** Inputs: FASTQ(s), a reference FASTA, and an optional primer FASTA. Steps: trim
   primers (cutadapt, with the error rate as a parameter); merge pairs (NGmerge or bbmerge, the same tools as D01); assign each read to
   a reference (exact index or minimap2/edlib for nanopore; D03/D04 already supply clusters); run a global alignment with edlib or the
   native VNX aligner; count events by position, base context and homopolymer run; estimate coverage per reference and the fraction
   of dropouts; compare Poisson, NB and lognormal by AIC; and calibrate quality as empirical error against reported Q. Output is the
   model JSON with a provenance sidecar.
3. **Tests, before any real data.** (i) Round trip: simulate from a known model, fit, and check that every parameter is recovered
   within binomial CIs. This uses the existing 5-sigma tolerance pattern in `tests/v6/channel`. (ii) A tiny committed fixture: 50
   CNR clusters (MIT-licensed, attribution kept) to give a deterministic regression of the fitted rates. (iii) Determinism across
   worker counts. Do not commit large data; keep a `datasets.lock.json` with URL, SHA-256 and size, plus a `fetch.py` that verifies
   the hashes.
4. **Fitting order.** CNR (D04) → Sokolovskii (D03) → DT4DDS (D02, per vendor, PCR and aging) → ETH benchmark (D01). The output
   would be models `illumina-twist-fit`, `illumina-electrochem-fit`, `ont-guppy-hac-fit` and `ont-guppy-fast-fit`, each PUBLIC-DATA-DERIVED. Then
   rerun `experiments/v6/channel/evaluate.py` on VNX payloads with these models. Report the results as SIMULATED using
   PUBLIC-DATA-DERIVED parameters, with Wilson CIs as now.
5. **Competitor runs on real reads (D01).** Use the dt4dds-benchmark harness to rerun the six codecs on the published reads and
   reproduce their reported outcomes. That validates the pipeline. VNX can only be compared under fitted simulation at matched code
   rate, strand length (~130-150 nt) and coverage. Use no superlatives.
6. **Validator extension.** Add an evidence class `PUBLIC-DATA-DERIVED` to `record.schema.json`. It requires a dataset accession, the
   SHA-256 values of the downloaded files, and a paper DOI or "unpublished". The other classes are unchanged, and a test covers it.

## Plan (b): readiness for a first real wet-lab round (order export and primers)

1. **A strand length that fits vendor limits.** Add an opt-in profile where payload plus 2x20-nt primers is ≤ 300 nt (a margin
   under the 350-nt Twist/IDT limit; costs and error rise with length). Better still, offer a ~150-200 nt profile, because most
   public datasets and the ETH benchmark use 110-157 nt. Then MiSeq/iSeq 2x150 covers the strand with overlap. Strands of 300 nt
   need MiSeq 2x300 or ONT. For ONT at about 300 nt, D23 (PRJNA1505625, 300 nt, FAST/HAC/SUP) could later supply a channel
   fit if the authors provide the references.
2. **Primer module (`vnxdna.v7.primers`, opt-in).** Generate or accept primer pairs with these checks: 20 nt; GC 40-60 %; Tm in a
   narrow window (pairs within about 2-3 °C, e.g. 55-62 °C nearest-neighbour); no homopolymer > 3; a 3' end with at most 2-3 G/C in
   the last 5 nt; no self- or cross-dimer above a ΔG threshold; and a minimum Hamming/edit distance from every payload window and
   every other primer. The last check is what D03 calls avoiding "primer-payload collisions". Organick 2018 designed a
   large primer library for random access; check its exact rules in the paper before coding. These primer rules are standard PCR
   guidance, not measured in the datasets above, so treat the thresholds as tunable defaults and record them in the order record.
   Low base diversity in shared primer regions degrades Illumina basecalling (Gimpel 2023). Spike in PhiX or use staggered/
   phased primers, and record which one in `sequencing`.
3. **Order export (`vnx order-export`).** From an encoded archive, write the vendor order file (CSV/XLSX columns name,sequence),
   the strand FASTA with primers attached, a manifest (VNX version/commit, profile, options, primer set, strand count and length,
   GC/homopolymer summary) and SHA-256 values that go straight into `synthesis.ordered_fasta` of the physical record. Pre-flight
   checks: length ≤ vendor max, allowed alphabet, no duplicate sequences, GC per strand and per window, max homopolymer, and
   primer uniqueness.
4. **Read ingestion for decode.** Primer search on both orientations (as in D03), trimming, PE merge, length filter, and then the
   existing VNX decoder. Test it first on D03/D01 reads (pipeline only), then on simulated reads with primers attached.
5. **Physical record.** It already exists. Add fields for primer set ID, PCR cycles, polymerase, PhiX %, and basecaller model/version
   for ONT. These are exactly the variables that drive the errors measured in D02.

## Caveats

- Sizes are SRA-normalised (runinfo `size_MB`), which can differ from the FASTQ download size.
- Several 2025-2026 BioProjects (D22, D23, D25, D31, D32, D34) have no publication located. Treat them as unverified and do not
  use them without the design files.
- The CNR README notes a generation error (centers not uniformly random), so a model fitted on it may be biased.
- Fitted models describe the source labs' chemistry and instruments. They do not predict VNX's own future wet-lab results.
