# 30 — Commercial competitor and standards research (as of 2026-10-05)

Scope: DNA data storage companies, research programmes and standards bodies, viewed from the position of VNX-DNA, a software-first company that wants to own the codec / archive-format / verification layer and leave the wet lab to others. Structured data for 38 organisations is in `30-companies.json`, which uses the same field names as this file.

Method: WebSearch/WebFetch; GitHub API (`gh api users/<x>`, `orgs/<x>/repos`, `search/repositories`); and the Google Patents query endpoint. Every patent number listed came back from a real query, and assignees were checked on the patent page. Where something could not be found it is marked UNKNOWN. This file contains no VNX-DNA technical figures. Threat and partner ratings are analyst judgement, not sourced facts.

Source limitation: snia.org returns HTTP 403 to automated fetches. The 2024–2025 SNIA material comes from PDFs reached through the Wayback Machine and from Library of Congress (LoC) mirrors. Some 2026 DNA Data Storage Alliance (DDSA) newsletter facts come only from search snippets and should be read by a person before they go into investor material.

---

## 1. Market landscape: who sells what today

| Layer | Who (2026) | Status |
|---|---|---|
| **End-to-end DNA archive service** | **AtlasBase** (ex-Atlas Data Storage, Twist spin-out): *Atlas Eon 100* service announced 2 Dec 2025; Thalia chip and Early Access Programme announced Sep 2026 | Selling, at small scale. Prices and customers are not public ([StorageNewsletter](https://www.storagenewsletter.com/2025/12/02/atlas-data-storage-introduces-the-worlds-first-scalable-dna-data-storage-offering/), [Blocks & Files](https://www.blocksandfiles.com/data-protection/2026/09/23/atlasbase-makes-thalia-dna-computer-and-storage-chip/5298506)) |
| | **Biomemory** (FR, plus the Catalog assets): DNA Card (1 KB, about $1,000 for two cards) since late 2023 / Jan 2024; datacenter appliances with an S3 interface promised for H2 2026 | KB-scale product shipping; datacenter product not yet confirmed shipped ([DCD](https://www.datacenterdynamics.com/en/news/dna-data-storage-startup-biomemory-acquires-catalog-technologies-plans-data-center-deployment-in-h2-2026/), [Blocks & Files](https://www.blocksandfiles.com/architecture/2026/03/06/french-dna-coming-to-a-datacenter-near-you-soon/4093753)) |
| | **Iridia** (US): storage-as-a-service targeted for 2026; molecular archive landed on the Moon 2 Mar 2025 | Launch not confirmed ([TechTarget](https://techtarget.com/searchdatabackup/news/366560519/DNA-storage-to-tackle-massive-archives), [GlobeNewswire](https://www.globenewswire.com/news-release/2025/03/04/3036808/0/en/Iridia-Makes-History-First-Cryptocurrency-and-Molecular-Off-World-Data-Archive-Successfully-Lands-on-the-Moon.html)) |
| | **Mimulus + GenScript**: "Glacier Storage Card" and the proprietary "Mimulus Code" | Pre-commercial; no metrics published ([Blocks & Files](https://www.blocksandfiles.com/file/2026/06/09/mimulus-molecular-dna-storage-project/5252656), [BioSpace](https://www.biospace.com/press-releases/genscript-and-mimulus-partner-to-industrialize-dna-based-data-storage-for-the-ai-era)) |
| | **UW MISL** (academic): the only documented government archive contract (Library of Congress, Sep 2025). About 1.0 of 1.5 GB had been synthesised, stored and validated by Mar 2026 | Pilot ([LoC slides](https://www.digitalpreservation.gov/meetings/DSA2026/0103_budaSmithColtellino_Designing%20Storage%20Architecture%20Conference%202026%20OCIO%20DSD_VC_NBS.pdf)) |
| **Synthesis (write)** | Twist, GenScript, Ansa (from $0.28/bp for clonal DNA), DNA Script, Evonetix, imec (fab) | Commercial, but priced and built for biology |
| **Sequencing (read)** | Illumina, BGI/MGI, nanopore; Western Digital and Quantum hold patents on solid-state and multi-nanopore decoding | Commercial |
| **Containment** | Imagene (DNAshell), Cache DNA (glass, biobanking first), Biomemory cards, Atlas capsules | Commercial |
| **Codec / format / verification software** | **In-house at every vendor** (Atlas patent *CA3249936A1 "Codecs for DNA data storage"*, Mimulus Code, Biomemory, Iridia ECC). Open academic codecs: Microsoft (MIT licence), BGI Chamaeleo/YYC (MIT), Tianjin HELIX/StairLoop, DNA Fountain, ETH dt4dds simulator and benchmark | **No independent commercial vendor found** |
| **Integration with IT systems** | Scality + Biomemory (2 Jun 2026); SNIA Swordfish DNA working draft (Jan 2026); Atlas "MXL" software layer | Early stage |

Consolidation and exits: Catalog's assets went to Biomemory (5 Mar 2026). Molecular Assemblies' assets went to Maravai for $11.2M (23 Jan 2025). Twist moved its storage business into Atlas (May 2025). Microsoft has not commercialised DNA storage; Project Silica (glass) completed its research phase in 2025.

Funding (cited figures only): Atlas raised a $155M seed (May 2025) and imec has also invested, amount undisclosed. Biomemory raised €5M (2022) and about $18M (Dec 2024). Iridia raised about $40M in total, most recently in 2021. Since 2012 about $1.4B has gone into DNA storage across fewer than 100 deals, nearly 80% of it to Twist and DNA Script ([Blocks & Files, Jan 2026](https://blocksandfiles.com/2026/01/16/dna-data-storage-when-the-physics-work-but-the-economics-dont/)).

## 2. Pricing and cost evidence (public only)

| Figure | Unit | Source / date | Caveat |
|---|---|---|---|
| ~$100 per MB written; target below $1/MB; more than 1,000,000× SSD and 2,500,000× tape | $/MB | [Blocks & Files, 16 Jan 2026](https://blocksandfiles.com/2026/01/16/dna-data-storage-when-the-physics-work-but-the-economics-dont/) | Industry estimate |
| Biomemory DNA Card: 1 KB of text, ~$1,000 for two cards (≈ $0.5–1M per MB) | $/KB | [DCD](https://www.datacenterdynamics.com/en/news/biomemory-launches-first-commercially-available-dna-storage-solution/), [TechRadar](https://www.techradar.com/pro/dna-storage-finally-reaches-mainstream-well-sort-of-but-it-will-cost-you-a-whopping-dollar1000-per-kb-yes-kilobyte) | A novelty product |
| Biomemory roadmap: "100 PB for $150,000 in 2026; 1,000 PB in 2030" | target | [TechRadar headline](https://www.techradar.com/pro/1kb-now-100pb-for-dollar150000-in-2026-1000pb-in-2030-biomemory-ceo-sheds-more-light-on-mind-boggling-expectations-for-its-dna-storage-platform) | Marketing target; full article text not retrieved |
| Bi Sheng-1: $122/MB, against $3,260/MB "commercial" | $/MB | [CAS, 9 Apr 2025](https://english.cas.cn/newsroom/cas_media/202504/t20250409_1040848.shtml) | Research device; 43.7 KB demo |
| China 2025: synthesising 2 MB costs ~$7,000, reading ~$2,000 (≈ $4,500/MB in total) | $/MB | [Xinhua, 9 Jun 2025](https://www.news.cn/tech/20250609/4970eba7b26f48fa9412da27359e07cd/c.html) | Quoted by researchers |
| ~$0.001 per nucleotide synthesis; DNA about 7 orders of magnitude costlier than tape | $/nt | [CORDIS MoSS](https://cordis.europa.eu/project/id/101058035) | Project baseline from 2022 |
| Ansa clonal DNA $0.38–0.28/bp (7.5–50 kb) | $/bp | [Drug Discovery News, Oct 2025](https://www.drugdiscoverynews.com/ansa-biotechnologies-redefines-what-s-possible-in-dna-synthesis-with-50-kb-sequence-perfect-clonal-product-16751) | Sequence-perfect biology product, not comparable to storage oligos |
| Iridia: "5–10× lower TCO"; 24–48 h retrieval | TCO claim | [TechTarget, Nov 2023](https://techtarget.com/searchdatabackup/news/366560519/DNA-storage-to-tackle-massive-archives) | Unverified claim |
| Atlas Eon 100 | — | [StorageNewsletter](https://www.storagenewsletter.com/2025/12/02/atlas-data-storage-introduces-the-worlds-first-scalable-dna-data-storage-offering/) | **Price not disclosed** |
| Silica glass: capacity 4.8→2 TB per platter, "glass cost per TB still lower" | qualitative | [MSR, LoC Mar 2026](https://www.digitalpreservation.gov/meetings/DSA2026/0124_Silica-2026-03-09-Library-of-Congress-sharing.pdf) | Competing medium |

Takeaway: no vendor publishes a price per MB for a service that actually ships at scale. Per-MB figures from different sources differ by up to four orders of magnitude, so a transparent, reproducible cost model is itself something the market lacks (see §6).

## 3. Standards analysis

**DNA Data Storage Alliance (a SNIA community).** Founded 2020 by Illumina, Microsoft, Twist and Western Digital; more than 40 members, roughly half academic and half industry.
- **Sector Zero v1.0** (approved 11 Nov 2023, released 12 Mar 2024). 70 bases in total: 35 identify the vendor and 35 identify the codec. Its job is to let a reader find the codec needed for Sector One ([SNIA PR](https://www.snia.org/news_events/newsroom/dna-data-storage-alliance-releases-its-first-specifications-storage-digital), [AnandTech](https://www.anandtech.com/show/21304)).
- **Sector One v1.0**: archive metadata, i.e. content description, file table and sequencer parameters ([SNIA](https://snia.org/standards/technology-standards-software/standards-portfolio/dna-data-storage-sector-one)).
- **DNA Stability Evaluation Method v1.0** (Sep 2024): a half-life metric for containment systems, so vendor durability claims can be compared ([SNIA](https://www.snia.org/educational-library/dna-stability-evaluation-method-dna-data-storage-containment-systems-v10-2024)).
- **Codecs white paper: Examples, Requirements and Metrics v1.0** and **Technology Review v1.0**, both 30 Jun 2025, plus a biosecurity policy position ([StorageNewsletter](https://www.storagenewsletter.com/2025/07/16/snia-published-two-dna-data-storage-white-papers/)).
- Working groups: Data Retention, Codecs ("open source codec TBD"), Interoperable Interfaces, Biosecurity, Roadmap. Work planned for 2025: a data-retention calculator, characterising the solid-state nanopore channel, and "archive self-discovery" as an alternative to the Rosetta registry ([SNIA 2025 preview](https://snia.org/sites/default/files/Preview/2025/20250115%20-%20DNA%20Data%20Storage%20Alliance%20-%20SNIA%20Preview%202025%20v2.pdf)).
- 2026: a **Swordfish working draft for managing DNA storage systems** (object stores plus "DNA process" resources), Jan 2026 ([SNIA draft](https://www.snia.org/sites/default/files/technical-work/swordfish/draft/DNA%20Data%20Storage%20in%20Swordfish%20-%20Working%20Draft.pdf)). Priorities: a Random Access reference paper, stability methods, and an industry progress report. New co-chairs are Franceschini (Biomemory) and Skliaustas (Genomika). The 2026 work areas are data retention, codecs, interoperability, biosecurity, roadmaps, and random access/addressability ([LoC DSA 2026 slides](https://www.digitalpreservation.gov/meetings/DSA2026/0230_skilaustas_DNA%20data%20storage%20alliance%20presentation.pdf)).
- **No reference implementation and no conformance suite were found.** The only public Sector Zero "Rosetta Stone" registry server is a third-party MIT project, inactive since Aug 2023 ([jchristn/RosettaStone](https://github.com/jchristn/RosettaStone)). The SNIA GitHub org (21 repos) has no DNA repository.

**ISO/IEC: JPEG DNA (ISO/IEC 25508-1).** Reached Draft International Standard at the 111th JPEG meeting (13–17 Apr 2026), after independent wet-lab synthesis and sequencing decoded correctly. International Standard expected before end-2026 ([jpeg.org](https://jpeg.org/items/20260608_press.html), [ISO](https://www.iso.org/standard/90579.html)). It covers images only.

**IEEE.** There is a DNA chapter in the IEEE Mass Storage Roadmap update, an ISIT 2024 workshop, and a JSAIT special issue on coding for DNA storage. **No IEEE standards project (P-number) for DNA storage was found.**

**OAIS.** The EU DNAMIC project explicitly targets compliance with the OAIS archival reference model ([CORDIS](https://cordis.europa.eu/project/id/101115389)), and LoC is the reference buyer.

**What this implies for an interoperable archive format or API (VNX design inputs):**
1. Every archive should be self-describing at the molecular level: a Sector Zero header (vendor ID + codec ID), a Sector One file table and metadata, then the payload. Atlas already ships a "boot record + metadata as DNA" with each capsule ([Atlas, LoC Mar 2026](https://www.digitalpreservation.gov/meetings/DSA2026/0231_banyai_2026.03.10%20Atlas%20LOC%20v1.pdf)).
2. The registry layer is missing. Somebody has to run, and spec, the vendor/codec registry, or the "self-discovery" alternative the DDSA is now exploring.
3. The management API is converging on Swordfish/Redfish (REST/JSON). The data path is converging on S3 (Biomemory, Scality). A VNX API should expose S3-compatible object semantics plus Swordfish-style job and resource resources: encode, synthesise, store, retrieve, sequence, decode, verify.
4. Codec metrics now have an industry reference, the SNIA Codecs white paper. VNX should report against it and against the open dt4dds-benchmark.
5. JPEG DNA should be treated as a payload codec for images. Being able to wrap it inside a Sector Zero/One archive would be a concrete interoperability claim.
6. Biosecurity: the DDSA wants "no problem sequences in the pipeline". Screening sequences at codec level, plus attestation, is an open requirement.

## 4. The most threatening players

1. **AtlasBase: HIGH.** $155M seed; its own CMOS chip (5.6B sites, TSMC + imec); a commercial service; a **patent application on DNA storage codecs** (CA3249936A1, priority 2022); an "MXL" software layer. It also says customers will decode with a desktop sequencer and an **"open-source script"**. If Atlas open-sources its decoder, the basic codec becomes free for Atlas capsules. The remaining value then lies in verification, multi-vendor portability and lifecycle management.
2. **Biomemory: HIGH.** The most enterprise-IT-oriented player: S3 interface, the Scality partnership (Scality's CEO sits on Biomemory's board), Catalog's search/compute and error-protection patents, and a co-chair of the DDSA. It occupies the "DNA tier inside existing storage software" slot that VNX would otherwise target.
3. **Open academic codecs: MEDIUM** (Microsoft MIT tools, BGI Chamaeleo/YinYang, Tianjin HELIX/StairLoop, ETH dt4dds). Free, wet-lab-validated encoders set the public baseline. A closed codec without physical validation is easy to dismiss.
4. **Mimulus + GenScript: MEDIUM.** Shows that synthesis suppliers may bundle a proprietary codec with synthesis capacity.
5. **Iridia: MEDIUM.** An integrated chip + service + ECC model; it has executed slowly.
6. **Silica (Microsoft, glass): MEDIUM, indirect.** Competes for the same cold-archive budget.

Freedom-to-operate flags, found while searching patents. These need review by an IP lawyer before VNX claims novelty: Atlas CA3249936A1 (codecs); Western Digital US20250174272A1 (state-transition-matrix decoding) and WO2023146570A1 (encoding and integrity markers); Seagate US20260004879A1 (symbol-linker encoding); Microsoft US11600360B2 (trace reconstruction) and US20170141793A1 (error correction for nucleotide stores); Molecular Assemblies US10982276B2 (homopolymer-encoded memory); Catalog US12437841B2 (error protection, now owned by Biomemory); Quantum US20230215516A1 (multi-nanopore retrieval).

## 5. Most realistic partners and first customers for a software-first VNX

These are hypotheses. No contact, relationship or customer exists.

**Providers (buy from them as an ordinary customer, to get physical validation):** Twist or GenScript oligo pools (write); Illumina or nanopore sequencing, either directly or through a sequencing service lab (read); Imagene or Cache DNA (containment, evaluated with the DDSA Stability Method). This is the lowest-friction way to turn SIMULATED results into physical evidence.

**Partners (in rough order of fit):**
1. **DNA Data Storage Alliance.** Join, then contribute an open-source Sector Zero/One reference reader/writer, a conformance test suite, and codec-metric reporting aligned with the white paper. Codecs and interoperability are open DDSA work items, and "open source codec TBD" has stayed open since 2024.
2. **Genomika / DNAMIC / DiDAX** (EU). Modular, OAIS-oriented and led by standards people. Genomika's co-founder now co-chairs the DDSA.
3. **Archive and storage incumbents without a DNA codec:** Quantum (DDSA board, archive channel, decoding patents) and Western Digital (former DDSA chair, nanopore decoding). Object-storage vendors other than Scality are untested.
4. **ETH dt4dds-benchmark / Microsoft datasets.** Publish VNX results on these to establish credibility.
5. **Academic codec groups** (Tianjin, BGI) for head-to-head benchmarks.

**First customers (archetypes):**
- **National archives and libraries running pilots.** LoC is the template: a public RFI (2025), a contract to a lab (Sep 2025), GB-scale validation, and engagement with the DDSA. What such buyers need, and labs do not provide, is independent verification, OAIS documentation and vendor-neutral decoding. Indian national and heritage archives are a hypothetical target only; no evidence was gathered.
- **Synthesis and sequencing providers** who want a "storage-ready" offering without building a codec (Twist after the Atlas spin-out, Ansa, DNA Script, BGI/MGI in Asia).
- **Research labs and EU consortia** that need a maintained, standards-conformant codec and simulator instead of one-off academic code.

## 6. Gaps competitors appear to miss

1. **No vendor-neutral, standards-conformant codec or reader.** Every commercial player builds its own codec in-house. Nobody publicly offers multi-vendor archive portability, meaning a reader that can identify any archive from Sector Zero and decode it.
2. **No reference implementation or conformance suite** for Sector Zero/One, and no maintained registry ("Rosetta") service. The only public one is an inactive third-party server from 2023.
3. **No independent verification or audit layer.** Durability and integrity claims (150 years, 1,000+ years, "IT-compatible UER") are vendor-stated. The DDSA has standardised a stability metric but not end-to-end proof of integrity after decoding. Cryptographic proof of decode is unclaimed ground.
4. **No Swordfish DNA implementation.** The management model is only a working draft (Jan 2026).
5. **Data-retention calculator, random-access reference design, and archive self-discovery** are DDSA work items with no product attached.
6. **Biosecurity screening at the codec level** (no hazardous sequences in payloads) is a stated DDSA requirement with no tooling.
7. **Transparent cost modelling.** Public $/MB figures span roughly $100 to about $1M per MB depending on source and product. No vendor publishes a reproducible TCO model.
8. **Geography.** No India-based DNA storage company remains (BioCompute reportedly moved to San Francisco, Jul 2026). India and Japan have no visible presence in DDSA leadership.

## 7. Corrections to existing VNX material (flag only; nothing edited)

- `vnxco/dossier.py` timeline says **"Feb 2026 Biomemory launches DNA Cards"**. Public sources show the DNA Card launched in 2023, with deliveries from Jan 2024 ([DCD](https://www.datacenterdynamics.com/en/news/biomemory-launches-first-commercially-available-dna-storage-solution/), [Notebookcheck](https://www.notebookcheck.net/Biomemory-DNA-Cards-to-store-data-as-synthetic-DNA-with-150-year-lifespan.778636.0.html)).
- The dossier says Iridia is **"backed by Seagate and Western Digital"**. Western Digital Capital is confirmed ([eeNews](https://www.eenewseurope.com/en/dna-based-memory-startup-raises-24-million/)); no primary source for a Seagate investment was found.
- The dossier gives Ansa a **"$68M financing"**. The verified figure is a $54.4M Series B (1 Oct 2025). The $68M may be a cumulative total; this was not reconciled.
- The **Atlas Data Storage → AtlasBase** rename (atlasds.com now redirects to atlasbase.com) and the reported CEO change (Jeff Treuhaft, Mar 2026, per Blocks & Files) are not yet reflected in the dossier.
- New 2025–2026 facts not yet in the dossier: Atlas Eon 100 (Dec 2025); the LoC–MISL DNA pilot (Sep 2025) and America's Time Capsule DNA pellet (Jul 2026); Scality–Biomemory (Jun 2026); Mimulus–GenScript (Apr 2026); JPEG DNA at DIS (Apr 2026); the Swordfish DNA draft (Jan 2026); new DDSA co-chairs; Silica research phase complete; Maravai's acquisition of Molecular Assemblies.

## 8. Counts

38 organisations profiled. GitHub: 10 have relevant public code (Microsoft, UW MISL, Helixworks, OligoArchive, MoSS, BGI, Tianjin, ETH, JPEG DNA-related tools, and the DDSA-adjacent RosettaStone); the other 28 are marked "NO SIGNIFICANT PUBLIC GITHUB IMPLEMENTATION FOUND". 53 patent records verified via Google Patents. Threat ratings: HIGH 2, MEDIUM 8 (including "medium, indirect"), LOW-MEDIUM 4, LOW 20, NONE/opportunity 4.
