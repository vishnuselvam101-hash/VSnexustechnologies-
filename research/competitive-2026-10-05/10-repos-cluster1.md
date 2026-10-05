# VNX-DNA repo audit, cluster 1 (US academic / Microsoft / dna-storage org / HEDGES / misc)

Access date for every URL: 2026-10-05. Companion data: `/root/vnx-dna-lab/research-2026-10-05/raw/10-repos-cluster1.json` (76 repo records + 1 negative-findings note; fields not verified are literally "unknown"). Clones (shallow) live in `/root/vnx-dna-lab/research-2026-10-05/repos/<owner>__<repo>`; nothing was copied into VNX.

Vault check: /root/VNX-Vault/Intel, wiki, Decisions contain nothing about TrellisBMA, HEDGES, dnastorage or framed (only a generic "methods to evaluate" note and one Zenodo item), so this audit starts from zero.

Evidence labels used: [WET] = physical wet-lab result reported by the paper's authors (I did not reproduce it); [SIM] = simulation; [MINE] = something I built/ran myself on 2026-10-05; [README] = repo's own claim, unverified.

Counts: about 240 distinct repos surfaced by GitHub searches (many irrelevant student simulators/lists), plus the org listings (dna-storage 15, uwmisl ~55, microsoft 22 keyword matches). 76 repos recorded; 11 audited in depth (code/README read, some built or run), 11 medium (README + metadata), 54 light (metadata + description). GitHub search API was rate-limited once and re-run later; search is not exhaustive.

## 1. Microsoft

Org search (`dna|trellis|homopolymer|nanopore|oligo|molecular`) over github.com/microsoft returned only three DNA-storage repos: TrellisBMA, DNABoundedHomopolymerEncoding, clustered-nanopore-reads-dataset (plus microsoft/DNATagging, Mathematica, not read, probably not storage). No Microsoft public DNA-storage file/archive codec exists. I found no public statement on the project's status; only repo activity is evidence.

### 1a. microsoft/TrellisBMA (MIT, archived, 11 stars, 14 commits, last 2024-05-11, 77cb3d3965)
- Paper: Srinivasavaradhan, Gopi, Pfister, Yekhanin, "Trellis BMA: Coded Trace Reconstruction on IDS Channels for DNA Storage", IEEE ISIT 2021 (peer-reviewed), https://arxiv.org/abs/2107.06440 . The task's guess of "Sabary" is wrong for this paper; Sabary et al. is the separate ML-SCS/reconstruction work that Trellis BMA cites.
- Algorithm [paper]: model the channel as insertion/deletion/substitution (IDS) with an HMM; build a multi-trace IDS trellis on top of the code trellis; exact BCJR is exponential in number of traces K, so Trellis BMA runs one single-trace trellis per trace and exchanges soft information between them (consensus-style update, "no-lookahead" and "lookahead" passes in trellis_bma.py), giving complexity linear in K. Outputs soft symbol posteriors for an outer decoder. Baselines implemented: BMALA (bma.py), "multiply posteriors" (Lenz et al. ITW 2020), full multi-trace BCJR (feasible only for K<=3).
- Error model: i.i.d. IDS with p_ins~0.017, p_del~0.020, p_sub~0.022 estimated from training clusters 1-2000 of the dataset; no position dependence, no dropout, no synthesis/sequencing bias.
- Constrained encoding: none. Inner codes are marker-repeat (MR) codes at N=110 with r=6 and r=10 repeats (rates 104/110 and 100/110) and rate-1/2 quaternary convolutional codes (memory 3-5, punctured), the latter inferior to MR above rate 3/4 (paper App. D). No GC/homopolymer constraints, no primers, no addressing, no outer code implemented (paper assumes one).
- Data structures (code): `conv_code` builds FSM/trellis (arrays of states/edges), `coded_ids_multiD` builds the IDS trellis (edge arrays e_from/e_to/e_type/time_type), numba `@njit` kernels for forward/backward/Viterbi passes, notebook-driven. No unit tests, no CI, no requirements pin seen.
- Performance: paper reports error-rate and achievable-information-rate curves only as figures; I did not extract numbers and did not run the notebooks. Stated results: Trellis BMA has lower error rate than BMALA on real data, but BMALA-HD gives better rate for >6 traces; soft outputs not well calibrated (authors' own limitation).
- Datasets: repo ships DataToProcess (137 MB). Real data is the Clustered Nanopore Reads dataset (below). Physical level 3 (sequencing data from a Twist-synthesised pool read on ONT MinION). Caveat: the 10,000 strands are random, not MR-encoded, so how the "coded" real-data curves were produced is not clear from the text I read.
- Limitations/maintenance: archived, single-purpose research code, Python+numba (not production), notebooks. Licence MIT -> safe to run in a benchmark harness.

### 1b. microsoft/clustered-nanopore-reads-dataset (MIT, 24 stars, 18 commits, last 2024-11-18, 6938f44796)
Centers.txt (10,000 x 110 nt) and Clusters.txt (269,709 ONT MinION reads in clusters; 30.5 MB, plain text in git). Twist synthesis, PCR, ONT LQK-LSK109, clustered with Rashtchian et al. NeurIPS 2017. Error rates ~0.017/0.020/0.022 (ins/del/sub) [paper]. IMPORTANT: authors' own note (8/12/2024) says the centers are not uniformly random (generation bug) and some clusters may be malformed. Level 3 [WET, by MISL]. Relevance 5 as a trace-reconstruction benchmark; not an archive/file benchmark.

### 1c. microsoft/DNABoundedHomopolymerEncoding (MIT, 2 stars, 8 commits, created 2025-09-24, last 2025-09-25, fbb8ae203f, not archived)
- What it is: enumerative rank/unrank codec mapping n-bit strings to length-N quaternary strings with max homopolymer run k in {1..5}. Finite-state machine counts length-t paths per state (GMP big integers), encode = N-th lexicographic path, decode = rank of path. k=1 is base-2 to base-3 conversion. Author: Sivakanth Gopi (writeup PDF in repo; no peer-reviewed paper found).
- Rates (README, k=1..5, N=96): 1.583, 1.917, 1.979, 1.990, 1.990 bits/base; slides give asymptotic limits 1.5850, 1.9227, 1.9823, 1.9957, 1.9989. [MINE] I built it (g++ -O3 -lgmpxx -lgmp) and `bhe_rates 110` gave 1.5818/1.9182/1.9818/1.9909/1.9909.
- Speed: README ~50 Mbps encode / ~80 Mbps decode single core. [MINE] k=3, N=150, 297 bits, 2000 random trials, one thread: 14 ms encode, 8 ms decode total, i.e. ~42 / ~74 Mbit/s, consistent with README. Round-trip had no errors.
- Limitations: homopolymer constraint only (no GC, no motifs, no primers), no error correction (a substitution or indel garbles a whole big-integer decode, so it must sit inside an ECC/ inner-code framework with short strands), k<=5, GMP dependency, no tests beyond random round-trip. Physical level 1 (no wet-lab; pure coding primitive).
- VNX: directly comparable with VNX's own constrained mapper (rate vs the information-theoretic limit). Good benchmark of "rate gap to capacity at N=100-200".

### 1d. UW MISL (github.com/uwmisl) data repos
~55 repos, mostly microfluidics (PurpleDrop) and molecular computing. Storage-relevant: data-nbt17 (Organick et al., Nature Biotechnology 2018 [WET]: 35 files, >200 MB, 13,448,372 unique 150-154-nt Twist oligos, Illumina NextSeq and ONT MinION; error-free per-file recovery claimed by authors; the repo contains only one sample id20 as git-LFS pointers, no licence), data-ncomms19-nanopore (assembly for nanopore readout, 2019, LFS FASTQ pointers), 2019-spotted-dna-data, storage-biasing-ncomms20 (data+code quantifying molecular bias, 2020, 23 commits), cas9-random-access and cas9-similarity-search (2022/2025), aging_data. None has a licence file except cas9-similarity-search (BSD-3-Clause) -> data cannot be assumed freely reusable. I did not find the SRA/ENA accession for Organick 2018 (the PDF text only points to the online version).

## 2. github.com/dna-storage (NCSU, Tuck / Keung labs)
Full org listing via `gh api orgs/dna-storage/repos` (15 repos): dnastorage, preview-cluster, dnapreview, ncomm-file-preview, DINOS, summer-camp-survival-notebooks, dnastorage-example-notebooks, framed, .github, ArchiGen, hedges-soft-decoder, RODAN-HEDGES, BINND, reframed, dnabind.

| Repo | Licence | Last commit | Class | Notes |
|---|---|---|---|---|
| reframed | custom BSD-2-style (NOASSERTION) | 2026-06-21 | competing implementation / benchmark infra | 45 commits, pytest (~135 tests). Pipelines: RS+Base4, RS+HEDGES, LT+Base4, LT+HEDGES, file-level LT. C++ fasthedges ext with python fallback. Fault injectors: fixed-rate, position-rate, pattern, real-FASTQ replay (+downsample), DNArSim (Julia). LSH clustering + MUSCLE + majority vote. DNA-encoded file header. Format-ID registry with stable IDs. I did not run its tests. |
| framed | same | 2024-05-31 | competing implementation (2023 paper) | Bioinformatics 2023, doi 10.1093/bioinformatics/btad572 [peer-reviewed]. HPC assumptions (LSF, tcsh, MPI, CentOS7, Julia). |
| dnastorage | LGPL-3.0 | 2021-05-10 | outdated research / prior art | ~5k lines: RS outer code, comma-free 8-mer index codewords (CFC8), Huffman/rotate/dense codecs, packetized files, FSMD formats. No HEDGES, no fountain. |
| hedges-soft-decoder | Oxford Nanopore Public License 1.0 (research-only) | 2024-06-18 | useful algorithm + dataset | Alignment-Matrix and Beam-Trellis soft decoding of HEDGES from Bonito CTC output, GPU. Paper (Bioinformatics, Jan 2025): hard decoder >25% byte error rate, prior soft decoder 2.25% at 183 s/read, new decoder 257x faster at 3.52% [WET reads, authors' numbers, via search summary]. Raw data on Zenodo DOIs 10.5281/zenodo.11454877, .11985455, .12014515. Licence is non-commercial. |
| ArchiGen | none | 2023-11-21 | complementary research | Python simulator of read amplification/re-synthesis (Agliamzanov & Tuck, IEEE ICRC 2023). No licence -> do not reuse. [SIM] |
| DINOS | custom BSD-style | 2021-11-02 | outdated research | Python 2.7 overhang-assembly (Golden-Gate-style) model; paper not located. |
| BINND / dnabind | MIT / none | 2026-04 / 2026-09 | complementary research | DNA-DNA binding predictor from millions of wet-lab interactions (README claim >80% accuracy; unverified). Possible future cross-talk/primer screen input. |
| RODAN-HEDGES | MIT | 2024-03-08 | complementary | RNA basecaller fork. |
| dnapreview, preview-cluster, ncomm-file-preview, example/summer notebooks, .github | LGPL-2.1/MIT | 2021-2022 | outdated/complementary | file "preview" via partial decode; not read. |

Prior art worth noting: (i) CFC8 comma-free index codewords and RS block/strand layout (dnastorage); (ii) the "BaseDNA" universal strand container, cascading pipeline with header encode/decode per component, and stable format-ID registry (reframed); (iii) fault injection as the primary test method incl. replay of real FASTQ with downsampling; (iv) read-amplification/re-synthesis modelling at archive level (ArchiGen); (v) soft decoding from basecaller posteriors.

## 3. HEDGES
- Original: github.com/whpress/hedges (note API name `whpress/HEDGES`), MIT, 7 stars, 10 commits, created 2024-11-03, last 2024-11-04 (86812c5049). Pure-C++ rewrite (the original Python-wrapped C++ modules "proved fragile"). Paper: Press, Hawkins, Jones, Schaub, Finkelstein, PNAS 117(31):18489-18496 (2020), https://pmc.ncbi.nlm.nih.gov/articles/PMC7414044 (arXiv 1812.01112 earlier).
- Wet-lab [WET, authors]: 5,865 Twist 300-nt oligos (23-nt primers + 254-nt payload), 18 packets x 255 strands at six code rates (1/6 ... 3/4), Illumina MiSeq ~50x. Measured error rates untreated 0.57% sub / 0.54% del / 0.23% ins; high-mutagenesis 2.38 / 0.82 / 0.39%. Untreated: all 18 packets decoded error-free at depth ~3; high mutagenesis: 16/18 (two failures at the highest rate 0.75). Constraints demonstrated: max run 4, 4<=GC<=8 per 12-nt window. [SIM, authors] error-free exabyte-scale storage feasible at DNA error rates up to 7-10% at code rate 0.25.
- [MINE] Built with the supplied script in 2.5 s and ran DNAcode_demo: 20 packets x 255 strands, code rate 0.5, simulated errors at 1.5x the paper's high-mutagenesis rates (3.57% sub, 1.23% del, 0.59% ins), all 20 packets decoded OK (matches typical_output_linux.txt); 125 s wall single thread, ~1.1 KB/s of message (includes encode + error injection, default compiler flags, one read per strand). Decoder is a heap-limited stack search; slow per strand.
- LICENCE TRAP: top-level MIT, but it bundles Schifra Reed-Solomon (`schifra/AA_README_LICENSE.txt`): free only for open-source/non-commercial under its own terms; commercial use needs Schifra's licence terms. Fine as an external research binary in a benchmark; replace the RS if HEDGES ideas are ever embedded in a product.
- Other HEDGES implementations: HaolingZHANG/pyHEDGES (GPL-3.0, one 419-line file, 2022, BGI copyright; fidelity unverified), dna-storage/reframed `fasthedges` (C++ extension, BSD-style) and hedges-soft-decoder, upmem/usecase_hedges (HEDGES+RS/LDPC on UPMEM DPUs, MIT, 2022), shulp2211/hedges and MeladSh/HedgesProject (student copies, no licence). No Rust/Go port found.

## 4. Storage-D, DNATerra, dnastore
- Storage-D: DNAstorage-iSynBio/Storage-D (MIT, 5 stars, 9 commits, last 2023-06-23). Paper iMeta 2024 (doi 10.1002/imt2.168, PMC11170965). Wukong codec ~1.98 bits/nt, regional GC check in 150-nt windows, homopolymer limit 3-5, RS + XOR redundancy, Primer3/BLAST flanking primers; also Church and Goldman, hooks for YYC and DNA Fountain. [WET, authors]: 1,909-3,319 x 200-nt strands from COVID-19 protocol + a TCM text, Twist synthesis, MiSeq, 100% recovery at >=30x and >99% at ~10x despite 3 missing oligos; in vivo 500-bp in E. coli and Halomonas for 7 days. Cost limit stated >$1500/MB. Codec has no indel correction of its own. Level 4. Web server storage.dailab.xyz:16666.
- DNATerra: y1151/DNATerra (MIT, 5 stars, created 2026-06-02, last 2026-07-25). Read simulator with empirical position-dependent noise profiles from three chip-synthesis platforms + NGS, GC/homopolymer/motif biases, depth mean/CV, ground-truth CIGAR/MD. No paper or independent validation found; realism is the repo's claim. Useful as a cross-check channel for VNX.
- dnastore: Mr-PU/dnastore (Apache-2.0, 1 star, created and last pushed 2026-07-25, 16 commits, ~2.3k lines Python). store/retrieve/update/delete API, append-only versioning with tombstones, plugin codecs (naive 2 b/base, rotating ~1.58, LT fountain), GF(256) RS erasure striping, CRC16-per-strand -> erasure, dropout-first simulators, FASTA/FASTQ export, FUSE VFS, vendor stubs. No indel correction, no wet-lab (level 1). Design ideas relevant to VNX's storage layer; not a competing codec.
- No other repos named DNATerra/dnastore/Storage-D with significant content were found. For Caltech (Bruck) and UIUC (Milenkovic): NO SIGNIFICANT PUBLIC GITHUB IMPLEMENTATION FOUND (GitHub and web search). Technion/Yaakobi: only omersabary/Reconstruction (no licence), GradHC and similar student repos with unverified affiliation.

## 5. Other discovered repos worth knowing (see JSON)
- Benchmark infrastructure: AAnzel/UNACORM (GPL-3.0, 377 commits, last 2026-09-07; preprint arXiv 2608.09673, 2026-08-10): standardised wrappers around Church, Grass 2D, Bornholt XOR, Goldman repetition, DNA-Aeon, NOREC4DNA Raptor/LT/Online; 9-file corpus; MESA error simulation; abstract states no single codec wins across all dimensions. This is the closest existing "harness" to what VNX needs. fml-ethz/dt4dds (GPL-3.0; Nature Communications 2023, doi 10.1038/s41467-023-41729-1) wet-lab-calibrated digital twin, plus dt4dds-benchmark and dt4dds-challenges (GPL-3.0). MESA (AGPL), DeSP (MIT), DNArSim (no licence).
- Competing codecs: MW55/DNA-Aeon (MIT; arithmetic-coded constraints + CRC markers + stack decoder + Raptor), HKU-BAL/Gungnir (BSD-3, 2025-26), ramy-khabbaz/MGCP (MIT), TeamErlich/dna-fountain (GPL-3.0, historic), Chamaeleo / YYC (MIT, BGI), nanopore_dna_storage and LDPC_DNA_storage (Chandak, MIT, 2020), DNAStorageToolkit (no licence).
- Trace reconstruction / clustering: GZHoffie/bbs (Rust, MIT), TReconLM (MLI-lab, no licence), omersabary/Reconstruction, DNAformer (itaiorr, MIT), Clover (GPL-3), GradHC, marker-code-tr, DSGMRecon.
- A long tail (about 150) of 2025-26 student/AI-generated "DNA storage simulators" (React/Streamlit demos, 0-1 stars); not audited; ignore unless they surface in a benchmark.

## What VNX could learn
1. Rate gap: BoundedHomopolymerEncoding shows an exact enumerative bound-achieving homopolymer code at 50-80 Mbps with GMP; compare VNX's constrained mapper rate at N=100-200 against 1.918/1.982/1.991 b/base (k=2/3/4).
2. HEDGES remains the reference for inner-code indel correction with constraints and the only one with published wet-lab depth-vs-rate data; but its stack decoder is slow (~1 KB/s in my run) and its soft/GPU descendant needs Bonito + NVIDIA GPU under a research-only licence.
3. reframed is the best architectural reference: component tags (outer / CW-to-CW / CW-to-DNA / DNA-to-DNA), per-component header (de)serialisation, stable format IDs, fault injection incl. real-FASTQ replay; adopt the ideas, test fixtures and metrics, not the code.
4. dnastore's dropout-first error model and append-only versioning match what VNX's archive layer should expose; but it ignores indels, so VNX's alignment/ECC is the differentiator.
5. Trace-reconstruction benchmarks are available (Microsoft CNR dataset; TrellisBMA/BMALA baselines; bbs; Sabary) but the CNR dataset has the known generation flaw; report results with that caveat and also use simulated sets.
6. Wet-lab data is scarce under open licences: CNR (MIT), HEDGES soft-decoder Zenodo data, Organick id20 (no licence, LFS), Chandak data repos. Most "physical" claims in repos are the papers' not the repos'.

## Benchmark-harness candidates (licence view; this is a research note, not legal advice)
- Run freely as external processes (permissive): TrellisBMA (MIT), BoundedHomopolymerEncoding (MIT), CNR dataset (MIT), whpress/hedges (MIT + Schifra terms: OK for non-commercial benchmarking), reframed/framed (BSD-style), dnastorage (LGPL), Storage-D (MIT), DNATerra (MIT), dnastore (Apache-2.0), DNA-Aeon (MIT), Gungnir (BSD-3), MGCP (MIT), bbs (MIT), DeSP (MIT), nanopore_dna_storage / LDPC_DNA_storage (MIT).
- Run only as separate tools, never linked or copied (copyleft): UNACORM, dt4dds(-benchmark), dna-fountain, pyHEDGES, Clover (GPL-3.0); NOREC4DNA, MESA (AGPL-3.0).
- Blocked / unclear: hedges-soft-decoder (ONT research-only), ArchiGen, TReconLM, Sabary Reconstruction, DNArSim, DNAStorageToolkit, uwmisl data repos (no licence file).
- Environment cost: reframed needs optional MPI/Julia for full features; hedges-soft-decoder needs CUDA GPU + Singularity; TrellisBMA needs numba + large notebooks.

## Not verified / gaps
- I did not run TrellisBMA, reframed tests, Storage-D, or any other repo besides building BHE and whpress/hedges.
- Paper numbers for Trellis BMA error rates are only in figures; HEDGES and Storage-D numbers come from PMC page summaries fetched via a summarising tool (cross-check against the papers before quoting). The hedges-soft-decoder numbers came from a search-result summary.
- Paper titles/authors of several MISL data repos, DINOS, BINND, Clover, DNA-Aeon, Gungnir were not confirmed.
- No information found on whether Microsoft's DNA storage effort is still active.

## Sources (title, year, link; evidence strength)
- Trellis BMA, ISIT 2021, https://arxiv.org/abs/2107.06440 (peer-reviewed conference, arXiv copy; read PDF text).
- Organick et al., Random access in large-scale DNA data storage, Nat Biotechnol 2018, https://racz.statistics.northwestern.edu/Organick+18NBT.pdf (peer-reviewed; wet-lab).
- Press et al., HEDGES, PNAS 2020, https://pmc.ncbi.nlm.nih.gov/articles/PMC7414044 (peer-reviewed; wet-lab + simulation).
- Huang et al., Storage-D, iMeta 2024, https://pmc.ncbi.nlm.nih.gov/articles/PMC11170965/ (peer-reviewed; wet-lab).
- Volkel et al., FrameD, Bioinformatics 2023, https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10563143/ (peer-reviewed; simulation framework).
- Volkel et al., Nanopore decoding with speed and versatility, Bioinformatics 2025, https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11755093/ (peer-reviewed; wet-lab reads).
- Agliamzanov & Tuck, ICRC 2023, https://api.openalex.org/works/doi:10.1109%2FICRC60800.2023.10386460 (peer-reviewed conference; simulation; only metadata seen).
- Gimpel et al., dt4dds, Nat Commun 2023, https://doi.org/10.1038/s41467-023-41729-1 (peer-reviewed; cited in repo README).
- Anzel et al., UNACORM, arXiv 2026, https://arxiv.org/abs/2608.09673 (preprint).
- Repos: https://github.com/microsoft/TrellisBMA , https://github.com/microsoft/DNABoundedHomopolymerEncoding , https://github.com/microsoft/clustered-nanopore-reads-dataset , https://github.com/uwmisl , https://github.com/dna-storage , https://github.com/whpress/hedges , https://github.com/DNAstorage-iSynBio/Storage-D , https://github.com/y1151/DNATerra , https://github.com/Mr-PU/dnastore , https://github.com/AAnzel/UNACORM , https://github.com/fml-ethz/dt4dds (all read via GitHub API/clones 2026-10-05; metadata exact, claims inside READMEs unverified).
