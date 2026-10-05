# VNX-DNA competitive gap analysis

Date: 2026-10-05. Branch `work/research-gate`. Subject of every VNX statement: commit **081697b** (`build/v6-p1`
merge of `work/b03-jsonschema`), read from committed state only. Package version at that commit is still 5.0.0
(`git describe` v5.0.0-30). Released baselines: tag v4.0.0 → a358ae8, tag v5.0.0 → 0d1c285.

Inputs: research-gate raw notes `00-vnx-baseline-and-readiness.md` (the one source for VNX facts),
`10-repos-cluster1.{md,json}`, `20-repos-cluster2.{md,json}`, `30-companies.{md,json}`, `40-datasets.{md,json}`
(all dated 2026-10-05), plus repository docs at 081697b. Full repository detail lives in
`docs/GITHUB_ECOSYSTEM_MAP.md` and algorithm detail in `docs/ALGORITHM_COMPARISON.md`. Both are companion documents
from the same research gate.

**Scope statement.** VNX-DNA has never synthesised, stored, amplified or sequenced DNA. Every VNX codec number in
this document is **SIMULATED** (software strands through a software channel that is not fitted to any platform;
`docs/LIMITATIONS.md` "NOT PHYSICALLY VALIDATED"), or **THEORETICAL** where it is arithmetic from the format
specification.

## Legend

**Evidence labels for external claims**

| label | meaning |
|---|---|
| [AR-SIM] | author-reported in silico (simulation by the paper or repository authors; not reproduced) |
| [AR-WET] | author-reported wet-lab (physical synthesis and/or sequencing by the authors; not reproduced) |
| [AUDIT] | measured by this audit 2026-10-05 (the research-gate auditors built or ran the code themselves) |
| [README] | a repository's own statement; not verified, no experiment behind it seen |
| [PRESS] | vendor or press statement about a company, product, price or funding; not a technical result |
| [SPEC] | a published specification, standard or working draft |

**VNX labels.** SIMULATED (with repository path @081697b), THEORETICAL (arithmetic, labelled), PRESENT / PARTIAL /
ABSENT (capability inventory in the baseline note, Task B).

**Status colours** (used in every comparison table):

| colour | rule |
|---|---|
| GREEN | VNX has the capability **and** either a same-protocol benchmark exists, or the compared competitors demonstrably lack it (documented in the audit) |
| YELLOW | VNX has it partly, or has it but no same-protocol benchmark exists against the competitors that also have it |
| RED | VNX lacks it |
| BLACK | the competitor side is proprietary or unknown, so no comparison is possible |

No same-protocol benchmark between VNX and any external codec exists today. GREEN therefore appears only where an
audited competitor demonstrably lacks the capability. GREEN means "present where the audited competitor is absent".
It is not a performance ranking.

---

## 1 Executive summary

**What VNX has (all SIMULATED or software engineering facts, @081697b).** VNX is a software codec and archive format
with these parts:

* a VNX4 container: chunking, content-addressed dedup, zstd, per-chunk AES-256-GCM, an RFC 6962 Merkle tree, an
  authenticated manifest, and a 96-byte self-describing superblock (`docs/VNX4_FORMAT.md`);
* 313-nt strands carrying 3-nt sync markers every 24 nt, an inner RS(70,54) code with CRC-32, and an outer Cauchy RS
  64+16 code;
* a V6 opt-in product code (stripes + column parity, interleaved order, adaptive planner; `docs/V6_OUTER_CODE.md`);
* V5 smart indel recovery and soft GMD/Chase decoding behind an unchanged CRC + SHA-256 verifier;
* native C kernels (aligner, FASTQ parser, SIMD RS), each bit-exact against a Python reference, with differential
  fuzzing and sanitizers;
* a 14-model channel simulator and digital (index-based) random access;
* about 1,693 tests at the committed state. This is an estimate: no suite log exists for 081697b.

Simulated V6 headline: the i.i.d. dropout threshold rises from 0.07 to 0.16 at about equal overhead, with 0 false
SUCCESS in 1,900 decodes (`experiments/v6/phase1/summary.md`, SIMULATED).

**What VNX lacks.**

* Any physical evidence: physical-validation level 1 of 4.
* Channel models fitted to real data. All 14 models are "not fitted to any measured platform".
* PCR primers and molecular random access.
* An address space beyond about 11 TB per archive. This is THEORETICAL, from the 4-byte group index (§10).
* Indel-correcting inner codes, and multi-read trace reconstruction of the HEDGES, DNA-Aeon or TrellisBMA kind.
* Read clustering without addresses. Orphans reach 31 % at a harsh simulated channel.
* Any standards conformance: DNA Data Storage Alliance Sector Zero/One, or JPEG DNA.
* Any network or object API. Biomemory claims an S3 interface and works with Scality. dnastore has an object API.
  The Alliance's management model is a Swordfish working draft.
* Key management.
* CI on any V6 commit.

V6 random access (`--select`) is broken on stripe archives (open job #56).

**What is unbenchmarked.**

* Every comparison with an external codec. VNX has never been run in the public harnesses
  (fml-ethz/dt4dds-benchmark, AAnzel/UNACORM) or on public read data (ETH PRJEB90546, CNR, DT4DDS).
* Density (VNX 9.8469 nt per input byte at v4-balanced, SIMULATED, `docs/V4_RESULTS.md`), indel tolerance, dropout
  tolerance and throughput. None of these can be placed next to HEDGES, DNA-Aeon, DNA Fountain, YYC, StairLoop or
  TrellisBMA, because the protocols differ.
* The vendors' codecs. AtlasBase, Biomemory, Mimulus and Iridia are proprietary (BLACK).

**The honest picture.** VNX's engineering depth covers integrity, encryption, format versioning, fail-closed
decoding, native-kernel safety and test volume. Against the audited open repositories, these are mostly GREEN
because the competitors demonstrably lack them. They are not codec-performance advantages. On the scientific
axes that buyers and reviewers use (density at a given error tolerance, physical recovery, random access in
molecules), VNX is RED or YELLOW until it does three things:

1. runs a same-protocol benchmark (dt4dds-benchmark adapter);
2. fits its channel to public reads (PUBLIC-DATA-DERIVED);
3. buys one physical synthesis and sequencing round.

The commercial opening found by the audit is a vendor-neutral, standards-conformant, verifiable codec and reader
layer. No commercial vendor of that layer was found (30-companies §6). VNX does not occupy that layer yet either.

---

## 2 Market landscape

Sources: `30-companies.md` §1–§2. All entries are [PRESS] unless marked otherwise.

| layer | players (2026) | status | source |
|---|---|---|---|
| End-to-end DNA archive service | AtlasBase (ex-Atlas Data Storage, Twist spin-out): Atlas Eon 100 service announced 2 Dec 2025; Thalia chip (5.6B synthesis sites) and Early Access Programme Sep 2026 | selling at small scale; price and customers not public | https://www.storagenewsletter.com/2025/12/02/atlas-data-storage-introduces-the-worlds-first-scalable-dna-data-storage-offering/ , https://www.blocksandfiles.com/data-protection/2026/09/23/atlasbase-makes-thalia-dna-computer-and-storage-chip/5298506 |
| | Biomemory (+ Catalog assets, Mar 2026): DNA Card (1 KB, ~$1,000 for two) since late 2023; datacenter appliance with S3 interface promised H2 2026 | KB-scale shipping; datacenter product not confirmed shipped | https://www.datacenterdynamics.com/en/news/dna-data-storage-startup-biomemory-acquires-catalog-technologies-plans-data-center-deployment-in-h2-2026/ , https://www.blocksandfiles.com/architecture/2026/03/06/french-dna-coming-to-a-datacenter-near-you-soon/4093753 |
| | Iridia: storage-as-a-service targeted 2026; lunar molecular archive 2 Mar 2025 | launch not confirmed | https://techtarget.com/searchdatabackup/news/366560519/DNA-storage-to-tackle-massive-archives |
| | Mimulus + GenScript: "Glacier Storage Card", proprietary "Mimulus Code" | pre-commercial, no metrics | https://www.blocksandfiles.com/file/2026/06/09/mimulus-molecular-dna-storage-project/5252656 |
| | UW MISL for the US Library of Congress: 1.5 GB pilot contract (Sep 2025); ~1.0 GB synthesised, stored and validated by Mar 2026 [AR-WET via LoC slides] | pilot | https://www.digitalpreservation.gov/meetings/DSA2026/0103_budaSmithColtellino_Designing%20Storage%20Architecture%20Conference%202026%20OCIO%20DSD_VC_NBS.pdf |
| Synthesis (write) | Twist, GenScript, Ansa, DNA Script, Evonetix, imec | commercial, priced for biology | 30-companies.json |
| Sequencing (read) | Illumina, BGI/MGI, Oxford Nanopore | commercial | 30-companies.json |
| Containment | Imagene (DNAshell), Cache DNA, Biomemory cards, Atlas capsules | commercial | 30-companies.json |
| Codec / format / verification software | in-house at every vendor (Atlas patent application CA3249936A1 "Codecs for DNA data storage"; Mimulus Code; Biomemory; Iridia ECC); open academic codecs (Microsoft MIT tools, BGI Chamaeleo/YYC, Tianjin StairLoop, DNA Fountain, ETH dt4dds) | **no independent commercial codec vendor found** | 30-companies.md §1 |
| IT integration | Scality + Biomemory (2 Jun 2026); SNIA Swordfish DNA working draft (Jan 2026); Atlas "MXL" layer | early | https://www.scality.com/press-releases/dna-data-storage-biomemory-scality |

**Money and cost [PRESS].**

* About $1.4B has been invested in DNA storage since 2012, nearly 80 % of it in Twist and DNA Script.
* Writing costs about $100/MB, against a stated target below $1/MB
  (https://blocksandfiles.com/2026/01/16/dna-data-storage-when-the-physics-work-but-the-economics-dont/).
* Public $/MB figures range from about $100 to about $1M per MB depending on source and product (30-companies §2).
* No vendor publishes a price for a service shipping at scale.

**Consolidation [PRESS].**

* Catalog's assets went to Biomemory (5 Mar 2026).
* Molecular Assemblies went to Maravai ($11.2M, Jan 2025).
* Twist moved its storage business into Atlas (May 2025).
* Microsoft has no DNA storage product.

**Adjacent competitor.** Microsoft Project Silica (glass) competes for the same cold-archive budget. Its research
phase completed in 2025 [PRESS,
https://www.digitalpreservation.gov/meetings/DSA2026/0124_Silica-2026-03-09-Library-of-Congress-sharing.pdf].

**Implication for VNX.** The market buys end-to-end physical services from vertically integrated vendors. The codec
layer is bundled and proprietary. A software-only VNX has nothing to sell to these buyers until it either (a)
interoperates with their media through standards, or (b) shows physical recovery through a third-party lab.

---

## 3 Competitor map

Threat ratings are the analyst judgement recorded in `30-companies.json`, not sourced facts. "VNX vs them" applies
the colour rule. Where the competitor's codec is proprietary, the comparison is BLACK by definition.

| organisation | category | codec openness | physical evidence (their claim) | threat (analyst) | VNX vs them |
|---|---|---|---|---|---|
| AtlasBase | vertically integrated: chip synthesis + service + codec | proprietary; patent application CA3249936A1; says read-back via an "open-source script" (not found publicly) | commercial service and chip fabrication reported [PRESS] | HIGH | BLACK |
| Biomemory (+ Catalog IP) | enzymatic DNA assembly; datacenter appliances; S3 | proprietary; >90 patents after the Catalog deal [PRESS] | KB-scale DNA Cards shipped [PRESS] | HIGH | BLACK; on API: RED (VNX has no S3 path) |
| Mimulus + GenScript | card form factor, proprietary codec | proprietary | none public | MEDIUM | BLACK |
| Iridia | nanopore polymer memory chip + service | proprietary | lunar archive [PRESS]; chip read/write not shown at scale | MEDIUM | BLACK |
| Open academic codecs (Microsoft, BGI, Tianjin, ETH) | free MIT/GPL codecs and simulators | open | many level-3 papers [AR-WET] | MEDIUM | YELLOW/RED per axis (§6–§16) |
| Microsoft Project Silica | glass medium | proprietary | prototypes [PRESS] | MEDIUM (indirect) | not comparable (different medium) |
| Scality | object storage partner of Biomemory | proprietary | n/a | MEDIUM (indirect) | RED on integration |
| Genomika / DNAMIC | OAIS-oriented microfactory; DDSA co-chair | unknown | not public | LOW-MEDIUM | BLACK |
| DiDAX (Technion-led EU) | codec research | unknown | unknown | LOW-MEDIUM | BLACK |
| C-Atom (Shenzhen, Wukong codec) | codec-first startup | proprietary | unknown | LOW-MEDIUM | BLACK |
| BioCompute (India → San Francisco) | end-to-end prototype | proprietary | self-reported | LOW-MEDIUM | BLACK |
| Western Digital, Seagate, Quantum | storage incumbents with decoding/encoding patents | proprietary | not public | LOW (FTO flags) | BLACK |
| Twist, Illumina, GenScript, Ansa, DNA Script, Evonetix, imec, Imagene, Cache DNA | write/read/containment suppliers | n/a | commercial | LOW / NONE | suppliers, not competitors |
| UW MISL | academic; LoC contractor | mostly open | ~1.0 GB validated for LoC [AR-WET] | LOW | RED on physical evidence |
| DNA Data Storage Alliance; JPEG DNA | standards | open specs | JPEG DNA wet-lab test decoded [AR-WET per press] | NONE (opportunity) | RED (no conformance) |

Freedom-to-operate flags found during the patent search (30-companies §4) need an IP lawyer before VNX claims
novelty:

* Atlas CA3249936A1 (codecs);
* Western Digital US20250174272A1 and WO2023146570A1;
* Seagate US20260004879A1;
* Microsoft US11600360B2 (trace reconstruction) and US20170141793A1;
* Molecular Assemblies US10982276B2;
* Catalog/Biomemory US12437841B2;
* Quantum US20230215516A1.

---

## 4 GitHub ecosystem map (summary)

Full detail lives in `docs/GITHUB_ECOSYSTEM_MAP.md`. Counts below come from the two repository clusters
(`10-repos-cluster1.md`, `20-repos-cluster2.md`).

| measure | cluster 1 (US academic, Microsoft, NCSU dna-storage, HEDGES, misc) | cluster 2 (BGI/YYC, fountain, classic, 2023–26) |
|---|---|---|
| raw hits | ~240 distinct repos | ~790 raw hits from ~45 searches |
| recorded | 76 repos + 1 negative note | 157 repos + 8 "no public code found" |
| in-depth audit | 11 (code/README read; some built or run) | 34 (about 14 code-read or executed) |
| built or run by the audit [AUDIT] | microsoft/DNABoundedHomopolymerEncoding, whpress/hedges | Chamaeleo smoke test |

**Shape of the ecosystem.**

1. **Harnesses.** fml-ethz/dt4dds-benchmark (GPL-3.0) and AAnzel/UNACORM (GPL-3.0) both accept an external codec
   through a thin wrapper. dt4dds-benchmark uses an `encode.sh`/`decode.sh` pair. VNX can plug in as a separate
   process.
2. **Indel-capable codecs with public code:**
   * HEDGES (whpress, MIT, but bundles Schifra RS under its own terms);
   * DNA-Aeon (MIT);
   * StairLoop (GPL-3.0);
   * Gungnir (BSD-3);
   * SPIDER-WEB (custom BGI licence; commercialisation needs permission);
   * DBGPS (GPL-3.0);
   * Derrick (MIT).
3. **Mappers and classic codecs.** Church, Goldman, Grass, Blawat, DNA Fountain and YYC are available in Chamaeleo,
   Storage-D and UNACORM. Several of these have no official code (Church 2012, Goldman 2013, Blawat 2016,
   Bornholt 2016, Organick 2018, Anavy 2019).
4. **Trace reconstruction and clustering:**
   * microsoft/TrellisBMA (MIT, archived);
   * GZHoffie/bbs (MIT);
   * DNAformer (MIT);
   * Clover (GPL-3.0);
   * TReconLM, GradHC and omersabary/Reconstruction (no licence).
5. **Simulators:**
   * dt4dds (GPL-3.0, calibrated on wet-lab data per its paper);
   * DeSP (MIT);
   * MESA (AGPL-3.0);
   * DNATerra (MIT, realism unverified);
   * DNArSim (no licence).
6. **System-level repos:**
   * Mr-PU/dnastore (Apache-2.0): object API, append-only versioning, FUSE; simulation only;
   * SSL-ACTX/helix (AGPL-3.0): Zstd, XChaCha20-Poly1305, RS, primers; README says not physically validated;
   * dna-storage/reframed (BSD-style): cascading pipeline, format-ID registry, real-FASTQ replay.
7. **A long tail.** About 150 student-built "DNA storage simulators" from 2025–26, not audited.

**Licence posture for any VNX harness** (research note, not legal advice):

* Run freely as external processes (permissive): TrellisBMA, BHE, CNR, whpress/hedges (Schifra caveat), reframed,
  Storage-D, DNATerra, dnastore, DNA-Aeon, Gungnir, bbs, DeSP, DNA-RS.
* Subprocess only, never linked or copied (GPL/AGPL): dt4dds(-benchmark), UNACORM, dna-fountain, pyHEDGES, Clover,
  StairLoop, DBGPS, NOREC4DNA, MESA.
* Blocked or unclear:
  * hedges-soft-decoder (Oxford Nanopore research-only licence);
  * mahoraga-codec (PolyForm Noncommercial);
  * ArchiGen, TReconLM and the uwmisl data repos (no licence).

---

## 5 Repository-by-repository findings (in-depth audited repos)

"Level" is the physical-validation level of the cited paper (1 software only, 2 in-vitro synthesis, 3 sequencing
experiments, 4 end-to-end archival including storage or ageing). It is not the level of the repository code.
VNX's level is 1.

| repo (licence, last meaningful commit) | what it is | evidence and key numbers | level | VNX relevance / status |
|---|---|---|---|---|
| microsoft/TrellisBMA (MIT, archived, 2024-05-11) https://github.com/microsoft/TrellisBMA | coded trace reconstruction on IDS channels (ISIT 2021, https://arxiv.org/abs/2107.06440); MR and convolutional inner codes; BMALA baseline | error-rate curves only as figures; Trellis BMA has a lower error rate than BMALA on real data; BMALA-HD gives a higher rate at >6 traces; soft outputs not calibrated (authors' own limitation) [AR-WET reads / AR-SIM] | 3 | reference for multi-read decoding; no tests, notebooks; VNX difference in §9 |
| microsoft/clustered-nanopore-reads-dataset (MIT, 2024-11-18) https://github.com/microsoft/clustered-nanopore-reads-dataset | 10,000 × 110-nt centers, 269,709 MinION reads, clustered | p_ins ~0.017, p_del ~0.020, p_sub ~0.022 [AR-WET]; authors' 2024 note: centers not uniformly random | 3 | channel-fit smoke test and trace-reconstruction benchmark; VNX cannot decode it (no VNX framing) |
| microsoft/DNABoundedHomopolymerEncoding (MIT, 2025-09-25) https://github.com/microsoft/DNABoundedHomopolymerEncoding | enumerative rank/unrank homopolymer-bounded mapper (GMP) | rates at N=110: 1.5818/1.9182/1.9818/1.9909/1.9909 bits/base for k=1..5; ~42/74 Mbit/s encode/decode, 1 thread, 0 round-trip errors in 2,000 trials [AUDIT] | 1 | rate-gap reference for VNX's screening approach (§7) |
| whpress/hedges (MIT + Schifra RS terms, 2024-11-04) https://github.com/whpress/hedges | HEDGES inner code (PNAS 2020, https://pmc.ncbi.nlm.nih.gov/articles/PMC7414044) | 5,865 × 300-nt Twist oligos; all 18 packets error-free at depth ~3 untreated; 16/18 under high mutagenesis [AR-WET]; error-free at 7–10 % errors at rate 0.25 [AR-SIM]; demo 20 packets at 1.5× high-mutagenesis rates all decoded, ~1.1 KB/s single thread including encode and error injection [AUDIT] | 3 | indel-correcting inner code with constraints that VNX lacks; benchmark in a harness only |
| dna-storage/reframed (BSD-2-style, 2026-06-21) https://github.com/dna-storage/reframed | multi-pipeline framework (RS/LT × Base4/HEDGES), fault injection incl. real-FASTQ replay, DNA-encoded header, stable format-ID registry | ~135 pytest functions, GitHub Actions (tests not run by the audit) [README] | 1 | architectural reference (component headers, format IDs); adopt ideas, not code |
| dna-storage/dnastorage (LGPL-3.0, 2021-05-10) | RS outer code, comma-free index codewords (CFC8), packetised files | prior art [README] | 1 | prior art for index codewords |
| dna-storage/hedges-soft-decoder (ONT research-only, 2024-06-18) | soft HEDGES decoding from basecaller output, GPU | hard decoder >25 % byte error, prior soft decoder 2.25 % at 183 s/read, new decoder 257× faster at 3.52 % [AR-WET reads; from a search summary, not cross-checked] https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11755093/ | 3 | licence blocks use; shows the soft/basecaller direction |
| DNAstorage-iSynBio/Storage-D (MIT, 2023-06-23) https://github.com/DNAstorage-iSynBio/Storage-D | Wukong codec, RS + XOR, Primer3/BLAST primers, web server | ~1.98 bits/nt coding potential; 100 % recovery at ≥30×, >99 % at ~10× with 3 missing oligos; in vivo 7 days (iMeta 2024, https://pmc.ncbi.nlm.nih.gov/articles/PMC11170965/) [AR-WET] | 4 | primer-per-file random access that VNX lacks; no indel correction in the codec |
| y1151/DNATerra (MIT, 2026-07-25) https://github.com/y1151/DNATerra | read simulator with position-dependent platform profiles | realism is the repo's claim; no paper [README] | 1 | cross-check channel for VNX |
| Mr-PU/dnastore (Apache-2.0, 2026-07-25) https://github.com/Mr-PU/dnastore | object store API (store/retrieve/update/delete), append-only versioning with tombstones, RS erasure striping, CRC16, FUSE, plugins | simulation only; no indel correction [README] | 1 | API and versioning design input (§12) |
| ntpz870817/Chamaeleo (MIT, code 2021-12-01) https://github.com/ntpz870817/Chamaeleo | mapping-level multi-codec toolkit (7 mappers, Hamming/RS) | Base/Goldman/Blawat/Church round trip OK; YYC fails on Python 3.12 (`TypeError`) [AUDIT] | 1 | cheap mapping-level baseline only |
| BGI-SynBio/YinYangCode (MIT, 2025-10-23) https://github.com/BGI-SynBio/YinYangCode | YYC constrained mapper (Nat Comput Sci 2022, https://doi.org/10.1038/s43588-022-00231-2) | 1.75–1.78 bits/nt observed; 10,103 × 200-nt in vitro; 99.9 % recovery above 10⁴ copies [AR-WET, via PMC summary] | 3 | constrained-mapper baseline |
| HaolingZHANG/DNASpiderWeb (custom BGI licence, 2024-06-21) https://github.com/HaolingZHANG/DNASpiderWeb | graph-generated constrained codes with built-in correction | up to 4 % edit errors at 5.5 % redundancy [AR-SIM], arXiv 2204.02855, no wet lab | 1 | competes with an indel-aware inner code; licence restricts commercial use |
| TeamErlich/dna-fountain (GPL-3.0, code 2016-09-09) https://github.com/TeamErlich/dna-fountain | LT fountain + screening + RS(2) (Science 2017, https://doi.org/10.1126/science.aaj2038) | 72,000 oligos, 2.14 MB, 7 % redundancy, 1.57 bits/nt [AR-WET, via search summary] | 3 | historic baseline; indel reads discarded |
| umr-ds/NOREC4DNA (AGPL-3.0, 2025-10-13) https://github.com/umr-ds/NOREC4DNA | LT/Online/Raptor with DNA rules | BMC Bioinformatics 2021 [AR-SIM] | 1 | fountain reference; subprocess only |
| MW55/DNA-Aeon (MIT) https://github.com/MW55/DNA-Aeon | arithmetic-coded constrained inner code + CRC sync markers + stack decoder + Raptor outer | Nat Commun 2023, https://doi.org/10.1038/s41467-023-36297-3, in vitro per abstract [AR-WET] | 3 | indel-capable codec with constraints; benchmark target |
| fml-ethz/dt4dds (GPL-3.0) https://github.com/fml-ethz/dt4dds | digital twin for synthesis/PCR/ageing/sequencing | fitted on 40 sequencing experiments (Nat Commun 2023, https://doi.org/10.1038/s41467-023-41729-1) [AR-WET calibration] | 3 | reference simulator; VNX cross-check |
| fml-ethz/dt4dds-benchmark (GPL-3.0) https://github.com/fml-ethz/dt4dds-benchmark | harness: 6 default codecs + wrappers, 6 clusterers, IDS/dropout/coverage workflows | used in Gimpel et al. 2026 (doi:10.1038/s41467-026-70548-3): codecs tolerate up to 14 % error and 65 % sequence loss [AR-SIM]; experimental storage at 43 / 13 EB/g [AR-WET] | 1 (harness) / 3 (paper) | **primary target for a VNX same-protocol benchmark** |
| AAnzel/UNACORM (GPL-3.0, 2026-09-07) https://github.com/AAnzel/UNACORM | 8-codec comparison over 9 files, MESA errors | "no single codec wins across all dimensions" (arXiv 2608.09673) [AR-SIM] | 1 | second harness; weaker channel |
| Guanjinqu/StairLoop (GPL-3.0) https://github.com/Guanjinqu/StairLoop | convolutional + LDPC with BCJR soft decoding | >6 % error or >30 % dropout within a block at <3× depth, electrochemical synthesis (Nat Commun 2025, https://doi.org/10.1038/s41467-025-64230-3) [AR-WET] | 3 | soft iterative decoding reference |
| HKU-BAL/Gungnir (BSD-3) https://github.com/HKU-BAL/Gungnir | hash-guided correction of sub/ins/del | complete recovery from a single copy at 20 % erroneous bases (https://doi.org/10.1038/s41467-026-71485-x) [AR-SIM] | 1 | single-copy indel reference |
| jeplb/mahoraga-codec (PolyForm NC) https://github.com/jeplb/mahoraga-codec | profile-HMM + LDPC/OSD + RS(GF 2¹⁶) | 155.8 / 25.9 EB/g on the DT4DDS channel, single author, not peer reviewed [AR-SIM] | 1 | not benchmarkable by VNX (licence) |
| SSL-ACTX/helix (AGPL-3.0) https://github.com/SSL-ACTX/helix | Zstd + XChaCha20-Poly1305 + RS + rotating trellis + primers + streaming | README states not physically validated [README] | 1 | system architecture similar to VNX's; claims unverified |
| reinhardh/dna_rs_coding (Apache-2.0, 2021) https://github.com/reinhardh/dna_rs_coding | 2-D RS (Grass/Heckel; Antkowiak 2020) | wet lab with ageing (Grass 2015) [AR-WET] | 3–4 | wrapped as DNA-RS in dt4dds-benchmark |

---

## 6 Algorithm comparison (summary)

Detail lives in `docs/ALGORITHM_COMPARISON.md`. Numbers are as reported by the authors and are **not comparable** with
VNX numbers, because no common protocol exists. Bits/nt for VNX are THEORETICAL, derived from the format: 40 payload
bytes per 313-nt strand gives 1.02 bits/nt before the outer code. The SIMULATED net value at v4-balanced is
8 / 9.8469 = 0.81 bits/nt, including the outer 64+16 code and metadata (`docs/V4_RESULTS.md`).

| system | mapping / constraints | ECC | indel handling | reported rate | level | VNX status on this axis |
|---|---|---|---|---|---|---|
| VNX V5/V6 | 2 bits/nt + SHAKE-128 scrambler screening (≤256 variants) for GC, homopolymer, motifs | inner RS(70,54)+CRC-32; outer Cauchy RS; V6 stripes + column parity | 3-nt markers → erased segments; V5 hypothesis search verified by RS + CRC; per-address consensus | 0.81 bits/nt net (SIMULATED) | 1 | n/a |
| HEDGES 2020 | hash-based nt code, windowed GC, run ≤4 | HEDGES + outer RS | yes (stack decoding) | code-rate dependent (1/6 … 3/4) [AR-WET] | 3 | RED (no indel code) |
| DNA-Aeon 2023 | arithmetic coding on constrained codebook | Raptor + CRC sync | yes (stack decoder) | not extracted | 3 | RED |
| StairLoop 2025 | convolutional + LDPC | LDPC × conv, BCJR | yes (BCJR) | not extracted | 3 | RED (no BCJR/LDPC) |
| DNA Fountain 2017 | LT + screening | fountain + RS(2) | none (discard) | 1.57 bits/nt [AR-WET] | 3 | YELLOW (VNX LT EXPERIMENTAL; forbidden in V6 superblock v2) |
| YYC 2022 | 2 rule sets × 2 bits | RS outer (paper) | none in codec | 1.75–1.78 observed [AR-WET] | 3 | YELLOW (screening, not constrained coding) |
| Wukong / Storage-D 2024 | Wukong | RS + XOR | none noted | ~1.98 potential [AR-WET] | 4 | YELLOW |
| Grass / DNA-RS | RS 2-D | RS | via reconstruction | ~0.84 (computed from abstract) [AR-WET] | 4 | YELLOW (VNX has a product code, unbenchmarked) |
| TrellisBMA 2021 | MR / convolutional inner | outer assumed | trellis trace reconstruction | figures only | 3 | RED (no trace reconstruction) |
| SPIDER-WEB (preprint) | constraint graph | built-in | yes | 4 % edits at 5.5 % redundancy [AR-SIM] | 1 | RED |
| Gungnir 2026 | hash signature + search | none or low | yes | 20 % errors at a single copy [AR-SIM] | 1 | RED |
| BHE (Microsoft) | enumerative homopolymer ≤k | none | none | 1.9909 bits/base at k=4, N=110 [AUDIT] | 1 | YELLOW (VNX enforces more constraints, at an unmeasured rate gap) |

---

## 7 Encoding comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| Bits → bases | 2 bits/nt fixed map 00 A, 01 C, 10 G, 11 T (`docs/VNX4_FORMAT.md` §11) | all codecs | — |
| GC control | global 40–60 % default, optional windowed (`src/vnxdna/v4/constraints.py`) | HEDGES 4 ≤ GC ≤ 8 per 12-nt window [AR-WET]; YYC 40–60 %; Storage-D 150-nt regional windows | YELLOW (present; no comparison of rate cost) |
| Homopolymer limit | ≤4 default; ≤3 over ~300 nt can make encoding fail (`docs/LIMITATIONS.md`, EXP-0014) | BHE exact enumerative k=1..5 [AUDIT]; HEDGES run ≤4 [AR-WET]; Storage-D 3–5 | YELLOW |
| Constraint method | **screening** of up to 256 SHAKE-128 scrambler variants; 1 variant byte per frame (8 bits per 313 nt, ≈0.026 bits/nt THEORETICAL) plus 33 marker nt per strand | constrained coding: BHE (enumerative, 1.9909 bits/base at k=4 [AUDIT]), DNA-Aeon (arithmetic coding on constrained codebook), YYC rule tables, SPIDER-WEB graph | RED (no constrained coding; rate gap to the constraint capacity never measured) |
| Tandem repeats, forbidden motifs (+RC) | PRESENT (rules tuple) | Chamaeleo screen.py checks homopolymer + GC only [AUDIT, code read] | GREEN vs Chamaeleo only (it demonstrably lacks motif rules); YELLOW vs others |
| Secondary structure / Tm / hairpin | ABSENT (`docs/LIMITATIONS.md`) | YYC optional minimum free energy check [AUDIT, code read]; Storage-D Primer3/BLAST for primers | RED |
| Primers / adapters | ABSENT; 313 nt + 2×20-nt primers = 353 nt, above the 350-nt maximum of Twist and IDT pools (vendor spec recorded in `40-datasets.md`; Twist DOC-001054 REV12, URL not captured) | Storage-D Primer3/BLAST primers [AR-WET]; HEDGES 23-nt primers [AR-WET]; D03 dataset has primers in reads (https://doi.org/10.5281/zenodo.10943282) | RED |
| Strand length options | 313 nt (balanced/indel/archival), 280 nt (dense) | public datasets and the ETH benchmark use 110–157 nt (`40-datasets.md`); HEDGES 300 nt | YELLOW (no short profile matching benchmark pools) |
| Address field | per frame: archive tag 2 B, group index 4 B, symbol index 2 B, covered by CRC-32 and inner RS | dnastorage CFC8 comma-free index codewords; reframed hierarchical index ints | YELLOW (works; flat 32-bit group space, see §10) |
| Compression | zstd keep-if-smaller before AEAD (`docs/VNX4_FORMAT.md` §5/§7) | helix Zstd [README]; Goldman Huffman | GREEN vs TrellisBMA/dnastore/Storage-D (no compression in those repos per the audit JSON); YELLOW vs helix |
| Synthesis-order export | PARTIAL: FASTA + JSON schema for synthesis records, no vendor adapter (`experiments/v6/physical/schema/synthesis.schema.json`) | Storage-D web server; none of the audited repos ships a vendor order adapter either | YELLOW |

**THEORETICAL rate observation.** VNX spends 1 of 70 frame bytes on the scrambler variant and 33 of 313 nt on markers
(10.5 %). BHE shows a homopolymer-only constraint (k=4) costs <0.5 % of 2 bits/base at N=110 [AUDIT]. The VNX
constraint set (GC + homopolymer + motifs) is stricter, so the two are not the same problem. The point is that
VNX has never measured its own rate gap to the capacity of its constraint set.

---

## 8 ECC comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| Inner code | RS(n=14+P+r, k=14+P) over GF(2⁸), r=16 at v4-balanced; accept iff CRC-32 passes (as received, or after bounded-distance decoding 2e+f ≤ r) (`docs/VNX4_FORMAT.md` §10) | HEDGES (hash convolutional-style + RS outer) [AR-WET]; DNA-Aeon (CRC markers + stack decoder) [AR-WET]; StairLoop (conv) [AR-WET]; mahoraga (LDPC/OSD) [AR-SIM]; dnastore CRC16 only [README] | YELLOW (works for substitutions/erasures; no indel-capable inner code) |
| Outer code | Cauchy RS K+M per group, MDS (64+16 default; 32+32 archival) (`src/vnxdna/ecc/cauchy.py`) | DNA Fountain LT [AR-WET]; DNA-Aeon Raptor [AR-WET]; NOREC4DNA LT/Online/Raptor [AR-SIM]; DNA-RS 2-D RS [AR-WET]; dnastore RS erasure striping [README] | YELLOW (no same-protocol comparison) |
| Product code | V6 opt-in: stripes of D data groups + Mc column-parity groups, iterative row/column erasure decoding, interleaved order (`docs/V6_OUTER_CODE.md`, `src/vnxdna/v6/outer.py`) | DNA-RS 2-D RS [AR-WET]; StairLoop LDPC × conv [AR-WET] | YELLOW |
| Adaptive redundancy | V6 planner chooses row length, stripe depth and Mc by analytic i.i.d. dropout bound under a redundancy budget; named profiles (`src/vnxdna/v6/profiles.py`) | NOREC4DNA `find_minimum_packets` optimiser [README] | YELLOW |
| Soft decisions | V5 opt-in GMD erasure ordering / Chase over the unchanged RS + CRC verifier (`src/vnxdna/v5/soft/`) | StairLoop BCJR soft [AR-WET]; hedges-soft-decoder from basecaller posteriors [AR-WET reads]; TrellisBMA soft posteriors [AR-SIM/AR-WET reads]; mahoraga soft HMM [AR-SIM] | YELLOW |
| Fountain | LT GF(2), quadratic Gaussian elimination, k ≤ 1024, EXPERIMENTAL; forbidden in superblock v2 | DNA Fountain, NOREC4DNA, DNA-Aeon Raptor | RED for production use |
| LDPC / polar / convolutional | ABSENT (backlog R-02) | StairLoop, mahoraga, Chandak LDPC_DNA_storage (MIT) | RED |
| Indel-correcting codes (VT, HEDGES-like, watermark) | ABSENT; indels → erasures via markers (`docs/INDEL_ENGINE.md`) | HEDGES, DNA-Aeon, SPIDER-WEB, Gungnir, StairLoop | RED |
| Integrity on top of ECC | CRC-32 per frame + chunk SHA-256 + Merkle + container SHA-256; fail closed | TrellisBMA none; Storage-D RS only; dnastore CRC16; reframed CRC/RS (audit JSON) | GREEN vs these four (they demonstrably lack an end-to-end cryptographic hash chain); BLACK vs vendors |

**VNX simulated ECC results** (V6 Phase 1, 20 seeds per cell, 0 false SUCCESS, `experiments/v6/phase1/summary.md`,
commits 5068232, efef85e, b38cfa7; all SIMULATED):

* i.i.d. dropout threshold: V5 0.07 → V6-plan-seq/adaptive 0.16 at ≈equal overhead (0.251 vs 0.249).
* Poisson coverage threshold: mean 3.0 (V5) → 2.0 (V6-plan-seq/adaptive).
* Burst loss + 2 % i.i.d.:
  * V5 fails at every burst ≥64 strands;
  * V6-rows255/adaptive survive 4,096-strand bursts;
  * V6-plan-seq survives up to 256.
* Cost: V6-adaptive encode 4.4 s against 0.39 s for V5.

**Not comparable** with Gimpel et al. 2026: the "up to 14 % error and 65 % sequence loss" there is [AR-SIM] on a
different channel, strand length and code rate. Not comparable with StairLoop's ">30 % dropout within a block"
either [AR-WET].

---

## 9 Alignment comparison

### 9.1 Landscape

| system | alignment / reconstruction method | needs clustering? | output | evidence |
|---|---|---|---|---|
| VNX V4–V6 | marker-template banded DP per read, integer costs, traceback assigns indels to marker segments → erasures | no (reads grouped by decoded address; orphans counted) | hard bytes + erasure flags; consensus posteriors per position | SIMULATED |
| microsoft/TrellisBMA | per-trace BCJR on a single-trace IDS trellis built on the code trellis; soft information exchanged across traces | yes (pre-clustered) | soft symbol posteriors | [AR-SIM] + real CNR reads [AR-WET] |
| HEDGES | stack (heap-limited) search decoder over the hash code | no | bytes | [AR-WET]; ~1.1 KB/s demo [AUDIT] |
| DNA-Aeon | stack decoder with CRC sync markers | no | bytes | [AR-WET] |
| StairLoop | BCJR (Cython) over reads, MPI | not stated | soft | [AR-WET] |
| reframed | LSH clustering + MUSCLE + majority vote | yes | consensus strand | [README] |
| hedges-soft-decoder | alignment-matrix / beam-trellis soft decoding from CTC output, GPU | no | soft | [AR-WET reads] |
| bbs, DNAformer, TReconLM | beam search / neural reconstruction | yes | strand estimate | [AR-SIM]/[README] |

### 9.2 Exactly what VNX V5/V6 does differently from Microsoft TrellisBMA

VNX facts are from `docs/INDEL_ENGINE.md`, `docs/V5_NATIVE_ALIGNMENT_CONTRACT.md`,
`docs/V5_PHASE3_INDEL_RECOVERY.md`, `docs/CONSENSUS.md` and the baseline note B.5. TrellisBMA facts are from
`10-repos-cluster1.md` §1a and https://arxiv.org/abs/2107.06440.

| dimension | Microsoft TrellisBMA | VNX V5 (unchanged in V6) |
|---|---|---|
| Problem solved | coded trace reconstruction: estimate one codeword from a cluster of K noisy traces | per-read synchronisation: find where the known markers sit in each read, so that indels become a small number of erased bytes for the inner RS code |
| Channel model | probabilistic IDS hidden Markov model with p_ins, p_del, p_sub (≈0.017/0.020/0.022 fitted on CNR training clusters) | none in the aligner: integer edit costs (marker mismatch 4, insertion = deletion = 6, deletion of a marker base +1), band B = 6 (`SyncCosts`, contract §4) |
| Use of the inner code during alignment | the trellis **is** the code trellis (marker-repeat N=110, r=6/10, or convolutional); alignment and decoding are joint | frame bases are wildcards during alignment; only the 3-nt markers (every 24 nt) are known. The code is used **after** alignment: V5 smart recovery enumerates indel placements inside a window as hypotheses and accepts a frame only when inner RS + CRC-32 verify and all verifying hypotheses agree (unanimity, fail closed) |
| Multi-read use | joint: one single-trace trellis per trace, soft information exchanged between traces ("lookahead"/"no-lookahead" passes); linear in K | separate: each read is aligned to the template independently; reads of the same address are then voted per position (erased positions abstain); posterior < 0.6 → erasure; V5 adds consensus realignment |
| How reads are grouped | external clustering (Rashtchian et al. NeurIPS 2017) | by the read's own decoded address (CRC-verified or tentative header). Reads with an erased header are orphans: 31 % at 1 % sub + 0.4 % ins + 0.4 % del in one simulated measurement (`docs/LIMITATIONS.md`). No unaddressed clustering in the V4+ decoder |
| Output | soft symbol posteriors for an assumed outer decoder (authors note poor calibration) | hard bytes + erasure flags, optional soft symbols into GMD/Chase; acceptance always by CRC-32, then chunk SHA-256 and container SHA-256 |
| Wrong-output protection | none in the repo (no integrity layer) | fail closed: 0 false acceptances / 0 false SUCCESS in the V5 Phase 3/4 experiments (SIMULATED, `docs/V5_PHASE3_INDEL_RECOVERY.md`, `docs/V5_COMPLETION_REPORT.md`) |
| Error granularity | per-symbol posterior | V4: whole 24-nt segment per indel (median 24 erased nt); V5 smart mode: median 4 erased nt per indel; 3.7 nt per deletion and 0 nt per insertion for one-indel reads (SIMULATED, V5 Phase 3) |
| Complexity | K × single-trace trellis (states × length); full multi-trace BCJR feasible only for K ≤ 3 | O(T·(2B+1)) per read for alignment; bounded hypothesis budget per read for smart recovery; deferred schedule runs it only for still-undecodable groups (Gate B), and in V6 only for selected stripes (513facb) |
| Implementation | Python + numba, notebooks, archived, no tests | C11 kernel via ctypes, bit-exact with the NumPy oracle (normative contract), 700,000 differential fuzz reads with 0 mismatches, gcc ASan+UBSan and clang UBSan clean, ×9.6–11.3 over the reference (28–30 Mbases/s, 1 core, SIMULATED micro-benchmark, `docs/V5_PHASE2_NATIVE_ALIGNMENT.md`) |
| V6 change | — | none to the aligner. V6 adds a native FASTQ parser (557 MiB/s, ×4.81) and native SIMD inner RS (e2e ×1.55) around it. Both were benchmarked on a dirty tree (8e517ef) |
| Data evaluated on | real MinION reads (CNR) + simulated | simulated only |

**Comparability: none today.** VNX cannot decode the CNR reads, because they carry no VNX frames. TrellisBMA has
never been run against VNX-framed strands. Two same-protocol options exist:

* (a) expose VNX's per-address consensus as a standalone trace-reconstruction function and score it on CNR
  clusters, with the 2024 centre-generation caveat;
* (b) simulate VNX strands under a CNR-fitted IDS model and run both decoders under matched redundancy.

Until one is done:

* multi-read consensus is YELLOW (VNX has it; unbenchmarked);
* soft joint trace reconstruction is RED (backlog F-06 / R-01);
* fail-closed integrity on the decode path is GREEN against TrellisBMA, which demonstrably has none.

---

## 10 Random-access comparison

### 10.1 What exists

| system | random-access mechanism | evidence | VNX status |
|---|---|---|---|
| VNX @081697b | **digital only**: container-level `vnx locate`, `vnx extract --file`, Merkle per-chunk verify; DNA-level selection decodes only groups touching the selected files; 513facb runs smart/soft only for the index and the selected stripes. Pass 1 still parses and aligns **every read**. No primers | SIMULATED (`docs/RANDOM_ACCESS.md`, V2 table up to 10 GB: 1 MiB slice from a 10 GB strand file in 1.22 s, 83,940 of 138,563,183 strands scanned, with a DNA index) | YELLOW (digital), RED (molecular) |
| V6 stripe archives | `--select` fails with "no superblock could be decoded" on V6 stripe archives (D=4, Mc=2) at coverage 2–3 where full decode succeeds: **open job #56 (P1)** | baseline B.6 | RED until fixed |
| Organick et al. 2018 (UW/Microsoft) | PCR primer-based selection of 35 files from 13,448,372 oligos, Illumina + MinION; error-free per-file recovery claimed | [AR-WET], https://racz.statistics.northwestern.edu/Organick+18NBT.pdf | RED |
| Storage-D | one primer pair per file (Primer3/BLAST) | [AR-WET] | RED |
| reframed | primers + hierarchical index integers | [README] | RED |
| dnastore | primer pair per object, metadata store | [README], simulation only | RED |
| uwmisl cas9-random-access | Cas9-based selection (repo, 2022) | repo only, no licence; not audited in depth | RED |
| SUSTech DNA cassette tape | barcode-addressed partitions on tape media | [PRESS], https://www.storagenewsletter.com/2025/09/17/rd-compact-cassette-tape-for-dna-based-data-storage/ | RED |
| DDSA | "Random Access reference paper" listed as a 2026 priority; random access/addressability a 2026 work area | [SPEC/PRESS], https://www.digitalpreservation.gov/meetings/DSA2026/0230_skilaustas_DNA%20data%20storage%20alliance%20presentation.pdf | — (nothing published found) |

### 10.2 Retrieving one file from a 100 TB / 1 PB / 1 EB logical archive without decoding everything

All figures in this subsection are **THEORETICAL** order-of-magnitude arithmetic. Inputs are taken from the VNX4
format at v4-balanced:

* P = 40 payload bytes per strand;
* K + M = 64 + 16, so 2,560 container bytes per group and 80 strands per group;
* 313 nt per strand;
* 9.8469 nt per input byte (SIMULATED measurement);
* 1 MiB chunks and 84 bytes per chunk-table entry.

Lines marked ASSUMPTION are not sourced in this audit. Replace them with quotes before using any figure externally.
"File" means one 1 MiB object.

**Step 1: address space.**

| quantity | 100 TB | 1 PB | 1 EB |
|---|---|---|---|
| logical bytes L | 1e14 | 1e15 | 1e18 |
| strands ≈ L/40 × 80/64 | 3.1e12 | 3.1e13 | 3.1e16 |
| nucleotides ≈ 9.8469 × L | 9.8e14 | 9.8e15 | 9.8e18 |
| VNX4 data groups ≈ L / 2,560 | 3.9e10 | 3.9e11 | 3.9e14 |
| fits the 4-byte group index (2³² = 4.3e9 groups ⇒ ≈1.1e13 B ≈ 11 TB per archive at v4-balanced)? | **no** (×9) | **no** (×91) | **no** (×9e4) |
| bits needed for a flat strand address | ~42 | ~45 | ~55 |

Consequences at the current format:

* One VNX4 archive cannot hold 100 TB. The superblock's group count is also 4 bytes, and V6 column-parity groups use
  group indices ≥ G, which reduces the space further.
* A multi-archive pool cannot rely on the 2-byte archive tag as a namespace. With random archive IDs, the
  birthday-collision probability reaches 50 % at about 300 archives in one pool (√(2·ln2·65,536) ≈ 301).
* A pool-level address therefore needs a format change: (container/capsule ID, partition ID, archive ID,
  64-bit group index).

**Step 2: index and metadata layout.**

| quantity | 100 TB | 1 PB | 1 EB |
|---|---|---|---|
| 1 MiB chunks | 9.5e7 | 9.5e8 | 9.5e11 |
| flat chunk table (84 B/entry) | 8.0 GB | 80 GB | 80 TB |
| strands to read that flat table from DNA (×1.25/40 B) | 2.5e8 | 2.5e9 | 2.5e12 |
| hierarchical index depth with 1 MiB nodes (≈12,483 entries per node) | 2 | 3 | 3 |
| strands for one root-to-leaf path (≈32,800 strands per 1 MiB node) | ~6.6e4 | ~9.8e4 | ~9.8e4 |
| strands for the 1 MiB file itself (410 groups × 80) | 32,800 | 32,800 | 32,800 |

Today the VNX4 container writes the chunk table, file table, manifest (≤1 MiB) and trailer after the body, inside
the same group sequence (`docs/VNX4_FORMAT.md` §1). Reading the index from DNA therefore means decoding the whole
flat table. That is fine at GB scale and infeasible at PB scale. Random access at these scales needs:

* an index that is a tree of independently decodable nodes;
* each node placed in its own selectable partition;
* a superblock or Sector One record that points at the root.

**Step 3: molecular selection (primer partitions).**

| quantity | 100 TB | 1 PB | 1 EB |
|---|---|---|---|
| ASSUMPTION: orthogonal primer pairs usable in one pool, Np ≈ 1e4 (literature value not verified in this audit) | | | |
| single-level partition size L/Np | 10 GB | 100 GB | 100 TB |
| two-level nested partitions (Np² ≈ 1e8) partition size | 1 MB | 10 MB | 10 GB |
| three-level nested (Np³ ≈ 1e12) partition size | — | — | 1 MB |
| added PCR substitutions at 1.09e-4 /nt/cycle (Gimpel 2023 [AR-WET], https://doi.org/10.1038/s41467-023-41729-1), ASSUMPTION 25 cycles per level | 2.7e-3 /nt (1 level) | 5.5e-3 /nt (2 levels) | 8.2e-3 /nt (3 levels) |
| strand length with 2 × 20-nt primers per level (313-nt payload strand) | 353 nt: above the 350-nt Twist/IDT maximum | 393 nt | 433 nt |

Two things follow:

* At EB scale, physical partitioning into separate containers (capsules, cards, tubes) has to be the first address
  level. Atlas capsules and Biomemory cards already work this way [PRESS].
* Nested primers cost strand length and add PCR error. VNX's 313-nt strand leaves no room for primers within
  vendor limits. A ~150–200 nt profile is needed (`40-datasets.md` plan b).

**Step 4: read-pool size and sequencing cost (mean coverage 3; V5 Poisson coverage threshold 3.0, V6 2.0,
SIMULATED).**

| quantity | 100 TB | 1 PB | 1 EB |
|---|---|---|---|
| reads to sequence the whole pool (VNX today: no molecular selection) | 9.4e12 | 9.4e13 | 9.4e16 |
| reads for one two-level partition (file + index path, if co-located) | ~1e5 | ~1e6 | ~1e9 |
| ASSUMPTION: FASTQ ≈ 700 B per 313-nt read; bytes to parse for the whole pool | 6.6e15 | 6.6e16 | 6.6e19 |
| single-process parse time at 557 MiB/s (V6 native parser, SIMULATED benchmark on a dirty tree) | ~130 days | ~3.6 years | ~3,600 years (THEORETICAL extrapolation from a dirty-tree benchmark) |
| ASSUMPTION: $1 per 1e6 reads (for scale only); whole-pool sequencing | ~$9.4M | ~$94M | ~$94B |
| same price, one two-level partition | per-run minimums dominate; per-read pricing does not apply at this size | | |

Read pool size is the controlling cost. Without molecular selection, VNX's digital selection saves decoding work but
not sequencing or parsing work. Every read must still be produced and parsed (baseline B.6: "pass-1 still parses
and aligns every read").

**Step 5: what VNX would need, in order.**

1. Fix job #56. Hypothesis, unverified: the select path may assume V4 sequential record ranges, while V6 interleaved
   order spreads superblock strands across the file. `docs/V6_OUTER_CODE.md` §3 states that
   `vnx locate --dna-profile` computes V4 sequential ranges and does not apply to V6 archives.
2. Add an opt-in short-strand profile that fits under vendor limits with primers.
3. Add a primer module with orthogonality and payload-collision checks (`40-datasets.md` plan b, item 2).
4. Define a partition-addressed pool format: 64-bit group index plus partition and container IDs.
5. Build a tree index whose nodes are independently decodable and partition-placed, pointed to from the superblock or
   a Sector One record.
6. Add a pass-1 prefilter that drops reads of other partitions by primer match before alignment.
7. Measure each step SIMULATED first, then on a physical pool.

Competitor status on items 2–5: primer selection is demonstrated [AR-WET] (Organick 2018, Storage-D). No audited
repo or vendor publishes a PB/EB-scale index design (BLACK for vendors).

---

## 11 Storage architecture comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| Normative container format | VNX4 container 4.0, strand frame 4, superblock 1/2 (`docs/VNX4_FORMAT.md`, `docs/V6_OUTER_CODE.md`) | reframed DNAFilePipeline with header + format-ID registry [README]; Storage-D FASTA; TrellisBMA none | GREEN vs TrellisBMA/Storage-D/dnastore (no normative spec found in the audit); YELLOW vs reframed |
| Self-describing pool | 96-byte superblock, 4× redundant (any Ks of 4·Ks strands) | Atlas boot record + metadata as DNA [PRESS, https://www.digitalpreservation.gov/meetings/DSA2026/0231_banyai_2026.03.10%20Atlas%20LOC%20v1.pdf]; DDSA Sector Zero/One [SPEC] | YELLOW (not Sector Zero/One conformant) |
| Multi-file, directories, dedup | PRESENT (file table, type 0/1, `dedup-content-address`) | dnastore objects; reframed file-level | GREEN vs TrellisBMA/Storage-D (single-object); YELLOW vs dnastore |
| Versioning / snapshots / append | ABSENT | dnastore append-only versioning with tombstones [README]; BGI YYC-FileVersionControl (not audited); Genomika dual-level encoding (modifiable metadata + immutable data) [PRESS] | RED |
| Format compatibility | golden V4/V5 archives must decode exactly (`tests/compat/test_v4_v5_archives.py`); unknown superblock version refused | reframed stable format-ID registry [README] | YELLOW |
| Streaming | decode PARTIAL (bounded memory, 8e517ef); encode and parallel pipeline ABSENT | helix streaming [README] | YELLOW |
| Scale demonstrated | 1 GiB clean-channel round trip (33,557,314 strands, encode 96.8 s, decode 244.4 s, 326 MB RSS); noisy ≤16 MiB (SIMULATED, `docs/V4_COMPLETION_REPORT.md`) | LoC/MISL ~1.0 GB physical [AR-WET]; Atlas "TB-scale by end 2026" [PRESS] | YELLOW (software scale only) |
| OAIS alignment | none documented | DNAMIC / Genomika target OAIS [PRESS, https://cordis.europa.eu/project/id/101115389] | RED |

---

## 12 API comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| CLI | `vnx` (typer): archive, inspect, list, verify, locate, extract, encode, decode, validate, channel simulate, benchmark; structured events `--events`, reports `--report` (`src/vnxdna/v4/cli.py`, `src/vnxdna/v6/observe.py`) | many audited repos ship scripts or notebooks; dt4dds-benchmark shell wrappers | GREEN vs TrellisBMA (notebooks only, demonstrably no CLI); YELLOW vs others |
| Library API | PARTIAL: `src/vnxdna/api.py` (V1–V3 era), `v2/api.py`; no `docs/API.md`; V9 SDK not started | Chamaeleo `AbstractCodingAlgorithm` encode/decode interface [AUDIT, code read]; reframed builder functions; dnastore `DNAStorage` class + entry-point plugins [README] | YELLOW |
| Object store / S3 data path | ABSENT | **Biomemory**: S3-standard interface claimed for datacenter appliances [PRESS, https://www.blocksandfiles.com/architecture/2026/03/06/french-dna-coming-to-a-datacenter-near-you-soon/4093753]; **Scality** partnership, integration roadmap "in coming months" [PRESS, https://www.scality.com/press-releases/dna-data-storage-biomemory-scality] | RED / BLACK (Biomemory implementation not public) |
| Object API with lifecycle | ABSENT | **dnastore** (Apache-2.0): store / retrieve / update / delete; update marks old strands obsolete; delete is a tombstone in an append-only store; FUSE VFS; vendor stubs; simulation only [README, https://github.com/Mr-PU/dnastore] | RED |
| Management API | ABSENT | **DNA Data Storage Alliance draft management API**: SNIA Swordfish working draft (Jan 2026) modelling DNA storage systems as object stores plus "DNA process" resources [SPEC, https://www.snia.org/sites/default/files/technical-work/swordfish/draft/DNA%20Data%20Storage%20in%20Swordfish%20-%20Working%20Draft.pdf]; no implementation found | RED (no one implements it yet; opportunity §23) |
| Vendor/service software layer | ABSENT | Atlas "MXL" Molecular Expression Layer [PRESS]; Atlas "open-source script" for self-service read-back [PRESS, not found publicly] | BLACK |
| FUSE / POSIX | ABSENT ("FUSE" hits in `src/` are the word "refuse") | dnastore optional FUSE [README] | RED, intentionally (appendix item 9) |

Design implication for a future VNX API (30-companies §3, item 3):

* the data path converges on S3;
* the management path converges on Swordfish/Redfish REST/JSON resources: encode, synthesise, store, retrieve,
  sequence, decode, verify;
* a VNX object API should be append-only (versions plus tombstones, as in dnastore), and should not offer a DELETE
  that implies molecules were erased (appendix item 4).

---

## 13 Simulation comparison

| capability | VNX channel (`src/vnxdna/v4/channel.py`, `src/vnxdna/v6/loss.py`, 14 models in `experiments/v6/channel/models/`) | competitor evidence | status |
|---|---|---|---|
| sub / ins / del | PRESENT, i.i.d. per base | all simulators | YELLOW |
| dropout (i.i.d.) and contiguous burst loss | PRESENT | dnastore dropout-first ~10–15 % default [README]; StairLoop >30 % per block [AR-WET] | YELLOW |
| coverage fixed / Poisson / negative binomial | PRESENT | dt4dds lognormal coverage, σ 0.58 (GC-constrained) vs 1.30 (unconstrained) [AR-WET calibration, Gimpel 2023] | YELLOW (no lognormal) |
| per-read deletion bursts, homopolymer multipliers, GC-dependent coverage, PCR-style duplication, N calls, reverse complement, quality scores | PRESENT | dt4dds deletion runs mean 2.6 nt [AR-WET]; DNATerra GC/homopolymer/motif bias [README] | YELLOW |
| position-dependent errors | ABSENT | dt4dds (electrochemical deletions >5 %/nt toward the 5' end) [AR-WET]; DNATerra [README]; reframed position-rate injector [README] | RED |
| synthesis vs sequencing error split (shared per molecule vs per read) | ABSENT | dt4dds stage-wise synthesis/PCR/ageing/SBS [AR-WET calibration] | RED |
| PCR dynamics, ageing, chimeras, truncation, breakage | ABSENT (`docs/LIMITATIONS.md`) | dt4dds PCR 1.09e-4 /nt/cycle, ageing 1.64e-4 /nt per half-life [AR-WET]; ArchiGen read amplification [AR-SIM]; MESA [AR-SIM] | RED |
| platform-fitted models | ABSENT: "illumina-like"/"nanopore-like" are "not fitted to any measured platform" | dt4dds fitted on 40 experiments [AR-WET]; DeSP validated against in vitro [README] | RED |
| real-read replay | ABSENT (VNX decodes only VNX4 frames) | reframed real-FASTQ replay + downsampling [README] | RED |
| provenance and versioning of models | PRESENT: named, versioned JSON models; seeded; results with provenance (`experiments/v6/channel`) | dt4dds-benchmark HDF5 sweeps, pinned tool versions [README] | YELLOW |
| evidence-class framework | PRESENT: REAL PHYSICAL RESULT / SIMULATED RESULT / SYNTHETIC SOFTWARE TEST (`docs/V6_PHYSICAL_VALIDATION_INTERFACE.md`); **no PUBLIC-DATA-DERIVED class** | none of the audited repos has an evidence-class schema | GREEN vs audited repos (demonstrably absent); gap: PUBLIC-DATA-DERIVED |

Public priors usable now (`40-datasets.md`, Gimpel 2023 Nat Commun 14:6026, https://doi.org/10.1038/s41467-023-41729-1,
[AR-WET]):

* deletions 6.7 ± 6.9, substitutions 7.9 ± 2.0 and insertions < 0.3 ± 0.2 per 1,000 nt over 40 datasets;
* iSeq 100 substitutions 1.8 ± 0.8e-3 /nt;
* coverage is lognormal.

The 53 % sub / 45 % del / 2 % ins composition used in Gimpel 2026 is the realistic mix that VNX's models do not yet
reproduce.

---

## 14 Testing comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| Test volume | 1,690 passed, 3 skipped in a full-suite run on a clean checkout of 081697b (2026-10-05, lab results/full-suite-081697b-20261005-0515.txt); the "1,781" shown by the status tool belongs to a WIP tree, not to a commit (baseline A.5); 154 test files | reframed ~135 test functions + GitHub Actions [AUDIT, grep count]; dnastore unit tests + CI badge [README]; Storage-D one test file; TrellisBMA none; BHE random round trip only; YYC none | GREEN vs TrellisBMA/Storage-D/YYC/BHE (demonstrably thin or absent); YELLOW vs reframed/dnastore (volume is not quality) |
| Golden / compatibility | aligner golden hashes 10/10; native RS vectors; stored V4/V5 archives must decode exactly (`tests/compat`) | reframed format-ID registry (no golden archives seen) | GREEN vs audited repos (none seen with cross-version golden archives) |
| Differential fuzzing native vs reference | V5 aligner 700,000 reads, 0 mismatches; V6 parser 100k + 3 × 20k; V6 RS ~1.05M comparisons, 0 mismatches | none seen in audited repos (reframed "fuzz" = fault injection) | GREEN vs audited repos |
| Sanitizers | gcc ASan+UBSan, clang UBSan(-trap); clang ASan + valgrind for V6 kernels; **no MSan; clang ASan never run for the V5 aligner; x86-64 only** | none seen | GREEN vs audited repos, with listed gaps |
| libFuzzer / atheris | PARTIAL: reads-parser libFuzzer harness outside the repo (~7M execs, 0 crashes); RS/aligner harnesses missing (job #10); atheris absent (job #21) | none seen | YELLOW |
| Property tests | hypothesis campaigns for archives, manifests, frames, read files, configs | none seen | GREEN vs audited repos |
| CI | main and v5.0.0 green; **build/v6-p1 never pushed, so CI has never run on V6** | reframed, dnastore, UNACORM have CI badges/workflows [README] | RED for V6 |
| Real-data tests | none (no wet-lab or public reads in the repo) | reframed real-FASTQ replay; TrellisBMA CNR | RED |

---

## 15 Performance comparison

Rule: author-reported numbers are listed next to VNX SIMULATED numbers only to show what exists. **Every row is "not
comparable"**, because no row shares input, channel, code rate, hardware and metric. The three [AUDIT] rows were
measured on the same host class as VNX (Xeon Gold 6240, shared VPS, ~10 % noise), but they measure different
functions.

| system | metric | number | label | VNX number (SIMULATED, source) | comparable? |
|---|---|---|---|---|---|
| BHE (Microsoft) | mapping encode/decode, 1 thread, k=3, N=150 | ~42 / ~74 Mbit/s | [AUDIT] | V4 encoder "a few MB/s per core", constraint screening + frame building (`docs/LIMITATIONS.md`) | **not comparable** (mapping primitive vs full encoder with ECC) |
| HEDGES (whpress) | demo throughput incl. encode + error injection + decode, 1 thread, rate 0.5 | ~1.1 KB/s of message | [AUDIT] | 4 MiB noisy decode 7.03 s (≈0.6 MB/s) with native RS (`benchmarks/v6/native_rs/results/bench.json`, dirty tree) | **not comparable** (different channel, code, pipeline stages) |
| Chamaeleo | 4,000-byte round trip (Base/Goldman/Blawat/Church) | 0.05–0.12 s | [AUDIT] | 1 MiB round trip 1.218 s (v5.0.0) vs 1.225 s (V6 tree) (uncommitted results dir) | **not comparable** |
| hedges-soft-decoder | decode speed vs prior soft decoder | 257× faster at 3.52 % byte error | [AR-WET reads; search summary] | V5 aligner ×9.6–11.3, 28–30 Mbases/s (`docs/V5_PHASE2_NATIVE_ALIGNMENT.md`) | **not comparable** |
| BHE README | encode/decode | ~50 / ~80 Mbps | [README] | V6 native read parser 557 MiB/s (×4.81) (`benchmarks/v6/native_reads/results/bench_reads.json`, dirty tree) | **not comparable** |
| DNA Fountain | density | 1.57 bits/nt | [AR-WET] | 0.81 bits/nt net at v4-balanced (`docs/V4_RESULTS.md`) | **not comparable** (different redundancy target, strand length, channel) |
| YYC | density | 1.75–1.78 bits/nt | [AR-WET] | 0.81 bits/nt | **not comparable** |
| Wukong | coding potential | ~1.98 bits/nt | [AR-WET] | raw mapping 2 bits/nt; 1.02 bits/nt before outer code (THEORETICAL) | **not comparable** |
| Gimpel 2026 (6 codecs) | storage density in experiment | 43 / 13 EB/g | [AR-WET] | none (VNX has no physical density) | **not comparable** |
| mahoraga | density on DT4DDS channel | 155.8 / 25.9 EB/g | [AR-SIM], single author | none | **not comparable** |
| VNX V4 | 1 GiB clean round trip | — | — | encode 96.8 s, decode 244.4 s, 326 MB RSS; clean throughput 4.0–4.5 MB/s at 4 workers; noisy (EXP-0011, cov 3) 0.17 → 0.91 MB/s at 1 → 8 workers (`docs/V4_COMPLETION_REPORT.md`) | — |

Provenance caveat for VNX: both V6 native-kernel benchmarks were recorded at 8e517ef with `git_dirty: true`, before
the kernels were committed. That is weaker than the repository's own standard (baseline A.4, discrepancy 6). The
V5 "reduced memory" claim holds for the aligner (19.2 → 6.0 MB) and not end to end: decode peak RSS was unchanged
at 167 MB on 1 worker.

---

## 16 Physical-validation comparison

Levels: 1 = software only; 2 = in-vitro synthesis; 3 = sequencing experiments (synthesised pool read back);
4 = full end-to-end archival including storage or ageing and retrieval. The levels describe the cited paper or
programme, not the repository code.

| project | level | evidence | label |
|---|---|---|---|
| **VNX-DNA @081697b** | **1** | no wet-lab or real-sequencing data anywhere in the codebase; physical-record schemas + validator exist (`experiments/v6/physical/`), example classified SYNTHETIC SOFTWARE TEST; commit 6c02f7a: "no physical testing has occurred" | SIMULATED |
| Grass 2015 / DNA-RS | 4 | silica encapsulation + accelerated ageing | [AR-WET] |
| Storage-D / Wukong | 4 | Twist synthesis, MiSeq, in vivo 7 days | [AR-WET] |
| UW MISL for LoC | 4 (programme) | ~1.0 of 1.5 GB synthesised, stored, validated | [AR-WET via LoC slides] |
| Organick 2018 | 3 | 35 files, 13.4M oligos, Illumina + MinION | [AR-WET] |
| HEDGES 2020 | 3 | 5,865 Twist oligos, MiSeq, mutagenesis | [AR-WET] |
| DNA Fountain 2017 | 3 | 72,000 Twist oligos | [AR-WET] |
| YYC 2022 | 3 (+ in vivo yeast) | 10,103 oligos in vitro | [AR-WET] |
| DNA-Aeon 2023 | 3 | in vitro per abstract | [AR-WET] |
| StairLoop 2025 | 3 | electrochemical synthesis, <3× depth | [AR-WET] |
| Gimpel 2026 six-codec benchmark | 3 | one pool, iSeq 100, two synthesis scenarios (ENA PRJEB90546) | [AR-WET] |
| TrellisBMA / CNR | 3 (reads by MISL) | Twist pool on MinION | [AR-WET] |
| JPEG DNA (ISO/IEC 25508-1) | 3 | independent wet-lab synthesis/sequencing decoded correctly before DIS | [PRESS, https://jpeg.org/items/20260608_press.html] |
| AtlasBase | unknown (commercial service claimed) | no independent third-party decode audit found | [PRESS] → BLACK |
| Biomemory | KB-scale cards shipped | datacenter system not demonstrated publicly | [PRESS] → BLACK |
| dnastore, helix, SPIDER-WEB, Gungnir, mahoraga, NOREC4DNA, DNATerra, BHE, Chamaeleo | 1 | simulation or software only | [AR-SIM]/[README]/[AUDIT] |

VNX status: **RED.** Every physically validated competitor is at level 3 or 4. VNX shares level 1 with the
simulation-only repositories. The lowest-cost route to level 3, with no VNX lab, is in `40-datasets.md` plan b:

1. a short-strand profile;
2. primers;
3. `vnx order-export`;
4. a purchased Twist/GenScript pool, sequenced by a service lab;
5. the existing physical-record schema.

Before that, plan a gives an intermediate evidence class: fit channel models to public reads and label the resulting
parameter files PUBLIC-DATA-DERIVED. The decode results stay SIMULATED.

---

## 17 Security comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| Encryption at rest | AES-256-GCM per chunk; HKDF-SHA256 subkeys; nonce = domain ‖ index under a per-archive salted key (`docs/VNX4_FORMAT.md` §6, `src/vnxdna/v4/crypto.py`) | helix XChaCha20-Poly1305 [README, unverified]; Storage-D "codec pins" (obfuscation, not cryptographic) [AR-WET paper]; TrellisBMA, dnastore, reframed none (audit JSON); Atlas "customer sends encrypted data" [PRESS] | GREEN vs TrellisBMA/dnastore/reframed/Storage-D (demonstrably absent); YELLOW vs helix; BLACK vs vendors |
| KDF | 32-byte key file or scrypt passphrase; scrypt caps (memory ≤1 GiB, N·r·p ≤ 2²⁵) checked before KDF (cc8d8c4) | none seen | GREEN vs audited repos |
| Integrity chain | file SHA-256 → chunk ID → RFC 6962 Merkle root → manifest SHA-256/HMAC → trailer; container SHA-256 in superblock; `vnx verify --chunk` Merkle proofs | CRC16 (dnastore), CRC/RS (reframed), RS (Storage-D), none (TrellisBMA) | GREEN vs audited repos |
| Fail-closed publish | verify before publish; PARTIAL extracts only verified files (exit 9); 0 false SUCCESS in all V5/V6 experiment decodes (SIMULATED) | no audited repo states a wrong-output policy | GREEN vs audited repos |
| Downgrade / malformed input | key + unencrypted archive refused unless `--allow-unencrypted` (7450ede); forged superblock = format error (deeee20); reports atomic, 0600, no symlink (b772ce5) | none seen | GREEN vs audited repos |
| Scan status | CRIT 0, HIGH 0, MED 1 (cppcheck uninitvar `v5/native/align.c:322`), LOW 37 (security scan @5c39a50); 10 earlier MEDIUMs triaged not reachable (c41f83e) | none seen | YELLOW (one open MEDIUM) |
| Key management (rotation, multi-recipient, KMS/HSM, escrow) | ABSENT | none found in repos; vendors BLACK | RED |
| Size/metadata leakage | encrypted archives leak approximate size, per-chunk compressibility and all ECC parameters; no padding (`docs/LIMITATIONS.md`) | not addressed by audited repos | RED (known, documented) |
| Known caveat | DNA decode of an encrypted archive without the key returns SUCCESS (ciphertext container); fail-closed only at extract | — | YELLOW |
| Biosecurity screening of payload sequences | ABSENT | DDSA biosecurity policy position wants "no problem sequences in the pipeline"; no tooling found [SPEC/PRESS, https://www.storagenewsletter.com/2025/07/16/snia-published-two-dna-data-storage-white-papers/] | RED (opportunity) |
| Long-horizon crypto agility | not documented | not addressed by anyone found | RED |

---

## 18 Interoperability comparison

| capability | VNX @081697b | competitor evidence | status |
|---|---|---|---|
| Read input formats | FASTA, FASTQ, plain; streaming native parser; **no BAM** | general tools (dnaio, minimap2) | YELLOW |
| Decode third-party pools | ABSENT: the decoder only decodes VNX4 frames | Chamaeleo wraps 7 mappers; reframed 5 pipelines; dt4dds-benchmark 6 codecs + wrappers | RED |
| Plug into external harnesses | ABSENT (no `encode.sh`/`decode.sh` wrapper, no Chamaeleo subclass) | dt4dds-benchmark and UNACORM accept external codecs via thin wrappers [AUDIT, README read] | RED (low cost to fix) |
| Molecular self-description standard | VNX superblock (proprietary layout) | DDSA Sector Zero (70 bases: 35 vendor + 35 codec ID) and Sector One (archive metadata, file table, sequencer parameters) [SPEC]; Atlas boot record [PRESS] | RED |
| Codec registry | none | DDSA "Rosetta" registry concept; the one public server found (jchristn/RosettaStone, MIT) is inactive since Aug 2023 [README] | RED (opportunity) |
| Image payload standard | none | JPEG DNA ISO/IEC 25508-1 at DIS (Apr 2026) [SPEC] | RED |
| Synthesis/sequencing record exchange | physical-record JSON schemas (synthesis, sample, storage, sequencing, decode, attestations) | none seen in repos | GREEN vs audited repos (absent there); not a standard |
| Prior open format attempts | — | Helixworks openMoSS converter [README, https://github.com/Helixworks/helix-dnadrive] | — |

---

## 19 Standards analysis

| standard | what it specifies | status | VNX status | action implied |
|---|---|---|---|---|
| **DDSA Sector Zero v1.0** | 70 bases: 35 identify the vendor, 35 the codec, so a reader can find the codec for Sector One | approved 11 Nov 2023, released 12 Mar 2024 [SPEC, https://www.snia.org/news_events/newsroom/dna-data-storage-alliance-releases-its-first-specifications-storage-digital] | RED | a VNX-encoded pool should carry a Sector Zero header; this needs a vendor/codec ID allocation |
| **DDSA Sector One v1.0** | archive metadata: content description, file table, sequencer parameters | released [SPEC, https://snia.org/standards/technology-standards-software/standards-portfolio/dna-data-storage-sector-one] | RED (VNX superblock + manifest cover similar ground in a proprietary layout) | map the VNX superblock/manifest onto Sector One; write a conformance reader/writer |
| **DNA Stability Evaluation Method v1.0** | half-life metric for containment systems | Sep 2024 [SPEC, https://www.snia.org/educational-library/dna-stability-evaluation-method-dna-data-storage-containment-systems-v10-2024] | not applicable to software directly | VNX's channel needs an ageing stage parameterised by half-life, so that retention calculations can cite this method |
| **Codecs white paper: Examples, Requirements and Metrics v1.0** (+ Technology Review v1.0, biosecurity position) | industry codec metrics and requirements | 30 Jun 2025 [SPEC, https://www.storagenewsletter.com/2025/07/16/snia-published-two-dna-data-storage-white-papers/] | RED (VNX does not report against it) | report VNX metrics in the white paper's terms alongside dt4dds-benchmark results; the white paper text was not read by this audit, so its exact metric list must be checked first |
| **DDSA draft management API (SNIA Swordfish, DNA)** | Redfish/Swordfish REST resources for DNA storage systems: object stores + "DNA process" resources | working draft Jan 2026 [SPEC, https://www.snia.org/sites/default/files/technical-work/swordfish/draft/DNA%20Data%20Storage%20in%20Swordfish%20-%20Working%20Draft.pdf] | RED | a future VNX service API should map its jobs (encode, verify, decode) onto these resources |
| DDSA work items 2025–26 | data-retention calculator, solid-state nanopore channel, archive self-discovery (alternative to Rosetta), Random Access reference paper, roadmap | in progress; co-chairs Franceschini (Biomemory) and Skliaustas (Genomika) [PRESS, LoC DSA 2026 slides] | RED | contribution opportunities (§23) |
| **JPEG DNA, ISO/IEC 25508-1** | image codec producing nucleotide sequences under biochemical constraints | DIS at the 111th JPEG meeting (13–17 Apr 2026); IS expected before end-2026 [SPEC, https://jpeg.org/items/20260608_press.html, https://www.iso.org/standard/90579.html] | RED | carry JPEG DNA payloads inside a Sector Zero/One-wrapped VNX archive as an interoperability demonstration; images only |
| IEEE | DNA chapter in the Mass Storage Roadmap; ISIT 2024 workshop; JSAIT special issue | no IEEE standards project found [PRESS] | — | none |
| OAIS (ISO 14721) | archival reference model | DNAMIC targets OAIS compliance [PRESS] | RED (no OAIS mapping documented) | document how VNX archives map to OAIS information packages |

No reference implementation or conformance suite was found for Sector Zero/One. The SNIA GitHub organisation (21
repositories) has no DNA repository (30-companies §3).

---

## 20 VNX strengths (evidence-backed only)

Each item states its evidence and what it has **not** been benchmarked against.

1. **End-to-end cryptographic integrity and fail-closed decoding.** The SHA-256 / Merkle / HMAC chain and the
   verify-before-publish rule are documented in `docs/VNX4_FORMAT.md` §9. The V5/V6 experiments (SIMULATED) recorded
   0 false SUCCESS. GREEN against TrellisBMA, Storage-D, dnastore and reframed, which demonstrably lack a
   cryptographic chain. Not benchmarked against vendor systems (BLACK).
2. **Authenticated per-chunk encryption with a bounded KDF** (AES-256-GCM, HKDF, capped scrypt). GREEN against the
   audited repos. Not benchmarked against helix (XChaCha20-Poly1305, unverified) or vendors.
3. **A normative, versioned format with cross-version golden archives** (`docs/VNX4_FORMAT.md`,
   `docs/V6_OUTER_CODE.md`, `tests/compat`). GREEN against repos without a spec. YELLOW against reframed (it has a
   format-ID registry).
4. **A product outer code with an analytic planner.** Simulated dropout threshold 0.07 → 0.16 at ≈equal overhead;
   4,096-strand burst survival (SIMULATED, `experiments/v6/phase1/summary.md`). Not benchmarked against DNA-RS 2-D
   RS, StairLoop LDPC × conv, DNA Fountain or Raptor under a common channel.
5. **Bounded, verified indel recovery.** Median erased nt per indel 24 → 4. Coverage-1 threshold 0.2–0.3 % →
   0.4–0.5 % ins+del (SIMULATED, `docs/V5_PHASE3_INDEL_RECOVERY.md`). Not benchmarked against HEDGES, DNA-Aeon,
   SPIDER-WEB, Gungnir or TrellisBMA.
6. **Native kernels with demonstrated equivalence and memory safety.** 700k + ~1.05M + 160k differential
   comparisons, 0 mismatches; ASan/UBSan/valgrind clean. GREEN against the audited repos (none seen with this
   discipline). Gaps: no MSan, no non-x86 builds.
7. **Disciplined evidence labelling.**
   * Every codec result is labelled SIMULATED.
   * A physical-record schema with evidence classes exists.
   * A competitor-comparability framework exists: `benchmarks/competitors/records.json`, 12 literature systems with
     computed comparability labels.

   GREEN against the audited repos (none has an evidence-class schema).
8. **Archive features beyond a codec:** multi-file, directories, dedup, compression, and Merkle per-chunk
   verification with digital random access. GREEN against TrellisBMA and Storage-D. YELLOW against dnastore (which
   has an object store, versioning and FUSE).

---

## 21 VNX weaknesses

1. **No physical evidence of any kind** (level 1). Competitors with public code are at levels 3–4.
2. **No same-protocol benchmark against any external codec.** Every comparative statement is therefore blocked.
3. **Channel models are not fitted to any platform.** There are no position-dependent errors, no
   synthesis/sequencing split, no lognormal coverage, and no PCR or ageing stage.
4. **No indel-correcting code and no joint multi-read reconstruction.** Indels cost erasures. A synthesis indel shared
   by every copy becomes a strand erasure.
5. **Orphans.** Reads with a damaged header cannot be grouped: 31 % at a harsh simulated channel. There is no
   unaddressed clustering in the V4+ decoder.
6. **Constraint method is screening, not coding.** Strict rules (homopolymer ≤3 over ~300 nt) can make encoding fail.
   The rate gap has never been measured.
7. **The strand is too long for primers within vendor limits** (353 nt > 350 nt). There is no short profile matching
   public datasets (110–157 nt).
8. **Random access is digital only.** Pass 1 parses and aligns every read. V6 `--select` is broken on stripe archives
   (job #56).
9. **Address space is about 11 TB per archive (THEORETICAL).** The flat chunk table becomes 8 GB at 100 TB.
10. **No API beyond the CLI**: no S3, no Swordfish, no versioning, no SDK documentation.
11. **No standards conformance**: Sector Zero/One, codec white paper metrics, JPEG DNA.
12. **Release hygiene on V6:**
    * the branch has never been pushed, so CI has never run;
    * `docs/V6_PHASE1_REPORT.md` is cited in the CHANGELOG but does not exist;
    * native benchmarks were recorded on a dirty tree;
    * test counts in status tools refer to a WIP tree;
    * one MEDIUM static-analysis finding is open;
    * clang ASan has not been run for the V5 aligner.
13. **Performance is CPU-bound Python/NumPy outside the three native kernels.** Noisy decode throughput is
    0.17–0.91 MB/s (V4, SIMULATED). Encode with the V6 adaptive plan is 11× slower than V5 (4.4 s against 0.39 s).
14. **No key management.** Encrypted-archive size leaks and there is no padding.

---

## 22 VNX missing capabilities

Ranked by how many other gaps each one unblocks. Every item answers the founder's ten questions. COST and
COMPLEXITY are engineering estimates (THEORETICAL), not quotes. No capability below has been benchmarked by VNX.

### 22.1 Same-protocol external benchmark (dt4dds-benchmark / UNACORM adapter)

| question | answer |
|---|---|
| WHY | Without it, no comparison with any codec is allowed under the colour rule, and every RED/YELLOW above stays unresolved |
| WHAT PROBLEM | VNX numbers are SIMULATED on VNX's own channel; reviewers will judge simulated claims against dt4dds (`20-repos-cluster2.md` §8 insight 2) |
| WHO NEEDS IT | investors, the DDSA codecs working group, academic partners, any archive buyer running an RFI (LoC template) |
| COMPETITOR EVIDENCE | Gimpel et al. 2026 benchmarked six codecs on one protocol and one pool (doi:10.1038/s41467-026-70548-3) [AR-SIM + AR-WET]; UNACORM 8 codecs (arXiv 2608.09673) [AR-SIM]; mahoraga already reports on the DT4DDS channel [AR-SIM] |
| BENCHMARK needed | VNX as an external `encode.sh`/`decode.sh` codec in dt4dds-benchmark at matched code rates (0.5/1.0/1.5 bits/nt), strand length ~130–150 nt and the published coverage sweeps; repeat the six default codecs to validate the harness |
| COST | low: wrapper scripts + a short-strand profile; Docker image exists |
| COMPLEXITY | low–medium (the harness is GPL-3.0: process boundary only, nothing linked or copied) |
| SECURITY IMPACT | none on the product; harness runs on public data |
| COMMERCIAL VALUE | high: it is the one route to a defensible "vs X" statement |
| MOAT? | no (anyone can run it); it is a credibility prerequisite, not a moat |

### 22.2 Platform-fitted channel models from public reads (PUBLIC-DATA-DERIVED)

| question | answer |
|---|---|
| WHY | the 14 VNX models are "not fitted to any measured platform"; simulated thresholds may not transfer |
| WHAT PROBLEM | position-dependent deletions, deletion runs (mean 2.6 nt), lognormal coverage and the synthesis/sequencing split are missing, and these drive consensus and outer-code behaviour |
| WHO NEEDS IT | the VNX decoder team; anyone reading VNX simulated results |
| COMPETITOR EVIDENCE | dt4dds fitted on 40 experiments (doi:10.1038/s41467-023-41729-1) [AR-WET]; reframed real-FASTQ replay [README]; DNATerra platform profiles [README] |
| BENCHMARK needed | round-trip fit test (simulate → fit → recover parameters within binomial CIs); fits on CNR (D04), D03, DT4DDS (D02), ETH (D01); rerun V6 Phase 1 sweeps under fitted models |
| COST | medium: fitter, schema v2, a PUBLIC-DATA-DERIVED evidence class, `datasets.lock.json` with hashes (`40-datasets.md` plan a) |
| COMPLEXITY | medium |
| SECURITY IMPACT | low; downloaded data must be hash-pinned, and parsers of external FASTQ are untrusted input (already fuzzed) |
| COMMERCIAL VALUE | medium–high: turns "simulated on a toy channel" into "simulated on a published-data channel" |
| MOAT? | weak (the data are public); the provenance discipline is a differentiator, not a moat |

### 22.3 Physical-validation round (order export + primers + vendor-length profile)

| question | answer |
|---|---|
| WHY | level 1 vs level 3–4 for every physically validated competitor |
| WHAT PROBLEM | no evidence that VNX strands synthesise, amplify and decode as simulated |
| WHO NEEDS IT | every buyer and investor; the DDSA; JPEG DNA-style independent tests |
| COMPETITOR EVIDENCE | HEDGES, DNA Fountain, YYC, DNA-Aeon, StairLoop, Organick 2018, Gimpel 2026 [AR-WET]; LoC buys from a lab (MISL) [PRESS] |
| BENCHMARK needed | one Twist/GenScript pool of VNX strands (short profile + primers), Illumina via a service lab, decoded with the existing physical-record schema; report REAL PHYSICAL RESULT only with provider, order ID and dates |
| COST | medium (purchased synthesis + sequencing; no VNX lab); vendor pricing not sourced in this audit |
| COMPLEXITY | medium (primer module, order export, read ingestion with primer trimming) |
| SECURITY IMPACT | biosecurity screening of ordered sequences becomes relevant (see 22.10) |
| COMMERCIAL VALUE | high |
| MOAT? | no; it removes a disqualifier |

### 22.4 Molecular random access (primer partitions + tree index + fix #56)

| question | answer |
|---|---|
| WHY | §10.2: without molecular selection, one file from 100 TB means ~1e13 reads (THEORETICAL) |
| WHAT PROBLEM | read-pool size, sequencing cost and parse time scale with the archive, not with the file |
| WHO NEEDS IT | any archive above GB scale; the DDSA Random Access work item |
| COMPETITOR EVIDENCE | Organick 2018 primer random access over 13.4M oligos [AR-WET]; Storage-D primer per file [AR-WET]; reframed primers + hierarchical index [README]; dnastore primer per object [README]; vendors BLACK |
| BENCHMARK needed | SIMULATED: fraction of reads from the target partition after a primer-match prefilter; cross-talk under fitted channels; job #56 regression test (D=4, Mc=2, coverage 2–3); later physical PCR selection |
| COST | medium–high |
| COMPLEXITY | high (format change: partition/container IDs, 64-bit group index; primer orthogonality; nested PCR error) |
| SECURITY IMPACT | partition IDs and primer sets are metadata visible without the key (a size and structure leak); must be documented |
| COMMERCIAL VALUE | high for PB-scale buyers |
| MOAT? | possible, if VNX defines a vendor-neutral partition-addressing scheme adopted through the DDSA; otherwise no |

### 22.5 Pool-scale address space and hierarchical metadata

| question | answer |
|---|---|
| WHY | THEORETICAL limit ≈11 TB per VNX4 archive (4-byte group index at v4-balanced); 2-byte archive tag collides at ~300 archives per pool |
| WHAT PROBLEM | 100 TB/1 PB/1 EB archives cannot be expressed; the flat chunk table becomes 8 GB / 80 GB / 80 TB |
| WHO NEEDS IT | any archive buyer above ~10 TB |
| COMPETITOR EVIDENCE | dnastorage CFC8 index codewords and reframed hierarchical index ints [README]; no audited repo or vendor publishes a PB/EB design (BLACK) |
| BENCHMARK needed | encode/decode of a synthetic ≥10 TB *logical* address space with sparse payload (metadata-only test); index depth and node-read counts vs §10.2 arithmetic |
| COST | medium |
| COMPLEXITY | high (new superblock/frame versions, golden fixtures, cross-version decode tests per the format-compat rules) |
| SECURITY IMPACT | index nodes must stay authenticated (Merkle over nodes) |
| COMMERCIAL VALUE | high (gate to large-archive conversations) |
| MOAT? | weak alone; strong combined with 22.8 standards conformance |

### 22.6 Indel-native inner code or joint multi-read trace reconstruction

| question | answer |
|---|---|
| WHY | indels cost erased segments; shared synthesis indels become strand erasures; electrochemical synthesis shows >5 %/nt deletions toward the 5' end [AR-WET, Gimpel 2023] |
| WHAT PROBLEM | coverage and redundancy needed at realistic deletion-dominated channels |
| WHO NEEDS IT | low-cost synthesis chemistries (electrochemical, enzymatic), nanopore readout |
| COMPETITOR EVIDENCE | HEDGES [AR-WET]; DNA-Aeon [AR-WET]; StairLoop BCJR [AR-WET]; TrellisBMA [AR-SIM/AR-WET reads]; Gungnir 20 % single-copy [AR-SIM]; SPIDER-WEB [AR-SIM] |
| BENCHMARK needed | CNR trace-reconstruction score for VNX consensus vs TrellisBMA/BMALA/bbs; dt4dds-benchmark at matched rate vs HEDGES and DNA-Aeon |
| COST | high (research) |
| COMPLEXITY | high; must keep the fail-closed verifier and format compatibility |
| SECURITY IMPACT | none if acceptance stays CRC + SHA-256 gated |
| COMMERCIAL VALUE | medium–high (lower coverage means lower read cost) |
| MOAT? | only if measured gains survive a same-protocol benchmark; patent FTO needed (Microsoft US11600360B2 trace reconstruction) |

### 22.7 Unaddressed read clustering at scale

| question | answer |
|---|---|
| WHY | 31 % orphans at a harsh simulated channel (`docs/LIMITATIONS.md`) |
| WHAT PROBLEM | reads with a damaged header are wasted coverage |
| WHO NEEDS IT | high-error channels, nanopore |
| COMPETITOR EVIDENCE | Rashtchian clustering used for CNR; reframed LSH; dt4dds-benchmark 6 clusterers; Clover (GPL) [README]; V3 `vnx-dna` already had minimizer second-chance clustering (`docs/CONSENSUS.md`), not carried into the V4+ decoder |
| BENCHMARK needed | orphan rate and recovered reads vs a header-only baseline under fitted channels; clustering accuracy on CNR |
| COST | medium |
| COMPLEXITY | medium (memory bounds at 1e9+ reads) |
| SECURITY IMPACT | denial-of-service via adversarial read pools; caps needed, as in V3 |
| COMMERCIAL VALUE | medium |
| MOAT? | no |

### 22.8 Standards conformance (Sector Zero/One reader/writer, conformance suite, codec-metric reporting)

| question | answer |
|---|---|
| WHY | no reference implementation or conformance suite exists publicly; "open source codec TBD" in the DDSA codecs group |
| WHAT PROBLEM | archives cannot be self-identified across vendors; buyers depend on one vendor's decoder |
| WHO NEEDS IT | national archives (LoC, OAIS-oriented EU projects), the DDSA, multi-vendor buyers |
| COMPETITOR EVIDENCE | DDSA specs [SPEC]; RosettaStone registry inactive since 2023 [README]; Atlas boot record (proprietary) [PRESS] |
| BENCHMARK needed | conformance test vectors round-trip (Sector Zero/One encode → decode), interoperability test with any other implementation that appears |
| COST | low–medium (spec reading, writer/reader, test vectors) |
| COMPLEXITY | medium (molecular encoding of Sector Zero is fixed by the spec; requires DDSA membership to obtain IDs) |
| SECURITY IMPACT | Sector Zero/One metadata are in the clear by design; must not carry key material |
| COMMERCIAL VALUE | high (vendor-neutral position, 30-companies §6 gaps 1–2) |
| MOAT? | yes, if VNX becomes the maintained reference implementation (an ecosystem position, not a technical lock-in) |

### 22.9 Object and management API (S3-compatible data path, Swordfish-style management, append-only versions)

| question | answer |
|---|---|
| WHY | enterprise buyers integrate through S3; the DDSA management model is Swordfish |
| WHAT PROBLEM | VNX is a CLI and a partial Python API; nothing an IT system can call |
| WHO NEEDS IT | storage integrators (Scality-like partners other than Biomemory's), archive operators |
| COMPETITOR EVIDENCE | Biomemory S3 claim + Scality partnership [PRESS]; dnastore object API with tombstones [README]; Swordfish DNA draft [SPEC] |
| BENCHMARK needed | API conformance tests (S3 subset: PUT/GET/HEAD/LIST, versioned DELETE markers); Swordfish resource validation against the draft schema |
| COST | medium |
| COMPLEXITY | medium–high (auth, multi-tenant, long-running jobs) |
| SECURITY IMPACT | high: new network attack surface, authentication and authorisation (V9–V11 scope) |
| COMMERCIAL VALUE | high |
| MOAT? | no (S3 is a commodity interface); value is integration speed |

### 22.10 Key management and biosecurity screening

| question | answer |
|---|---|
| WHY | archives meant to outlive keys and staff; DDSA requires "no problem sequences in the pipeline" |
| WHAT PROBLEM | a lost key is a lost archive (`docs/LIMITATIONS.md`); no rotation, escrow, multi-recipient or KMS/HSM; no screening of payload strands against hazardous sequences |
| WHO NEEDS IT | regulated archives, governments, synthesis providers that screen orders |
| COMPETITOR EVIDENCE | DDSA biosecurity policy position [SPEC/PRESS]; Atlas "customer sends encrypted data" (key handling not public) [PRESS]; no audited repo has either |
| BENCHMARK needed | key-rotation and recovery drills; screening false-positive rate on random VNX payloads against a published screening method |
| COST | medium |
| COMPLEXITY | medium |
| SECURITY IMPACT | high (this is the security feature) |
| COMMERCIAL VALUE | medium–high for government buyers |
| MOAT? | possible for codec-level biosecurity attestation (no tooling found anywhere) |

Further gaps outside the top ten:

* constrained coding with Tm and secondary-structure screening (§7);
* versioning and append (§11);
* LDPC or soft iterative outer decoding (§8);
* a transparent, parameterised cost and retention calculator (§23);
* CI and fuzz-harness completion on V6 (§14).

---

## 23 Opportunities competitors appear to miss

Sources: `30-companies.md` §6 and the repository audits. "Appear" because vendor internals are BLACK.

1. **Vendor-neutral, standards-conformant reader and codec.** Every commercial player builds its codec in-house. None
   offers multi-vendor archive portability.
2. **Reference implementation and conformance suite for Sector Zero/One, plus a maintained codec registry.** The one
   public registry server is inactive since Aug 2023.
3. **Independent verification and audit layer.** Durability and integrity claims are vendor-stated. Cryptographic
   proof of decode (a Merkle proof per chunk tied to an attested decode record) is unclaimed. VNX already has the
   Merkle and physical-record pieces (§17, §16).
4. **Implementation of the Swordfish DNA management draft.** None found.
5. **DDSA work items without a product:** data-retention calculator, Random Access reference design, archive
   self-discovery.
6. **Codec-level biosecurity screening with attestation.** Stated as a DDSA requirement; no tooling found.
7. **Transparent cost and TCO modelling.** Public $/MB figures span about four orders of magnitude. No vendor
   publishes a reproducible model.
8. **An evidence-class discipline for public data.** No audited repo separates SIMULATED, PUBLIC-DATA-DERIVED and
   REAL PHYSICAL results in a schema. VNX has three of the four classes.
9. **Geography.** No India-based DNA storage company remains (BioCompute moved to San Francisco). India and Japan
   have no visible DDSA leadership [PRESS].
10. **Maintenance gap in open baselines.** Chamaeleo (2021 code), TeamErlich (2016 code), the Goldman port (2015) and
    TrellisBMA (archived) are stale. HEDGES' old binding was purged. Pinned, reproducible baseline containers for
    these codecs would be useful to the field. Licence limits apply (appendix item 1).

---

## 24 Recommended V6–V12 priorities

Summary (full detail, exit criteria and the ten-question table in [VNX_GLOBAL_ROADMAP.md](VNX_GLOBAL_ROADMAP.md) §2–3):

- **V6, competitive baseline:** fix #56/#13; land the clean-commit performance work and CI; benchmark lab B0/B1 on dt4dds-benchmark against DNA-Aeon, HEDGES, DNA-RS, YYC and DNA Fountain; measure the mapping rate gap against DNABoundedHomopolymerEncoding; RS/aligner fuzz harnesses; consensus design.
- **V7, channel intelligence:** fitted channel models (PUBLIC-DATA-DERIVED); quality-weighted consensus; a short-strand profile at ≤ 300 nt with primers; a primer module, order export and read ingestion; milestone: first oligo-pool order.
- **V8, extreme scale:** streaming and parallel pipelines; hierarchical molecular random access; a wider address space (64-bit group index, collision-free archive IDs; see §10).
- **V9, storage engine:** Sector Zero/One reference implementation and conformance suite, registry, versioning, catalogue, JPEG DNA wrapping, stable SDK.
- **V10, adaptive coding:** soft input end to end, the LDPC vs. RS decision, the inner-code decision, adaptive redundancy, an ML-decoder evaluation.
- **V11, provider integration:** adapter interface, repeatable bought wet-lab rounds, Swordfish-aligned management API, biosecurity screening, retention calculator.
- **V12, unified platform:** SDK, CLI, Storage API, Enterprise Server, verification service.

## 25 Recommended V13–V25 priorities

See [VNX_GLOBAL_ROADMAP.md](VNX_GLOBAL_ROADMAP.md) §4:

- **V13–V20, advanced storage infrastructure:** multi-pool and multi-vendor archives, erasure across physical pools, billion-object catalogue, key management, cryptographic proof of decode, format migration, OAIS packaging.
- **V21–V25, provider interoperability:** certified per-chemistry adapters, cross-vendor reading, a public fitted-channel library, DDSA conformance programme.

Each band is gated by the saturation criteria in roadmap §5.

## 26 Recommended V26–V50 research directions

See [VNX_GLOBAL_ROADMAP.md](VNX_GLOBAL_ROADMAP.md) §4:

- **V26–V30:** finish ecosystem interoperability.
- **V31–V40:** large-scale archival and AI-data applications, only after GB-scale physical round trips.
- **V41–V50:** composite alphabets, enzymatic-native codes, in-storage search, ML decoders if they win on fitted channels, formal verification of fail-closed decoding, external validation.

Versions stop early when the saturation criteria are met.

---

## Appendix: Features we should NOT build (and why)

Each item cites the evidence that makes building it a poor use of VNX effort.

| # | do not build | why (evidence) |
|---|---|---|
| 1 | **A fork, copy or link of a GPL/AGPL harness or codec** (dt4dds, dt4dds-benchmark, UNACORM, NOREC4DNA, MESA, dna-fountain, StairLoop, DBGPS, Clover, pyHEDGES) | copyleft would contaminate VNX code. Both harnesses accept external codecs through process-boundary wrappers (`20-repos-cluster2.md` §7), so a fork adds nothing |
| 2 | **Our own wet lab** | write and read are commercial services (Twist, GenScript, Illumina, service labs). LoC bought its pilot from an existing lab (MISL) [PRESS]. About $1.4B has gone into the field, ~80 % to two synthesis companies [PRESS]. `40-datasets.md` plan b reaches level 3 by buying synthesis and sequencing as a customer |
| 3 | **GPU acceleration before profiling** | after the V6 native kernels, inner RS is 2.2 % of decode (`benchmarks/v6/native_rs/results/profile-after.txt`) and the parser runs at 557 MiB/s (SIMULATED). `docs/LIMITATIONS.md` states CPU-first by design. The one GPU decoder found (hedges-soft-decoder) needs CUDA + Bonito under a research-only licence. Measure the next bottleneck first |
| 4 | **An S3 DELETE (or update-in-place) that pretends molecular media is mutable** | synthesised pools are write-once. dnastore models update/delete as append-only tombstones [README]. Biomemory/Scality position DNA as a cold tier [PRESS]. A DELETE that implies erasure misleads compliance users. Offer delete markers plus crypto-shredding (destroy the per-archive key), and document that molecules persist |
| 5 | **Chasing codec density records without a same-protocol benchmark** | reported densities (1.57, 1.75–1.78, ~1.98 bits/nt; 43/13 EB/g; mahoraga 155.8 EB/g single-author simulation) come from different channels and protocols. UNACORM reports that no single codec wins across all dimensions [AR-SIM]. Density claims without dt4dds-benchmark parity fail the colour rule |
| 6 | **Our own registry or "standard" that competes with DDSA Sector Zero/One** | DDSA specs exist and are released [SPEC]; aeonscript's 7-layer "open standard" draft has 1 star and no evidence [README]. Contribute a reference implementation instead (§22.8) |
| 7 | **Another mapping-level multi-codec toolkit** | Chamaeleo (MIT) already wraps 7 mappers. Mappers are commodity and appear in three frameworks (`20-repos-cluster2.md` §8 insight 1) |
| 8 | **Embedding HEDGES (bundled Schifra RS), SPIDER-WEB, mahoraga or hedges-soft-decoder code in the product** | Schifra is free only for non-commercial/open-source use; the BGI licence requires permission for commercialisation; mahoraga is PolyForm Noncommercial; hedges-soft-decoder is ONT research-only (`10-repos-cluster1.md` §3, `20-repos-cluster2.md` §2.4, §5). Benchmark them as external binaries only |
| 9 | **A FUSE / POSIX filesystem over DNA** | retrieval is a batch process measured in hours to days (Iridia claims 24–48 h [PRESS]). VNX decode is MB/s-class batch work (`docs/LIMITATIONS.md`). POSIX semantics (random writes, mtime, rename) do not map onto write-once pools. dnastore's FUSE layer is a simulation convenience [README] |
| 10 | **Our own synthesis chip, sequencer or containment** | Atlas Thalia (5.6B sites, $155M seed, TSMC + imec) [PRESS]; Imagene, Cache DNA and Biomemory cards cover containment [PRESS]. A software-first company cannot compete on hardware capital |
| 11 | **A new "realistic" simulator presented as calibrated** | dt4dds is calibrated on 40 sequencing experiments [AR-WET]. Fit VNX's existing models to public reads (PUBLIC-DATA-DERIVED) and cross-check against dt4dds rather than claiming independent realism |
| 12 | **Neural/learned decoders before the classical baseline is benchmarked** | `docs/INDEL_ENGINE.md` §7: "classical baseline first". DNAformer weights and data are "on request"; TReconLM has no licence. No VNX baseline result exists yet to beat |
| 13 | **Molecular compute / DNA search features** | Atlas and Catalog compute claims are not independently validated [PRESS]. These features sit outside the codec/format/verification layer VNX targets |
| 14 | **Published $/GB or TCO claims presented as facts** | public $/MB figures span about $100 to about $1M per MB depending on source [PRESS]. Ship a parameterised calculator with cited inputs instead (§23 item 7) |
| 15 | **In-vivo storage** | YYC in yeast and Storage-D in E. coli exist [AR-WET]. In-vivo work adds biosafety and regulatory burden, conflicts with the DDSA biosecurity direction, and needs a wet lab (item 2) |
| 16 | **Fountain codes as the default outer code** | VNX's LT is EXPERIMENTAL, probabilistic and quadratic (k ≤ 1024). V6 superblock v2 forbids it (`docs/V6_OUTER_CODE.md` §1). The MDS Cauchy RS product code already shows the simulated dropout and burst gains (§8). Keep LT as research until a same-protocol benchmark against Raptor (DNA-Aeon) exists |
