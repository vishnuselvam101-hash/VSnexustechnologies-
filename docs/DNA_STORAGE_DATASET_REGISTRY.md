# DNA-storage dataset registry

Status: research output of 2026-10-05, documentation only. Machine-readable copy: `docs/data/dataset_registry.json` (copied and normalised from the research file `40-datasets.json`; no value changed).

**Classification: PUBLIC-DATA-DERIVED registry (metadata only).** VNX-DNA has not synthesised, stored or sequenced any DNA and has no wet-lab results. Every dataset below was produced by another group. Results obtained from these data must be labelled PUBLIC-DATA-DERIVED; channel results generated from fitted models stay SIMULATED.

Counts: 38 profiled datasets (priority 1: 4, 2: 7, 3: 11, 4: 10, 5: 6); 28 further candidate accessions listed but not profiled; 9 targets searched for and not found.

## 1. How entries were verified

- Accessions were found by EBI project search, then each was checked against NCBI SRA runinfo (run count, spots, SRA-normalised size, instrument), the ENA project XML, the Zenodo, figshare and GitHub APIs, and the data-availability statement of the paper (Europe PMC full text) where one exists. No accession was taken from memory.
- Only small files were downloaded, to count strands (ETH design FASTA 1.6 MB, CNR Centers.txt 1.1 MB, one Chandak oligo file).
- "not verified" means the value was not checked against a primary source in this pass; do not use those values in code until someone checks them.
- Source strength: peer-reviewed = journal or IEEE conference paper; preprint = arXiv or bioRxiv; unverified = SRA/ENA project with no publication found; vendor = a manufacturer document.
- ENA and SRA give no explicit licence. The INSDC policy places no restrictions on use, so the registry says "unclear" and the policy.
- Priority: 1 = use first; 5 = little use to VNX. "Ground truth" means the reference strands (and ideally the encoded file) are published.

## 2. What VNX can and cannot do with these datasets

VNX-DNA cannot decode any of them, because they use other encodings. They are suitable for two purposes only: (a) fitting channel models (error rates by position, substitution matrix, deletion run length, coverage distribution, dropout), and (b) running competitor decoders on real reads, which is only possible where the dataset ships the encoded strands and a published protocol (D01). The "Suitability" column below is derived from the `vnx_use` field of the source registry.

## 3. Registry (summary)

| ID | Dataset | Accession(s) | Paper (status) | Licence | Synthesis | Sequencing | Strand length | Coverage | Format | Ground truth published | Suitability for VNX | Prio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D01 | ETH codec benchmark pool experiments (6 literature codecs, best/worst-case synthesis) | ENA PRJEB90546 | Gimpel AL, Remschak A, Stark WJ, Heckel R, Grass RN. Comparison of state-of-the-art error-correction coding for sequence-based DNA data storage. Nat Commun 17:3963 (2026). doi:10.1038/s41467-026-70548-3 (preprint doi:10.1101/2025.07.11.664297) (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); notebooks+design files: GPL-3.0; article CC BY 4.0 | two scenarios: material-deposition synthesis (high fidelity) and electrochemical synthesis (low fidelity) per paper abstract; vendor names not re-checked in Methods (DT4DDS 2023 from same group used Twist = material deposition, GenScript/CustomArray = electrochemical) | ILLUMINA Illumina iSeq 100 | design_files.fasta (bestcase/Cov10): 11,293 designs x 129 nt (verified by download of the 1.6 MB design file); paper standardised ~150 nt incl. flanks | experiments at several sequencing coverages (directories Cov10, Cov1000 etc. in exp_data/bestcase and worstcase) | raw FASTQ (ENA, paired-end); demultiplexed per-codec read text files + design_files.fasta + scafstats in GitHub | yes: YES: design_files.fasta per experiment and codec_data/<codec>/encoded.txt for aeon/fountain/goldman/hedges/rs/yinyang (verified file listing) | channel-model fitting / competitor-decoder runs | 1 |
| D02 | DT4DDS error and bias characterisation (synthesis, PCR, aging, Illumina SBS) | ENA PRJEB65931 | Gimpel AL, Stark WJ, Heckel R, Grass RN. A digital twin for DNA data storage based on comprehensive quantification of errors and biases. Nat Commun 14:6026 (2023). doi:10.1038/s41467-023-41729-1 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); dt4dds code GPL-3.0; notebooks repo licence not stated (unclear); article CC BY 4.0 | Twist Bioscience (material deposition) and GenScript/CustomArray (electrochemical); GC-constrained and unconstrained pools | ILLUMINA Illumina iSeq 100 | 4 pools of 12,000-12,472 sequences, 143-157 nt total (paper); design_files.fasta for Twist GCall = 12,000 x 108 nt data region (downloaded and counted) | per-experiment; coverage distributions reported normalised to mean | raw paired-end FASTQ (ENA); processed design/scafstats files in GitHub | yes: YES: design_files.fasta per condition in dt4dds_notebooks/data and dt4dds/scripts (random sequences, no encoded file) | channel-model fitting | 1 |
| D03 | Nanopore end-to-end channel dataset (GenScript pool, MinION, guppy fast/HAC) | Zenodo 10.5281/zenodo.10943282 | Welter L, Sokolovskii R, et al. An End-to-End Coding Scheme for DNA-Based Data Storage With Nanopore-Sequenced Reads. arXiv:2406.12955 (2024) (preprint (arXiv); journal version not verified) | CC BY 4.0 | GenScript; 91,766 oligos | OXFORD_NANOPORE MinION (pore/flow cell not stated in record); guppy fast and high-accuracy modes; pass (Q>=8) and fail groups kept | 150 nt = 110-nt pseudo-random payload + 20-nt primer at each end; 3 groups (30,589/30,589/30,588), each with its own primer pair | not extracted | oligos.fasta (14.7 MB), primers_synthesis.fasta, clustered_read_segments.tar.gz (138 MB; 12 sub-archives: 3 files x accuracy mode x pass/fail) | yes: YES (oligos.fasta; payload is pseudo-random, no encoded file) | channel-model fitting | 1 |
| D04 | Clustered Nanopore Reads (CNR) dataset | GitHub microsoft/clustered-nanopore-reads-dataset | Srinivasavaradhan SR, Gopi S, Pfister HD, Yekhanin S. Trellis BMA: Coded trace reconstruction on IDS channels for DNA storage. IEEE ISIT 2021, pp. 2453-2458 (arXiv:2107.06440) (peer-reviewed conference) | MIT | Twist Bioscience, PCR amplified | OXFORD_NANOPORE MinION (ligation adapters; basecaller not stated in README) | 110 nt (Centers.txt, verified) | mean ~27 reads/cluster; some empty clusters (Gu et al. removed them) | Centers.txt + Clusters.txt (plain text, clusters separated by '=' lines) | yes: YES (Centers.txt) | channel-model fitting | 1 |
| D05 | Organick et al. 2018 random-access dataset (file id20 subset) | GitHub uwmisl/data-nbt17 (Git LFS) | Organick L, et al. Random access in large-scale DNA data storage. Nat Biotechnol 36:242-248 (2018). doi:10.1038/nbt.4079 (peer-reviewed) | no licence file (unclear) | Twist Bioscience for most pools per paper (not re-verified for id20) | Illumina (instrument for id20 not verified) | 150 nt incl. primers per paper (not re-verified) | not extracted | id20.fastq.gz (1,363,110,383 B) + id20.refs.txt.gz (18,284,141 B) via Git LFS (LFS batch API: download available) | yes: YES (id20.refs.txt.gz reference strands) | channel-model fitting | 2 |
| D06 | DNAformer datasets (Technion; Twist; Illumina MiSeq + ONT incl. raw signals) | Zenodo 10.5281/zenodo.13896773; Zenodo 10.5281/zenodo.17473983; Zenodo 10.5281/zenodo.17399364 | Bar-Lev D, Orr I, Sabary O, Etzion T, Yaakobi E. Scalable and robust DNA-based storage via coding theory and deep learning. Nat Mach Intell 7:639-649 (2025). doi:10.1038/s42256-025-01003-z (peer-reviewed) | CC BY 4.0 (data); code MIT | Twist Bioscience | ILLUMINA MiSeq paired-end (PEAR-merged); OXFORD_NANOPORE (pilot + 2 flow cells; raw FAST5 signals published) | not verified here | not extracted | raw FASTQ(.gz), binned text (header=reference, '*' separator, reads), FAST5 raw signal zips | yes: YES: binned headers give encoded sequence; 17473983 also gives sequences_random_file.txt, sequences_semantic_file.txt, rand_file.bin and semantic_file.zip (original files) | channel-model fitting / competitor-decoder runs | 2 |
| D07 | Technion binned benchmark: Grass 2015, Erlich 2017, Srinivasavaradhan 2021 | Zenodo 10.5281/zenodo.14296588 | Datasets from Grass et al. Angew Chem 54:2552 (2015) doi:10.1002/anie.201411378; Erlich & Zielinski Science 355:950 (2017) doi:10.1126/science.aaj2038; Srinivasavaradhan et al. ISIT 2021; binned by Bar-Lev et al. Nat Mach Intell 2025 (peer-reviewed sources; binning is DERIVED) | CC BY 4.0 (record) | Grass 2015: CustomArray (per Heckel 2019 context; not re-verified); Erlich: Twist; CNR: Twist | Illumina (Grass, Erlich); ONT MinION (CNR) | Erlich 152-nt data region (Heckel et al. 2019 Table 1); CNR 110 nt; Grass not extracted | not extracted | binned text: header line = encoded reference, then reads | yes: YES (reference per cluster). Only public route found to Grass 2015 reads. | channel-model fitting | 2 |
| D08 | DNA Fountain raw reads (master pool + dilution/deep-copy experiments) | ENA PRJEB19305; ENA PRJEB19307 | Erlich Y, Zielinski D. DNA Fountain enables a robust and efficient storage architecture. Science 355:950-954 (2017). doi:10.1126/science.aaj2038 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); DNA Fountain code GPL-3.0 | Twist Bioscience (72,000 oligos) | ILLUMINA Illumina MiSeq | 152-nt data region (Heckel et al. 2019 Table 1); full oligo incl. primers not re-verified | 281-503x read coverage (Heckel 2019 Table 1) | paired-end FASTQ | partial: PARTIAL: reference strands available via Technion binned Erlich.txt (D07); original oligo order file not located in TeamErlich/dna-fountain | channel-model fitting / competitor-decoder runs | 2 |
| D09 | Goldman et al. 2013 (EBI) - 5 files in 153,335 features | ENA PRJEB1186 | Goldman N, et al. Towards practical, high-capacity, low-maintenance information storage in synthesized DNA. Nature (2013). doi:10.1038/nature11875 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); EBI download page terms not stated (unclear) | Agilent OLS | ILLUMINA Illumina HiSeq 2000 | 117 nt data features (Heckel 2019); features.txt includes a 33-bp adapter at each end (EBI page) | ~519x (Heckel 2019 Table 1) | paired-end FASTQ; AYB base calls on EBI page | yes: YES: features.txt (153,335 designed strings) + the 5 encoded files + intermediate DNA strings (EBI page) | channel-model fitting / competitor-decoder runs | 3 |
| D10 | HEDGES in vitro test reads | SRA PRJNA631961 (SAMN14897329-35) | Press WH, Hawkins JA, Jones SK Jr, Schaub JM, Finkelstein IJ. HEDGES error-correcting code for DNA storage corrects indels and allows sequence constraints. PNAS (2020). doi:10.1073/pnas.2004821117 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); HEDGES code MIT (includes Numerical Recipes/Schifra routines, non-commercial terms) | not verified here | ILLUMINA Illumina MiSeq | not verified here | not extracted | paired-end FASTQ (avg 600 bp per spot = 2x300) | not_found: NOT FOUND as a strand list (repo has code + demo text only); would need regeneration with HEDGES encoder - unverified | channel-model fitting / competitor-decoder runs | 3 |
| D11 | Antkowiak et al. 2020 photolithographic synthesis reads | Figshare collection 10.6084/m9.figshare.c.5128901.v1 | Antkowiak PL, et al. Low cost DNA data storage using photolithographic synthesis and advanced information reconstruction and error correction. Nat Commun (2020). doi:10.1038/s41467-020-19148-3 (peer-reviewed) | CC BY 4.0 (figshare); code Apache-2.0 | photolithographic in-house synthesis (low cost, high error) | Illumina (R1 FASTQ files only in figshare; instrument not re-verified) | not verified here | not extracted | FASTQ.gz | unclear: UNCLEAR: encoded sequences may be in MLI-lab repo; not verified | channel-model fitting | 3 |
| D12 | Chandak et al. nanopore convolutional-code dataset (raw FAST5 + FASTQ + oligos) | GitHub shubhamchandak94/nanopore_dna_storage_data (branches master, bonito) | Chandak S, et al. Overcoming high nanopore basecaller error rates for DNA storage via basecaller-decoder integration and convolutional codes. IEEE ICASSP 2020. doi:10.1109/ICASSP40776.2020.9053441 (bioRxiv 10.1101/2019.12.20.871939) (peer-reviewed conference) | data repo: none stated (unclear); code repo MIT | not verified here | OXFORD_NANOPORE MinION (flow cell not verified); guppy and bonito basecalls | merged.fa: 12,301 oligos; first oligo 158 nt incl. 25-nt primers at both ends (downloaded and checked) | not extracted | FAST5 raw signal, FASTQ, oligo FASTA, decoded lists, stats | yes: YES: oligo_files (per experiment + merged) and encoded_file | channel-model fitting / competitor-decoder runs | 2 |
| D13 | Lopez et al. 2019 nanopore assembly readout | GitHub uwmisl/data-ncomms19-nanopore (Git LFS) | Lopez R, et al. DNA assembly for nanopore data storage readout. Nat Commun (2019). doi:10.1038/s41467-019-10978-4 (peer-reviewed) | none stated (unclear) | not verified here | OXFORD_NANOPORE MinION | assembled long constructs (not re-verified) | not extracted | FASTQ.gz (LFS) + seqs_*.txt references | yes: YES: seqs_365-dishes/Vitruvian/apollo/space_shuttle.txt mapped to runs | low (see detail) | 4 |
| D14 | Chen et al. 2020 molecular bias coverage data | GitHub uwmisl/storage-biasing-ncomms20 | Chen YJ, Takahashi CN, Organick L, et al. Quantifying molecular bias in DNA data storage. Nat Commun 11:3264 (2020). doi:10.1038/s41467-020-16958-3 (peer-reviewed) | none stated (unclear) | Twist (biased vs non-biased chip; per data_readme) | Illumina (coverage arrays derived by BWA) | n/a | the dataset IS coverage distributions | .npy coverage arrays + CSV | see_ground_truth_text: n/a | channel-model fitting | 2 |
| D15 | Lee et al. 2019 template-independent enzymatic (TdT) synthesis | SRA PRJNA521561 / SRP185459 | Lee HH, Kalhor R, Goela N, Bolot J, Church GM. Terminator-free template-independent enzymatic DNA synthesis for digital information storage. Nat Commun (2019). doi:10.1038/s41467-019-10258-1 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | enzymatic (TdT, terminator-free; homopolymer-run based) | ILLUMINA Illumina MiSeq; OXFORD_NANOPORE MinION | short enzymatic strands (see paper) | n/a | FASTQ | see_ground_truth_text: processed data + code in GitHub (not verified file-level) | low (see detail) | 4 |
| D16 | Lee et al. 2020 photon-directed multiplexed enzymatic synthesis | SRA PRJNA633646 / SRP262219 | Lee H, Wiegand DJ, et al. Photon-directed multiplexed enzymatic DNA synthesis for molecular digital data storage. Nat Commun (2020). doi:10.1038/s41467-020-18681-5 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | enzymatic, photon-directed | ILLUMINA Illumina MiniSeq; OXFORD_NANOPORE MinION | see paper | n/a | FASTQ | see_ground_truth_text: MATLAB scripts in GitHub | low (see detail) | 5 |
| D17 | DNA-Aeon evaluation data | SRA PRJNA855029 (SRR19954693-97) | Welzel M, et al. DNA-Aeon provides flexible arithmetic coding for constraint adherence and error correction in DNA storage. Nat Commun (2023). doi:10.1038/s41467-023-36297-3 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); article CC BY 4.0 | not verified here | ILLUMINA Illumina MiSeq | not verified here | not extracted | paired-end FASTQ (2x300) | unclear: UNCLEAR (source data files; not verified) | channel-model fitting / competitor-decoder runs | 3 |
| D18 | DNA StairLoop experiments | SRA PRJNA1306341; Figshare 10.6084/m9.figshare.28902032.v2; Figshare 10.6084/m9.figshare.26212682 (encoded oligos) | Yan Z, Qu G, Chen X, Zheng G, Wu H. DNA StairLoop: enabling high-fidelity data recovery and robust error correction in DNA-based data storage. Nat Commun (2025). doi:10.1038/s41467-025-64230-3 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); code CC BY 4.0; figshare licence not checked | not verified here | ILLUMINA Illumina MiSeq | not verified here | not extracted | paired-end FASTQ + figshare raw/encoded files | yes: YES per DAS (encoded oligos + raw digital information on figshare; not opened) | channel-model fitting / competitor-decoder runs | 3 |
| D19 | Magnetic DNA RAM with nanopore readout (Stanford) | SRA PRJNA758230 | Lau B, Chandak S, Roy S, Tatwawadi K, et al. Magnetic DNA random access memory with nanopore readouts and exponentially-scaled combinatorial addressing. Sci Rep (2023). doi:10.1038/s41598-023-29575-z (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | not verified here | ILLUMINA Illumina iSeq 100; OXFORD_NANOPORE MinION; OXFORD_NANOPORE PromethION | see paper | not extracted | FASTQ (Illumina) + nanopore reads | see_ground_truth_text: oligo generation code in bonito branch (regenerate; not verified) | channel-model fitting / competitor-decoder runs | 3 |
| D20 | HEDGES nanopore nominal FAST5/FASTQ evaluation set (NCSU/JHU) | Zenodo 10.5281/zenodo.11985455 | Volkel KD, Hook PW, Keung A, Timp W, Tuck JM. Nanopore decoding with speed and versatility for data storage. Bioinformatics btaf006 (2025). doi:10.1093/bioinformatics/btaf006 (peer-reviewed) | CC BY 4.0 | not verified here | OXFORD_NANOPORE (FAST5 + FASTQ) | not verified here | not extracted | tar.gz with FAST5 + FASTQ | unclear: UNCLEAR (not opened) | low (see detail) | 3 |
| D21 | Takahashi et al. 2024 particle-radiation damage reads (Microsoft/UW) | Zenodo 10.5281/zenodo.12713629 | Takahashi CN, Ward DP, Cazzaniga C, Frost C, Rech P, Ganguly K, et al. Evaluating the risk of data loss due to particle radiation damage in a DNA data storage system. Nat Commun (2024). doi:10.1038/s41467-024-51768-x (peer-reviewed) | CC BY 4.0 | not verified here | Illumina (not verified) | see sequences files | not extracted | radiation_fastqs.zip + 'file 15/31 sequences.txt' | yes: YES (sequence files for encoded files 15 and 31; mapping in paper Source Data) | channel-model fitting | 3 |
| D22 | Joint index-payload codes: large electrochemical + inkjet pools (Tianjin) | SRA PRJNA1439609; Zenodo 10.5281/zenodo.19203651 (encoded sequences) | 'Joint index and payload codes enable secure and indel-resilient DNA data storage' (Chen W, Ge Q, et al., Tianjin Univ.) - publication record not found in Europe PMC on 2026-10-05; treat as unpublished/unknown venue (unverified) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); sequences CC BY 4.0 | electrochemical synthesis (ECS-Pool1 999,998 x 170 nt; ECS-Pool2 1,000,000 x 170 nt) and inkjet printing synthesis (IPS-Pool1-3, 4,000 x 248 nt each) | ILLUMINA Illumina NovaSeq X Plus; OXFORD_NANOPORE MinION; OXFORD_NANOPORE PromethION | 170 nt and 248 nt | not extracted | FASTQ + encoded sequence text files | yes: YES (encoded sequences on Zenodo, 346 MB) | channel-model fitting | 2 |
| D23 | Direct nanopore readout with UEP oligos (Tianjin), 300-nt strands | SRA PRJNA1505625 | not located (BioProject released 2026-07-31) (unverified) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | not stated in project; 179,928 oligos x 300 nt storing 7.55 MB | ILLUMINA Illumina HiSeq 2000; OXFORD_NANOPORE PromethION; ONT basecalling in FAST, HAC and SUP modes (project description) | 300 nt | not extracted | FASTQ | not_found: NOT FOUND (no published design file located) | channel-model fitting | 3 |
| D24 | Composite ranging codes as indices (Tianjin) - Illumina + MinION + PromethION | SRA PRJNA1371011; SRA PRJNA1276869 | Zhang Y, Qin R, Ge Q, Guo Q, Chen W. From spacecraft ranging to massive DNA data storage: Composite ranging codes as indices and error correction references. Sci Adv (2026). doi:10.1126/sciadv.aec1469 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | not verified here | ILLUMINA Illumina NovaSeq 6000; OXFORD_NANOPORE MinION; OXFORD_NANOPORE PromethION | not verified | not extracted | FASTQ | unclear: UNCLEAR (Zenodo verification dataset 15550094 has no public files) | channel-model fitting | 4 |
| D25 | High-density low-coverage coding (SWJTU), 2 x 30,000 oligos | SRA PRJNA1436205 | High information density and low coverage data storage in DNA with efficient channel coding schemes, arXiv:2410.04886 (title from search result; author list not verified) (preprint (arXiv)) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | phosphoramidite (vendor not verified) | ILLUMINA Illumina NovaSeq 6000 | not verified | low coverage is the topic | paired-end FASTQ | not_found: NOT FOUND | channel-model fitting | 4 |
| D26 | DNA-of-Things (Stanford bunny, multi-generation) | ENA PRJEB35217 | Koch J, Gantenbein S, Masania K, Stark WJ, Erlich Y, Grass RN. A DNA-of-things storage architecture to create materials with embedded memory. Nat Biotechnol (2020). doi:10.1038/s41587-019-0356-z (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | Twist (DNA Fountain encoding; not re-verified) | ILLUMINA Illumina MiSeq | not verified | per generation | paired-end FASTQ | see_ground_truth_text: design files in fml-ethz/dt4dds scripts/bunny_generations/design_files.fasta (listed, not opened) | low (see detail) | 3 |
| D27 | Yin-Yang codec experimental data (BGI) | CNGB CNSA CNP0001650 | Ping Z, Chen S, Zhou G, et al. Towards practical and robust DNA-based data archiving using the yin-yang codec system. Nat Comput Sci (2022). doi:10.1038/s43588-022-00231-2 (peer-reviewed) | unclear (CNGB terms) | not verified here | not verified (CNGB record not opened) | not verified | not extracted | raw sequencing (CNSA) | unclear: UNCLEAR | competitor-decoder runs | 4 |
| D28 | Bidirectional Beam Search post-processed benchmark subsets (CNR, DNAformer, Chandak) | Zenodo record 16959565 | Gu Z, et al. Efficient trace reconstruction in DNA storage systems using Bidirectional Beam Search. bioRxiv 10.1101/2025.04.16.644694 (2025) (preprint) | CC BY 4.0 | as sources | ONT (all three sources) | 110 nt (CNR), others per source | subsampled | clusters/centers text | yes: YES (centers/refs) | low (see detail) | 3 |
| D29 | SeqFormer datasets (Illumina + ONT, preprocessed) | Zenodo 10.5281/zenodo.21377476 | SeqFormer (Shenzhen University BioinfoSZU) - associated publication not located (unverified) | CC BY 4.0 | not stated | ILLUMINA; OXFORD_NANOPORE | not stated | unknown | text + HDF5 | unclear: UNCLEAR | low (see detail) | 4 |
| D30 | DNA-DISK enzymatic synthesis on digital microfluidics | SRA PRJNA1143195 | Li K, Lu X, Liao J, et al. DNA-DISK: Automated end-to-end data storage via enzymatic single-nucleotide DNA synthesis and sequencing on digital microfluidics. PNAS 121 (2024). doi:10.1073/pnas.2410164121 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | enzymatic TdT single-nucleotide on DMF | ILLUMINA Illumina NovaSeq 6000 | short | n/a | FASTQ | unclear: UNCLEAR | low (see detail) | 5 |
| D31 | PCRobot robotic PCR amplification for DNA data storage (2026, nanopore) | ENA PRJEB111897 | not located (unverified) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | not stated | OXFORD_NANOPORE MinION | ~280-290 nt mean read length (SRA avgLength) | n/a | FASTQ | not_found: NOT FOUND | low (see detail) | 4 |
| D32 | LANL 'DNA storage error model' reads | SRA PRJNA978627 | not located (unverified) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | not stated | ILLUMINA NextSeq 500 | not stated | n/a | FASTQ | not_found: NOT FOUND | low (see detail) | 4 |
| D33 | MPHAC-DIS massively parallel homogeneous amplification of chip-scale DNA | ENA PRJEB82436 | Weng Z, Li J, Wu Y, et al. Massively parallel homogeneous amplification of chip-scale DNA for DNA information storage (MPHAC-DIS). Nat Commun 16 (2025). doi:10.1038/s41467-025-55986-9 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | chip-scale array synthesis | ILLUMINA Illumina NovaSeq 6000 | not verified | amplification bias data | FASTQ + figshare decoding demo | see_ground_truth_text: decoding demo on figshare (not opened) | low (see detail) | 4 |
| D34 | 'Error profile of the DNA storage channel' (Wellcome Sanger Institute) | ENA PRJEB32885 | not located (no PubMed link in ENA record) (unverified) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | not stated | ILLUMINA Illumina MiSeq | not stated | n/a | paired-end FASTQ (135 runs) | not_found: NOT FOUND | low (see detail) | 4 |
| D35 | Davos Bitcoin DNA challenge (Goldman encoding), solved 2018 | ENA PRJEB41462 | links PubMed 23354052 (Goldman et al. 2013); challenge solution by S. Wuyts (Univ. Antwerp) (peer-reviewed method; dataset unpublished) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | Agilent (not verified) | ILLUMINA Illumina MiSeq | Goldman scheme | n/a | paired-end FASTQ (2x75) | no: NO (challenge files not published as designs) | low (see detail) | 5 |
| D36 | Composite DNA letters (Anavy et al.) | ENA PRJEB32427 | Anavy L, Vaknin I, Atar O, Amit R, Yakhini Z. Data storage in DNA with fewer synthesis cycles using composite DNA letters. Nat Biotechnol (2019). doi:10.1038/s41587-019-0240-x (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions) | composite-letter synthesis | ILLUMINA Illumina HiSeq 2500 | see paper | very deep | paired-end FASTQ | unclear: UNCLEAR | low (see detail) | 5 |
| D37 | Helixworks motif-based storage runs (ONT R10.4.1 FAST5) | Zenodo 10.5281/zenodo.15839178 (+15839390, 15848607, 15849818, 16032156) | described at OSF 10.17605/OSF.IO/PCDTJ (not peer-reviewed as far as checked) (vendor/company data release) | CC BY-SA 4.0 | enzymatic ligation of motifs (Helixworks) | OXFORD_NANOPORE R10.4.1 / FLO-MIN114 (FAST5) | motif assemblies | n/a | FAST5 .7z + encoded.tsv + master_db.txt | yes: YES (motif design TSV) | low (see detail) | 5 |
| D38 | Single-molecule assembly-free readout from medium-length encoded DNA (plasmids, POD5) | SRA PRJNA1235219; Zenodo 10.5281/zenodo.16883332 | Chen W, Qin R, Guo Q, et al. Approaching single-molecule assembly-free readout from medium-length encoded DNA. Nat Commun (2025). doi:10.1038/s41467-025-65004-7 (peer-reviewed) | reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); Zenodo CC BY 4.0 | plasmid-cloned medium-length DNA | OXFORD_NANOPORE MinION (POD5 + FASTQ) | kb-scale plasmid inserts | n/a | FASTQ + POD5 | yes: YES (original files, codewords, encoded sequences on Zenodo) | low (see detail) | 5 |

## 4. Registry (detail per dataset)

Every field of the source record is shown. URLs are the verification sources.

### D01: ETH codec benchmark pool experiments (6 literature codecs, best/worst-case synthesis)

- **Accession(s):** ENA PRJEB90546
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB90546; https://github.com/fml-ethz/dt4dds-benchmark_notebooks; https://github.com/fml-ethz/dt4dds-benchmark; https://doi.org/10.5281/zenodo.17391328
- **Paper:** Gimpel AL, Remschak A, Stark WJ, Heckel R, Grass RN. Comparison of state-of-the-art error-correction coding for sequence-based DNA data storage. Nat Commun 17:3963 (2026). doi:10.1038/s41467-026-70548-3 (preprint doi:10.1101/2025.07.11.664297)
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); notebooks+design files: GPL-3.0; article CC BY 4.0
- **Synthesis technology:** two scenarios: material-deposition synthesis (high fidelity) and electrochemical synthesis (low fidelity) per paper abstract; vendor names not re-checked in Methods (DT4DDS 2023 from same group used Twist = material deposition, GenScript/CustomArray = electrochemical)
- **Sequencing platform:** ILLUMINA Illumina iSeq 100
- **Error characteristics:** paper: synthetic benchmark used 53% sub / 45% del / 2% ins composition 'resembling' DT4DDS data; codecs' error tolerance up to 14% error and 65% sequence loss in silico; experimental storage at 43 EB/g (material deposition) and 13 EB/g (electrochemical)
- **Strand length:** design_files.fasta (bestcase/Cov10): 11,293 designs x 129 nt (verified by download of the 1.6 MB design file); paper standardised ~150 nt incl. flanks
- **Primers included:** design FASTA appears to be data region only; adapter/primer flanks handled in processing notebooks (verify)
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 10 runs, 11,398,491 spots, 1,034 MB (SRA-normalised), layout PAIRED
- **Coverage:** experiments at several sequencing coverages (directories Cov10, Cov1000 etc. in exp_data/bestcase and worstcase)
- **Data format:** raw FASTQ (ENA, paired-end); demultiplexed per-codec read text files + design_files.fasta + scafstats in GitHub
- **Ground truth published:** YES: design_files.fasta per experiment and codec_data/<codec>/encoded.txt for aeon/fountain/goldman/hedges/rs/yinyang (verified file listing)
- **Size:** ENA ~1.0 GB; GitHub notebooks repo ~544 MB
- **Reproducibility:** README: bbmap 39.01, NGmerge 0.3, seqtk 1.4; install scripts in 00_Tools; Python 3.10
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit channel models (Illumina iSeq, two synthesis fidelities, multiple coverages); run competitor decoders (6 codecs with the exact encoded strands and real reads); cannot be decoded by VNX (foreign encoding)
- **Priority:** 1
- **Verified against:** ENA project XML, SRA runinfo, Europe PMC data-availability text, GitHub tree listing, design file downloaded (1.6 MB)

### D02: DT4DDS error and bias characterisation (synthesis, PCR, aging, Illumina SBS)

- **Accession(s):** ENA PRJEB65931
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB65931; https://github.com/fml-ethz/dt4dds_notebooks; https://github.com/fml-ethz/dt4dds; https://doi.org/10.5281/zenodo.8329043
- **Paper:** Gimpel AL, Stark WJ, Heckel R, Grass RN. A digital twin for DNA data storage based on comprehensive quantification of errors and biases. Nat Commun 14:6026 (2023). doi:10.1038/s41467-023-41729-1
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); dt4dds code GPL-3.0; notebooks repo licence not stated (unclear); article CC BY 4.0
- **Synthesis technology:** Twist Bioscience (material deposition) and GenScript/CustomArray (electrochemical); GC-constrained and unconstrained pools
- **Sequencing platform:** ILLUMINA Illumina iSeq 100
- **Error characteristics:** across 40 datasets: 6.7±6.9 deletions, 7.9±2.0 substitutions, <0.3±0.2 insertions per 1000 nt; deletions cluster (mean run 2.6 nt) and rise to >5%/nt toward the 5' end for electrochemical synthesis; material deposition <1 deletion per 2000 nt; PCR substitutions 1.09e-4 /nt/cycle (61% A>G/T>C); aging C>T/G>A 1.64e-4 /nt per half-life; iSeq 100 PhiX substitutions 1.8±0.8e-3 /nt, indels <0.1e-3; coverage lognormal, sigma 0.58 (GC-constrained) vs 1.30 (unconstrained) for the synthesis shown in Fig. 2b; low base diversity in priming regions degrades basecalling
- **Strand length:** 4 pools of 12,000-12,472 sequences, 143-157 nt total (paper); design_files.fasta for Twist GCall = 12,000 x 108 nt data region (downloaded and counted)
- **Primers included:** reads include co-synthesised priming regions; design FASTA is the data region
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 161 runs, 231,154,225 spots, 10,558 MB (SRA-normalised), layout PAIRED
- **Coverage:** per-experiment; coverage distributions reported normalised to mean
- **Data format:** raw paired-end FASTQ (ENA); processed design/scafstats files in GitHub
- **Ground truth published:** YES: design_files.fasta per condition in dt4dds_notebooks/data and dt4dds/scripts (random sequences, no encoded file)
- **Size:** ENA ~10.6 GB (SRA-normalised)
- **Reproducibility:** dt4dds pip package + notebooks; analysis pipeline included in dt4dds
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit channel models stage-by-stage (synthesis vendor, PCR cycles, aging, sequencing) - best single source for VNX model parameters; no decoder test (random sequences, no file)
- **Priority:** 1
- **Verified against:** Europe PMC full text (data availability + numbers quoted), SRA runinfo, GitHub tree listing

### D03: Nanopore end-to-end channel dataset (GenScript pool, MinION, guppy fast/HAC)

- **Accession(s):** Zenodo 10.5281/zenodo.10943282
- **URLs:** https://doi.org/10.5281/zenodo.10943282; https://arxiv.org/abs/2406.12955
- **Paper:** Welter L, Sokolovskii R, et al. An End-to-End Coding Scheme for DNA-Based Data Storage With Nanopore-Sequenced Reads. arXiv:2406.12955 (2024)
- **Paper status:** preprint (arXiv); journal version not verified
- **Licence:** CC BY 4.0
- **Synthesis technology:** GenScript; 91,766 oligos
- **Sequencing platform:** OXFORD_NANOPORE MinION (pore/flow cell not stated in record); guppy fast and high-accuracy modes; pass (Q>=8) and fail groups kept
- **Error characteristics:** used to estimate parameters of an end-to-end channel model (see paper); not extracted here
- **Strand length:** 150 nt = 110-nt pseudo-random payload + 20-nt primer at each end; 3 groups (30,589/30,589/30,588), each with its own primer pair
- **Primers included:** yes; primers_synthesis.fasta published
- **Reads:** clustered read segments (count not extracted)
- **Coverage:** not extracted
- **Data format:** oligos.fasta (14.7 MB), primers_synthesis.fasta, clustered_read_segments.tar.gz (138 MB; 12 sub-archives: 3 files x accuracy mode x pass/fail)
- **Ground truth published:** YES (oligos.fasta; payload is pseudo-random, no encoded file)
- **Size:** ~153 MB
- **Reproducibility:** primer search by BLAST, segments 150±15 nt, assignment by Levenshtein distance (record description)
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit nanopore channel models (fast vs HAC, pass vs fail) with primers present; test VNX primer-trimming/orientation logic on real nanopore segments; no decoder test (pseudo-random payload)
- **Priority:** 1
- **Verified against:** Zenodo API record metadata + file list

### D04: Clustered Nanopore Reads (CNR) dataset

- **Accession(s):** GitHub microsoft/clustered-nanopore-reads-dataset
- **URLs:** https://github.com/microsoft/clustered-nanopore-reads-dataset; https://arxiv.org/abs/2107.06440
- **Paper:** Srinivasavaradhan SR, Gopi S, Pfister HD, Yekhanin S. Trellis BMA: Coded trace reconstruction on IDS channels for DNA storage. IEEE ISIT 2021, pp. 2453-2458 (arXiv:2107.06440)
- **Paper status:** peer-reviewed conference
- **Licence:** MIT
- **Synthesis technology:** Twist Bioscience, PCR amplified
- **Sequencing platform:** OXFORD_NANOPORE MinION (ligation adapters; basecaller not stated in README)
- **Error characteristics:** IDS channel, nanopore; not quantified in README. Caveat (README, Aug 2024): centers have long-range dependencies, not uniformly random as first stated
- **Strand length:** 110 nt (Centers.txt, verified)
- **Primers included:** no (reads trimmed to strand region)
- **Reads:** 269,709 reads in 10,000 clusters (README)
- **Coverage:** mean ~27 reads/cluster; some empty clusters (Gu et al. removed them)
- **Data format:** Centers.txt + Clusters.txt (plain text, clusters separated by '=' lines)
- **Ground truth published:** YES (Centers.txt)
- **Size:** 31 MB
- **Reproducibility:** git clone; no processing needed
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit per-read IDS nanopore model quickly; smoke test of fitting code; benchmark VNX consensus/trace reconstruction against published TrellisBMA/DNAformer/BBS numbers; no file decode
- **Priority:** 1
- **Verified against:** GitHub API (licence, file sizes), README fetched, Centers line length checked

### D05: Organick et al. 2018 random-access dataset (file id20 subset)

- **Accession(s):** GitHub uwmisl/data-nbt17 (Git LFS)
- **URLs:** https://github.com/uwmisl/data-nbt17; https://www.microsoft.com/en-us/research/publication/random-access-in-large-scale-dna-data-storage/
- **Paper:** Organick L, et al. Random access in large-scale DNA data storage. Nat Biotechnol 36:242-248 (2018). doi:10.1038/nbt.4079
- **Paper status:** peer-reviewed
- **Licence:** no licence file (unclear)
- **Synthesis technology:** Twist Bioscience for most pools per paper (not re-verified for id20)
- **Sequencing platform:** Illumina (instrument for id20 not verified)
- **Error characteristics:** not extracted; widely used Illumina benchmark (Rashtchian 2017 clustering)
- **Strand length:** 150 nt incl. primers per paper (not re-verified)
- **Primers included:** unknown until download
- **Reads:** not counted
- **Coverage:** not extracted
- **Data format:** id20.fastq.gz (1,363,110,383 B) + id20.refs.txt.gz (18,284,141 B) via Git LFS (LFS batch API: download available)
- **Ground truth published:** YES (id20.refs.txt.gz reference strands)
- **Size:** ~1.38 GB
- **Reproducibility:** git lfs pull; no README in repo
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit Illumina channel (Twist + NextSeq-era chemistry); clustering/consensus benchmark at large pool scale; no file decode
- **Priority:** 2
- **Verified against:** GitHub API contents, LFS pointer sizes, LFS batch availability check

### D06: DNAformer datasets (Technion; Twist; Illumina MiSeq + ONT incl. raw signals)

- **Accession(s):** Zenodo 10.5281/zenodo.13896773; Zenodo 10.5281/zenodo.17473983; Zenodo 10.5281/zenodo.17399364
- **URLs:** https://doi.org/10.5281/zenodo.13896773; https://doi.org/10.5281/zenodo.17473983; https://doi.org/10.5281/zenodo.17399364; https://github.com/itaiorr/Deep-DNA-based-storage
- **Paper:** Bar-Lev D, Orr I, Sabary O, Etzion T, Yaakobi E. Scalable and robust DNA-based storage via coding theory and deep learning. Nat Mach Intell 7:639-649 (2025). doi:10.1038/s42256-025-01003-z
- **Paper status:** peer-reviewed
- **Licence:** CC BY 4.0 (data); code MIT
- **Synthesis technology:** Twist Bioscience
- **Sequencing platform:** ILLUMINA MiSeq paired-end (PEAR-merged); OXFORD_NANOPORE (pilot + 2 flow cells; raw FAST5 signals published)
- **Error characteristics:** not extracted here (see paper)
- **Strand length:** not verified here
- **Primers included:** raw reads yes; binned files trimmed (reads_preprocessor.py)
- **Reads:** 5 datasets (pilot/test x Illumina/nanopore)
- **Coverage:** not extracted
- **Data format:** raw FASTQ(.gz), binned text (header=reference, '*' separator, reads), FAST5 raw signal zips
- **Ground truth published:** YES: binned headers give encoded sequence; 17473983 also gives sequences_random_file.txt, sequences_semantic_file.txt, rand_file.bin and semantic_file.zip (original files)
- **Size:** 13896773: ~38.5 GB total (binned files 74-468 MB each); 17473983: ~1.2 GB; 17399364: ~12.4 GB signals
- **Reproducibility:** scripts in itaiorr/Deep-DNA-based-storage
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit Illumina and nanopore models on the same Twist pool; raw-signal work (future soft decoding); competitor decoder (DNAformer) re-run
- **Priority:** 2
- **Verified against:** Zenodo API metadata + file lists for all three records

### D07: Technion binned benchmark: Grass 2015, Erlich 2017, Srinivasavaradhan 2021

- **Accession(s):** Zenodo 10.5281/zenodo.14296588
- **URLs:** https://doi.org/10.5281/zenodo.14296588
- **Paper:** Datasets from Grass et al. Angew Chem 54:2552 (2015) doi:10.1002/anie.201411378; Erlich & Zielinski Science 355:950 (2017) doi:10.1126/science.aaj2038; Srinivasavaradhan et al. ISIT 2021; binned by Bar-Lev et al. Nat Mach Intell 2025
- **Paper status:** peer-reviewed sources; binning is DERIVED
- **Licence:** CC BY 4.0 (record)
- **Synthesis technology:** Grass 2015: CustomArray (per Heckel 2019 context; not re-verified); Erlich: Twist; CNR: Twist
- **Sequencing platform:** Illumina (Grass, Erlich); ONT MinION (CNR)
- **Error characteristics:** see sources
- **Strand length:** Erlich 152-nt data region (Heckel et al. 2019 Table 1); CNR 110 nt; Grass not extracted
- **Primers included:** no (primers detected and truncated by reads_preprocessor.py)
- **Reads:** not counted
- **Coverage:** not extracted
- **Data format:** binned text: header line = encoded reference, then reads
- **Ground truth published:** YES (reference per cluster). Only public route found to Grass 2015 reads.
- **Size:** Grass.txt 310 MB, Erlich.txt 1.98 GB, Srinivasavaradhan.txt 20 MB
- **Reproducibility:** scripts in itaiorr/Deep-DNA-based-storage
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit models across three chemistries quickly (pre-clustered); trace-reconstruction benchmark
- **Priority:** 2
- **Verified against:** Zenodo API metadata + file list

### D08: DNA Fountain raw reads (master pool + dilution/deep-copy experiments)

- **Accession(s):** ENA PRJEB19305; ENA PRJEB19307
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB19305; https://www.ebi.ac.uk/ena/browser/view/PRJEB19307; https://github.com/TeamErlich/dna-fountain
- **Paper:** Erlich Y, Zielinski D. DNA Fountain enables a robust and efficient storage architecture. Science 355:950-954 (2017). doi:10.1126/science.aaj2038
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); DNA Fountain code GPL-3.0
- **Synthesis technology:** Twist Bioscience (72,000 oligos)
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** Heckel et al. 2019 (Sci Rep 2019, doi:10.1038/s41598-019-45832-6) re-analysed: substitution-dominated in correct-length reads; dilution series down to low physical redundancy
- **Strand length:** 152-nt data region (Heckel et al. 2019 Table 1); full oligo incl. primers not re-verified
- **Primers included:** yes in raw reads
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 16 runs, 479,469,649 spots, 68,720 MB (SRA-normalised), layout PAIRED
- **Coverage:** 281-503x read coverage (Heckel 2019 Table 1)
- **Data format:** paired-end FASTQ
- **Ground truth published:** PARTIAL: reference strands available via Technion binned Erlich.txt (D07); original oligo order file not located in TeamErlich/dna-fountain
- **Size:** ~68.7 GB SRA-normalised (PRJEB19307 is 66.6 GB; PRJEB19305 2.1 GB is enough to start)
- **Reproducibility:** DNA Fountain decoder in GitHub (Python 2 era)
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit Illumina MiSeq + Twist model; dilution series for dropout vs physical redundancy; run DNA Fountain decoder as competitor
- **Priority:** 2
- **Verified against:** ENA project XML, SRA runinfo, DT4DDS data-availability statement citing both accessions

### D09: Goldman et al. 2013 (EBI) - 5 files in 153,335 features

- **Accession(s):** ENA PRJEB1186
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB1186; https://www.ebi.ac.uk/goldman-srv/DNA-storage/
- **Paper:** Goldman N, et al. Towards practical, high-capacity, low-maintenance information storage in synthesized DNA. Nature (2013). doi:10.1038/nature11875
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); EBI download page terms not stated (unclear)
- **Synthesis technology:** Agilent OLS
- **Sequencing platform:** ILLUMINA Illumina HiSeq 2000
- **Error characteristics:** re-analysed by Heckel et al. 2019; HiSeq 2000 reads of exactly 104 nt
- **Strand length:** 117 nt data features (Heckel 2019); features.txt includes a 33-bp adapter at each end (EBI page)
- **Primers included:** yes (adapters)
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 2 runs, 145,428,991 spots, 18,278 MB (SRA-normalised), layout PAIRED
- **Coverage:** ~519x (Heckel 2019 Table 1)
- **Data format:** paired-end FASTQ; AYB base calls on EBI page
- **Ground truth published:** YES: features.txt (153,335 designed strings) + the 5 encoded files + intermediate DNA strings (EBI page)
- **Size:** ~18.3 GB SRA-normalised
- **Reproducibility:** EBI page README.html
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** historic Agilent/HiSeq channel fit; Goldman codec re-run as competitor (also in D01)
- **Priority:** 3
- **Verified against:** ENA XML, SRA runinfo, EBI page fetched

### D10: HEDGES in vitro test reads

- **Accession(s):** SRA PRJNA631961 (SAMN14897329-35)
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA631961; https://github.com/whpress/HEDGES
- **Paper:** Press WH, Hawkins JA, Jones SK Jr, Schaub JM, Finkelstein IJ. HEDGES error-correcting code for DNA storage corrects indels and allows sequence constraints. PNAS (2020). doi:10.1073/pnas.2004821117
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); HEDGES code MIT (includes Numerical Recipes/Schifra routines, non-commercial terms)
- **Synthesis technology:** not verified here
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** see paper
- **Strand length:** not verified here
- **Primers included:** yes (primers IF538/IF539 given in DAS)
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 7 runs, 4,917,226 spots, 1,413 MB (SRA-normalised), layout PAIRED
- **Coverage:** not extracted
- **Data format:** paired-end FASTQ (avg 600 bp per spot = 2x300)
- **Ground truth published:** NOT FOUND as a strand list (repo has code + demo text only); would need regeneration with HEDGES encoder - unverified
- **Size:** ~1.4 GB
- **Reproducibility:** C++ build scripts in repo
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit MiSeq 2x300 model only if references can be regenerated; HEDGES decoder competitor run (preferably done via D01)
- **Priority:** 3
- **Verified against:** Europe PMC DAS, SRA runinfo, GitHub tree

### D11: Antkowiak et al. 2020 photolithographic synthesis reads

- **Accession(s):** Figshare collection 10.6084/m9.figshare.c.5128901.v1
- **URLs:** https://doi.org/10.6084/m9.figshare.c.5128901.v1; https://github.com/MLI-lab/noisy_dna_data_storage
- **Paper:** Antkowiak PL, et al. Low cost DNA data storage using photolithographic synthesis and advanced information reconstruction and error correction. Nat Commun (2020). doi:10.1038/s41467-020-19148-3
- **Paper status:** peer-reviewed
- **Licence:** CC BY 4.0 (figshare); code Apache-2.0
- **Synthesis technology:** photolithographic in-house synthesis (low cost, high error)
- **Sequencing platform:** Illumina (R1 FASTQ files only in figshare; instrument not re-verified)
- **Error characteristics:** high error rates (photolithography); not extracted
- **Strand length:** not verified here
- **Primers included:** likely in raw reads (verify)
- **Reads:** 2 files: I16 (100 KB file) 994 MB, I18 (323 KB file) 3,150 MB fastq.gz; file 3 only on request
- **Coverage:** not extracted
- **Data format:** FASTQ.gz
- **Ground truth published:** UNCLEAR: encoded sequences may be in MLI-lab repo; not verified
- **Size:** ~4.1 GB
- **Reproducibility:** MLI-lab/noisy_dna_data_storage
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** stress-test VNX model fitting on a high-error synthesis channel
- **Priority:** 3
- **Verified against:** figshare API collection + article file lists; Europe PMC DAS

### D12: Chandak et al. nanopore convolutional-code dataset (raw FAST5 + FASTQ + oligos)

- **Accession(s):** GitHub shubhamchandak94/nanopore_dna_storage_data (branches master, bonito)
- **URLs:** https://github.com/shubhamchandak94/nanopore_dna_storage_data; https://github.com/shubhamchandak94/nanopore_dna_storage; https://doi.org/10.1101/2019.12.20.871939
- **Paper:** Chandak S, et al. Overcoming high nanopore basecaller error rates for DNA storage via basecaller-decoder integration and convolutional codes. IEEE ICASSP 2020. doi:10.1109/ICASSP40776.2020.9053441 (bioRxiv 10.1101/2019.12.20.871939)
- **Paper status:** peer-reviewed conference
- **Licence:** data repo: none stated (unclear); code repo MIT
- **Synthesis technology:** not verified here
- **Sequencing platform:** OXFORD_NANOPORE MinION (flow cell not verified); guppy and bonito basecalls
- **Error characteristics:** stats/merged.sorted.error_stats.csv gives sub/ins/del per read position (samtools stats)
- **Strand length:** merged.fa: 12,301 oligos; first oligo 158 nt incl. 25-nt primers at both ends (downloaded and checked)
- **Primers included:** yes
- **Reads:** not counted
- **Coverage:** not extracted
- **Data format:** FAST5 raw signal, FASTQ, oligo FASTA, decoded lists, stats
- **Ground truth published:** YES: oligo_files (per experiment + merged) and encoded_file
- **Size:** GitHub repo ~1.9 GB plus externally hosted parts
- **Reproducibility:** per-directory download instructions
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit nanopore model incl. positional error profile; raw-signal soft-decoding research; convolutional-code competitor
- **Priority:** 2
- **Verified against:** GitHub API README/listing, oligo file sampled

### D13: Lopez et al. 2019 nanopore assembly readout

- **Accession(s):** GitHub uwmisl/data-ncomms19-nanopore (Git LFS)
- **URLs:** https://github.com/uwmisl/data-ncomms19-nanopore
- **Paper:** Lopez R, et al. DNA assembly for nanopore data storage readout. Nat Commun (2019). doi:10.1038/s41467-019-10978-4
- **Paper status:** peer-reviewed
- **Licence:** none stated (unclear)
- **Synthesis technology:** not verified here
- **Sequencing platform:** OXFORD_NANOPORE MinION
- **Error characteristics:** not extracted
- **Strand length:** assembled long constructs (not re-verified)
- **Primers included:** unknown
- **Reads:** 5 runs (13,15,16,18,20); run13 fastq.gz 575,880,507 B
- **Coverage:** not extracted
- **Data format:** FASTQ.gz (LFS) + seqs_*.txt references
- **Ground truth published:** YES: seqs_365-dishes/Vitruvian/apollo/space_shuttle.txt mapped to runs
- **Size:** several GB
- **Reproducibility:** read_me file maps files to runs
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** long-read nanopore channel (assembly) - low relevance to VNX short-strand design
- **Priority:** 4
- **Verified against:** GitHub API listing, LFS batch availability

### D14: Chen et al. 2020 molecular bias coverage data

- **Accession(s):** GitHub uwmisl/storage-biasing-ncomms20
- **URLs:** https://github.com/uwmisl/storage-biasing-ncomms20
- **Paper:** Chen YJ, Takahashi CN, Organick L, et al. Quantifying molecular bias in DNA data storage. Nat Commun 11:3264 (2020). doi:10.1038/s41467-020-16958-3
- **Paper status:** peer-reviewed
- **Licence:** none stated (unclear)
- **Synthesis technology:** Twist (biased vs non-biased chip; per data_readme)
- **Sequencing platform:** Illumina (coverage arrays derived by BWA)
- **Error characteristics:** coverage only: PCR from 200 down to 8 copies (triplicates), homopolymer files
- **Strand length:** n/a
- **Primers included:** n/a
- **Reads:** no reads; per-strand coverage .npy arrays
- **Coverage:** the dataset IS coverage distributions
- **Data format:** .npy coverage arrays + CSV
- **Ground truth published:** n/a
- **Size:** ~36 MB
- **Reproducibility:** notebooks per figure
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit VNX coverage model (negative binomial vs lognormal) and low-copy dropout; DT4DDS used run36/run42 for the same purpose
- **Priority:** 2
- **Verified against:** GitHub API README + data_readme.txt

### D15: Lee et al. 2019 template-independent enzymatic (TdT) synthesis

- **Accession(s):** SRA PRJNA521561 / SRP185459
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA521561; https://github.com/citizenlee/DNAinfostorage
- **Paper:** Lee HH, Kalhor R, Goela N, Bolot J, Church GM. Terminator-free template-independent enzymatic DNA synthesis for digital information storage. Nat Commun (2019). doi:10.1038/s41467-019-10258-1
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** enzymatic (TdT, terminator-free; homopolymer-run based)
- **Sequencing platform:** ILLUMINA Illumina MiSeq; OXFORD_NANOPORE MinION
- **Error characteristics:** enzymatic synthesis produces variable run lengths; see paper
- **Strand length:** short enzymatic strands (see paper)
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 7 runs, 13,411,764 spots, 1,243 MB (SRA-normalised), layout SINGLE
- **Coverage:** n/a
- **Data format:** FASTQ
- **Ground truth published:** processed data + code in GitHub (not verified file-level)
- **Size:** ~1.2 GB
- **Reproducibility:** GitHub code
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** enzymatic-synthesis channel exploration only (different encoding paradigm)
- **Priority:** 4
- **Verified against:** Europe PMC DAS, SRA runinfo

### D16: Lee et al. 2020 photon-directed multiplexed enzymatic synthesis

- **Accession(s):** SRA PRJNA633646 / SRP262219
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA633646; https://github.com/dwiegand740/Photon_Enzymatic_Synthesis
- **Paper:** Lee H, Wiegand DJ, et al. Photon-directed multiplexed enzymatic DNA synthesis for molecular digital data storage. Nat Commun (2020). doi:10.1038/s41467-020-18681-5
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** enzymatic, photon-directed
- **Sequencing platform:** ILLUMINA Illumina MiniSeq; OXFORD_NANOPORE MinION
- **Error characteristics:** see paper
- **Strand length:** see paper
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 2 runs, 277,770 spots, 32 MB (SRA-normalised), layout SINGLE
- **Coverage:** n/a
- **Data format:** FASTQ
- **Ground truth published:** MATLAB scripts in GitHub
- **Size:** ~32 MB
- **Reproducibility:** MATLAB scripts
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** enzymatic channel exploration only
- **Priority:** 5
- **Verified against:** Europe PMC DAS, SRA runinfo

### D17: DNA-Aeon evaluation data

- **Accession(s):** SRA PRJNA855029 (SRR19954693-97)
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA855029; https://github.com/MW55/DNA-Aeon
- **Paper:** Welzel M, et al. DNA-Aeon provides flexible arithmetic coding for constraint adherence and error correction in DNA storage. Nat Commun (2023). doi:10.1038/s41467-023-36297-3
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); article CC BY 4.0
- **Synthesis technology:** not verified here
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** see paper/source data
- **Strand length:** not verified here
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 5 runs, 9,618,468 spots, 3,062 MB (SRA-normalised), layout PAIRED
- **Coverage:** not extracted
- **Data format:** paired-end FASTQ (2x300)
- **Ground truth published:** UNCLEAR (source data files; not verified)
- **Size:** ~3.1 GB
- **Reproducibility:** DNA-Aeon v1.0 code; MESA config in source data
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** Illumina channel fit if references obtained; DNA-Aeon competitor (preferably via D01)
- **Priority:** 3
- **Verified against:** Europe PMC DAS, SRA runinfo

### D18: DNA StairLoop experiments

- **Accession(s):** SRA PRJNA1306341; Figshare 10.6084/m9.figshare.28902032.v2; Figshare 10.6084/m9.figshare.26212682 (encoded oligos)
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1306341; https://doi.org/10.6084/m9.figshare.26212682; https://github.com/Guanjinqu/StairLoop
- **Paper:** Yan Z, Qu G, Chen X, Zheng G, Wu H. DNA StairLoop: enabling high-fidelity data recovery and robust error correction in DNA-based data storage. Nat Commun (2025). doi:10.1038/s41467-025-64230-3
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); code CC BY 4.0; figshare licence not checked
- **Synthesis technology:** not verified here
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** see paper
- **Strand length:** not verified here
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 3 runs, 62,359,131 spots, 6,102 MB (SRA-normalised), layout PAIRED
- **Coverage:** not extracted
- **Data format:** paired-end FASTQ + figshare raw/encoded files
- **Ground truth published:** YES per DAS (encoded oligos + raw digital information on figshare; not opened)
- **Size:** ~6.1 GB SRA
- **Reproducibility:** GitHub + Zenodo 10.5281/zenodo.16837109
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** Illumina channel fit with published oligos; StairLoop competitor decoder
- **Priority:** 3
- **Verified against:** Europe PMC DAS, SRA runinfo

### D19: Magnetic DNA RAM with nanopore readout (Stanford)

- **Accession(s):** SRA PRJNA758230
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA758230; https://github.com/shubhamchandak94/nanopore_dna_storage/tree/bonito
- **Paper:** Lau B, Chandak S, Roy S, Tatwawadi K, et al. Magnetic DNA random access memory with nanopore readouts and exponentially-scaled combinatorial addressing. Sci Rep (2023). doi:10.1038/s41598-023-29575-z
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** not verified here
- **Sequencing platform:** ILLUMINA Illumina iSeq 100; OXFORD_NANOPORE MinION; OXFORD_NANOPORE PromethION
- **Error characteristics:** see paper
- **Strand length:** see paper
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 8 runs, 33,545,876 spots, 9,243 MB (SRA-normalised), layout PAIRED/SINGLE
- **Coverage:** not extracted
- **Data format:** FASTQ (Illumina) + nanopore reads
- **Ground truth published:** oligo generation code in bonito branch (regenerate; not verified)
- **Size:** ~9.2 GB
- **Reproducibility:** bonito branch
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** nanopore (MinION + PromethION) channel fit; conv-code competitor
- **Priority:** 3
- **Verified against:** Europe PMC DAS, SRA runinfo

### D20: HEDGES nanopore nominal FAST5/FASTQ evaluation set (NCSU/JHU)

- **Accession(s):** Zenodo 10.5281/zenodo.11985455
- **URLs:** https://doi.org/10.5281/zenodo.11985455
- **Paper:** Volkel KD, Hook PW, Keung A, Timp W, Tuck JM. Nanopore decoding with speed and versatility for data storage. Bioinformatics btaf006 (2025). doi:10.1093/bioinformatics/btaf006
- **Paper status:** peer-reviewed
- **Licence:** CC BY 4.0
- **Synthesis technology:** not verified here
- **Sequencing platform:** OXFORD_NANOPORE (FAST5 + FASTQ)
- **Error characteristics:** see paper
- **Strand length:** not verified here
- **Primers included:** see paper
- **Reads:** not counted
- **Coverage:** not extracted
- **Data format:** tar.gz with FAST5 + FASTQ
- **Ground truth published:** UNCLEAR (not opened)
- **Size:** ~7.3 GB
- **Reproducibility:** code at 10.5281/zenodo.11454877
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** nanopore channel with HEDGES-encoded strands; soft-decoder comparison
- **Priority:** 3
- **Verified against:** Zenodo API metadata

### D21: Takahashi et al. 2024 particle-radiation damage reads (Microsoft/UW)

- **Accession(s):** Zenodo 10.5281/zenodo.12713629
- **URLs:** https://doi.org/10.5281/zenodo.12713629
- **Paper:** Takahashi CN, Ward DP, Cazzaniga C, Frost C, Rech P, Ganguly K, et al. Evaluating the risk of data loss due to particle radiation damage in a DNA data storage system. Nat Commun (2024). doi:10.1038/s41467-024-51768-x
- **Paper status:** peer-reviewed
- **Licence:** CC BY 4.0
- **Synthesis technology:** not verified here
- **Sequencing platform:** Illumina (not verified)
- **Error characteristics:** irradiated vs control samples (damage-induced loss)
- **Strand length:** see sequences files
- **Primers included:** see paper
- **Reads:** not counted
- **Coverage:** not extracted
- **Data format:** radiation_fastqs.zip + 'file 15/31 sequences.txt'
- **Ground truth published:** YES (sequence files for encoded files 15 and 31; mapping in paper Source Data)
- **Size:** ~6.9 GB
- **Reproducibility:** Source Data mapping
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit a damage/aging stage (dropout + substitution increase) for VNX storage-loss models
- **Priority:** 3
- **Verified against:** Zenodo API metadata, Europe PMC title search

### D22: Joint index-payload codes: large electrochemical + inkjet pools (Tianjin)

- **Accession(s):** SRA PRJNA1439609; Zenodo 10.5281/zenodo.19203651 (encoded sequences)
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1439609; https://doi.org/10.5281/zenodo.19203651
- **Paper:** 'Joint index and payload codes enable secure and indel-resilient DNA data storage' (Chen W, Ge Q, et al., Tianjin Univ.) - publication record not found in Europe PMC on 2026-10-05; treat as unpublished/unknown venue
- **Paper status:** unverified
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); sequences CC BY 4.0
- **Synthesis technology:** electrochemical synthesis (ECS-Pool1 999,998 x 170 nt; ECS-Pool2 1,000,000 x 170 nt) and inkjet printing synthesis (IPS-Pool1-3, 4,000 x 248 nt each)
- **Sequencing platform:** ILLUMINA Illumina NovaSeq X Plus; OXFORD_NANOPORE MinION; OXFORD_NANOPORE PromethION
- **Error characteristics:** not extracted
- **Strand length:** 170 nt and 248 nt
- **Primers included:** unknown
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 3 runs, 12,140,100 spots, 1,919 MB (SRA-normalised), layout PAIRED/SINGLE
- **Coverage:** not extracted
- **Data format:** FASTQ + encoded sequence text files
- **Ground truth published:** YES (encoded sequences on Zenodo, 346 MB)
- **Size:** ~1.9 GB SRA + 346 MB
- **Reproducibility:** not checked
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit ~1M-strand electrochemical channel on Illumina + ONT (scale test for VNX clustering)
- **Priority:** 2
- **Verified against:** ENA XML, SRA runinfo, Zenodo API

### D23: Direct nanopore readout with UEP oligos (Tianjin), 300-nt strands

- **Accession(s):** SRA PRJNA1505625
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1505625
- **Paper:** not located (BioProject released 2026-07-31)
- **Paper status:** unverified
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** not stated in project; 179,928 oligos x 300 nt storing 7.55 MB
- **Sequencing platform:** ILLUMINA Illumina HiSeq 2000; OXFORD_NANOPORE PromethION; ONT basecalling in FAST, HAC and SUP modes (project description)
- **Error characteristics:** not extracted
- **Strand length:** 300 nt
- **Primers included:** unknown
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 8 runs, 4,798,080 spots, 594 MB (SRA-normalised), layout PAIRED/SINGLE
- **Coverage:** not extracted
- **Data format:** FASTQ
- **Ground truth published:** NOT FOUND (no published design file located)
- **Size:** ~0.6 GB
- **Reproducibility:** unknown
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** fit fast/HAC/SUP nanopore models at VNX-like strand length (~300 nt) once references are obtained (contact authors)
- **Priority:** 3
- **Verified against:** ENA XML, SRA runinfo

### D24: Composite ranging codes as indices (Tianjin) - Illumina + MinION + PromethION

- **Accession(s):** SRA PRJNA1371011; SRA PRJNA1276869
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1371011; https://doi.org/10.5281/zenodo.17911697; https://github.com/dna-storage-lab/DNAStorage_LCRC
- **Paper:** Zhang Y, Qin R, Ge Q, Guo Q, Chen W. From spacecraft ranging to massive DNA data storage: Composite ranging codes as indices and error correction references. Sci Adv (2026). doi:10.1126/sciadv.aec1469
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** not verified here
- **Sequencing platform:** ILLUMINA Illumina NovaSeq 6000; OXFORD_NANOPORE MinION; OXFORD_NANOPORE PromethION
- **Error characteristics:** see paper
- **Strand length:** not verified
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 16 runs, 4,351,182 spots, 833 MB (SRA-normalised), layout PAIRED/SINGLE
- **Coverage:** not extracted
- **Data format:** FASTQ
- **Ground truth published:** UNCLEAR (Zenodo verification dataset 15550094 has no public files)
- **Size:** ~0.8 GB
- **Reproducibility:** Zenodo code
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** cross-platform channel fit if references available
- **Priority:** 4
- **Verified against:** Europe PMC DAS, SRA runinfo

### D25: High-density low-coverage coding (SWJTU), 2 x 30,000 oligos

- **Accession(s):** SRA PRJNA1436205
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1436205; https://arxiv.org/abs/2410.04886
- **Paper:** High information density and low coverage data storage in DNA with efficient channel coding schemes, arXiv:2410.04886 (title from search result; author list not verified)
- **Paper status:** preprint (arXiv)
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** phosphoramidite (vendor not verified)
- **Sequencing platform:** ILLUMINA Illumina NovaSeq 6000
- **Error characteristics:** paper tailors codes to dominant errors of phosphoramidite synthesis + Illumina (not extracted)
- **Strand length:** not verified
- **Primers included:** unknown
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 1 runs, 24,964,645 spots, 3,729 MB (SRA-normalised), layout PAIRED
- **Coverage:** low coverage is the topic
- **Data format:** paired-end FASTQ
- **Ground truth published:** NOT FOUND
- **Size:** ~3.7 GB
- **Reproducibility:** unknown
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** low-coverage channel fit if references obtained
- **Priority:** 4
- **Verified against:** ENA XML, SRA runinfo

### D26: DNA-of-Things (Stanford bunny, multi-generation)

- **Accession(s):** ENA PRJEB35217
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB35217
- **Paper:** Koch J, Gantenbein S, Masania K, Stark WJ, Erlich Y, Grass RN. A DNA-of-things storage architecture to create materials with embedded memory. Nat Biotechnol (2020). doi:10.1038/s41587-019-0356-z
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** Twist (DNA Fountain encoding; not re-verified)
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** used by DT4DDS for coverage-efficiency and external validation
- **Strand length:** not verified
- **Primers included:** yes in raw reads
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 8 runs, 15,714,301 spots, 1,458 MB (SRA-normalised), layout PAIRED
- **Coverage:** per generation
- **Data format:** paired-end FASTQ
- **Ground truth published:** design files in fml-ethz/dt4dds scripts/bunny_generations/design_files.fasta (listed, not opened)
- **Size:** ~1.5 GB
- **Reproducibility:** dt4dds notebooks data/DoT
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** coverage drift over repeated PCR generations
- **Priority:** 3
- **Verified against:** ENA XML, SRA runinfo, DT4DDS DAS

### D27: Yin-Yang codec experimental data (BGI)

- **Accession(s):** CNGB CNSA CNP0001650
- **URLs:** https://db.cngb.org/search/project/CNP0001650/; https://github.com/ntpz870817/DNA-storage-YYC
- **Paper:** Ping Z, Chen S, Zhou G, et al. Towards practical and robust DNA-based data archiving using the yin-yang codec system. Nat Comput Sci (2022). doi:10.1038/s43588-022-00231-2
- **Paper status:** peer-reviewed
- **Licence:** unclear (CNGB terms)
- **Synthesis technology:** not verified here
- **Sequencing platform:** not verified (CNGB record not opened)
- **Error characteristics:** see paper
- **Strand length:** not verified
- **Primers included:** unknown
- **Reads:** not counted
- **Coverage:** not extracted
- **Data format:** raw sequencing (CNSA)
- **Ground truth published:** UNCLEAR
- **Size:** unknown
- **Reproducibility:** YYC code
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** YYC competitor (preferably via D01, which includes YYC with ground truth)
- **Priority:** 4
- **Verified against:** accession taken verbatim from Europe PMC data-availability text; CNGB page itself not fetched

### D28: Bidirectional Beam Search post-processed benchmark subsets (CNR, DNAformer, Chandak)

- **Accession(s):** Zenodo record 16959565
- **URLs:** https://zenodo.org/records/16959565; https://github.com/GZHoffie/bbs-test; https://doi.org/10.1101/2025.04.16.644694
- **Paper:** Gu Z, et al. Efficient trace reconstruction in DNA storage systems using Bidirectional Beam Search. bioRxiv 10.1101/2025.04.16.644694 (2025)
- **Paper status:** preprint
- **Licence:** CC BY 4.0
- **Synthesis technology:** as sources
- **Sequencing platform:** ONT (all three sources)
- **Error characteristics:** script to measure ins/del/sub rates and coverage included
- **Strand length:** 110 nt (CNR), others per source
- **Primers included:** no
- **Reads:** subsampled clusters
- **Coverage:** subsampled
- **Data format:** clusters/centers text
- **Ground truth published:** YES (centers/refs)
- **Size:** ~81 MB
- **Reproducibility:** bbs-test scripts
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** small, ready-made trace-reconstruction benchmark for VNX consensus
- **Priority:** 3
- **Verified against:** Zenodo API metadata

### D29: SeqFormer datasets (Illumina + ONT, preprocessed)

- **Accession(s):** Zenodo 10.5281/zenodo.21377476
- **URLs:** https://doi.org/10.5281/zenodo.21377476
- **Paper:** SeqFormer (Shenzhen University BioinfoSZU) - associated publication not located
- **Paper status:** unverified
- **Licence:** CC BY 4.0
- **Synthesis technology:** not stated
- **Sequencing platform:** ILLUMINA; OXFORD_NANOPORE
- **Error characteristics:** not stated
- **Strand length:** not stated
- **Primers included:** unknown
- **Reads:** not counted
- **Coverage:** unknown
- **Data format:** text + HDF5
- **Ground truth published:** UNCLEAR
- **Size:** illumina_data.zip 11 MB, nanopore_data.zip 59 MB (+3 GB benchmark pickles)
- **Reproducibility:** notebooks
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** small consensus benchmark; provenance must be checked before use
- **Priority:** 4
- **Verified against:** Zenodo API metadata

### D30: DNA-DISK enzymatic synthesis on digital microfluidics

- **Accession(s):** SRA PRJNA1143195
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1143195
- **Paper:** Li K, Lu X, Liao J, et al. DNA-DISK: Automated end-to-end data storage via enzymatic single-nucleotide DNA synthesis and sequencing on digital microfluidics. PNAS 121 (2024). doi:10.1073/pnas.2410164121
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** enzymatic TdT single-nucleotide on DMF
- **Sequencing platform:** ILLUMINA Illumina NovaSeq 6000
- **Error characteristics:** see paper
- **Strand length:** short
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 24 runs, 11,806,966 spots, 447 MB (SRA-normalised), layout SINGLE
- **Coverage:** n/a
- **Data format:** FASTQ
- **Ground truth published:** UNCLEAR
- **Size:** ~0.45 GB
- **Reproducibility:** SI
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** enzymatic channel exploration
- **Priority:** 5
- **Verified against:** Europe PMC DAS, SRA runinfo

### D31: PCRobot robotic PCR amplification for DNA data storage (2026, nanopore)

- **Accession(s):** ENA PRJEB111897
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB111897
- **Paper:** not located
- **Paper status:** unverified
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** not stated
- **Sequencing platform:** OXFORD_NANOPORE MinION
- **Error characteristics:** PCR fidelity focus
- **Strand length:** ~280-290 nt mean read length (SRA avgLength)
- **Primers included:** likely
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 6 runs, 3,307,971 spots, 802 MB (SRA-normalised), layout SINGLE
- **Coverage:** n/a
- **Data format:** FASTQ
- **Ground truth published:** NOT FOUND
- **Size:** ~0.8 GB
- **Reproducibility:** unknown
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** PCR-stage error modelling if references obtained
- **Priority:** 4
- **Verified against:** ENA XML, SRA runinfo

### D32: LANL 'DNA storage error model' reads

- **Accession(s):** SRA PRJNA978627
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA978627
- **Paper:** not located
- **Paper status:** unverified
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** not stated
- **Sequencing platform:** ILLUMINA NextSeq 500
- **Error characteristics:** error-model development (project description only)
- **Strand length:** not stated
- **Primers included:** unknown
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 4 runs, 21,936,290 spots, 2,901 MB (SRA-normalised), layout PAIRED
- **Coverage:** n/a
- **Data format:** FASTQ
- **Ground truth published:** NOT FOUND
- **Size:** ~2.9 GB
- **Reproducibility:** unknown
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** only after references are obtained from authors
- **Priority:** 4
- **Verified against:** ENA XML, SRA runinfo

### D33: MPHAC-DIS massively parallel homogeneous amplification of chip-scale DNA

- **Accession(s):** ENA PRJEB82436
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB82436; https://figshare.com/projects/MPHAC-DIS_Seq/217696; https://github.com/NABMElab/MPHAC-DIS
- **Paper:** Weng Z, Li J, Wu Y, et al. Massively parallel homogeneous amplification of chip-scale DNA for DNA information storage (MPHAC-DIS). Nat Commun 16 (2025). doi:10.1038/s41467-025-55986-9
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** chip-scale array synthesis
- **Sequencing platform:** ILLUMINA Illumina NovaSeq 6000
- **Error characteristics:** amplification uniformity focus
- **Strand length:** not verified
- **Primers included:** see paper
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 2 runs, 10,656,626 spots, 483 MB (SRA-normalised), layout SINGLE
- **Coverage:** amplification bias data
- **Data format:** FASTQ + figshare decoding demo
- **Ground truth published:** decoding demo on figshare (not opened)
- **Size:** ~0.5 GB
- **Reproducibility:** GitHub + figshare
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** coverage/amplification-bias model
- **Priority:** 4
- **Verified against:** Europe PMC DAS, SRA runinfo

### D34: 'Error profile of the DNA storage channel' (Wellcome Sanger Institute)

- **Accession(s):** ENA PRJEB32885
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB32885
- **Paper:** not located (no PubMed link in ENA record)
- **Paper status:** unverified
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** not stated
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** purpose: characterise synthesis/amplification/sequencing noise (project description)
- **Strand length:** not stated
- **Primers included:** unknown
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 135 runs, 59,788,247 spots, 10,384 MB (SRA-normalised), layout PAIRED
- **Coverage:** n/a
- **Data format:** paired-end FASTQ (135 runs)
- **Ground truth published:** NOT FOUND
- **Size:** ~10.4 GB
- **Reproducibility:** unknown
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** potentially valuable error-profile data; needs designs from submitters
- **Priority:** 4
- **Verified against:** ENA XML, SRA runinfo

### D35: Davos Bitcoin DNA challenge (Goldman encoding), solved 2018

- **Accession(s):** ENA PRJEB41462
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB41462
- **Paper:** links PubMed 23354052 (Goldman et al. 2013); challenge solution by S. Wuyts (Univ. Antwerp)
- **Paper status:** peer-reviewed method; dataset unpublished
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** Agilent (not verified)
- **Sequencing platform:** ILLUMINA Illumina MiSeq
- **Error characteristics:** not reported
- **Strand length:** Goldman scheme
- **Primers included:** yes
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 1 runs, 23,047,704 spots, 1,384 MB (SRA-normalised), layout PAIRED
- **Coverage:** n/a
- **Data format:** paired-end FASTQ (2x75)
- **Ground truth published:** NO (challenge files not published as designs)
- **Size:** ~1.4 GB
- **Reproducibility:** n/a
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** low value
- **Priority:** 5
- **Verified against:** ENA XML, SRA runinfo

### D36: Composite DNA letters (Anavy et al.)

- **Accession(s):** ENA PRJEB32427
- **URLs:** https://www.ebi.ac.uk/ena/browser/view/PRJEB32427
- **Paper:** Anavy L, Vaknin I, Atar O, Amit R, Yakhini Z. Data storage in DNA with fewer synthesis cycles using composite DNA letters. Nat Biotechnol (2019). doi:10.1038/s41587-019-0240-x
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions)
- **Synthesis technology:** composite-letter synthesis
- **Sequencing platform:** ILLUMINA Illumina HiSeq 2500
- **Error characteristics:** composite letters - different channel
- **Strand length:** see paper
- **Primers included:** yes
- **Reads:** NCBI SRA runinfo (queried 2026-10-05): 7 runs, 441,281,211 spots, 32,795 MB (SRA-normalised), layout PAIRED
- **Coverage:** very deep
- **Data format:** paired-end FASTQ
- **Ground truth published:** UNCLEAR
- **Size:** ~32.8 GB
- **Reproducibility:** n/a
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** not applicable to VNX (composite alphabet)
- **Priority:** 5
- **Verified against:** ENA XML, SRA runinfo

### D37: Helixworks motif-based storage runs (ONT R10.4.1 FAST5)

- **Accession(s):** Zenodo 10.5281/zenodo.15839178 (+15839390, 15848607, 15849818, 16032156)
- **URLs:** https://doi.org/10.5281/zenodo.15839178
- **Paper:** described at OSF 10.17605/OSF.IO/PCDTJ (not peer-reviewed as far as checked)
- **Paper status:** vendor/company data release
- **Licence:** CC BY-SA 4.0
- **Synthesis technology:** enzymatic ligation of motifs (Helixworks)
- **Sequencing platform:** OXFORD_NANOPORE R10.4.1 / FLO-MIN114 (FAST5)
- **Error characteristics:** motif-level
- **Strand length:** motif assemblies
- **Primers included:** yes
- **Reads:** large
- **Coverage:** n/a
- **Data format:** FAST5 .7z + encoded.tsv + master_db.txt
- **Ground truth published:** YES (motif design TSV)
- **Size:** ~28-35 GB per record
- **Reproducibility:** OSF description
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** R10.4.1 signal data only; motif channel not applicable to VNX
- **Priority:** 5
- **Verified against:** Zenodo API metadata

### D38: Single-molecule assembly-free readout from medium-length encoded DNA (plasmids, POD5)

- **Accession(s):** SRA PRJNA1235219; Zenodo 10.5281/zenodo.16883332
- **URLs:** https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1235219; https://doi.org/10.5281/zenodo.16883332
- **Paper:** Chen W, Qin R, Guo Q, et al. Approaching single-molecule assembly-free readout from medium-length encoded DNA. Nat Commun (2025). doi:10.1038/s41467-025-65004-7
- **Paper status:** peer-reviewed
- **Licence:** reads: INSDC open access; no explicit licence stated (unclear; INSDC policy places no use restrictions); Zenodo CC BY 4.0
- **Synthesis technology:** plasmid-cloned medium-length DNA
- **Sequencing platform:** OXFORD_NANOPORE MinION (POD5 + FASTQ)
- **Error characteristics:** see paper
- **Strand length:** kb-scale plasmid inserts
- **Primers included:** n/a
- **Reads:** 7,571 reads (SRA)
- **Coverage:** n/a
- **Data format:** FASTQ + POD5
- **Ground truth published:** YES (original files, codewords, encoded sequences on Zenodo)
- **Size:** ~150 MB
- **Reproducibility:** GitHub + Zenodo code
- **Suitability for VNX (channel-model fitting vs competitor-decoder runs):** not aligned with VNX short-oligo design
- **Priority:** 5
- **Verified against:** Europe PMC DAS, Zenodo API, SRA runinfo

## 5. Searched for but not registered

- Church, Gao, Kosuri 2012 (Science 337:1628) raw reads: no SRA/ENA project found by EBI project search; not registered
- Grass et al. 2015 raw reads: no repository accession found; only the Technion binned derivative (D07)
- Heckel/Mikutis/Grass 2019 'High/Low PR' CustomArray datasets: no accession stated in the Sci Rep full text
- Sabary SOLQC standalone dataset: none found; Technion data are D06/D07; Zenodo 7370899 'Deep DNA - First Pilot' (CC BY 4.0, 760 MB FASTQ) has no description of platform or references
- Wukong / Storage-D (Tianjin) experimental reads: no dataset accession verified
- Catalog / Biomemory public sequencing data: none found
- Kaggle 'DNA data storage' datasets: none found
- NOREC4DNA / MESA: software tools; related reads are DNA-Aeon PRJNA855029 (D17)
- Zenodo 7995806 (cited by SynDe): E. coli genomic nanopore data, not DNA storage; excluded

Further candidate accessions listed but not profiled:

- PRJNA1514025 (distributed implicit indexing, Pool-64K/120K, NovaSeq X + MinION, ~765 MB)
- PRJNA1336187 (secure scalable indel-resilient; HiSeq + MinION; Zenodo 17226943 has no public files)
- PRJNA1345374/PRJNA1258704 (composite letters, Nat Commun 2026 doi:10.1038/s41467-026-68861-y)
- PRJNA1432432 (ZAT-DNA, Nat Commun 2026 doi:10.1038/s41467-026-72869-9)
- PRJNA1022044 (DNA tape CRISPR base editors, doi:10.1038/s41467-023-42223-4)
- PRJNA1225866 (electro-switchable addressing, doi:10.1093/nar/gkaf733)
- PRJNA1447402 (modulation-based encryption, doi:10.1016/j.isci.2026.115933)
- PRJNA1514022 (DNA-Sam, NovaSeq X Plus, ~27 GB)
- PRJNA1522504 (DNA printing, NovaSeq X + iSeq)
- PRJNA1091804 (3D cultural heritage, 11.8 GB)
- PRJNA1465046 (electrically programmable writing, 7.1 GB)
- PRJNA1353600 (chemically modified DNA, SOLiD, 69.5 GB)
- PRJNA292092 (Cambridge dynamic multilayer, 2016)
- PRJNA555140 (DNA micro-disk WORM)
- PRJNA612868 (DNA punch cards)
- PRJNA625964 (in-cell storage)
- PRJNA739508 (NTU, HiSeq 2000)
- PRJEB34960 (UNICEF CRC, EBI)
- PRJEB43002 (photolithographic oligo fidelity)
- PRJEB71104/PRJEB63282 (shortmer combinatorial)
- PRJEB62556/PRJEB104881 (long DNA assembly, ONT)
- PRJEB91149 (polymer fibres)
- Zenodo 22694320 (DNA-GUARD, preprint 2026)
- Zenodo 19158795 (SynDe reproducibility archive, 9.6 GB)
- Zenodo 15523009 (DynaByte raw data, 37.9 GB)
- Zenodo 17112794 (SUSTag nanopore tags)
- GitHub uwmisl/aging_data (processed aging sequencing analysis)
- GitHub uwmisl/2019-spotted-dna-data (primers listed in README)

## 6. Recommended first three

1. **D01: ENA PRJEB90546 + https://github.com/fml-ethz/dt4dds-benchmark_notebooks.** Only dataset found with real reads, ground truth, and competitor decoders run on the same reads under a published protocol (bbmap 39.01, NGmerge 0.3, seqtk 1.4 pinned). Any VNX comparison must use matched code rate (0.5/1.0/1.5 bit/nt) and be labelled SIMULATED, because VNX strands are not in that pool. Paper: Gimpel et al., Nat Commun 17:3963 (2026), doi:10.1038/s41467-026-70548-3 (peer-reviewed).
2. **D03: Zenodo 10.5281/zenodo.10943282 (CC BY 4.0, ~153 MB).** 91,766 GenScript oligos of 150 nt (110-nt payload + 20-nt primers), MinION, guppy fast vs high-accuracy, pass vs fail, primer FASTA published. Small, has primers in the reads, so it tests the primer-trimming and orientation steps that VNX lacks. Source is an arXiv preprint (not peer-reviewed as checked). Start with D04 (CNR, MIT, 31 MB, 110 nt, Twist + MinION) as a day-0 smoke test of the fitting code; note its README caveat (2024) that the centers are not uniformly random. Paper: Welter, Sokolovskii et al., arXiv:2406.12955 (2024).
3. **D02: ENA PRJEB65931 + https://github.com/fml-ethz/dt4dds_notebooks.** 161 iSeq 100 runs (~10.6 GB) covering Twist and GenScript/CustomArray, GC-constrained and unconstrained pools, PCR cycles and accelerated ageing; design FASTAs in GitHub. Source for fitting per-stage parameters of the VNX channel; published numbers can serve as priors before any download. Paper: Gimpel et al., Nat Commun 14:6026 (2023), doi:10.1038/s41467-023-41729-1 (peer-reviewed).

Next: D05 Organick id20 (1.4 GB, refs included), D12 Chandak (raw FAST5 + oligos), D22 Tianjin ~1M-strand electrochemical pools (Illumina + ONT, encoded sequences on Zenodo), D06 DNAformer (Twist, Illumina + ONT + raw signal, original files published).

## 7. Published parameters usable as priors now

Priors only; each row keeps its source. Do not treat them as VNX measurements.

| Quantity | Value | Source | Strength |
|---|---|---|---|
| Overall rates over 40 datasets (Illumina, Twist + GenScript) | del 6.7+-6.9, sub 7.9+-2.0, ins <0.3+-0.2 per 1000 nt | Gimpel 2023 Nat Commun (D02) https://doi.org/10.1038/s41467-023-41729-1 | peer-reviewed, measured |
| Deletion clustering | runs of consecutive deletions, mean length 2.6 nt; substitutions approximately independent | Gimpel 2023 | peer-reviewed |
| Electrochemical synthesis | deletion rate rises toward 5' end, >5 %/nt at the end | Gimpel 2023 | peer-reviewed |
| Material-deposition synthesis | < 1 deletion per 2000 nt, no strong positional dependence | Gimpel 2023 | peer-reviewed |
| PCR (KAPA SYBR FAST, Taq-based) | 1.09e-4 sub /nt /cycle; 61 % A>G/T>C | Gimpel 2023 | peer-reviewed |
| Aging (accelerated) | 1.64e-4 sub /nt per half-life; 77 % C>T/G>A | Gimpel 2023 | peer-reviewed |
| Illumina iSeq 100 (PhiX) | sub 1.8+-0.8e-3 /nt; ins and del < 0.1e-3 /nt; minimum near cycle 20, rising after 25 | Gimpel 2023 | peer-reviewed |
| Coverage distribution | lognormal; sigma 0.58 (GC-constrained) vs 1.30 (unconstrained) for the synthesis shown in Fig. 2b | Gimpel 2023 | peer-reviewed |
| Illumina reading errors | sub ~4e-4 to 1.5e-3 /nt; indels ~1e-6 | Heckel et al. 2019 Sci Rep (citing a prior study); URL not recorded in the audit | peer-reviewed, secondary |
| Homopolymers | sub and del rise for runs > 6 | Heckel 2019 (citing ref. 24) | peer-reviewed, secondary |
| Twist Oligo Pools | up to 350 nt; error 'up to 1:3,000 nt'; >90 % of oligos within <2x of mean; >0.2 fmol/oligo | Twist product sheet DOC-001054 REV12 (2026); URL not recorded in the audit, not verified here | vendor spec |
| IDT oPools | 40-350 nt; 'error rates below one per 2 kb'; scales 1/10/50 pmol/oligo | IDT launch material (Lab Manager article); URL not recorded, not verified here | vendor spec, secondary |
| Codec benchmark error composition | 53 % sub / 45 % del / 2 % ins (used as a realistic mix) | Gimpel 2026 Nat Commun (D01) https://doi.org/10.1038/s41467-026-70548-3 | peer-reviewed |

## 8. What the VNX repository has today (gaps)

Committed state of branch `build/v6-p1` at 081697b (worktree /root/vnx-dna-lab/v6).

- docs/V6_PHYSICAL_VALIDATION_INTERFACE.md and experiments/v6/physical/: record schema (synthesis, sample, storage, sequencing, decode, result, attestations), validate.py (VALID/INVALID/INCOMPLETE), and a SYNTHETIC SOFTWARE TEST example (66 strands of 313 nt, profile v4-balanced, illumina-like model). Three evidence classes exist: REAL PHYSICAL RESULT, SIMULATED RESULT, SYNTHETIC SOFTWARE TEST. There is no PUBLIC-DATA-DERIVED class.
- experiments/v6/channel/models/*.json: 14 models, all labelled SIMULATED, 'not fitted to any measured platform'. Parameters: i.i.d. sub/ins/del, dropout, coverage fixed/Poisson/negative-binomial with dispersion, duplication, homopolymer multipliers (min run 3), GC bias (strength/optimum), per-read deletion bursts, N rate, reverse-complement rate, quality scores. Errors are i.i.d. per base apart from the homopolymer, burst and GC terms.
- No ingestion path for real reads: no primer trimming, no paired-end merge, no read-to-reference assignment, no fitting code, no primer design and no order-export tool.
- VNX strands carry no primers. The example strand of 313 nt plus two 20-nt primers comes to 353 nt, over the 350-nt maximum of both Twist and IDT pools (vendor specs above; URLs not recorded, not verified here).
- VNX cannot decode any of these datasets: they use other encodings. They serve to fit channel models and to run competitor decoders on real reads (research file 00-vnx-baseline-and-readiness.md B.12).
- Backlog R-03 (public dataset ingestion and channel fitting, P4) is open; unaddressed read clustering at scale and BAM input are absent.

## 9. Plan (a): fit VNX channel models to public data

Implementable and testable. Paths marked proposed do not exist yet.

1. Model schema v2, opt-in. Add optional fields to the channel JSON; the 14 v1 models stay valid: classification ('SIMULATED' or 'PUBLIC-DATA-DERIVED-FIT'), provenance (accessions, run IDs, SHA-256 of each downloaded file, fitter commit), position_profile (per-position sub/ins/del multipliers, length L or binned), sub_matrix (4x4), deletion_run_length (geometric mean, for the 2.6-nt clustering), coverage_model 'lognormal' with sigma, and a synthesis vs sequencing split. Synthesis errors are shared by all reads from one molecule; sequencing errors are independent per read, which matters for consensus decoding. Simulated output from a fitted model is still SIMULATED; only the parameter file is PUBLIC-DATA-DERIVED.
2. Fitter experiments/v7/fit/fit_channel.py (proposed path, not yet in the repository). Inputs: FASTQ(s), a reference FASTA, an optional primer FASTA. Steps: trim primers (cutadapt, error rate as a parameter); merge pairs (NGmerge or bbmerge, as in D01); assign each read to a reference (exact index or minimap2/edlib for nanopore; D03/D04 already supply clusters); global alignment with edlib or the native VNX aligner; count events by position, base context and homopolymer run; estimate coverage per reference and the dropout fraction; compare Poisson, NB and lognormal by AIC; calibrate quality as empirical error against reported Q. Output: model JSON with a provenance sidecar.
3. Tests before any real data: (i) round trip: simulate from a known model, fit, check every parameter is recovered within binomial CIs (existing 5-sigma tolerance pattern in tests/v6/channel); (ii) a tiny committed fixture of 50 CNR clusters (MIT-licensed, attribution kept) for a deterministic regression of fitted rates; (iii) determinism across worker counts. Do not commit large data; keep a datasets.lock.json with URL, SHA-256 and size plus a fetch.py that verifies hashes.
4. Fitting order: CNR (D04) -> Sokolovskii (D03) -> DT4DDS (D02, per vendor, PCR and ageing) -> ETH benchmark (D01). Proposed output models: illumina-twist-fit, illumina-electrochem-fit, ont-guppy-hac-fit, ont-guppy-fast-fit, each PUBLIC-DATA-DERIVED. Then rerun experiments/v6/channel/evaluate.py on VNX payloads with these models and report the results as SIMULATED using PUBLIC-DATA-DERIVED parameters, with Wilson CIs as now.
5. Competitor runs on real reads (D01): use the dt4dds-benchmark harness to rerun the six codecs on the published reads and reproduce their reported outcomes, which validates the pipeline. VNX can only be compared under fitted simulation at matched code rate, strand length (~130-150 nt) and coverage. No superlatives.
6. Validator extension: add an evidence class PUBLIC-DATA-DERIVED to record.schema.json. It requires a dataset accession, the SHA-256 values of the downloaded files, and a paper DOI or 'unpublished'. Other classes are unchanged; a test covers it.

## 10. Plan (b): readiness for a first real wet-lab round

No wet-lab round has been run. This is a plan for the order-export and primer work that would precede one.

1. A strand length that fits vendor limits. Finding: the 313-nt strand plus two 20-nt primers is 353 nt, over the 350-nt maximum of Twist and IDT pools. Add an opt-in profile where payload plus 2x20-nt primers is <=300 nt (a margin under the limit; cost and error rise with length). Alternatively, offer a ~150-200 nt profile, because most public datasets and the ETH benchmark use 110-157 nt; MiSeq/iSeq 2x150 then covers the strand with overlap. Strands of 300 nt need MiSeq 2x300 or ONT. For ONT at about 300 nt, D23 (PRJNA1505625, 300 nt, FAST/HAC/SUP) could later supply a channel fit if the authors provide the references.
2. Primer module (vnxdna.v7.primers, proposed, opt-in). Generate or accept primer pairs with these checks: 20 nt; GC 40-60 %; Tm in a narrow window (pairs within about 2-3 C, e.g. 55-62 C nearest-neighbour); no homopolymer > 3; a 3' end with at most 2-3 G/C in the last 5 nt; no self- or cross-dimer above a delta-G threshold; a minimum Hamming/edit distance from every payload window and every other primer (D03 calls this avoiding primer-payload collisions). Organick 2018 designed a large primer library for random access; check its exact rules in the paper before coding. These rules are standard PCR guidance, not measured in the datasets above; treat thresholds as tunable defaults and record them in the order record. Low base diversity in shared primer regions degrades Illumina basecalling (Gimpel 2023): spike in PhiX or use staggered/phased primers, and record which in `sequencing`.
3. Order export (`vnx order-export`, proposed). From an encoded archive, write the vendor order file (CSV/XLSX columns name,sequence), the strand FASTA with primers attached, a manifest (VNX version/commit, profile, options, primer set, strand count and length, GC/homopolymer summary) and SHA-256 values that go into synthesis.ordered_fasta of the physical record. Pre-flight checks: length <= vendor max, allowed alphabet, no duplicate sequences, GC per strand and per window, max homopolymer, primer uniqueness.
4. Read ingestion for decode: primer search on both orientations (as in D03), trimming, PE merge, length filter, then the existing VNX decoder. Test first on D03/D01 reads (pipeline only), then on simulated reads with primers attached.
5. Physical record: it already exists. Add fields for primer set ID, PCR cycles, polymerase, PhiX %, and basecaller model/version for ONT. These are the variables that drive the errors measured in D02.

## 11. Caveats

- Sizes are SRA-normalised (runinfo size_MB) and can differ from the FASTQ download size.
- Several 2025-2026 BioProjects (D22, D23, D25, D31, D32, D34) have no publication located. Treat them as unverified and do not use them without the design files.
- The CNR README notes a generation error (centers not uniformly random), so a model fitted on it may be biased.
- Fitted models describe the source labs' chemistry and instruments. They do not predict VNX's own future wet-lab results.

## 12. Relation to other documents

- `docs/COMPETITOR_MATRIX.md` and `docs/data/competitor_matrix.csv` use this registry for the "fitted channel models / real datasets" rows.
- `docs/V6_PHYSICAL_VALIDATION_INTERFACE.md` describes the existing record schema that the plan extends.
- No code, format or CLI behaviour changed with this document.
