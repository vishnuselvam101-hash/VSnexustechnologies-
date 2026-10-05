# Cluster 2 - GitHub audit: BGI / Yin-Yang ecosystem, fountain codes, classic codecs, newer repos

Access date: 2026-10-05. Companion data: `20-repos-cluster2.json` (165 records: 157 GitHub repos catalogued + 8 "NO PUBLIC CODE FOUND" records). Clones for code reading: `/root/vnx-dna-lab/research-2026-10-05/repos/` (shallow, never copied into VNX). Vault check: `/root/VNX-Vault/wiki/concepts/DNA storage methods to evaluate.md` only lists RS / soft-decision / hyperbolic bound / mediation analysis from an advisory-model note; nothing on these repos exists yet.

Evidence labels used throughout: **[code read]** = I read the source; **[ran]** = I executed it; **[README]** = repo text only; **[abstract]** = paper abstract (OpenAlex/PMC/arXiv); **[search]** = web-search summary, not re-verified. WET-LAB = results others report from physical experiments. SIM = simulation.

## 0. Method and counts

* Discovery: ~790 raw GitHub hits from ~45 `search/repositories` queries (name/description/topics/readme, topics dna-storage, dna-data-storage, dna-computing, molecular-storage, etc.), plus org/user listings (BGI-SynBio, ntpz870817, HaolingZHANG, umr-ds, fml-ethz, dna-storage, uwmisl, MLI-lab, HKU-BAL, TJU-QiGe, dna-storage-lab, Guanjinqu) and links found inside dt4dds-benchmark and UNACORM READMEs. Most raw hits are name collisions (dnaCenter, Chamaeleon web builders, game "DNA" mods).
* Catalogued with metadata (stars, forks, licence, languages, HEAD sha/date): 157 GitHub repos relevant or borderline. Audited "in-depth": 34 (of which about 14 were code-read or executed by me; the rest README + paper abstract). The remaining 123 are shallow (GitHub metadata + description) and marked `audit_depth: shallow`.
* The search API is relevance/keyword based; I cannot claim to have seen every repo. Repos with <3 stars were mostly kept only if they are DNA-storage specific.
* "Last meaningful commit": for in-depth repos I distinguish code commits from README/dependabot commits (e.g. TeamErlich/dna-fountain HEAD 2025-07-08 is README only; code last changed 2016-09-09).

## 1. Short answer

1. There is an existing, MIT-licensed multi-codec framework from BGI (Chamaeleo), but it is a mapping-level toolkit: 7 mappers, 2 trivial ECCs (Hamming, RS), no read-level IDS model, no clustering, no consensus. It is 4+ years stale and its Yin-Yang class fails on Python 3.12 (I ran it).
2. The serious, current benchmark infrastructure is elsewhere: **fml-ethz/dt4dds-benchmark** (ETH, GPL-3.0; 6 default codecs + DBGPS/LDPC/Modulation wrappers, 6 clusterers, full pipeline with IDS/dropout/coverage workflows) and **AAnzel/UNACORM** (Marburg/Hattab, GPL-3.0; 8 codecs, arXiv Aug 2026). Both accept an external codec through a thin wrapper (shell-script pair or Python wrapper), so VNX can plug in without any code being shared.
3. Public code exists for a surprisingly narrow slice of the literature: DNA Fountain, HEDGES, DNA-Aeon, NOREC4DNA, YYC, SPIDER-WEB, DNA-RS (Grass/Heckel), Derrick, StairLoop, Gungnir, DBGPS, nanopore convolutional/LDPC. Church 2012, Goldman 2013, Blawat 2016, Bornholt 2016, Organick 2018, Anavy 2019: no official codec found (only re-implementations or data).

## 2. BGI / Yin-Yang ecosystem (special audit)

### 2.1 Who owns what
| Repo | Real owner | Licence | Stars | HEAD | Notes |
|---|---|---|---|---|---|
| ntpz870817/Chamaeleo | personal account of BGI's Zhi Ping (ntpz870817); org mirror BGI-SynBio/Chamaeleo (0 stars) | MIT | 46 | 54bdf37bef 2022-02-22 (docs); last code fix 23724477ae 2021-12-01 | PyPI Chamaeleo 1.34 (2021-12-01) |
| ntpz870817/DNA-storage-YYC | same | MIT | 27 | cc8ad67fe1 2024-05-06 (readme); yyc/ last changed 58758c129d 2021-06-02 | older home of YYC, pip `yyc` |
| BGI-SynBio/YinYangCode | BGI-SynBio org | MIT | 4 | f1dedbe9b7 2025-10-23 (regex fix in validity) | newer YYC repo, README refs Nat Comput Sci paper |
| HaolingZHANG/DNASpiderWeb | personal account of Haoling Zhang (BGI) | custom "BGI-Research Shenzhen General Open-source Licence v1.0" (LICENSE.pdf; GitHub says NOASSERTION) | 11 | f6d6311911 2024-06-21; dsw/ last 09365fed67 2023-03-06 | mirror Guanjinqu/DNASpiderWeb says GPL-3.0 (older copy) |
| HaolingZHANG/pyHEDGES | same | GPL-3.0 | 5 | abb3a9a9db 2022-04-20 | port of whpress/HEDGES (MIT, C++) |
| BGI-SynBio/YYC-FileVersionControl, DNA-BF | BGI-SynBio | none declared | 1 each | 2023-11 / 2024-04 | not audited; YYC-FileVersionControl is conceptually near VNX versioning |
There is no "DNA-storage-YYC" org-level BGI repo besides these; Zhang's page github.com/HaolingZHANG lists no other DNA-storage code.

### 2.2 Chamaeleo
* **Problem**: "integrated evaluation platform for DNA storage" - one place to run several classical bit-to-base transcoders and compare them. Paper: Ping et al., Synthetic Biology Journal 2020; English preprint https://www.biorxiv.org/content/10.1101/2020.01.02.892588v3 (abstract only fetched: states it collects "several classical coding schemes", "better flexibility and expandability than original packages"; it did not list codecs or numbers in the abstract).
* **Codecs wrapped [code read]**: Base (2 bit/nt), Church 2012, Goldman 2013, Grass 2015, Blawat 2016, DNA Fountain 2017 (LT with screening), Yin-Yang 2022. ECC: Hamming, Reed-Solomon (`reedsolo`). HEDGES is NOT inside (a student fork Taltalite/Chamaeleo_HEDGES exists, 1 star).
* **Constraints**: `utils/screen.py` checks max homopolymer and max GC; DNAFountain default homopolymer 4, GC 0.5+0.2; YYC default 4 / 0.6.
* **Metrics [code read, pipelines.py]**: information density (bit_size/total nt), encode/decode time, "error rate"/"recover rate" after random mutation, GC histogram, share of strands with GC 40-60%, max GC bias, max homopolymer. Pipelines: Transcode, Robustness, BasicFeature, OptimalChoice.
* **Error model**: toy. Picks a fraction of sequences and applies one random sub/ins/del each; no dropout, coverage, reads, clustering or consensus.
* **Decoding/ECC reality**: inner codes have no indel resynchronisation; the only protection is RS/Hamming on bit segments.
* **Performance**: no published throughput. My smoke test [ran]: 4000 random bytes, Python 3.12.3, Chamaeleo 1.34 from PyPI: Base, Goldman, Blawat and Church round-trip OK in 0.05-0.12 s; **YinYangCode raises `TypeError: 'float' object cannot be interpreted as an integer`** (random.randint with `math.pow` result, rejected by Python 3.12); Grass requires segment length divisible by 16; DNAFountain with my default arguments did not decode (likely my parameters, not investigated).
* **Licence**: MIT (c) 2019 BGI-Research.
* **Novelty**: low algorithmically (a collection), useful as a design: abstract codec interface + pipelines.
* **Physical validation**: level 1 (software). The wrapped codecs themselves have level-3 papers.
* **Could VNX plug in?** Yes, trivially: subclass `AbstractCodingAlgorithm`, implement `encode(bit_segments) -> list[list[str]]` and `decode(dna_sequences) -> bit_segments` (120-bit segments by default; indexing, RS/Hamming and metrics come from the pipeline). Caveats: (a) bit-segment/list-of-char API is slow and would mis-measure a native/SIMD codec, (b) it cannot test VNX's indel handling or dropout, (c) Python <=3.11 or a one-line patch needed. Verdict: use it only as a cheap *mapping-level density/GC/homopolymer comparison* (all of its numbers are reproducible in an afternoon); use dt4dds-benchmark for real evaluation.

### 2.3 Yin-Yang codec (YYC) - repos above
* **Problem**: earlier mappers pursued density at the price of biochemical compatibility or decoding failure (abstract). YYC combines two binary segments per strand through rule tables (1,536 rule combinations) so each nt carries 2 bits yet constraints hold.
* **Encoding strategy [code read yyc/scheme.py]**: split data into "good/bad" segment sets, pair segments, search rule/matrix combinations (`search_count`), validate each strand with `validity.check` (homopolymer <=4, GC 40-60%, optional minimum free energy via external folding call). Decoding needs the pickled rule model (virtual nucleotide A).
* **ECC**: not in the repo. Paper [abstract + PMC summary]: Reed-Solomon outer code (16-bit in vitro, 24-bit in vivo) with ~25-30% logical redundancy; colony voting for in-vivo.
* **Performance (paper, via PMC text, not re-verified)**: up to ~1.95 bit/nt coding potential, 1.75-1.78 bit/nt observed across file types; 10,103 oligos x 200 nt in vitro, 99.9% recovery above 1e4 copies and 87.53% at <=1e2 copies; ~54 kbp stored in yeast, 432.2 EB/g, 38.9-95.0% per-colony recovery after ~1000 generations. WET-LAB (peer-reviewed, Nat Comput Sci 2022; co-author G. Church).
* **Limitations**: no indel correction in the codec itself (needs physical redundancy + RS); pure Python (~1.9k LOC, no tests); decoding depends on a pickled model; Python 3.12 bug in Chamaeleo copy (the standalone yyc package was not run by me).
* **Novelty**: constrained mapper with selectable rule sets; incremental over Goldman/DNA Fountain in density-with-constraints, not a new ECC idea.
* **Applicability**: good as a constrained-mapper baseline and as a wet-lab-validated reference; not an end-to-end archive system.

### 2.4 SPIDER-WEB (DNASpiderWeb)
* **Problem**: bit-to-base transcoding and error correction are usually separate algorithms, which raises complexity or weakens indel tolerance.
* **Strategy**: graph-based generation of customised coding algorithms under arbitrary local constraints (biofilter module), path-based correction, "path matching" saturation repair. Claims correction of up to 4% edit errors (substitutions + indels) at 5.5% logical redundancy, real-time retrieval of MB-level data with ~100x speed-up vs conventional methods, potential at exabyte scale [arXiv abstract/README, author claims].
* **Evidence**: arXiv:2204.02855 v3 (2023-03-30), 47 pages; **simulation only - the arXiv page reports no wet-lab work** (fetched). I found no peer-reviewed publication of it (searched 2026-10-05). Experiments are single-threaded and "may take several months on a laptop" (README).
* **Licence caveat**: custom BGI-Research licence (LICENSE.pdf): non-exclusive free use/modification/redistribution, but products containing it must be open-sourced and *commercialisation requires separate permission* from BGI; no use of BGI names for publicity. Not OSI. For VNX (a commercial effort) benchmarking internally is probably fine; embedding is not without a licence. Ask BGI.
* **Novelty**: high on paper (unifies mapping+correction), unproven physically. Relevant to VNX because it competes with an "indel-aware inner code" design.

### 2.5 pyHEDGES
Python port of HEDGES (Press et al., PNAS 2020; in silico + synthesized DNA, abstract: corrects insertions, deletions, substitutions, supports sequence constraints, extrapolates to error-free recovery at up to 10% errors). Single file, GPL-3.0, slow. For benchmarking use the original **whpress/hedges** (MIT, C++, HEAD 86812c5049 2024-11-04). Note dt4dds-benchmark states the old Python-C++ version it needs was purged upstream and installs from the fork shulp2211/hedges (no licence, 0 stars).

## 3. TeamErlich / DNA Fountain and derivatives
* **TeamErlich/dna-fountain** (GPL-3.0, 163 stars, 53 forks): Python 2 + Cython reference of Erlich & Zielinski, Science 2017 (WET-LAB, peer-reviewed). LT droplets with robust soliton, rejection screening for GC/homopolymer, 4-byte seed, RS(2) per oligo, 7% redundancy default (`--alpha 0.07`); example encodes a 2.1 MB tar.gz into 72,000 oligos [README; search for paper numbers: 72,000 oligos, 2.14 MB, 215 PB/g, 7% redundancy]. No indel handling; indel-bearing reads are discarded; decode uses abundance-sorted exact reads ("Naive" clusterer in dt4dds-benchmark). Code last changed 2016-09-09; the only later commits are README notes pointing to a Python 3 port.
* **jdbrody/dna-fountain** (GPL-3.0, 11 stars, ARCHIVED, last 2023-06-29): Python 3 port by Du/Wu/Brody; the version used by dt4dds-benchmark.
* Dozens of unlicensed student ports (jeter1112/dna-fountain-simplified 22 stars, no licence; ThreeE999, mythflipped, talfig, v1t3ls0n, yihangdu...). Not safe for a harness.
* **NOREC4DNA** (umr-ds, Marburg; AGPL-3.0; 12 stars; HEAD 51f9660970 2025-10-13): LT, Online and Raptor (RU10) fountain codes with DNA rules and the MOSLA/MESA simulation API, plus an optimiser (`find_minimum_packets`) that selects low-error-probability packets; BMC Bioinformatics 2021 (simulation-heavy). "Dorn" is the example input file, not an algorithm.
* **DNA-Aeon** (MW55, MIT, 20 stars): arithmetic-coding inner code with CRC sync markers + stack decoder (handles sub/ins/del), outer NOREC4DNA Raptor for strand loss; ConstrainedKaos (Java, MIT, 8 stars) generates constrained codebooks. Nat Commun 2023; in vitro tests per abstract. This is the strongest *public* indel-capable codec with constraints and a working repo; wrapped by both benchmark harnesses.
* **MESA** (umr-ds/mesa_dna_sim, AGPL-3.0, 17 stars): Dockerised Flask service simulating synthesis/storage/sequencing/PCR errors; heavy to run in a loop.
* OFC4DNA, DR4DNA (umr-ds, AGPL, 1-2 stars): sibling projects; purpose not verified.

## 4. Classic and other codecs: where the code is
| Algorithm | Public code | Notes |
|---|---|---|
| Church 2012 | NO OFFICIAL CODE FOUND | re-implementations in Chamaeleo, Storage-D, UNACORM (ke-pm/DNA-Encoding) |
| Goldman 2013 | NO OFFICIAL CODE FOUND | allanino/DNA (MIT, 50 stars, Python 2, last 2015) is the de facto port |
| Grass 2015 (ETH RS + silica) | reinhardh/dna_data_storage (Apache-2.0, 14 stars, 2017) | wet-lab with ageing; successor reinhardh/dna_rs_coding (Apache-2.0, 37 stars, 2021) |
| Blawat 2016 | NO PUBLIC CODE FOUND (only Chamaeleo class) | |
| Bornholt 2016 | NO OFFICIAL CODE FOUND | StausWimbes/XOR_based_DNA_encoding (UNACORM), tiny student repos |
| Organick 2018 | DATA ONLY uwmisl/data-nbt17 (12 stars, no licence) | no codec |
| Erlich 2017 | TeamErlich/dna-fountain | see 3 |
| Anavy 2019 composite DNA | NO PUBLIC CODE FOUND | 2026 follow-up code at TJU-QiGe (MIT) |
| Antkowiak 2020 (photolithographic) | reinhardh/dna_rs_coding + fml-ethz/dt4dds-challenges | wet-lab |
| Wukong | DNAstorage-iSynBio/Storage-D (MIT, 5 stars) | iMeta 2024, claims ~1.98 bit/nt coding potential, in-vitro 200-nt pool |
| DNA-QLC | Larissa-11/DNA-QLC (no licence, 3 stars) | BMC Genomics 2024; image VAE + Levenshtein code |
| Derrick | wushigang2/derrick (MIT, 1 star) | NSR 2023; in vitro ONT 7x |
| SnakeEngine, DNAMapper, "DNAsimulator" | could not identify any DNA-storage tool under these names | do not cite without a reference |
| "Dorn" | test file in NOREC4DNA, not an algorithm | |
| DBG-based (DBGPS) | Scilence2022/DBGPS_Python (GPL-3.0, 5 stars) | Nat Commun 2022 |
| MESA | umr-ds/mesa_dna_sim | see 3 |
| DeSP | WangLabTHU/DeSP (MIT, 12 stars) | BMC Bioinf 2022, validated against in vitro |
| DT4DDS | fml-ethz/dt4dds (GPL-3.0, 12 stars) | Nat Commun 2023, fitted on 40 sequencing experiments |
| Badread | rrwick/Badread (GPL-3.0, 305 stars) | general ONT read simulator, not oligo-pool specific |
| Clover | Guanjinqu/Clover (GPL-3.0, 19 stars) | not Microsoft; Microsoft/UW clustering has no official code found |
| dnaio | marcelm/dnaio (MIT, 71 stars) | general FASTQ I/O; irrelevant to codecs |
| DNAformer | itaiorr/Deep-DNA-based-storage (MIT, 23 stars) | datasets "on request" |

## 5. Newer (2023-2026) repos worth knowing
* **HKU-BAL/Gungnir** (BSD-3, Go, 13 stars): proof-of-work/hash-guided correction of sub/ins/del, nanopore-aware constraints; "complete recovery from a single copy with 20% erroneous bases" is **in silico** (Nat Commun 2026 / bioRxiv 2025).
* **Guanjinqu/StairLoop** (GPL-3.0, 5 stars): conv+LDPC with BCJR, MPI decoder; Nat Commun 2025, in vitro with electrochemical synthesis: >6% error or >30% dropout within a block at <3x depth (abstract).
* **jeplb/mahoraga-codec** (PolyForm Noncommercial 1.0.0, pure Python, 2026): soft-decision profile-HMM + LDPC/OSD + RS(GF 2^16). arXiv 2604.20810; all numbers are single-author **simulation** on the DT4DDS channel (155.8 / 25.9 EB/g; 282 years projected). Not peer reviewed. Licence blocks for-profit internal use without a licence, so NOT benchmarkable by VNX as is.
* **SSL-ACTX/helix** (AGPL-3.0, Rust, 3 stars): full systems stack in simulation (Zstd, XChaCha20-Poly1305, RS erasure, rotating trellis, primers, streaming). README itself says it is not physically validated. Closest in architecture to what VNX describes; claims unverified.
* **Mr-PU/dnastore** (Apache-2.0, 1 star): object-store API with append-only versioning over a simulator; fountain/rotating/naive codecs; plugin entry points.
* **aeonscript-spec/aeonscript** (spec v0.1 draft, 1 star): proposed 7-layer open standard; no evidence.
* Reconstruction/clustering: microsoft/TrellisBMA (MIT, archived), GZHoffie/bbs (Rust, MIT), MLI-lab/TReconLM (transformer, no licence), yo-tam/DNA-Data-Storage (MIT), RobuSeqNet (no licence), GradHC (no licence), Clover.
* Nanopore/soft decoding: shubhamchandak94/nanopore_dna_storage (MIT), dna-storage/hedges-soft-decoder, PParkJy/DeCoBase, TJU-QiGe soft-decision readout (MIT; Briefings Bioinf 2025).
* Constrained mapping: microsoft/DNABoundedHomopolymerEncoding (MIT; README claims ~50/80 Mbps enc/dec per core - not measured by me), MGCP, minminlittleshrimp/helix.
* Benchmarks/simulators: dt4dds, dt4dds-challenges, DeSP, MESA, DNArSim, DNATerra, prongs1996/DNAStorageToolkit.
* No repo found for: deep-learning *codecs* with public weights besides DNAformer/TReconLM; encryption-focused DNA storage beyond toys (Storage-D "codec pin", helix AEAD).

## 6. Algorithm comparison (what is reported, not re-measured)
nt-per-byte = 8 / bits-per-nt (computed by me). "Level": 1 software only, 2 in-vitro synthesis, 3 sequencing experiments, 4 full end-to-end archival incl. storage/ageing/retrieval. Stated physical level reflects the cited paper only.

| Project (year) | Mapping | ECC | Indel handling | Reported bits/nt (nt/byte) | GC | Homopolymer | Level |
|---|---|---|---|---|---|---|---|
| Church 2012 | binary, 1 bit/nt (A/C=0,G/T=1) | none | none | 1.0 (8.0) per YYC table [search] | not controlled | not controlled | 3 |
| Goldman 2013 | Huffman -> rotating ternary | 4x overlap + parity trit | none (redundancy) | <=1.585 raw max (log2 3) | not controlled | none by construction | 3 |
| Grass 2015 | RS-oriented mapping | RS (2D) | none | ~0.84 (9.5) computed from abstract 83 kB / 4991x158 nt | unknown | unknown | 4 (accelerated ageing in silica) |
| Blawat 2016 | unknown | forward error correction | unknown | not extracted | unknown | unknown | unknown |
| Bornholt 2016 | XOR-based redundancy encoding | XOR | none | not extracted | unknown | unknown | 3 (151 kB wet lab + sim) |
| Erlich 2017 DNA Fountain | LT droplets + screening | fountain + RS(2) | none (discard) | 1.57 (5.1) per YYC table [search]; 7% redundancy | 45-55% example | <=3 example | 3 |
| Organick 2018 | (not retrievable) | | | | | | 3 |
| HEDGES 2020 | hash-based nt code | HEDGES + outer RS | yes (sub/ins/del) | code-rate dependent; not extracted | windowed | avoids repeats | 3 (in silico + synthesized) |
| Antkowiak 2020 | RS + reconstruction | RS | via reconstruction | not extracted | unknown | unknown | 3 |
| YYC 2022 | 2 rules x 2 bits | RS outer (paper) | none inside codec | 1.95 coding potential, 1.75-1.78 observed (4.1-4.6) [PMC] | 40-60% | <=4 | 3 (+in vivo yeast) |
| DBGPS 2022 | fountain + de Bruijn assembly | fountain/EC | yes (assembly) | not extracted | unknown | unknown | 3 |
| NOREC4DNA 2021 | LT/Online/Raptor + rules | fountain (+RS) | none | not extracted | rules | rules | 1 |
| DNA-Aeon 2023 | arithmetic coding on constrained codebook | Raptor + CRC sync | yes (stack decoder) | not extracted | user window | user limit | 3 |
| Derrick 2023 | randomised + CRC64 | RS + soft decision | via consensus | not extracted | unknown | unknown | 3 (ONT 7x) |
| Wukong/Storage-D 2024 | Wukong | RS | none noted | ~1.98 coding potential (4.04) | selectable | selectable | 3 |
| StairLoop 2025 | conv + LDPC | LDPC+conv | yes (BCJR) | not extracted | unknown | unknown | 3 |
| DNAformer 2025 | 2 bit/nt + TP codes | tensor-product | DNN reconstruction | 1.6 (5.0) high-noise regime [preprint abstract] | unknown | unknown | 3 |
| SPIDER-WEB (preprint) | constraint graph | built-in | yes (4% edit at 5.5% redundancy) | not extracted | local filter | local filter | 1 |
| Gungnir 2026 | hash-signature + search | none/low | yes (20% errors, single copy, in silico) | not extracted | yes | yes | 1 |
| mahoraga 2026 (preprint) | 126-nt soft decision | LDPC+OSD+RS(2^16) | HMM alignment | EB/g figures only | unknown | unknown | 1 |
| Nanopore conv. code (Chandak) | convolutional | conv + Viterbi | basecaller-integrated | not extracted | unknown | unknown | 3 |
| Anavy 2019 composite DNA | composite letters | unknown | n/a | ~25% capacity gain [search] | n/a | n/a | 3 |

## 7. Best benchmark targets for VNX
Ordered by value for a local harness (licence check in JSON `benchmarkable`):
1. **fml-ethz/dt4dds-benchmark** + **fml-ethz/dt4dds** (GPL-3.0): the only public harness with calibrated experimental workflows (synthesis, PCR, ageing, SBS), dropout/coverage/IDS, 6 clusterers, HDF5 sweeps, runtime limits. Adding a VNX codec = `encode.sh`/`decode.sh` (text-in/text-out) + a dataclass. Requirements: Python 3.10, Docker image `agimpel/dt4dds-benchmark`. VNX would be an external executable: no GPL contamination as long as nothing is linked or copied.
2. **AAnzel/UNACORM** (GPL-3.0): ready-made 8-codec comparison on 9 baseline files with 5 runs each; aligns metrics with the DNA Data Storage Alliance; headline conclusion "no single codec wins". Cheaper entry point, weaker channel model (MESA or a toy simulator), codecs are student re-implementations for Church/Grass/Bornholt/Goldman.
3. **Codecs to run inside them**: DNA-Aeon (MIT), HEDGES (whpress, MIT), DNA Fountain (jdbrody port, GPL), DNA-RS (reinhardh, Apache), YYC (BGI-SynBio, MIT), NOREC4DNA Raptor/LT (AGPL: subprocess only), DBGPS (GPL), LDPC (MIT), Derrick (MIT), Gungnir (BSD), StairLoop (GPL).
4. **Chamaeleo**: only as a quick mapping-level baseline (density, GC, homopolymer for Church/Goldman/Blawat/YYC/Fountain); do not rely on it for robustness.
5. **Datasets**: uwmisl/data-nbt17, storage-biasing-ncomms20, nanopore_dna_storage_data, dt4dds-benchmark `input_files/`, UNACORM `Data/Original` (9 files). Check each dataset's licence before redistribution (most have none declared).
6. **Not benchmarkable as-is**: mahoraga (PolyForm NC), repos with no licence (TReconLM, GradHC, RobuSeqNet, uwmisl data), SPIDER-WEB for anything beyond internal evaluation.

## 8. Insights for VNX
1. Differentiation must be on IDS-aware, read-level decoding plus system features; plain mappers (Church/Goldman/YYC/Fountain) are commodity and already in three benchmark frameworks.
2. dt4dds is the de facto credibility standard for simulation-based claims; mahoraga's preprint already benchmarks on it. Any VNX simulated result should be reproduced on dt4dds-benchmark and labelled SIM.
3. Strong wet-lab evidence concentrates in a few labs (Grass/Heckel ETH, Church/BGI, Erlich, Microsoft/UW, DNA-Aeon Marburg, StairLoop, Derrick). Most new GitHub projects (helix, dnastore, aeonscript, mahoraga) are simulation-only, small, and self-assessed.
4. Licences: a mix of GPL/AGPL (run unmodified, no copying), a custom BGI licence for SPIDER-WEB (commercialisation needs permission), PolyForm NC for mahoraga, and many unlicensed repos. Keep VNX integration at the process boundary.
5. Maintenance risk: Chamaeleo (2022), TeamErlich (2016 code), Goldman port (2015), TrellisBMA (archived), HEDGES old binding (purged) - old harness numbers are not reproducible without pinning environments.

## 9. Gaps and caveats
* I did not run any codec except Chamaeleo's smoke test; performance numbers above are authors' claims.
* YYC paper numbers were taken from a fetched PMC page via a summariser; verify against the PDF before external citation.
* DNAformer journal: web-search summary says Nature Machine Intelligence, Feb 2025; I could not open the journal page.
* Paper-level facts for Organick 2018, Blawat 2016, Anavy 2019, Antkowiak, Goldman were not retrievable in full (OpenAlex abstracts empty or secondary summary only).
* Licence reading is not legal advice.

## 10. Sources (access 2026-10-05)
* https://github.com/ntpz870817/Chamaeleo ; https://pypi.org/project/Chamaeleo/ ; https://www.biorxiv.org/content/10.1101/2020.01.02.892588v3 (preprint)
* https://github.com/BGI-SynBio/YinYangCode ; https://github.com/ntpz870817/DNA-storage-YYC ; https://doi.org/10.1038/s43588-022-00231-2 ; https://pmc.ncbi.nlm.nih.gov/articles/PMC10766522 (peer-reviewed)
* https://github.com/HaolingZHANG/DNASpiderWeb ; https://arxiv.org/abs/2204.02855 (preprint)
* https://github.com/HaolingZHANG/pyHEDGES ; https://github.com/whpress/hedges ; https://doi.org/10.1073/pnas.2004821117 (peer-reviewed)
* https://github.com/TeamErlich/dna-fountain ; https://github.com/jdbrody/dna-fountain ; https://doi.org/10.1126/science.aaj2038 ; https://zenodo.org/record/889697
* https://github.com/umr-ds/NOREC4DNA ; https://doi.org/10.1186/s12859-021-04318-x ; https://github.com/MW55/DNA-Aeon ; https://doi.org/10.1038/s41467-023-36297-3 ; https://github.com/umr-ds/mesa_dna_sim ; https://doi.org/10.1093/bioinformatics/btaa140
* https://github.com/fml-ethz/dt4dds ; https://github.com/fml-ethz/dt4dds-benchmark ; https://doi.org/10.1038/s41467-023-41729-1 ; https://doi.org/10.1039/D4DD00220B
* https://github.com/AAnzel/UNACORM ; https://arxiv.org/abs/2608.09673 (preprint)
* https://github.com/itaiorr/Deep-DNA-based-storage ; https://arxiv.org/abs/2109.00031 ; https://doi.org/10.5281/zenodo.14266018
* https://github.com/HKU-BAL/Gungnir ; https://doi.org/10.1038/s41467-026-71485-x ; https://github.com/Guanjinqu/StairLoop ; https://doi.org/10.1038/s41467-025-64230-3
* https://github.com/jeplb/mahoraga-codec ; https://doi.org/10.48550/arxiv.2604.20810 (preprint) ; https://github.com/wushigang2/derrick ; https://doi.org/10.1093/nsr/nwad229
* https://github.com/DNAstorage-iSynBio/Storage-D ; https://doi.org/10.1002/imt2.168 ; https://github.com/WangLabTHU/DeSP ; https://doi.org/10.1186/s12859-022-04723-w
* https://github.com/Scilence2022/DBGPS_Python ; https://doi.org/10.1038/s41467-022-33046-w ; https://github.com/reinhardh/dna_rs_coding ; https://doi.org/10.1002/anie.201411378 ; https://doi.org/10.1038/s41467-020-19148-3
* https://github.com/allanino/DNA ; https://doi.org/10.1038/nature11875 ; https://doi.org/10.1126/science.1226355 ; https://doi.org/10.1145/2872362.2872397 ; https://doi.org/10.1038/nbt.4079 ; https://doi.org/10.1038/s41587-019-0240-x
* https://github.com/SSL-ACTX/helix ; https://github.com/Mr-PU/dnastore ; https://github.com/aeonscript-spec/aeonscript ; https://github.com/microsoft/TrellisBMA ; https://arxiv.org/abs/2107.06440 ; https://github.com/microsoft/DNABoundedHomopolymerEncoding
