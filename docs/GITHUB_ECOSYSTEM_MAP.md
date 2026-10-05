# GitHub ecosystem map: public DNA-storage software, 2026-10-05

Status: research note. Not a benchmark. No physical DNA was synthesised or sequenced for this document, and no VNX-DNA code was compared with any external code on a shared protocol.

Access date for every external URL below: 2026-10-05.

## 0. How to read this document

Evidence labels used in this document:

| Label | Meaning |
|---|---|
| author-reported, in silico | A number or claim from the repository text or the paper's authors, from simulation. The audit did not reproduce it. |
| author-reported, wet-lab | A number or claim from the paper's authors, from physical synthesis and/or sequencing. The audit did not reproduce it. |
| measured by this audit (2026-10-05) | Something the auditor built or ran on 2026-10-05. Only three such runs exist: the build and round-trip of `microsoft/DNABoundedHomopolymerEncoding`, the build and demo run of `whpress/hedges`, and a smoke test of the PyPI package of Chamaeleo. |
| SIMULATED | A VNX-DNA result. Every VNX-DNA codec result is software strands through a software channel. |

Provenance of the data. The repository metadata and the per-repository findings come from two audit passes whose working files are `10-repos-cluster1.json` / `10-repos-cluster1.md` (called "pass 1" below) and `20-repos-cluster2.json` / `20-repos-cluster2.md` ("pass 2"), kept in `research/competitive-2026-10-05/` of the lab directory (not part of this repository when this file was written). Statements about VNX-DNA come only from `00-vnx-baseline-and-readiness.md` in the same directory (called "the baseline"), which read repository state at commit `081697b` (branch `build/v6-p1`). Repository paths cited for VNX-DNA are relative to that commit. Licence readings are a research note, not legal advice.

Nothing in this document says that VNX-DNA is better than, ahead of, or equivalent to any listed project. No same-protocol benchmark exists; the status is "not benchmarked".

## 1. Method

### 1.1 Searches and listings

| Item | Pass 1 | Pass 2 |
|---|---|---|
| Scope | US academic, Microsoft, dna-storage org, HEDGES, misc | BGI / Yin-Yang, fountain codes, classic codecs, newer (2023-2026) repos |
| Discovery | GitHub searches surfaced about 240 distinct repositories (many unrelated student simulators and lists); org listings: dna-storage 15 repos, uwmisl about 55, microsoft 22 keyword matches | About 790 raw GitHub hits from about 45 `search/repositories` queries (name, description, topics, README); org/user listings: BGI-SynBio, ntpz870817, HaolingZHANG, umr-ds, fml-ethz, dna-storage, uwmisl, MLI-lab, HKU-BAL, TJU-QiGe, dna-storage-lab, Guanjinqu; links found inside the dt4dds-benchmark and UNACORM READMEs |
| Catalogued (a record with metadata) | 76 repositories + 1 negative-findings note | 157 repositories + 8 "no public code found" records |
| In-depth | 11 ("deep": code or README read, some built or run) | 34 (about 14 of them code-read or executed per the pass-2 notes; the rest README plus paper abstract) |
| Medium (README + metadata) | 11 | not used |
| Shallow (metadata + description only) | 54 ("light") | 123 |

The two passes overlap: 54 repositories have a record in both. After de-duplication there are **179 distinct catalogued repositories** (76 + 157 - 54). Of these, 47 were read at medium depth or deeper in at least one pass (section 6) and 132 are shallow records (Appendix A). The raw-hit counts (about 240 and about 790) cannot be merged because the hit lists were not kept.

The 8 "no public code found" records are: Church 2012, Goldman 2013, Blawat 2016, Bornholt 2016, Organick 2018 (codec), Anavy 2019, the names SnakeEngine / DNAMapper / DNAsimulator (no DNA-storage tool found under these names), and Microsoft/UW read clustering (Rashtchian et al. 2017).

### 1.2 Rate limits

Pass 1 reports that the GitHub search API was rate-limited once and the searches were re-run later. Pass 2 does not record any rate-limit event. Neither pass claims the search is exhaustive; the search API is keyword and relevance based.

### 1.3 What was not covered

- Repositories not reachable by the queries used. About 150 student and generated "DNA storage simulators" (React or Streamlit demos with 0-1 stars) were seen by pass 1 and deliberately not audited.
- Non-GitHub hosting (GitLab, Zenodo-only, institutional servers). Some datasets are on Zenodo and are mentioned only where a repository points to them.
- Closed-source and commercial software. No company product was audited here.
- No public GitHub implementation was found for the Caltech (Bruck) and UIUC (Milenkovic) groups. Technion (Yaakobi) public code found was `omersabary/Reconstruction` (no licence) and student repositories of unverified affiliation.
- Microsoft: no public statement about the status of its DNA-storage effort was found; only repository activity is evidence.
- Most repositories were not run. The three measured items are listed in section 0. Reframed's tests, TrellisBMA's notebooks, and Storage-D were not run.
- Paper numbers for HEDGES, Storage-D and YYC were taken from fetched PMC page summaries and must be checked against the papers before external quotation. The hedges-soft-decoder numbers came from a search-result summary. The DNAformer journal version could not be opened (only the arXiv preprint abstract was read).
- Paper-level facts for Organick 2018, Blawat 2016, Anavy 2019, Antkowiak 2020 and Goldman 2013 could not be retrieved in full.
- Star counts, commit dates and licences are a snapshot of 2026-10-05.

### 1.4 Definitions used in the tables

Physical validation level (as defined in pass 2): 1 = software only; 2 = in-vitro synthesis; 3 = sequencing experiments; 4 = full end-to-end archival including storage, ageing and retrieval. The level reflects the cited paper, not the repository software. The two passes did not use the same rubric: for 5 repositories the levels differ and both values are shown as "x (pass 1) / y (pass 2)" where both passes recorded a number. Where one pass wrote "unknown" only the other value is shown.

Relevance to VNX (1-5): the auditor's own score; "a/b" means the passes scored differently. It is a prioritisation aid, not a quality score.

Class codes: CI = competing implementation; CR = complementary research; OR = outdated research; UD = useful dataset; UA = useful algorithm; UB = useful benchmark. Codes were derived by keyword from the classification text of each pass (a repository can carry several), with two manual corrections (`dna-storage/dnastorage` = OR, CR; `dna-storage/hedges-soft-decoder` = UA, UD).

## 2. Ecosystem map by role

Counts are from the 179 distinct catalogued repositories. Repositories listed from shallow records were classified from their GitHub description only and were not read.

| Role | Repositories (licence) | What is there | Evidence depth |
|---|---|---|---|
| End-to-end codecs / pipelines | `whpress/hedges` (MIT + Schifra); `MW55/DNA-Aeon` (MIT); `DNAstorage-iSynBio/Storage-D` (MIT); `BGI-SynBio/YinYangCode`, `ntpz870817/DNA-storage-YYC` (MIT); `TeamErlich/dna-fountain` (GPL-3.0); `reinhardh/dna_rs_coding`, `reinhardh/dna_data_storage` (Apache-2.0); `Scilence2022/DBGPS_Python` (GPL-3.0); `wushigang2/derrick` (MIT); `Guanjinqu/StairLoop` (GPL-3.0); `HKU-BAL/Gungnir` (BSD-3-Clause); `jeplb/mahoraga-codec` (PolyForm-NC); `HaolingZHANG/DNASpiderWeb` (custom BGI); `dna-storage/framed`, `reframed` (custom BSD-2-style); `dna-storage/dnastorage` (LGPL-3.0); `ramy-khabbaz/MGCP` (MIT) | Public code exists for a narrow slice of the literature. Church 2012, Goldman 2013, Blawat 2016, Bornholt 2016, Organick 2018 and Anavy 2019 have no official public codec (only re-implementations or data) | in-depth, except MGCP (shallow) |
| Mapping and constrained codes | YYC repos; `microsoft/DNABoundedHomopolymerEncoding` (MIT); `allanino/DNA` (Goldman port, MIT); Chamaeleo mappers (MIT); `HFLoechel/ConstrainedKaos` (MIT); DNASpiderWeb; `minminlittleshrimp/helix` (MIT, "capacity-approaching constrained codes", by description); `ylu1997/Code_Generation_For_DNA_Storage` (MIT, by description) | Mappers that hold GC and homopolymer limits; only the Microsoft repository is an exact enumerative codec | in-depth for the first five, shallow for the rest |
| Inner codes for indels | HEDGES (`whpress/hedges`, `pyHEDGES` GPL-3.0, `fasthedges` inside reframed, `hedges-soft-decoder` ONT licence, `RODAN-HEDGES` MIT); DNA-Aeon (arithmetic code + CRC sync markers + stack decoder); Gungnir (hash-guided search); StairLoop (convolutional + LDPC, BCJR); `shubhamchandak94/nanopore_dna_storage` (convolutional + Viterbi, MIT); marker-repeat codes inside TrellisBMA; `dna-storage-lab/LCRC_FBA` (MIT, always-aligned half-markers, by description) | Few public indel-capable codecs. HEDGES and DNA-Aeon are the two with maintained public code and published wet-lab or in-vitro tests | in-depth, except LCRC_FBA and RODAN-HEDGES |
| Outer and fountain codes | DNA Fountain (`TeamErlich`, `jdbrody` port, GPL-3.0); NOREC4DNA (LT, Online, Raptor RU10; AGPL-3.0); Raptor outer code inside DNA-Aeon; RS in `dna_rs_coding`, Chamaeleo, Derrick, HEDGES (Schifra); LDPC in `LDPC_DNA_storage` (MIT) and StairLoop; DBGPS (fountain + de Bruijn assembly) | RS, LT/Raptor and LDPC all have public code. No public Cauchy-RS or product-code DNA implementation was identified (one Rust repo, `wowinter13/dnacodec-rs`, mentions Cauchy by description and is shallow) | in-depth, except dnacodec-rs |
| Trace reconstruction, clustering, consensus | `microsoft/TrellisBMA` (MIT, archived); `GZHoffie/bbs` (MIT); `MLI-lab/TReconLM` (no licence); `itaiorr/Deep-DNA-based-storage` (DNAformer, MIT); `omersabary/Reconstruction` (BMA, ML-SCS, DivBMA; no licence); `Guanjinqu/Clover` (GPL-3.0); `bensdvir/GradHC`, `shaoqi7818/HSFC`, `BioPIM/ConCluD`, `prongs1996/DSGMRecon`, `yiliangw/marker-code-tr`, `qinyunnn/RobuSeqNet`, `yo-tam/DNA-Data-Storage` (by description); clusterer and MSA wrappers inside dt4dds-benchmark (Naive, CD-HIT, Clover, LSH, MMseqs2, Starcode; kalign) | Reconstruction is mostly research code. No official public code for the Microsoft/UW 2017 clustering | TrellisBMA, bbs, TReconLM, DNAformer, Clover in-depth; others shallow |
| Simulators and digital twins | `fml-ethz/dt4dds` (GPL-3.0); `WangLabTHU/DeSP` (MIT); `umr-ds/mesa_dna_sim` (AGPL-3.0); `y1151/DNATerra` (MIT); `BHam-1/DNArSim` (no licence); `dna-storage/ArchiGen` (no licence); `rrwick/Badread` (GPL-3.0, general ONT simulator); `fml-ethz/dt4dds-challenges` (GPL-3.0) | dt4dds is the only twin whose parameters were fitted to real data (40 sequencing experiments, per its paper) | dt4dds, DeSP, MESA, DNATerra in-depth |
| Benchmark harnesses | `fml-ethz/dt4dds-benchmark` (GPL-3.0); `AAnzel/UNACORM` (GPL-3.0); Chamaeleo (mapping-level only); `dna-storage/framed` / `reframed` (fault injection); `fml-ethz/dds-pipeline` (GPL-3.0, by description) | Two current multi-codec harnesses accept an external codec through a thin wrapper | in-depth |
| Datasets | `microsoft/clustered-nanopore-reads-dataset` (MIT); `uwmisl/data-nbt17` (id20 sample only, git-LFS pointers, no licence); `uwmisl/data-ncomms19-nanopore`, `2019-spotted-dna-data`, `storage-biasing-ncomms20`, `cas9-random-access` (no licence); `dna-storage/hedges-soft-decoder` (raw reads on Zenodo DOIs 10.5281/zenodo.11454877, .11985455, .12014515); `shubhamchandak94/nanopore_dna_storage_data`, `LDPC_DNA_storage_data` (no licence declared); inputs shipped with dt4dds-benchmark and UNACORM | Open licensing of wet-lab data is scarce. The Organick 2018 SRA/ENA accession was not found | medium or in-depth |
| Storage, object and API layers | `Mr-PU/dnastore` (Apache-2.0); `SSL-ACTX/helix` (AGPL-3.0); Storage-D (primer design, random-access options); `dna-storage/dnastorage` (packetised files); reframed (format registry with stable IDs); `BGI-SynBio/YYC-FileVersionControl` (no licence, not audited); `aeonscript-spec/aeonscript` (spec draft) | All are simulation-only research prototypes or proposals. `dnastore` and `helix` expose store/retrieve APIs over a simulated pool and state no wet-lab validation | in-depth |
| AI/ML decoders | DNAformer (`itaiorr`); TReconLM; `yo-tam/DNA-Data-Storage` (transformer, by description); `qinyunnn/RobuSeqNet`; `chill868686/adaptive-coder`; `shubhamsrivast4u/IDS_single_read_DNA`; `zhengzangw/Deep-Consensus-Finding` (by description); `dna-storage/BINND` (DNA-DNA binding predictor, not a decoder) | DNAformer and TReconLM need a GPU; DNAformer datasets are "on request"; TReconLM has no licence | DNAformer, TReconLM, BINND in-depth; others shallow |

## 3. Per-organisation sections

### 3.1 Microsoft (github.com/microsoft)

An organisation search (`dna|trellis|homopolymer|nanopore|oligo|molecular`) returned three DNA-storage repositories. A fourth, `microsoft/DNATagging` (Mathematica, MIT), was not opened and is probably not storage. No public Microsoft DNA-storage file or archive codec exists in this result.

| Repo | Facts | Evidence |
|---|---|---|
| [microsoft/TrellisBMA](https://github.com/microsoft/TrellisBMA) | MIT, archived, 11 stars, 14 commits, last commit 2024-05-11 (`77cb3d3965`). Paper: Srinivasavaradhan, Gopi, Pfister, Yekhanin, "Trellis BMA: Coded Trace Reconstruction on IDS Channels for DNA Storage", IEEE ISIT 2021, [arXiv 2107.06440](https://arxiv.org/abs/2107.06440). Python + numba notebooks, no tests, no CI, no pinned requirements seen. Implements a soft-output trace-reconstruction method plus baselines (BMALA, "multiply posteriors", full multi-trace BCJR). Details of the algorithm and its error model are in `ALGORITHM_COMPARISON.md`, sections 4 and 5. | Repository facts: read by the auditor. Algorithm and results: author-reported (paper). The notebooks were not run |
| [microsoft/DNABoundedHomopolymerEncoding](https://github.com/microsoft/DNABoundedHomopolymerEncoding) | MIT, 2 stars, 8 commits, created 2025-09-24, last commit 2025-09-25 (`fbb8ae203f`), not archived. Enumerative rank/unrank codec from n-bit strings to length-N quaternary strings with maximum homopolymer run k in 1..5. Author S. Gopi (write-up PDF in the repository; no peer-reviewed paper found). Homopolymer constraint only: no GC, motifs, error correction; k at most 5; GMP dependency; no test suite beyond random round-trip. Physical level 1. README rates at N=96 (k=1..5): 1.583 / 1.917 / 1.979 / 1.990 / 1.990 bits/base; slide asymptotic limits 1.5850 / 1.9227 / 1.9823 / 1.9957 / 1.9989. Measured by this audit: `bhe_rates 110` gave 1.5818 / 1.9182 / 1.9818 / 1.9909 / 1.9909; k=3, N=150 (297 bits), 2000 random trials, one thread: 14 ms encode and 8 ms decode in total, about 42 and 74 Mbit/s (README states about 50 and 80 Mbit/s); round trip without errors. | Rates and speed: "measured by this audit (2026-10-05)" and author-reported, in silico (README, slides). This audit's runs used one machine and one thread |
| [microsoft/clustered-nanopore-reads-dataset](https://github.com/microsoft/clustered-nanopore-reads-dataset) | MIT, 24 stars, 18 commits, last commit 2024-11-18 (`6938f44796`). `Centers.txt` (10,000 x 110 nt) and `Clusters.txt` (269,709 ONT MinION reads in clusters, 30.5 MB plain text). Twist synthesis, PCR, ONT LQK-LSK109 reads, clustered with the Rashtchian et al. NeurIPS 2017 algorithm; error rates about 0.017 insertion / 0.020 deletion / 0.022 substitution (from the paper). **Known flaw:** the authors' own note dated 8/12/2024 says the centres are not uniformly random (a generation bug) and some clusters may be malformed. Physical level 3 (reads produced by the UW MISL group). A trace-reconstruction benchmark, not an archive or file benchmark. | author-reported, wet-lab (sequencing); flaw statement is the authors' |

### 3.2 dna-storage organisation (NCSU; Tuck and Keung labs)

Full listing via the GitHub API: 15 repositories (dnastorage, preview-cluster, dnapreview, ncomm-file-preview, DINOS, summer-camp-survival-notebooks, dnastorage-example-notebooks, framed, .github, ArchiGen, hedges-soft-decoder, RODAN-HEDGES, BINND, reframed, dnabind).

| Repo | Licence | Last commit | Class | Notes |
|---|---|---|---|---|
| [reframed](https://github.com/dna-storage/reframed) | custom BSD-2-style (GitHub: NOASSERTION) | 2026-06-21 | CI, CR, UB | 45 commits, pytest suite (about 135 test functions by the auditor's count), a CI workflow file. Pipelines RS+Base4, RS+HEDGES, LT+Base4, LT+HEDGES, file-level LT. C++ `fasthedges` extension with Python fallback. Fault injectors: fixed-rate, position-rate, pattern, replay of real FASTQ with downsampling, DNArSim (Julia). LSH clustering with MUSCLE alignment and majority vote. DNA-encoded file header; format-ID registry with stable IDs. Its tests were not run by the auditor |
| [framed](https://github.com/dna-storage/framed) | same | 2024-05-31 | CI, CR, UA, UB | FrameD paper: Bioinformatics 2023, [doi:10.1093/bioinformatics/btad572](https://doi.org/10.1093/bioinformatics/btad572). Assumes an HPC environment (LSF, tcsh, MPI, CentOS7, Julia) |
| [dnastorage](https://github.com/dna-storage/dnastorage) | LGPL-3.0 | 2021-05-10 | OR, CR | About 5k lines. RS outer code, comma-free 8-mer index codewords (CFC8), Huffman / rotate / dense codecs, packetised files. No HEDGES, no fountain |
| [hedges-soft-decoder](https://github.com/dna-storage/hedges-soft-decoder) | ONT Public License 1.0 (research-only) | 2024-06-18 | UA, UD | Alignment-Matrix and Beam-Trellis soft decoding of HEDGES from Bonito CTC output; needs an NVIDIA GPU and Singularity. Paper: Volkel et al., Bioinformatics 2025, [PMC11755093](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11755093/). Reported (author-reported, wet-lab reads; relayed from a search summary, not verified against the paper): hard decoder byte error rate above 25%; previous soft decoder 2.25% at 183 s per read; new decoder 257 times faster at 3.52%. Licence is non-commercial |
| [ArchiGen](https://github.com/dna-storage/ArchiGen) | none | 2023-11-21 | CR | Simulator of read amplification and re-synthesis for pool/block allocation (Agliamzanov and Tuck, IEEE ICRC 2023). No licence: do not reuse |
| [DINOS](https://github.com/dna-storage/DINOS) | custom BSD-style | 2021-11-02 | CR, OR | Python 2.7 model of overhang-based assembly; paper not located |
| [BINND](https://github.com/dna-storage/BINND), [dnabind](https://github.com/dna-storage/dnabind) | MIT / none | 2026-04-12 / 2026-09-10 | CR | DNA-DNA binding predictor trained on a wet-lab dataset of millions of interactions; the README claim of over 80% accuracy is unverified. Possible future cross-talk or primer screen input |
| RODAN-HEDGES, dnapreview, preview-cluster, ncomm-file-preview, notebooks | MIT / LGPL-2.1 | 2021-2024 | CR, OR | RNA basecaller fork; partial-decode "file preview"; not read |

Design ideas recorded by pass 1 as prior art: comma-free index codewords and RS block/strand layout (dnastorage); a universal strand container, per-component header (de)serialisation and a stable format-ID registry (reframed); fault injection including replay of real FASTQ with downsampling; archive-level read-amplification modelling (ArchiGen); soft decoding from basecaller posteriors.

#### HEDGES (the original, outside the dna-storage org)

- [whpress/hedges](https://github.com/whpress/hedges): MIT, 7 stars, 10 commits, created 2024-11-03, last commit 2024-11-04 (`86812c5049`). A pure C++ rewrite; the older Python-wrapped C++ modules were described as fragile and purged upstream (dt4dds-benchmark therefore installs the fork `shulp2211/hedges`, 0 stars, no licence).
- Paper: Press, Hawkins, Jones, Schaub, Finkelstein, PNAS 117(31):18489-18496 (2020), [PMC7414044](https://pmc.ncbi.nlm.nih.gov/articles/PMC7414044), [doi:10.1073/pnas.2004821117](https://doi.org/10.1073/pnas.2004821117).
- author-reported, wet-lab: 5,865 Twist 300-nt oligos (23-nt primers + 254-nt payload), 18 packets of 255 strands at six code rates (1/6 to 3/4), Illumina MiSeq at about 50x. Error rates measured by the authors: untreated 0.57% substitution / 0.54% deletion / 0.23% insertion; high-mutagenesis 2.38% / 0.82% / 0.39%. Untreated: all 18 packets decoded without error at depth about 3. High mutagenesis: 16 of 18 (the two failures at the highest rate, 0.75). Constraints demonstrated: maximum run 4, 4 to 8 G/C per 12-nt window.
- author-reported, in silico: error-free exabyte-scale storage feasible at DNA error rates up to 7-10% at code rate 0.25.
- measured by this audit (2026-10-05): built in 2.5 s; `DNAcode_demo` with 20 packets of 255 strands, code rate 0.5, simulated errors at 1.5 times the paper's high-mutagenesis rates (3.57% substitution, 1.23% deletion, 0.59% insertion): all 20 packets decoded; 125 s wall time on one thread with default compiler flags, about 1.1 KB/s of message (encode, error injection and decode together, one read per strand).
- Licence trap: top-level MIT, but the repository bundles the Schifra Reed-Solomon library under its own terms (free for open-source or non-commercial use; commercial use needs Schifra's terms). Acceptable as an external research binary in a benchmark; the RS component would have to be replaced before any embedding in a product.
- Other implementations: `HaolingZHANG/pyHEDGES` (GPL-3.0, one 419-line file, 2022, BGI-copyright, fidelity unverified); reframed `fasthedges`; hedges-soft-decoder; `upmem/usecase_hedges` (MIT, 2022, not read); student copies without licence (`shulp2211/hedges`, `MeladSh/HedgesProject`). No Rust or Go port was found.

### 3.3 BGI, YYC, Chamaeleo, DNASpiderWeb, pyHEDGES

| Repo | Owner (real) | Licence | Stars | HEAD / last code change | Notes |
|---|---|---|---|---|---|
| [ntpz870817/Chamaeleo](https://github.com/ntpz870817/Chamaeleo) | personal account of a BGI author; org mirror `BGI-SynBio/Chamaeleo` (0 stars) | MIT | 46 | HEAD `54bdf37bef` 2022-02-22 (docs); last code fix `23724477ae` 2021-12-01 | PyPI Chamaeleo 1.34 |
| [ntpz870817/DNA-storage-YYC](https://github.com/ntpz870817/DNA-storage-YYC) | same | MIT | 27 | HEAD `cc8ad67fe1` 2024-05-06 (readme); `yyc/` last changed `58758c129d` 2021-06-02 | older home of YYC |
| [BGI-SynBio/YinYangCode](https://github.com/BGI-SynBio/YinYangCode) | BGI-SynBio | MIT | 4 | HEAD `f1dedbe9b7` 2025-10-23 (regex fix); algorithm code last changed 2021-06-02 in the older repo | newer YYC repo |
| [HaolingZHANG/DNASpiderWeb](https://github.com/HaolingZHANG/DNASpiderWeb) | personal account of a BGI author | custom "BGI-Research Shenzhen General Open-source Licence v1.0" (GitHub: NOASSERTION) | 11 | HEAD `f6d6311911` 2024-06-21; `dsw/` last `09365fed67` 2023-03-06 | mirror `Guanjinqu/DNASpiderWeb` labels it GPL-3.0 (an older copy) |
| [HaolingZHANG/pyHEDGES](https://github.com/HaolingZHANG/pyHEDGES) | same | GPL-3.0 | 5 | `abb3a9a9db` 2022-04-20 | port of HEDGES, slow |
| BGI-SynBio/YYC-FileVersionControl, DNA-BF | BGI-SynBio | none declared | 1 each | 2023-11 / 2024-04 | not audited; YYC-FileVersionControl is conceptually near a versioning layer |

- **Chamaeleo.** An "integrated evaluation platform" wrapping Base (2 bit/nt), Church 2012, Goldman 2013, Grass 2015, Blawat 2016, DNA Fountain 2017 and Yin-Yang, with Hamming and RS as ECC (HEDGES is not inside). Its error model applies one random substitution, insertion or deletion to a chosen fraction of sequences: no dropout, coverage, reads, clustering or consensus. Paper: Ping et al., Synthetic Biology Journal 2020, [bioRxiv preprint](https://www.biorxiv.org/content/10.1101/2020.01.02.892588v3) (abstract only was read). Measured by this audit (2026-10-05), smoke test only: 4000 random bytes, Python 3.12.3, Chamaeleo 1.34 from PyPI; Base, Goldman, Blawat and Church round-trip in 0.05-0.12 s; the Yin-Yang class raised `TypeError: 'float' object cannot be interpreted as an integer` on Python 3.12; Grass requires a segment length divisible by 16; DNAFountain with the auditor's default arguments did not decode (probably the auditor's parameters; not investigated). Suitable at most as a mapping-level density / GC / homopolymer comparison.
- **YYC.** Two binary segments are combined by rule tables (1,536 rule combinations) so that each nucleotide carries 2 bits under constraints. The repository holds the inner mapper only: no indel correction, no outer code (the paper used RS). Paper: Ping et al., Nature Computational Science 2, 234-242 (2022), [doi:10.1038/s43588-022-00231-2](https://doi.org/10.1038/s43588-022-00231-2), [PMC10766522](https://pmc.ncbi.nlm.nih.gov/articles/PMC10766522). Pure Python (about 1.9k lines, no tests); decoding needs a pickled rule model.
- **DNASpiderWeb (SPIDER-WEB).** Graph-based generation of coding algorithms under arbitrary local constraints with built-in correction. Preprint only: [arXiv:2204.02855](https://arxiv.org/abs/2204.02855) v3 (2023-03-30); the arXiv page reports no wet-lab work and no peer-reviewed version was found. README: experiments are single-threaded and "may take several months on a laptop".
- **Licence caveat for SPIDER-WEB.** The custom BGI licence allows free use, modification and redistribution, but products containing the code must be open-sourced and commercialisation requires separate permission from BGI. It is not an OSI licence.

### 3.4 TeamErlich (DNA Fountain), NOREC4DNA and DNA-Aeon (Marburg)

- [TeamErlich/dna-fountain](https://github.com/TeamErlich/dna-fountain): GPL-3.0, 163 stars, 53 forks, the most-starred DNA-storage code found. Python 2 + Cython reference of Erlich and Zielinski, Science 355(6328):950-954 (2017), [doi:10.1126/science.aaj2038](https://doi.org/10.1126/science.aaj2038). Code last changed 2016-09-09 (`5fd4d98292`); later commits (HEAD `8ee2777aa5`, 2025-07-08) are README-only. Droplets with a robust soliton distribution, rejection screening for GC and homopolymers, 4-byte seed, RS(2) per oligo, default redundancy 7%. No indel handling: reads with indels are discarded. The Python 3 port [jdbrody/dna-fountain](https://github.com/jdbrody/dna-fountain) (GPL-3.0, 11 stars, archived, last commit 2023-06-29) is the version used by dt4dds-benchmark. Many student ports exist, mostly without a licence.
- [umr-ds/NOREC4DNA](https://github.com/umr-ds/NOREC4DNA): AGPL-3.0, 12 stars, HEAD `51f9660970` 2025-10-13. LT, Online and Raptor (RU10) fountain codes with DNA rules, the MOSLA/MESA simulation API and an optimiser that selects low-error-probability packets. Paper: BMC Bioinformatics 22:406 (2021), [doi:10.1186/s12859-021-04318-x](https://doi.org/10.1186/s12859-021-04318-x), mostly in silico.
- [MW55/DNA-Aeon](https://github.com/MW55/DNA-Aeon): MIT, 20 stars, last C++ change `c13efeae84` 2022-06-09, HEAD `6e33bb6fc4` 2025-01-14. Arithmetic-coding inner code over a constraint-adhering codebook (ConstrainedKaos, MIT, 8 stars), periodic CRC sync markers, a stack-algorithm decoder for substitutions, insertions and deletions, and an outer Raptor code from NOREC4DNA (AGPL-3.0, a git submodule, so run as a separate process). Nature Communications 14 (2023), [doi:10.1038/s41467-023-36297-3](https://doi.org/10.1038/s41467-023-36297-3); in silico and in vitro tests per the abstract. It is wrapped by both benchmark harnesses and is the public indel-capable constrained codec with a working repository.
- [umr-ds/mesa_dna_sim](https://github.com/umr-ds/mesa_dna_sim) (MESA): AGPL-3.0, 17 stars; a Dockerised Flask service (Postgres, Redis) that simulates synthesis, storage, sequencing and PCR errors; heavy to run in a loop. OFC4DNA and DR4DNA (AGPL-3.0, 1-2 stars) are sibling projects whose purpose was not verified.

### 3.5 ETH Zurich (fml-ethz): dt4dds and dt4dds-benchmark

- [fml-ethz/dt4dds](https://github.com/fml-ethz/dt4dds): GPL-3.0, 12 stars, last commit `784b635c88` 2025-07-10, Python 3.10. A digital twin of array synthesis, PCR, accelerated ageing and Illumina sequencing-by-synthesis, plus an error-analysis pipeline for real sequencing data. Paper: Gimpel, Stark, Heckel, Grass, Nature Communications 14, 6026 (2023), [doi:10.1038/s41467-023-41729-1](https://doi.org/10.1038/s41467-023-41729-1); parameters fitted to 40 real sequencing experiments (author-reported). The software is level 1 (simulation); its parameters come from wet-lab data. Companion repository `dt4dds-challenges` (C++, GPL-3.0).
- [fml-ethz/dt4dds-benchmark](https://github.com/fml-ethz/dt4dds-benchmark): GPL-3.0, 2 stars, HEAD `0928fd7f26` 2025-08-28, v1.0 2025-06-25; no published paper (README: "manuscript in preparation"). Default codecs: DNA-Aeon, DNA Fountain (jdbrody port), DNA-RS (Heckel/Grass), "Goldman" (modified `allanino/DNA`), HEDGES (old Python-C++ version via the `shulp2211/hedges` fork), Yin-Yang. Wrappers also exist for DBGPS, LDPC (Chandak) and Modulation. Clusterers: Naive (Erlich), CD-HIT, Clover, LSH (Heckel and Darestani), MMseqs2, Starcode. Tools: NGmerge, kalign. Workflows: error generator with substitution, insertion, deletion, dropout and coverage parameters, best-case and worst-case, serial PCR and dilution, downsampling, motif-based errors; sweeps stored as HDF5 with runtime limits. Extension contract: an executable pair `encode.sh` / `decode.sh` taking plain text files (one sequence per line) plus a dataclass of codec parameters; no FASTA or FASTQ.
- ETH Grass group codec code: `reinhardh/dna_data_storage` (Apache-2.0, 14 stars, 2017; Grass et al. 2015, [doi:10.1002/anie.201411378](https://doi.org/10.1002/anie.201411378)) and `reinhardh/dna_rs_coding` (Apache-2.0, 37 stars, last commit 2021-03-10; Heckel et al., Nature Protocols 2019, and Antkowiak et al., Nat Commun 11, 5345 (2020), [doi:10.1038/s41467-020-19148-3](https://doi.org/10.1038/s41467-020-19148-3)).

### 3.6 UW MISL (github.com/uwmisl)

About 55 repositories, mostly microfluidics (PurpleDrop) and molecular computing. Storage-relevant ones are data repositories:

| Repo | Content | Licence |
|---|---|---|
| [data-nbt17](https://github.com/uwmisl/data-nbt17) | Data for Organick et al., Nature Biotechnology 2018, [doi:10.1038/nbt.4079](https://doi.org/10.1038/nbt.4079) (author-reported, wet-lab: 35 files, over 200 MB, 13,448,372 unique 150-154 nt Twist oligos, Illumina NextSeq and ONT MinION; error-free per-file recovery claimed). The repository holds one sample (id20) as git-LFS pointers of about 135 bytes; the payload needs an LFS download. The SRA/ENA accession was not found | none |
| data-ncomms19-nanopore | Nanopore run FASTQs (LFS pointers) and sequence lists, 2019 | none |
| 2019-spotted-dna-data | Sequencing data for spotted-DNA storage | none |
| storage-biasing-ncomms20 | Data and notebooks quantifying synthesis, PCR and sequencing bias; useful for position-dependent bias models | none |
| cas9-random-access | Cas9 enrichment and nanopore reads of a 3-of-25 file access | none |
| cas9-similarity-search | similarity search with Cas9 (2025) | BSD-3-Clause |

Without a licence file the data cannot be assumed reusable.

### 3.7 Storage-D

[DNAstorage-iSynBio/Storage-D](https://github.com/DNAstorage-iSynBio/Storage-D): MIT, 5 stars, 9 commits, last commit 2023-06-23 (`3ede6def8a`). Paper: iMeta 2024, [doi:10.1002/imt2.168](https://doi.org/10.1002/imt2.168), [PMC11170965](https://pmc.ncbi.nlm.nih.gov/articles/PMC11170965/). Python framework with the Wukong codec (about 1.98 bits/nt coding potential, regional GC check in 150-nt windows, homopolymer limit 3-5), RS plus XOR redundancy, Primer3/BLAST primer design, Church and Goldman re-implementations, and hooks for YYC and DNA Fountain. author-reported, wet-lab: 1,909-3,319 strands of 200 nt (a COVID-19 protocol and a traditional-medicine text), Twist synthesis, MiSeq; 100% recovery at 30x or more and over 99% at about 10x despite 3 missing oligos; in vivo 500-bp storage in E. coli and Halomonas for 7 days; stated cost limit above $1,500/MB (values relayed through a page summary, not verified against the PDF). The codec has no indel correction of its own. Physical level 4 (pass 1) / 3 (pass 2).

### 3.8 DNATerra

[y1151/DNATerra](https://github.com/y1151/DNATerra): MIT, 5 stars, created 2026-06-02, last commit 2026-07-25 (`7d7430be5d`). A read-level simulator with empirical position-dependent noise profiles from three chip-synthesis platforms plus NGS, GC / homopolymer / motif biases, depth mean and coefficient of variation, and ground-truth CIGAR/MD output. No paper or independent validation was found; the realism is the repository's own claim. Level 1.

### 3.9 dnastore (Mr-PU)

[Mr-PU/dnastore](https://github.com/Mr-PU/dnastore): Apache-2.0, 1 star, created and last pushed 2026-07-25, 16 commits, about 2.3k lines of Python. A store / retrieve / update / delete API over a simulated pool; append-only versioning with tombstones; plug-in codecs (naive 2 bits/base, rotating about 1.58, LT fountain); GF(256) RS erasure striping; CRC16 per strand turned into erasures; dropout-first simulators; FASTA/FASTQ export; a FUSE virtual file system; vendor stubs. No indel correction, no wet-lab data (level 1). Relevant as architecture prior art, not as a competing codec.

### 3.10 Others worth knowing

- [AAnzel/UNACORM](https://github.com/AAnzel/UNACORM): GPL-3.0, 2 stars, HEAD `8b1dbd81f9` 2026-09-07 (377 commits per pass 1). Preprint: [arXiv:2608.09673](https://arxiv.org/abs/2608.09673) (2026-08-10), simulation-based. Standardised wrappers for 8 codecs (SimpleCode = Church 2012 re-implementation, The2DCode = Grass 2015, XORBasedCode = Bornholt 2016, RepetitionCode = Goldman 2013, DNA-Aeon, NOREC4DNA LT, Online and Raptor), nine baseline files, five runs per codec and file, MESA or its own error simulator, SLURM mode. The abstract states that no single algorithm is optimal across all metrics. The Church/Grass/Bornholt/Goldman codecs are student re-implementations whose fidelity to the originals is unverified.
- [HKU-BAL/Gungnir](https://github.com/HKU-BAL/Gungnir) (BSD-3-Clause, Go, 13 stars): hash-signature search; Nature Communications 2026, [doi:10.1038/s41467-026-71485-x](https://doi.org/10.1038/s41467-026-71485-x); in silico benchmarking per the abstract; code not run.
- [Guanjinqu/StairLoop](https://github.com/Guanjinqu/StairLoop) (GPL-3.0): convolutional + LDPC with BCJR; Nature Communications 2025, [doi:10.1038/s41467-025-64230-3](https://doi.org/10.1038/s41467-025-64230-3); in vitro with electrochemical synthesis per the abstract.
- [jeplb/mahoraga-codec](https://github.com/jeplb/mahoraga-codec): PolyForm Noncommercial 1.0.0, pure Python, 1 star; [arXiv:2604.20810](https://doi.org/10.48550/arxiv.2604.20810). All numbers are single-author simulation on the dt4dds channel and not peer reviewed.
- [SSL-ACTX/helix](https://github.com/SSL-ACTX/helix) (AGPL-3.0, Rust, 3 stars): an archiver stack in simulation (Zstd, authenticated encryption per block, RS erasure default 10+5, rotating base-3 trellis, primers, 4 MB streaming blocks). Its README states it has not been physically validated; the claims are unverified.
- [aeonscript-spec/aeonscript](https://github.com/aeonscript-spec/aeonscript): a draft specification (v0.1) for a 7-layer open standard; no evidence of an implementation.
- [wushigang2/derrick](https://github.com/wushigang2/derrick) (MIT, 1 star): RS plus soft-decision decoding; National Science Review 2023, [doi:10.1093/nsr/nwad229](https://doi.org/10.1093/nsr/nwad229). Uses the `bsalign` library under a separate licence.
- Others catalogued: `Scilence2022/DBGPS_Python` (GPL-3.0), `shubhamchandak94/nanopore_dna_storage` and `LDPC_DNA_storage` (MIT, Chandak), `GZHoffie/bbs` (MIT), `MLI-lab/TReconLM` (no licence), `Guanjinqu/Clover` (GPL-3.0), `WangLabTHU/DeSP` (MIT), `rrwick/Badread` (GPL-3.0, 305 stars, general nanopore read simulator).

## 4. Classification of audited repositories

Each of the 47 repositories read at medium depth or deeper carries the codes in the last column of section 6. By class:

| Class | Repositories |
|---|---|
| Competing implementation (CI) | BGI-SynBio/YinYangCode; dna-storage/framed; dna-storage/reframed; DNAstorage-iSynBio/Storage-D; HaolingZHANG/pyHEDGES; HKU-BAL/Gungnir; jeplb/mahoraga-codec; Mr-PU/dnastore; MW55/DNA-Aeon; ntpz870817/Chamaeleo; ntpz870817/DNA-storage-YYC; SSL-ACTX/helix; TeamErlich/dna-fountain; umr-ds/NOREC4DNA; whpress/HEDGES; wushigang2/derrick |
| Complementary research (CR) | aeonscript-spec/aeonscript; dna-storage/ArchiGen; dna-storage/BINND; dna-storage/DINOS; dna-storage/dnastorage; dna-storage/framed; dna-storage/reframed; Guanjinqu/StairLoop; HaolingZHANG/DNASpiderWeb; itaiorr/Deep-DNA-based-storage; microsoft/DNABoundedHomopolymerEncoding; umr-ds/mesa_dna_sim; uwmisl/cas9-random-access; uwmisl/storage-biasing-ncomms20; y1151/DNATerra |
| Outdated research (OR) | allanino/DNA; dna-storage/DINOS; dna-storage/dnastorage; microsoft/TrellisBMA (archived; "outdated-but-prior-art" in pass 1); ntpz870817/Chamaeleo; TeamErlich/dna-fountain (code unchanged since 2016) |
| Useful dataset (UD) | dna-storage/hedges-soft-decoder; fml-ethz/dt4dds; itaiorr/Deep-DNA-based-storage; microsoft/clustered-nanopore-reads-dataset; microsoft/TrellisBMA; shubhamchandak94/nanopore_dna_storage; uwmisl/2019-spotted-dna-data; uwmisl/cas9-random-access; uwmisl/data-nbt17; uwmisl/data-ncomms19-nanopore; uwmisl/storage-biasing-ncomms20 |
| Useful algorithm (UA) | BGI-SynBio/YinYangCode; dna-storage/framed; dna-storage/hedges-soft-decoder; DNAstorage-iSynBio/Storage-D; Guanjinqu/Clover; Guanjinqu/StairLoop; GZHoffie/bbs; HaolingZHANG/DNASpiderWeb; HaolingZHANG/pyHEDGES; HKU-BAL/Gungnir; itaiorr/Deep-DNA-based-storage; jeplb/mahoraga-codec; microsoft/DNABoundedHomopolymerEncoding; microsoft/TrellisBMA; MLI-lab/TReconLM; MW55/DNA-Aeon; ntpz870817/DNA-storage-YYC; reinhardh/dna_rs_coding; Scilence2022/DBGPS_Python; shubhamchandak94/LDPC_DNA_storage; shubhamchandak94/nanopore_dna_storage; TeamErlich/dna-fountain; umr-ds/NOREC4DNA; whpress/HEDGES; wushigang2/derrick |
| Useful benchmark (UB) | AAnzel/UNACORM; allanino/DNA; dna-storage/framed; dna-storage/reframed; DNAstorage-iSynBio/Storage-D; fml-ethz/dt4dds; fml-ethz/dt4dds-benchmark; Guanjinqu/Clover; GZHoffie/bbs; microsoft/clustered-nanopore-reads-dataset; microsoft/DNABoundedHomopolymerEncoding; microsoft/TrellisBMA; MLI-lab/TReconLM; MW55/DNA-Aeon; ntpz870817/Chamaeleo; reinhardh/dna_rs_coding; TeamErlich/dna-fountain; umr-ds/mesa_dna_sim; umr-ds/NOREC4DNA; WangLabTHU/DeSP; whpress/HEDGES; y1151/DNATerra |

Classification is the auditors' judgement from README, metadata and (for a minority) code reading. A "competing implementation" label means the repository covers part of the same functional space as VNX-DNA; it does not imply any ranking.

## 5. Licence table, with traps

Licence distribution over the 179 catalogued repositories (counted from the JSON records; custom licences identified in the audit notes are grouped as "custom / non-OSI"): MIT 62; none declared 62; GPL-3.0 18; Apache-2.0 9; NOASSERTION or unknown 8; custom / non-OSI 7; AGPL-3.0 6; BSD-3-Clause 3; GPL-2.0 1; LGPL-2.1 1; LGPL-3.0 1; BSD-2-Clause 1. About one third of the catalogued repositories carry no licence, so the default is "all rights reserved".

VNX-DNA is MIT-licensed (`LICENSE` of this repository). "Reuse" below means copying or porting code into VNX-DNA. This table is a research note, not legal advice; get legal review before any step beyond reading.

| Licence | Repositories (examples) | Read | Run as external process | Benchmark | Link | Reuse code in VNX |
|---|---|---|---|---|---|---|
| MIT / BSD-3-Clause / BSD-2-Clause / Apache-2.0 | TrellisBMA; DNABoundedHomopolymerEncoding; clustered-nanopore-reads-dataset; Storage-D; DNATerra; DNA-Aeon; YinYangCode; Chamaeleo; Gungnir (BSD-3); bbs; DNAformer; dna_rs_coding (Apache); dnastore (Apache); derrick; DeSP; Chandak repos; allanino/DNA; ConstrainedKaos | yes | yes | yes | yes | yes, keeping the notice (Apache-2.0: also the NOTICE file and patent terms). Check bundled third-party parts: derrick uses `bsalign` (separate licence); Storage-D uses Primer3 and BLAST |
| MIT with a bundled restricted component | whpress/hedges (bundles Schifra RS under its own terms: free for open-source or non-commercial use) | yes | yes | yes, non-commercial benchmarking | no for the Schifra part | no for Schifra. HEDGES' own code is MIT, but a commercial product would need a replacement RS. Re-implementing from the paper is a separate legal question (patents not assessed) |
| Custom BSD-style (GitHub shows NOASSERTION) | dna-storage/framed, reframed, DINOS | yes | yes | yes | read the licence text first | read the licence text first; the audit describes it as BSD-2-style |
| LGPL-3.0 / LGPL-2.1 | dna-storage/dnastorage, dnapreview | yes | yes | yes | possible under LGPL terms | not without legal review: copying LGPL code into an MIT project changes the terms for that code |
| GPL-3.0 / GPL-2.0 | TeamErlich/dna-fountain, jdbrody port; fml-ethz/dt4dds, dt4dds-benchmark, dt4dds-challenges; AAnzel/UNACORM; Guanjinqu/Clover, StairLoop; DBGPS_Python; pyHEDGES; Badread | yes | yes, as a separate unmodified process | yes (VNX plugs in as an external executable pair; no code shared) | no | no |
| AGPL-3.0 | umr-ds/NOREC4DNA, mesa_dna_sim, OFC4DNA, DR4DNA; SSL-ACTX/helix; BioPIM/ConCluD | yes | yes, unmodified, local only | yes locally | no | no. Do not expose over a network as a service without legal review (network-use clause) |
| Custom BGI-Research licence v1.0 | HaolingZHANG/DNASpiderWeb | yes | internal evaluation is probably fine (audit reading) | internal only | no | no: products containing it must be open-sourced and commercialisation needs BGI's separate permission. Ask BGI |
| PolyForm Noncommercial 1.0.0 | jeplb/mahoraga-codec | yes | no for for-profit use without a licence | no | no | no |
| Oxford Nanopore Public License 1.0 (research-only) | dna-storage/hedges-soft-decoder | yes | academic research only after legal review | academic only | no | no. Also needs an NVIDIA GPU, Bonito and Singularity |
| No licence file (all rights reserved) | uwmisl data repos (except cas9-similarity-search, BSD-3-Clause); ArchiGen; TReconLM; omersabary/Reconstruction; DNArSim; DNAStorageToolkit; RobuSeqNet; GradHC; shulp2211/hedges; most student fountain ports | yes | ask the owner first | ask the owner first | no | no. Data in these repositories cannot be assumed freely redistributable |
| Datasets under MIT | microsoft/clustered-nanopore-reads-dataset | yes | n/a | yes, with its known generation flaw reported alongside any result | n/a | data may be reused with notice |

Specific traps found:

1. `whpress/hedges` is MIT at the top level but bundles Schifra RS (see row above).
2. `ntpz870817/Chamaeleo` is MIT but its Yin-Yang class fails on Python 3.12 (measured by this audit).
3. `jeplb/mahoraga-codec` shows NOASSERTION on GitHub but its LICENSE is PolyForm Noncommercial 1.0.0.
4. `DNASpiderWeb` shows NOASSERTION on GitHub; a mirror (`Guanjinqu/DNASpiderWeb`) says GPL-3.0 for an older copy. Use the repository's own LICENSE.pdf.
5. `dna-storage/hedges-soft-decoder` is under the Oxford Nanopore Public License 1.0, which is research-only.
6. `DNA-Aeon` is MIT but its NOREC4DNA submodule is AGPL-3.0: run as a separate process.
7. `uwmisl` data repositories hold a mix of git-LFS pointers and no licence.
8. dt4dds-benchmark's HEDGES wrapper depends on an unlicensed fork (`shulp2211/hedges`).

## 6. Repository-by-repository findings (in-depth and medium audits)

47 repositories were read at medium depth or deeper in at least one pass (pass 1 "deep" and "medium", pass 2 "in-depth"). For repositories audited in both passes the record from the deeper pass is shown. "Last commit" is the date of the last meaningful (code) commit named in the audit record, not necessarily the repository HEAD (examples: `TeamErlich/dna-fountain` code 2016-09-09 versus README-only HEAD 2025-07-08; `ntpz870817/Chamaeleo` code 2021-12-01 versus docs HEAD 2022-02-22; `HaolingZHANG/DNASpiderWeb` code 2023-03-06 versus HEAD 2024-06-21). Level, Rel. and Class are defined in section 1.4. Stars as of 2026-10-05.

| Repository | Licence | Stars | Last commit | Purpose | Level | Rel. | Class |
|---|---|---|---|---|---|---|---|
| [AAnzel/UNACORM](https://github.com/AAnzel/UNACORM) | GPL-3.0 | 2 | 2026-09-07 | Open-source modular benchmarking platform (Streamlit dashboard + headless SLURM script) with standardised wrapper functions for... | 1 | 5 | UB |
| [aeonscript-spec/aeonscript](https://github.com/aeonscript-spec/aeonscript) | MIT + CC-BY-SA per README (GitHub: NOASSERTION) | 1 | 2026-06-21 | Proposed open standard (7-layer stack) for DNA archival with a reference Python implementation; unvalidated | 1 | 2 | CR |
| [allanino/DNA](https://github.com/allanino/DNA) | MIT | 50 | 2015-04-26 | Python 2 script encoding/decoding arbitrary files with the Goldman scheme (Huffman -> ternary -> rotating code, 4x overlapping segments). | 3 | 3 | OR, UB |
| [BGI-SynBio/YinYangCode](https://github.com/BGI-SynBio/YinYangCode) | MIT | 4 | 2025-10-23 | Transcoding (bits->nt) with biochemical constraints; two bit-streams 'yin' and 'yang' combined by a rule table into one nt per two bits;... | 3 | 4 | CI, UA |
| [dna-storage/ArchiGen](https://github.com/dna-storage/ArchiGen) | none | 0 | 2023-11-21 | Python simulator of read amplification / re-synthesis in a DNA archive with block-to-pool allocation (random vs hot-distributed grouping,... | 1 | 3 | CR |
| [dna-storage/BINND](https://github.com/dna-storage/BINND) | MIT | 2 | 2026-04-12 | CNN predicting DNA-DNA binding from an ultra-high-throughput wet-lab dataset of millions of interactions (README claims >80% accuracy and... | 3 | 2 | CR |
| [dna-storage/DINOS](https://github.com/dna-storage/DINOS) | custom BSD-style (GitHub: NOASSERTION) | 1 | 2021-11-02 | Python 2.7 model code for DINOs: overhang-based (Golden Gate style) assembly of arbitrary messages into DNA; models ideal, realistic and... | 1 | 1 | CR, OR |
| [dna-storage/dnastorage](https://github.com/dna-storage/dnastorage) | LGPL-3.0 | 6 | 2021-05-10 | Core encoding/decoding/file-manipulation Python library for modeling DNA storage: Reed-Solomon outer code, comma-free-codeword (CFC8)... | 1 | 2/3 | OR, CR |
| [dna-storage/framed](https://github.com/dna-storage/framed) | custom BSD-2-style (GitHub: NOASSERTION) | 1 | 2024-05-31 | Simulation / fault-injection framework: modular codec pipeline (RS, fountain, HEDGES), MPI-parallel decode, clustering (LSH), MUSCLE... | 3 | 4/2 | CI, CR, UA, UB |
| [dna-storage/hedges-soft-decoder](https://github.com/dna-storage/hedges-soft-decoder) | ONT Public License 1.0, research-only (GitHub: NOASSERTION) | 1 | 2024-06-18 | GPU soft decoding of HEDGES codes from nanopore basecaller (Bonito) CTC output: Alignment Matrix trellis algorithm and Beam Trellis;... | 3 | 3 | UA, UD |
| [dna-storage/reframed](https://github.com/dna-storage/reframed) | custom BSD-2-style (GitHub: NOASSERTION) | 0 | 2026-06-21 | Modernised FrameD: pip-installable, pytest suite, pure-Python fallbacks, pipeline builders (ReedSolomon_Base4, Basic_Hedges,... | 1 | 5/2 | CI, CR, UB |
| [DNAstorage-iSynBio/Storage-D](https://github.com/DNAstorage-iSynBio/Storage-D) | MIT | 5 | 2023-06-23 | Python codec framework integrating Wukong (new), Church 2012 and Goldman 2013 implementations; hooks for YYC and DNA Fountain; RS adding,... | 4 (pass 1) / 3 (pass 2) | 3 | CI, UA, UB |
| [fml-ethz/dt4dds](https://github.com/fml-ethz/dt4dds) | GPL-3.0 | 12 | 2025-07-10 | Customisable Python 'digital twin' of array synthesis -> PCR -> accelerated ageing -> sequencing-by-synthesis, plus an error-analysis... | 3 (pass 1) / 1 (pass 2) | 5 | UD, UB |
| [fml-ethz/dt4dds-benchmark](https://github.com/fml-ethz/dt4dds-benchmark) | GPL-3.0 | 2 | 2025-08-28 | Benchmarking suite: wrappers for encoding with any codec, simulating arbitrary workflows under standardised conditions (ErrorGenerator,... | 1 | 5 | UB |
| [Guanjinqu/Clover](https://github.com/Guanjinqu/Clover) | GPL-3.0 | 19 | 2023-03-28 | Clustering of billions of unordered reads for DNA storage via tree structure; pip install dna-clover. | 3 | 3 | UA, UB |
| [Guanjinqu/StairLoop](https://github.com/Guanjinqu/StairLoop) | GPL-3.0 | 5 | 2025-08-13 | Coding scheme for high-error electrochemical DNA synthesis: convolutional + LDPC concatenation with BCJR iterative decoding, MPI-parallel... | 3 | 4 | CR, UA |
| [GZHoffie/bbs](https://github.com/GZHoffie/bbs) | MIT | 8 | 2026-07-07 | Rust trace reconstruction (bidirectional beam search), prebuilt binary release v0.2.0. | 1 | 4/3 | UA, UB |
| [HaolingZHANG/DNASpiderWeb](https://github.com/HaolingZHANG/DNASpiderWeb) | custom BGI-Research licence v1.0 (GitHub: NOASSERTION) | 11 | 2023-03-06 | Graph-based all-in-one coding: generates customised bit-to-nt algorithms under arbitrary local biochemical constraints with built-in error... | 1 | 2/3 | CR, UA |
| [HaolingZHANG/pyHEDGES](https://github.com/HaolingZHANG/pyHEDGES) | GPL-3.0 | 5 | 2022-04-20 | Python re-implementation of the original HEDGES encoder/decoder, used as a comparator in SPIDER-WEB experiments. | 1 (pass 1) / 3 (pass 2) | 2/4 | CI, UA |
| [HKU-BAL/Gungnir](https://github.com/HKU-BAL/Gungnir) | BSD-3-Clause | 13 | 2026-03-02 | Proof-of-work style codec: each fragment carries a hash signature; decoder tests 'educated guesses' of edits until the hash matches -... | 1 | 4 | CI, UA |
| [itaiorr/Deep-DNA-based-storage](https://github.com/itaiorr/Deep-DNA-based-storage) | MIT | 23 | 2025-05-13 | DNAformer: CNN+transformer trained on simulated noisy clusters to reconstruct strands (replaces clustering+consensus), plus a 'CPL'... | 3 | 3/4 | CR, UD, UA |
| [jeplb/mahoraga-codec](https://github.com/jeplb/mahoraga-codec) | PolyForm Noncommercial 1.0.0 (GitHub: NOASSERTION) | 1 | 2026-06-18 | Pure-Python codec: 126-nt strands, soft-decision inner decode (profile-HMM alignment, log-product fusion across reads, LDPC + OSD with CRC... | 1 | 2/3 | CI, UA |
| [microsoft/clustered-nanopore-reads-dataset](https://github.com/microsoft/clustered-nanopore-reads-dataset) | MIT | 24 | 2024-11-18 | Benchmark dataset for trace reconstruction: Centers.txt (10,000 x 110-nt references) and Clusters.txt (269,709 ONT MinION reads, clustered). | 3 | 5 | UD, UB |
| [microsoft/DNABoundedHomopolymerEncoding](https://github.com/microsoft/DNABoundedHomopolymerEncoding) | MIT | 2 | 2025-09-25 | Rank/unrank codec: map binary strings of length n to quaternary strings of length N with max homopolymer run <= k (k=1..5), achieving the... | 1 | 4/3 | CR, UA, UB |
| [microsoft/TrellisBMA](https://github.com/microsoft/TrellisBMA) | MIT | 11 | 2024-05-11 | Reference implementation of Trellis BMA (soft-output coded trace reconstruction on multi-trace IDS trellis) plus baselines (BMALA,... | 3 | 4 | OR, UD, UA, UB |
| [MLI-lab/TReconLM](https://github.com/MLI-lab/TReconLM) | none declared | 3 | 2026-06-04 | Decoder-only transformer for trace reconstruction trained on synthetic noisy traces; pretrained models + data on Hugging Face... | 1 | 3 | UA, UB |
| [Mr-PU/dnastore](https://github.com/Mr-PU/dnastore) | Apache-2.0 | 1 | 2026-07-25 | Object-storage-style API over simulated DNA: store/retrieve/update/delete, versioned tombstone metadata store, plug-in codecs, RS erasure... | 1 | 3 | CI |
| [MW55/DNA-Aeon](https://github.com/MW55/DNA-Aeon) | MIT | 20 | 2025-01-14 | Concatenated code: inner arithmetic-coding encoder with periodic CRC sync markers and a stack-algorithm decoder (corrects sub/ins/del),... | 3 | 5 | CI, UA, UB |
| [ntpz870817/Chamaeleo](https://github.com/ntpz870817/Chamaeleo) | MIT | 46 | 2021-12-01 | Library/'kit' wrapping classical bit-to-base transcoding schemes plus Hamming/RS ECC, indexing, and pipelines for transcoding, robustness,... | 1 | 3/5 | CI, OR, UB |
| [ntpz870817/DNA-storage-YYC](https://github.com/ntpz870817/DNA-storage-YYC) | MIT | 27 | 2021-06-02 | Transcoding (bits->nt) with biochemical constraints; two bit-streams 'yin' and 'yang' combined by a rule table into one nt per two bits;... | 3 | 3/4 | CI, UA |
| [reinhardh/dna_rs_coding](https://github.com/reinhardh/dna_rs_coding) | Apache-2.0 | 37 | 2021-03-10 | Reed-Solomon outer/inner coding for DNA with mapping, used for ETH Grass-group experiments; C++ with Boost. | 3 | 4 | UA, UB |
| [Scilence2022/DBGPS_Python](https://github.com/Scilence2022/DBGPS_Python) | GPL-3.0 | 5 | 2022-01-18 | 'DBG-based' codec: de Bruijn graph + greedy path search to reassemble strands despite breaks, rearrangements, indels; fountain droplets... | 3 | 3 | UA |
| [shubhamchandak94/LDPC_DNA_storage](https://github.com/shubhamchandak94/LDPC_DNA_storage) | MIT | 11 | 2020-09-05 | Regular LDPC codes for Illumina-sequenced DNA storage with soft/LLR decoding (C). | 3 | 3 | UA |
| [shubhamchandak94/nanopore_dna_storage](https://github.com/shubhamchandak94/nanopore_dna_storage) | MIT | 22 | 2020-09-11 | Convolutional-code inner code + Viterbi decoder integrated with the basecaller (Flappie/bonito) soft information to cut nanopore reading... | 3 | 3 | UD, UA |
| [SSL-ACTX/helix](https://github.com/SSL-ACTX/helix) | AGPL-3.0 | 3 | 2026-02-10 | Rust 'systems-level DNA storage archiver': Zstd, XChaCha20-Poly1305 per block with Argon2id/HKDF keys, RS erasure (default 10+5), rotating... | 1 | 2/3 | CI |
| [TeamErlich/dna-fountain](https://github.com/TeamErlich/dna-fountain) | GPL-3.0 | 163 | 2016-09-09 | Reference implementation of the DNA Fountain architecture: LT/Luby-transform droplets with robust soliton distribution, screening of each... | 4 (pass 1) / 3 (pass 2) | 4 | CI, OR, UA, UB |
| [umr-ds/mesa_dna_sim](https://github.com/umr-ds/mesa_dna_sim) | AGPL-3.0 | 17 | 2023-10-19 | 'MOSLA Error Simulator': web app + REST API (Flask/uwsgi/Postgres/Redis/Docker) assessing DNA fragments against synthesis/PCR/sequencing... | 1 | 3 | CR, UB |
| [umr-ds/NOREC4DNA](https://github.com/umr-ds/NOREC4DNA) | AGPL-3.0 | 12 | 2025-10-13 | Framework to use/test/compare fountain codes (LT, Online, Raptor RU10) for DNA storage with built-in DNA rules and MESA/MOSLA simulation... | 1 | 3/4 | CI, UA, UB |
| [uwmisl/2019-spotted-dna-data](https://github.com/uwmisl/2019-spotted-dna-data) | none | 1 | 2019-03-07 | Sequencing data for spotted-DNA storage experiments; sequencing-data.json maps experiment->instance->index->paths; primers listed in README. | 3 | 2 | UD |
| [uwmisl/cas9-random-access](https://github.com/uwmisl/cas9-random-access) | none | 0 | 2022-07-04 | Random access via Cas9 enrichment + nanopore; FASTQ of 3-of-25 file access (20200715 run), adapted C3POa consensus pipeline. | 3 | 2/3 | CR, UD |
| [uwmisl/data-nbt17](https://github.com/uwmisl/data-nbt17) | none | 12 | 2018-01-11 | Data for Organick 2018: only a MANIFEST, id20.fastq.gz and id20.refs.txt.gz (git-LFS pointers of ~135 bytes in the GitHub tree; real... | 4 | 3/4 | UD |
| [uwmisl/data-ncomms19-nanopore](https://github.com/uwmisl/data-ncomms19-nanopore) | none | 1 | 2019-05-20 | Nanopore run FASTQs (runs 13,15,16,18,20; LFS pointers) and sequence lists (apollo, Vitruvian, space shuttle, 365 dishes). | 3 | 2/3 | UD |
| [uwmisl/storage-biasing-ncomms20](https://github.com/uwmisl/storage-biasing-ncomms20) | none | 3 | 2021-04-08 | Data and Jupyter code quantifying synthesis/PCR/sequencing bias across oligo pools - useful for position/sequence-dependent bias models. | 3 | 3 | CR, UD |
| [WangLabTHU/DeSP](https://github.com/WangLabTHU/DeSP) | MIT | 12 | 2022-02-14 | Simulates errors across all DNA-storage stages (sequence loss + within-sequence errors) to optimise redundancy. | 3 (pass 1) / 1 (pass 2) | 3 | UB |
| [whpress/HEDGES](https://github.com/whpress/HEDGES) | MIT (bundles Schifra RS, own terms) | 7 | 2024-11-04 | Original HEDGES (Hash Encoded, Decoded by Greedy Exhaustive Search) reference: 2024 pure-C++ rewrite (old Python-wrapped C++ modules... | 3 | 5 | CI, UA, UB |
| [wushigang2/derrick](https://github.com/wushigang2/derrick) | MIT | 1 | 2025-10-16 | 'Derrick': RS code + soft-decision decoding driven by a DNA-specific error prediction model; C program with bsalign-based consensus. | 3 | 4 | CI, UA |
| [y1151/DNATerra](https://github.com/y1151/DNATerra) | MIT | 5 | 2026-07-25 | Read-level simulator for DNA-storage sequencing: empirical noise profiles from 3 chip-synthesis platforms + NGS (full read, random access,... | 1 | 4/2 | CR, UB |

## 7. Where VNX-DNA sits against the role map (not benchmarked)

From the baseline (`00-vnx-baseline-and-readiness.md`, repository state `081697b`). All VNX-DNA results are SIMULATED; the baseline states "There is NO wet-lab or real-sequencing data anywhere in the codebase" (B.11) and `docs/LIMITATIONS.md` says "NOT PHYSICALLY VALIDATED".

| Role | VNX-DNA status per baseline | Repository evidence |
|---|---|---|
| End-to-end codec and archive | PRESENT: VNX4 container, chunking, deduplication, Merkle structures, AES-256-GCM, 313-nt strands, CLI | `docs/VNX4_FORMAT.md`, `src/vnxdna/v4/` |
| Mapping and constraints | PARTIAL: 2 bits/nt mapping with constraint screening by up to 256 scrambler variants, "not constrained coding"; secondary-structure, Tm and primer handling ABSENT | `src/vnxdna/v4/constraints.py`, `docs/LIMITATIONS.md` |
| Inner code for indels | marker-template banded DP turns indels into erased segments; no VT/HEDGES-like indel-correcting code (ABSENT) | `src/vnxdna/v4/sync.py`, `src/vnxdna/v5/native/align.c` |
| Outer code | Cauchy RS (64+16 default) PRESENT; V6 product code with column parity PRESENT, opt-in; LT fountain PARTIAL, experimental; LDPC, polar, convolutional ABSENT | `src/vnxdna/ecc/cauchy.py`, `src/vnxdna/v6/outer.py`, `docs/V6_OUTER_CODE.md` |
| Trace reconstruction and clustering | consensus with soft posteriors and address snapping PRESENT; trace reconstruction for indel channels ABSENT; clustering of unaddressed reads ABSENT | `src/vnxdna/v5/indel/`, V4 decoder |
| Simulator | PRESENT: ChannelConfig, 14 named versioned models, "not fitted to any measured platform"; position-dependent errors, chimeras, strand breakage, PCR dynamics ABSENT | `src/vnxdna/v4/channel.py`, `experiments/v6/channel/models/*.json` |
| Benchmark harness | competitor records framework with computed comparability labels; 12 literature systems, no claims; no run on dt4dds-benchmark or UNACORM | `benchmarks/competitors/records.json` |
| Datasets | FASTA/FASTQ ingestion PRESENT; public-dataset ingestion and channel fitting ABSENT; third-party reads without VNX framing cannot be decoded | baseline B.12 |
| Storage / object / API layer | container CLI PRESENT; REST, object store, FUSE ABSENT; versioning and snapshots ABSENT; Python API PARTIAL | baseline B.7 |

Consequence for any comparison with the repositories above: VNX-DNA can only decode its own frames. Public datasets (Microsoft CNR, Organick id20, Zenodo HEDGES reads) can be used to fit channel models, not to decode (baseline B.12).

## 8. Limitations of this map

- Snapshot of 2026-10-05; the search is not exhaustive (section 1).
- Physical validation levels come from papers and READMEs, were assigned by two passes with different rubrics, and 5 repositories carry two different values.
- Most statements about repository content come from the READMEs and metadata; only about 14 repositories in pass 2 and some in pass 1 had code read, and three runs were made (section 0).
- Paper figures for HEDGES, YYC, Storage-D and hedges-soft-decoder were relayed through summaries and should be checked against the papers.
- The appendix descriptions are the GitHub descriptions or short auditor notes; many shallow records say "unknown".
- Licence readings are not legal advice.
- A separate company survey (`30-companies.*`) and dataset survey (`40-datasets.*`) exist in the same directory and were not used for this document.

## Appendix A. Shallow records (long tail)

132 repositories that were catalogued from GitHub metadata and description only in both passes where they appear (or read at a shallower level than the 47 above). Licence, stars, last commit and purpose are copied from the JSON records (`10-repos-cluster1.json`, `20-repos-cluster2.json`); "none declared" means GitHub reports no licence; "unknown" means the audit did not record the field. Purpose is truncated to about 120 characters. These repositories were not read; they must not be treated as audited.

| Repository | Licence | Stars | Last commit | Purpose (from audit record) |
|---|---|---|---|---|
| [15-minute-discourse/dna-data-storage](https://github.com/15-minute-discourse/dna-data-storage) | none declared | 1 | 2025-02-22 | Associated repository for the "Is DNA the KEY to Solving the Data Storage Crisis?" 15 minute discourse podcast on... |
| [abdul-rasool/EDS-Effective-DNA-Storage-System](https://github.com/abdul-rasool/EDS-Effective-DNA-Storage-System) | none declared | 4 | 2024-06-05 | EDS: An Effective DNA-Based File Storage System for Practical Archiving and Retrieval of Medical MRI Data |
| [aeonscript-spec/aeonproof](https://github.com/aeonscript-spec/aeonproof) | NOASSERTION | 1 | 2026-06-21 | A proof vault for the age of fakes - cryptography detects tampering, DNA archives the proof for centuries. Built on... |
| [am-saksham/dna-data-storage](https://github.com/am-saksham/dna-data-storage) | none declared | 1 | 2026-09-12 | unknown (no description) |
| [armanhajizadeh/Rapid-Information-Retrieval-from-DNA-Storage-](https://github.com/armanhajizadeh/Rapid-Information-Retrieval-from-DNA-Storage-) | none declared | 1 | 2025-05-02 | non sequencing, with multiplexed PCR |
| [arnabsaha7/DataHelix-Encoding-Decoding](https://github.com/arnabsaha7/DataHelix-Encoding-Decoding) | none declared | 1 | 2024-06-07 | DataHelix is a Python tool for encoding any dataset into DNA sequences and decoding them back when necessary, aiming... |
| [bensdvir/GradHC](https://github.com/bensdvir/GradHC) | none declared | 3 | 2023-07-24 | An implementation of the Gradual Hash-based clustering (GradHC) algorithm for DNA storage systems. |
| [BertZan/Modulation-based-DNA-storage](https://github.com/BertZan/Modulation-based-DNA-storage) | GPL-3.0 | 1 | 2022-09-09 | unknown (no description) |
| [BGI-SynBio/Chamaeleo](https://github.com/BGI-SynBio/Chamaeleo) | MIT | 0 | 2022-02-22 | BGI DNA Storage Kit |
| [BGI-SynBio/DNA-BF](https://github.com/BGI-SynBio/DNA-BF) | none declared | 1 | 2024-04-20 | unknown (no description) |
| [BGI-SynBio/YYC-FileVersionControl](https://github.com/BGI-SynBio/YYC-FileVersionControl) | none declared | 1 | 2023-11-20 | unknown (no description) |
| [BHam-1/DNArSim](https://github.com/BHam-1/DNArSim) | none declared | 10 | 2021-12-06 | DNA Archive Simulator (DNArSim) is a memory channel model that simulate the entire DNA Data Storage process: from... |
| [Bioinformaticslave/DNA_Steganography](https://github.com/Bioinformaticslave/DNA_Steganography) | none declared | 1 | 2026-06-14 | Little algorithm which encodes text into synthetic DNA sequences and recover it through a reversible DNA-inspired data... |
| [BioPIM/ConCluD](https://github.com/BioPIM/ConCluD) | AGPL-3.0 | 0 | 2025-05-20 | Fork of ConCluD: consensus using primer clustering (AGPL-3.0) |
| [chamorin/dnabin](https://github.com/chamorin/dnabin) | none declared | 2 | 2026-03-26 | Encode and decode DNA sequence to binary data |
| [chill868686/adaptive-coder](https://github.com/chill868686/adaptive-coder) | Apache-2.0 | 2 | 2023-03-01 | DNA storage adaptive coder using neural network |
| [dinglulu/Polus](https://github.com/dinglulu/Polus) | none declared | 2 | 2026-07-15 | Soft-Decision-Integrated Codec Enhancement Pipeline for DNA Storage |
| [Divyam989/dna-storage-device](https://github.com/Divyam989/dna-storage-device) | none declared | 3 | 2026-04-03 | Software version to see how DNA can be used to Store Data |
| [dna-storage-lab/Bootstrap-readout-using-hidden-references](https://github.com/dna-storage-lab/Bootstrap-readout-using-hidden-references) | MIT | 1 | 2026-07-21 | We propose a multi-stage alignment and error correction strategy via multiple-fold hidden references, transforming the... |
| [dna-storage-lab/DNAStorage_LCRC](https://github.com/dna-storage-lab/DNAStorage_LCRC) | MIT | 1 | 2026-07-21 | An accompanying indexing and progressive recovery framework with specialized long composite ranging codes (LCRCs) for... |
| [dna-storage-lab/LCRC_FBA](https://github.com/dna-storage-lab/LCRC_FBA) | MIT | 1 | 2026-07-21 | The software implements an soft information generation algorithm based on always-aligned half-markers for DNA data... |
| [dna-storage/.github](https://github.com/dna-storage/.github) | unknown | unknown | unknown | unknown |
| [dna-storage/dnabind](https://github.com/dna-storage/dnabind) | none | 0 | 2026-09-10 | Official code release for 'Reading between the strands: biophysically informed sequence encodings for weak-affinity... |
| [dna-storage/dnapreview](https://github.com/dna-storage/dnapreview) | LGPL-2.1 | 0 | 2021-05-10 | unknown |
| [dna-storage/dnastorage-example-notebooks](https://github.com/dna-storage/dnastorage-example-notebooks) | MIT | 0 | 2022-08-12 | unknown |
| [dna-storage/ncomm-file-preview](https://github.com/dna-storage/ncomm-file-preview) | MIT | 1 | 2022-03-23 | unknown |
| [dna-storage/preview-cluster](https://github.com/dna-storage/preview-cluster) | MIT | 0 | 2021-04-05 | unknown |
| [dna-storage/RODAN-HEDGES](https://github.com/dna-storage/RODAN-HEDGES) | MIT | 1 | 2024-03-08 | unknown (no description) |
| [dna-storage/summer-camp-survival-notebooks](https://github.com/dna-storage/summer-camp-survival-notebooks) | unknown | unknown | unknown | unknown |
| [DNAvid/DNA-IDstorage](https://github.com/DNAvid/DNA-IDstorage) | none declared | 3 | 2017-10-09 | Bridge to IPFS decentralized storage with encryption |
| [dphiffer/dna-codec](https://github.com/dphiffer/dna-codec) | MIT | 2 | 2017-02-21 | Encoding and decoding data into DNA base pairs |
| [Eko-Refugium/DNAbyte](https://github.com/Eko-Refugium/DNAbyte) | MIT | 0 | 2026-05-07 | End-to-end simulator (MIT, 128 commits) |
| [erlanders177/bioforge](https://github.com/erlanders177/bioforge) | NOASSERTION | 1 | 2026-08-28 | High-performance bioinformatics engine for edge computing. 5-bit encoding, vectorised alignment, DNA->protein... |
| [feeka/mt_dna_as_storage](https://github.com/feeka/mt_dna_as_storage) | none declared | 1 | 2026-08-02 | unknown (no description) |
| [fml-ethz/dds-pipeline](https://github.com/fml-ethz/dds-pipeline) | GPL-3.0 | 1 | 2025-07-10 | Easy-to-use pipelines for encoding, decoding, and analyzing error patterns in DNA data storage. |
| [fml-ethz/dt4dds-challenges](https://github.com/fml-ethz/dt4dds-challenges) | GPL-3.0 | 3 | 2024-07-05 | C++ routine to simulate current challenges for error-correction coding in DNA data storage. |
| [Guanjinqu/Helix](https://github.com/Guanjinqu/Helix) | none declared | 6 | 2025-01-16 | A Novel Biological Image Storage System based on DNA Data Storage |
| [gyfbianhuanyun/DNA2DNA_Codec_for_Homopolymer_constraints](https://github.com/gyfbianhuanyun/DNA2DNA_Codec_for_Homopolymer_constraints) | none declared | 2 | 2024-09-26 | Adaptable DNA Storage Coding: An Efficient Framework for Homopolymer Constraint Transitions |
| [gyfbianhuanyun/DNA_storage_channel_codec](https://github.com/gyfbianhuanyun/DNA_storage_channel_codec) | MIT | 1 | 2024-08-09 | DNA storage channel codec |
| [gyfbianhuanyun/DNA_storage_simulator_GAN](https://github.com/gyfbianhuanyun/DNA_storage_simulator_GAN) | NOASSERTION | 4 | 2022-11-08 | unknown (no description) |
| [HFLoechel/ConstrainedKaos](https://github.com/HFLoechel/ConstrainedKaos) | MIT | 8 | 2022-02-15 | Fractal Construction of Constrained Code Words for DNA Storage Systems |
| [hihihhi/dna-storage-simulation](https://github.com/hihihhi/dna-storage-simulation) | MIT | 1 | 2026-07-31 | Seeded simulations of DNA storage encoding, IDS channels, coding bounds, and custom beam-search decoding |
| [iamnitishpattar/helixvault](https://github.com/iamnitishpattar/helixvault) | MIT | 1 | 2026-09-25 | "A DNA-based data storage system built with React and FastAPI |
| [jamesmtuck/DNA_stability](https://github.com/jamesmtuck/DNA_stability) | MIT | 3 | 2021-01-07 | Analysis of DNA stability on design of error correction codes for DNA-based data storage. |
| [jamesmtuck/DORIS](https://github.com/jamesmtuck/DORIS) | MIT | 4 | 2021-05-16 | Code associated with DORIS: Dynamic DNA-based information storage. https://doi.org/10.1101/836429 |
| [jbkrause/archive2dna](https://github.com/jbkrause/archive2dna) | GPL-3.0 | 1 | 2022-03-17 | Encodes a binary information package into DNA and decodes DNA back into the original binary representation. |
| [jdbrody/dna-fountain](https://github.com/jdbrody/dna-fountain) | GPL-3.0 | 11 | 2023-06-29 | DNA-Fountain |
| [jeter1112/dna-fountain-simplified](https://github.com/jeter1112/dna-fountain-simplified) | none declared | 22 | 2021-12-27 | unknown (no description) |
| [Jiozhang/SEEKER-encoding-and-decoding](https://github.com/Jiozhang/SEEKER-encoding-and-decoding) | MIT | 1 | 2023-11-08 | Complete encoding and decoding process compatible with SEEKER for keyword search in DNA data storage |
| [KathanS/DNA_Codec](https://github.com/KathanS/DNA_Codec) | none declared | 1 | 2023-04-20 | DNA Codec with Polar Codes, PGP Encryption, Huffman Compression. This web application is used to encode the data into... |
| [Kritika11052005/DNA_Storage](https://github.com/Kritika11052005/DNA_Storage) | none declared | 1 | 2026-07-12 | Experimental notebook: VQ-quantized neural activations encoded to DNA sequences, with tamper-evidence and... |
| [Larissa-11/DNA-QLC](https://github.com/Larissa-11/DNA-QLC) | none declared | 3 | 2023-09-28 | unknown (no description) |
| [lasso-sustech/IEC_Codes](https://github.com/lasso-sustech/IEC_Codes) | GPL-3.0 | 1 | 2024-10-30 | Integrated error correction (GPL-3.0; unreviewed) |
| [lcbb/ssDNA-memory](https://github.com/lcbb/ssDNA-memory) | GPL-3.0 | 1 | 2019-03-17 | Single-stranded, kilobase DNA memory encoding scheme for archival in phage |
| [marcelm/dnaio](https://github.com/marcelm/dnaio) | MIT | 71 | 2026-09-15 | Efficiently read and write sequencing data from Python |
| [MeladSh/HedgesProject](https://github.com/MeladSh/HedgesProject) | none | 0 | 2023-01-06 | Student HEDGES project (2023); no licence. |
| [michaelting/ASCII_DNA_Translator](https://github.com/michaelting/ASCII_DNA_Translator) | BSD-2-Clause | 11 | 2013-09-06 | DNA as an information storage medium using either 4-base codon or binary representations of 256-ASCII |
| [microsoft/DNATagging](https://github.com/microsoft/DNATagging) | MIT | 4 | 2025-03-06 | Unknown beyond name; Mathematica repo with no description (not opened). Possibly related to DNA tagging, not storage... |
| [minminlittleshrimp/helix](https://github.com/minminlittleshrimp/helix) | MIT | 1 | 2026-01-09 | HELIX: Implementation of capacity-approaching constrained codes for DNA storage. Based on: Nguyen et al. -... |
| [mkeoliya/dna_storage_simulator](https://github.com/mkeoliya/dna_storage_simulator) | none declared | 5 | 2022-10-11 | unknown (no description) |
| [MLI-lab/noisy_dna_data_storage](https://github.com/MLI-lab/noisy_dna_data_storage) | Apache-2.0 | 9 | 2020-09-22 | Data recovery from millions of noisy reads |
| [MohakBajaj/DNA-Data-EncoderDecoder](https://github.com/MohakBajaj/DNA-Data-EncoderDecoder) | none declared | 1 | 2022-11-17 | DNA Data Encoder and Decoder |
| [Mooreniah/DNA-dual-rule-rotary-encoding-storage-system-DRRC-](https://github.com/Mooreniah/DNA-dual-rule-rotary-encoding-storage-system-DRRC-) | none declared | 1 | 2024-03-05 | A codec scheme for DNA storage |
| [MustafaKpn/DNAcodeX](https://github.com/MustafaKpn/DNAcodeX) | MIT | 1 | 2023-09-18 | DNAcodeX - A System for Encoding and Decoding Data in DNA-based Storage |
| [mythflipped/DNA_fountain_for_python2-3](https://github.com/mythflipped/DNA_fountain_for_python2-3) | none declared | 3 | 2022-01-06 | unknown (no description) |
| [nawazia/RaptorPJPEG](https://github.com/nawazia/RaptorPJPEG) | MIT | 4 | 2026-03-20 | Implementation of "Progressive decoding of DNA-stored JPEG data with on-the-fly error correction" |
| [nhlpl/DNA-based-archival-storage-system](https://github.com/nhlpl/DNA-based-archival-storage-system) | MIT | 1 | 2026-04-10 | DNA-based archival storage system using a hybrid swarm (ant colony optimization + bacterial chemotaxis) to evolve... |
| [nhlpl/DNA-Cryptography](https://github.com/nhlpl/DNA-Cryptography) | MIT | 1 | 2026-04-09 | A modular, open framework for DNA-native cryptographic data storage. |
| [omersabary/Reconstruction](https://github.com/omersabary/Reconstruction) | none | 5 | 2023-05-23 | C++ implementations of BMA, ML-SCS, DivBMA/other reconstruction algorithms for deletion/IDS channels. |
| [PFGitCode/LDPC-Codes-for-DNA-Storage-with-NanoporeSequencing](https://github.com/PFGitCode/LDPC-Codes-for-DNA-Storage-with-NanoporeSequencing) | none declared | 2 | 2020-08-28 | unknown (no description) |
| [Pobedinsky/Study-and-Evaluation-of-the-HiDNA-codec](https://github.com/Pobedinsky/Study-and-Evaluation-of-the-HiDNA-codec) | none declared | 1 | 2024-07-02 | unknown (no description) |
| [PParkJy/DeCoBase](https://github.com/PParkJy/DeCoBase) | none declared | 1 | 2026-08-12 | Real-Time Data Readout in DNA Storage System via Decoding-Coupled Basecalling |
| [PParkJy/SAD-DNAstorage](https://github.com/PParkJy/SAD-DNAstorage) | none declared | 2 | 2025-07-01 | Sequence analysis and decoding with extra low-quality reads for DNA data storage |
| [prongs1996/DNAStorageToolkit](https://github.com/prongs1996/DNAStorageToolkit) | none declared | 11 | 2024-04-16 | An open-source, end-to-end DNA data storage toolkit that facilitates every step of the DNA-based data storage pipeline. |
| [prongs1996/DSGMRecon](https://github.com/prongs1996/DSGMRecon) | none declared | 1 | 2025-01-31 | Double-Sided Greedy Median Algorithm for Fast and Accurate Trace Reconstruction in DNA Data Storage |
| [qinyunnn/RobuSeqNet](https://github.com/qinyunnn/RobuSeqNet) | none declared | 7 | 2023-10-30 | Robust Sequence Reconstruction from Contaminated Clusters Using Deep Neural Network for DNA Storage |
| [quanguo2088/Approaching-single-molecule-data-readout-for-DNA-Storage](https://github.com/quanguo2088/Approaching-single-molecule-data-readout-for-DNA-Storage) | MIT | 3 | 2026-07-21 | Here we provide the code of the approaching single-molecule data readout for DNA storage. |
| [ramy-khabbaz/MGCP](https://github.com/ramy-khabbaz/MGCP) | MIT | 6 | 2026-09-11 | Python implementation of MGC+ channel coding for binary and DNA data storage, including encoding, decoding,... |
| [RattleyCooper/dNa](https://github.com/RattleyCooper/dNa) | MIT | 1 | 2021-09-19 | Deflate Nim Archive |
| [reinhardh/dna_data_storage](https://github.com/reinhardh/dna_data_storage) | Apache-2.0 | 14 | 2017-07-27 | Error correction scheme for storing information on DNA |
| [remi-moreau/dna-storage-pipeliner](https://github.com/remi-moreau/dna-storage-pipeliner) | none declared | 1 | 2026-08-14 | A pipelining engine to provide functional and versatile dna storage pipelines. |
| [remi-moreau/dspl-quick-start](https://github.com/remi-moreau/dspl-quick-start) | none declared | 1 | 2026-08-14 | Quick start setup with plotting scripts for data analysis produced by remi-moreau/dna-storage-pipeliner |
| [romaingrx/dna-pcc-master-thesis](https://github.com/romaingrx/dna-pcc-master-thesis) | none declared | 1 | 2022-06-17 | Master thesis on point cloud compression for DNA based storage |
| [rrwick/Badread](https://github.com/rrwick/Badread) | GPL-3.0 | 305 | 2026-07-24 | a long read simulator that can imitate many types of read problems |
| [rzy0901/My_DNA_fountain](https://github.com/rzy0901/My_DNA_fountain) | none declared | 3 | 2022-01-05 | Information Theory Homework in SUSTech. |
| [SampleBias/bi0cyph3r](https://github.com/SampleBias/bi0cyph3r) | MIT | 4 | 2026-09-17 | bi0cyph3r DNA cryptography: encode and decode messages as DNA sequences. |
| [Scilence2022/DNA-Fountain-Shadow](https://github.com/Scilence2022/DNA-Fountain-Shadow) | NOASSERTION | 1 | 2026-04-22 | unknown (no description) |
| [shaoqi7818/HSFC](https://github.com/shaoqi7818/HSFC) | none declared | 1 | 2024-06-01 | HSFC: Hash sketches fuzzy clustering for reliable DNA storage data reconstruction |
| [shivendrra/biosaic](https://github.com/shivendrra/biosaic) | MIT | 2 | 2025-05-17 | Tokenizer for encoding/decoding dna sequences |
| [shubhamchandak94/LDPC_DNA_storage_data](https://github.com/shubhamchandak94/LDPC_DNA_storage_data) | none declared | 2 | 2019-11-13 | LDPC codes for Illumina sequencing-based DNA storage - Data |
| [shubhamchandak94/nanopore_dna_storage_data](https://github.com/shubhamchandak94/nanopore_dna_storage_data) | none declared | 3 | 2019-10-11 | Convolutional coding and basecaller-decoder integration for nanopore sequencing based DNA storage - data |
| [shubhamsrivast4u/IDS_single_read_DNA](https://github.com/shubhamsrivast4u/IDS_single_read_DNA) | none declared | 2 | 2025-05-03 | code for the paper "NEURON-IDS: One-Shot Neural Error Correction for DNA Storage Sequences" |
| [shubhranshsinghvi/Clustering-Billions-of-Reads-for-DNA-storage](https://github.com/shubhranshsinghvi/Clustering-Billions-of-Reads-for-DNA-storage) | none declared | 1 | 2023-03-27 | unknown (no description) |
| [shulp2211/hedges](https://github.com/shulp2211/hedges) | none declared | 0 | 2020-06-06 | HEDGES Error Correcting Code for DNA |
| [sm4006/DNA-DATA-STORAGE](https://github.com/sm4006/DNA-DATA-STORAGE) | none declared | 1 | 2026-05-18 | https://sm4006.github.io/DNA-DATA-STORAGE/ |
| [sm4006/GENESTACK](https://github.com/sm4006/GENESTACK) | none declared | 1 | 2026-04-10 | GeneStack is a DNA data storage research platform that encodes digital files into DNA sequences, enables lossless... |
| [Sombiri/DNAsmart](https://github.com/Sombiri/DNAsmart) | BSD-3-Clause | 3 | 2024-09-03 | DNAsmart: DNA Storage Multi-Attribute Ranking Tool |
| [soneylegal/Blood-Ledger](https://github.com/soneylegal/Blood-Ledger) | none declared | 2 | 2026-04-25 | Formal framework for DNA-based data storage, focusing on reliability engineering, error-bound modeling, and metabolic... |
| [soneylegal/bloodledgersourcecode](https://github.com/soneylegal/bloodledgersourcecode) | NOASSERTION | 1 | 2026-09-02 | Formal framework for DNA-based data storage, focusing on reliability engineering, error-bound modeling, and metabolic... |
| [SRINIVASTA/silicon-to-dna](https://github.com/SRINIVASTA/silicon-to-dna) | MIT | 1 | 2026-08-26 | An interactive DNA Digital Data Storage simulator built with Streamlit. Encodes digital assets into biological base... |
| [sugatoray/genespeak](https://github.com/sugatoray/genespeak) | MIT | 14 | 2022-11-21 | A library to encode text as DNA and decode DNA to text. |
| [sunghunbae/decode](https://github.com/sunghunbae/decode) | MIT | 8 | 2020-03-26 | Decode NGS data from DNA-Encoded Library Screen |
| [Surya2709/DNA-cryptography-in-cloud-storage](https://github.com/Surya2709/DNA-cryptography-in-cloud-storage) | MIT | 3 | 2021-04-22 | Encryption and decryption of confedential data using dna based cryptography in cloud storage |
| [talfig/DNA-Fountain](https://github.com/talfig/DNA-Fountain) | none declared | 4 | 2025-01-25 | DNA fountain encoding algorithm |
| [Taltalite/Chamaeleo_HEDGES](https://github.com/Taltalite/Chamaeleo_HEDGES) | MIT | 1 | 2021-09-23 | unknown (no description) |
| [ThreeE999/DNA_fountain_py3](https://github.com/ThreeE999/DNA_fountain_py3) | none declared | 2 | 2021-06-09 | Python3 version DNA fountain code |
| [thupchnsky/ModifiedBasesAnalysis](https://github.com/thupchnsky/ModifiedBasesAnalysis) | MIT | 3 | 2022-08-06 | Official implementation of paper "Expanding the Molecular Alphabet of DNA-Based Data Storage Systems with Nanopore... |
| [TJU-QiGe/Soft-decision-data-readout-for-DNA-storage](https://github.com/TJU-QiGe/Soft-decision-data-readout-for-DNA-storage) | MIT | 8 | 2026-07-21 | Here we provide the code of the soft-decision data readout pipeline used for encoded large DNA. |
| [TJU-QiGe/Two-stage-composite-letter-detection-method-using-set-partitioning](https://github.com/TJU-QiGe/Two-stage-composite-letter-detection-method-using-set-partitioning) | MIT | 1 | 2026-07-21 | Here, we provide the code for the composite letter detection pipeline used in the composite DNA storage system. |
| [tuxity/dnacoder](https://github.com/tuxity/dnacoder) | GPL-3.0 | 1 | 2024-10-10 | A Golang package for encoding and decoding data into DNA sequences |
| [umr-ds/DR4DNA](https://github.com/umr-ds/DR4DNA) | AGPL-3.0 | 2 | 2024-11-28 | unknown (no description) |
| [umr-ds/OFC4DNA](https://github.com/umr-ds/OFC4DNA) | AGPL-3.0 | 1 | 2024-06-05 | unknown (no description) |
| [umr-ds/RepairNatrix](https://github.com/umr-ds/RepairNatrix) | MIT | 3 | 2023-04-14 | unknown (no description) |
| [upmem/usecase_hedges](https://github.com/upmem/usecase_hedges) | MIT | 0 | 2022-08-24 | HEDGES inner + RS/LDPC outer pipeline running on UPMEM DPU processing-in-memory (repo description). Not read. |
| [uwmisl/cas9-similarity-search](https://github.com/uwmisl/cas9-similarity-search) | BSD-3-Clause | 1 | 2025-05-15 | DNA similarity search using Cas9 (data-in-DNA retrieval). |
| [uwmisl/NanoporeTERs](https://github.com/uwmisl/NanoporeTERs) | none | 8 | 2021-01-20 | Nanopore-readable tags (TERs) code/data; not a storage codec. |
| [uwmisl/Porcupine](https://github.com/uwmisl/Porcupine) | NOASSERTION | 7 | 2021-01-22 | Molecular tagging of physical objects with DNA (not storage codec). |
| [uwmisl/primo-similarity-search](https://github.com/uwmisl/primo-similarity-search) | none declared | 4 | 2021-07-08 | Code and models for the primo similarity search project |
| [v1t3ls0n/DNA-fountain](https://github.com/v1t3ls0n/DNA-fountain) | none declared | 1 | 2025-02-06 | DNA Fountain Algorithm |
| [VanLoo-lab/DNAStream](https://github.com/VanLoo-lab/DNAStream) | none declared | 1 | 2026-03-12 | DNAStream is an HDF5-based data structure for efficient storage, indexing, logging and retrieval of processed DNA... |
| [WeAreFlowsta/flowsta-private-dna](https://github.com/WeAreFlowsta/flowsta-private-dna) | Apache-2.0 | 10 | 2026-10-04 | Holochain DNA for encrypted personal data storage with zero-knowledge access |
| [whoashish115/gene-archive](https://github.com/whoashish115/gene-archive) | MIT | 10 | 2025-05-13 | Gene Archive is a Python command-line DNA data storage simulator that encodes files into DNA-like sequences, simulates... |
| [wowinter13/dnacodec-rs](https://github.com/wowinter13/dnacodec-rs) | Apache-2.0 | 0 | 2026-08-04 | Rust codec: LDPC inner with belief propagation (2026, unreviewed) |
| [YANKEESEAN/DNA-storage-paper-presentation](https://github.com/YANKEESEAN/DNA-storage-paper-presentation) | none declared | 1 | 2026-07-18 | DNAformer: A scalable and robust DNA storage system combining deep learning and coding theory. 3,200 faster and 40%... |
| [yihangdu/dna_fountain_modify](https://github.com/yihangdu/dna_fountain_modify) | GPL-3.0 | 1 | 2022-04-23 | unknown (no description) |
| [yiliangw/marker-code-tr](https://github.com/yiliangw/marker-code-tr) | MIT | 1 | 2023-01-07 | Python implementation for DNA trace reconstruction with marker code |
| [ylu1997/Code_Generation_For_DNA_Storage](https://github.com/ylu1997/Code_Generation_For_DNA_Storage) | MIT | 1 | 2025-03-14 | Codebook Generation Scheme for Error Correction Code in DNA Storage |
| [yo-tam/DNA-Data-Storage](https://github.com/yo-tam/DNA-Data-Storage) | MIT | 5 | 2023-12-19 | Single Read Reconstruction for DNA Data Storage Using Transformers (official implementation) |
| [zhangjitaoBGI/DNA-storage-based-on-reference-genome](https://github.com/zhangjitaoBGI/DNA-storage-based-on-reference-genome) | Apache-2.0 | 3 | 2021-11-27 | A DNA storage scheme based on genome sequence to store image files in DNA |
| [zhengzangw/Deep-Consensus-Finding](https://github.com/zhengzangw/Deep-Consensus-Finding) | MIT | 4 | 2021-12-05 | Deep neural network to find the consensus of clustering in DNA storage system |
| [ZihuiYan/SPaRe-DNA](https://github.com/ZihuiYan/SPaRe-DNA) | Apache-2.0 | 1 | 2026-04-08 | Semantic parity redundancy in latent space for DNA storage of visual data |
| [zlice/SNA](https://github.com/zlice/SNA) | GPL-2.0 | 2 | 2018-06-08 | DNA storage encode and decode |
