# VNX-DNA product strategy

Status: DRAFT for founder review. Date: 2026-10-05. Company: VS Nexus Technologies (India).
Scope: the product and commercial strategy for VNX-DNA as a software infrastructure layer for molecular/DNA archival storage.
This document makes no claim of customers, revenue, partners, patents or physical validation. VNX-DNA has none of these.

## How to read this document

| Label | Meaning |
|---|---|
| **[Sn]**, **[Pn]**, **[Rn]**, **[Dn]** | External fact. The full URL or accession is in the source list (§16). Every one comes from the research pass of 2026-10-05 (`research/competitive-2026-10-05/`: 30-companies, 10/20-repos, 40-datasets). |
| **Baseline** | A fact about VNX-DNA from the read-only audit of commit `081697b` (branch `build/v6-p1`) in `the verified baseline note (workforce records, 2026-10-05)`. Statuses PRESENT / PARTIAL / ABSENT / BROKEN are taken from that audit. |
| **SIMULATED** | A VNX-DNA technical result. Every one comes from a committed results file in this repository (path given) and was produced with software strands and a software channel. No DNA has been synthesised, stored or sequenced by VNX-DNA. |
| **Judgement** | Analyst opinion. It is not a sourced fact and should be tested. |
| **[FOUNDER]** | A placeholder the founder must fill in. Nothing has been invented for it. |

---

## 1. Summary of recommendations

1. **Stay software-first and product-first; do not own a wet lab** (Judgement, reasons and risks in §6). Buy synthesis and sequencing as an ordinary customer when physical evidence is needed.
2. **Open the read path and keep it open.** The archive format specification, a reference encoder and decoder, the CLI, the core SDK and the benchmark harness should be open source (permissive). The code up to v5.0.0 is already public under the MIT licence (§5.0), so this mostly formalises the current position. Charge for operations, assurance and integration: Enterprise Server, verification attestations, provider-specific adapters, conformance testing, support. The governing rule (Judgement): **no archive written by VNX may need a paid component to be read back.**
3. **First product: "VNX Verify".** An open reference reader, verifier and conformance kit for the VNX archive format and for the DNA Data Storage Alliance (DDSA) Sector Zero / Sector One specifications. No reference implementation or conformance suite for those specifications was found [S20][S24][S27]. VNX already has the integrity chain and fail-closed verification this product needs (Baseline). It does not have Sector Zero/One support (Baseline search: ABSENT).
4. **Most realistic first paying customer** (Judgement, comparison in §7): a synthesis or sequencing provider, or a new entrant, that wants a "storage-ready" offer without building a codec. The engagement would be a paid integration pilot: provider adapter, benchmark on the provider's error profile, and a codec licence with support. The longer-cycle reference customer is a national archive or library that needs independent verification and vendor-neutral decoding; the Library of Congress pilot is the template [S18][S19]. No contact with any of these organisations exists.
5. **Storage API: native archive verbs first, an S3-compatible gateway later and only on demand** (§13). DELETE on write-once molecules has to be defined as tombstone plus crypto-erasure plus attested physical destruction. Each has a different guarantee, and the API must say which one was achieved.
6. **Before seeking investment**, produce the eight items in §12. The four that matter most: a same-protocol benchmark on the public ETH harness, one channel model fitted to a real public dataset, one wet-lab round bought from providers, and one design-partner letter of intent.
7. **Get a freedom-to-operate review** of the nine patents flagged in §10 before claiming novelty or taking licence fees. VNX holds no patents and claims none.
8. **Fix the existing pitch material** before it is shown again (§14). Several market facts are now wrong, and some VNX descriptions say more than the evidence supports.

---

## 2. Strategy statement (founder strategy, preserved)

VNX-DNA moves from a research project and service-style engineering to a **product-first, software-first infrastructure layer** for molecular/DNA archival storage. It does not own the wet lab.

Pipeline:

```
Digital data
  → VNX Archive (files, directories, manifest)
  → compression / deduplication
  → encryption / integrity (AEAD, hashes, Merkle tree)
  → VNX Codec (bits → constrained strands, inner and outer ECC)
  → ECC
  → [synthesis provider] → [storage / containment] → [sequencing provider]
  → alignment / indel recovery → consensus / soft decoding
  → channel modelling (simulation now, fitted models next)
  → provider-independent storage API
  → future synthesis / sequencing providers via adapters
```

Positioning: **"the software infrastructure layer for reliable molecular/DNA archival storage."**

A possible later positioning is **"the archival storage layer for AI-generated data."** Judgement: keep this for later, for two reasons. (a) This research gathered no market evidence on archival demand for AI-generated data. (b) The framing is already in use: GenScript and Mimulus announced their partnership "to industrialize DNA-based data storage for the AI era" [S17]. It would not differentiate VNX today.

---

## 3. Where VNX-DNA actually stands (Baseline, commit 081697b)

**What exists**
- Container format VNX4 with a normative specification (`docs/VNX4_FORMAT.md`). It provides:
  - zstd compression;
  - content-addressed deduplication;
  - AES-256-GCM per chunk with HKDF-SHA256 subkeys;
  - a key file or scrypt passphrase;
  - an RFC 6962/9162 Merkle tree;
  - a manifest and superblock;
  - fail-closed publish, where partial extraction releases only verified files.
- Encoder for 313-nt strands. It uses 2 bits/nt with GC, homopolymer and motif screening, inner RS(GF(2^8)) plus CRC-32 per frame, outer Cauchy RS, and 3-nt sync markers.
- Product-code outer layer, opt-in (V6): stripes plus column parity, an adaptive planner, and named redundancy profiles.
- Read-side recovery: marker-template banded alignment (native C kernel), smart indel recovery (opt-in), soft decoding (opt-in), consensus with soft posteriors, and a recovery planner with budgets and decision provenance. Native read parser and native SIMD inner RS (V6, x86-64 only).
- Channel simulator with 14 named, versioned models. None is fitted to a measured platform.
- Competitor comparability framework covering 12 literature systems.
- Physical-validation record schemas and a validator. Commit `6c02f7a` is titled "no physical testing has occurred".
- CLI `vnx`: archive, inspect, list, verify, locate, extract, encode, decode, validate, channel simulate, benchmark, keygen, native, version.

**What is partial, absent or broken**
- Python API: PARTIAL (V1–V3 era, no API docs; the V9 SDK has not started).
- Not present: REST service, object store, primers, order export, BAM input, decoding of third-party codecs, unaddressed read clustering, key management (rotation, KMS/HSM), versioning/append, and molecular (PCR) random access.
- Random access with `--select` on V6 stripe archives is BROKEN (open job #56).
- V6 has never run in CI because the branch is not on the remote.

**Physical validation:** none. Every codec result is SIMULATED (Baseline B.11).

**Selected committed results (all SIMULATED)**

| Result | Value | File |
|---|---|---|
| 1 GiB round trip, **clean** channel | encode 96.8 s, decode 244.4 s, 326 MB peak RSS. The largest noisy run is 16 MiB. | `docs/V4_COMPLETION_REPORT.md` |
| Density at v4-balanced | 9.8469 nt per input byte, 313-nt strands | `docs/V4_RESULTS.md` |
| i.i.d. dropout threshold, V5 vs V6 at ≈equal overhead | 0.07 → 0.16. 20 seeds per cell, 0 false SUCCESS. | `experiments/v6/phase1/summary.md` |
| Poisson mean-coverage threshold | 3.0 (V5) → 2.0 (V6) | `experiments/v6/phase1/summary.md` |
| Test suite at the v5.0.0 release | 1,013 passed. No full-suite log exists for 081697b. | `docs/V5_COMPLETION_REPORT.md` |

**Licence and visibility of the existing code.** Checked read-only on 2026-10-05:
- The GitHub repository `VSnexustechnologies-` is **public**.
- `pyproject.toml` declares `license = "MIT"`, and every tag up to v5.0.0 carries an MIT `LICENSE`.
- The `LICENSE` file is 11 lines long and appears to omit the standard warranty disclaimer. GitHub classifies it as "Other". This needs legal review (§10).
- `build/v6-p1` (the V6 work) has **not** been published.

---

## 4. Market context (sourced, short)

**Who sells what**
- **End-to-end services** are selling at small scale or are still announced:
  - AtlasBase (formerly Atlas Data Storage): Atlas Eon 100 service, Dec 2025, price not disclosed [S2]; Thalia chip and Early Access, Sep 2026 [S3].
  - Biomemory: DNA Card since late 2023 / Jan 2024 [S9][S12]; datacenter appliances with an S3 interface promised for H2 2026 [S6][S7].
  - Iridia: service targeted for 2026, launch not confirmed [S13].
  - Mimulus with GenScript: pre-commercial [S16][S17].
- **Codec / format / verification software is in-house at every vendor.** Examples: the Atlas patent application "Codecs for DNA data storage" [P1], the "Mimulus Code" [S16], Biomemory, Iridia ECC. **No independent commercial codec vendor was found** (30-companies §1).
- **Free academic codecs set the public bar.** Microsoft's tools are MIT [R1][R2]. BGI's Chamaeleo / Yin-Yang are MIT [R3]. DNA-Aeon is MIT [R4]. HEDGES is MIT but bundles Schifra RS under separate terms [R5]. ETH's dt4dds and dt4dds-benchmark are GPL-3.0 [R6][R7], and UNACORM is GPL-3.0 [R8].
- **Integration with IT:** Scality and Biomemory, 2 Jun 2026 [S8]; the SNIA Swordfish DNA working draft, Jan 2026 [S25]; the Atlas "MXL" software layer [S3].
- **Public buyer precedent:** the US Library of Congress issued an RFI in 2025 [S19] and awarded a contract to UW MISL in Sep 2025. By Mar 2026 about 1.0 of 1.5 GB had been synthesised, stored and validated [S18].

**Standards**
- DDSA Sector Zero v1.0: 70 bases, a 35-base vendor ID plus a 35-base codec ID [S20].
- DDSA Sector One v1.0: archive metadata and file table [S21].
- DDSA Stability Evaluation Method v1.0 [S22].
- DDSA codecs white paper (requirements and metrics) and Technology Review, Jun 2025 [S23].
- The DDSA Codecs working group lists an "open source codec TBD" [S24].
- **No reference implementation and no conformance suite were found.** The only public Rosetta registry server is an inactive third-party MIT project [S27].
- JPEG DNA (ISO/IEC 25508-1) reached DIS in Apr 2026. It covers images only [S28].

**Money**
- About $1.4B has gone into DNA storage since 2012, nearly 80% of it to Twist and DNA Script [S1].
- Atlas raised a $155M seed [S38]. Biomemory raised €5M (2022) and about $18M (Dec 2024) [S39].
- Writing costs about $100/MB against a target below $1/MB [S1].

Judgement: the software revenue pool today is small because physical volumes are small. Every vendor is at pilot or KB scale (§8). A software-first VNX should plan for a long period in which revenue comes from engagements, not volume licences.

---

## 5. Product architecture

### 5.0 Licensing context that constrains every option

**Facts**
- v0.1 to v5.0.0 are public under MIT (§3). That code cannot be "closed" retroactively: anyone may keep using, forking and selling it under MIT.
- The real licensing decision is therefore about **V6 and later** and about **new components** (server, adapters, fitted models).
- Competitor and benchmark code has its own constraints (10/20-repos). GPL/AGPL harnesses (dt4dds, UNACORM, NOREC4DNA, MESA) must be run only as separate processes, never linked or copied. SPIDER-WEB has a custom BGI licence under which commercialisation needs permission [R9]. mahoraga is PolyForm Noncommercial [R10]. Many repositories have no licence at all.

**Licence options considered for each component**

| Option | What it means |
|---|---|
| open source | Permissive licence: MIT, or Apache-2.0 for an explicit patent grant |
| source-available | Code is visible, but commercial use needs a licence |
| commercial | Closed or licensed binary |
| enterprise-only | Sold only within Enterprise Server |
| future IP | Hold back; decide after FTO review and any filing decision |

### 5.1 Component overview

| Component | Purpose (one line) | Current state (Baseline) | Licensing recommendation |
|---|---|---|---|
| VNX-DNA SDK | Library API to archive, encode, decode, verify | PARTIAL | Open source |
| VNX CLI (`vnx`) | Operator and researcher tool | PRESENT | Open source |
| VNX Archive Format | Self-describing, verifiable archive and DNA framing | PRESENT (VNX4 + superblock v1/v2); Sector Zero/One ABSENT | Open specification + open reference implementation |
| VNX Codec | Bits to constrained strands with inner/outer ECC | PRESENT (screening, not constrained coding; no primers) | Open source |
| VNX Recovery Engine | Gets verified data back from noisy, incomplete reads | PRESENT core; clustering and trace reconstruction ABSENT; V6 select BROKEN | Reference path open; scale/performance extensions source-available or commercial (decide after evidence) |
| VNX Storage API | Provider-independent archive verbs | ABSENT | Open specification |
| VNX Enterprise Server | Multi-tenant catalogue, jobs, keys, attestations, gateways | ABSENT | Commercial / enterprise-only |
| VNX Provider Adapter | Order export, read ingestion, physical records per provider | PARTIAL (FASTA export, record schemas) | Open interface; certified vendor adapters commercial |
| VNX Benchmark Suite | Reproducible, comparable codec evaluation | PARTIAL (simulator, comparability framework; no fitted models, no external-harness integration) | Open source harness + published results; paid engagements |

### 5.2 VNX-DNA SDK

- **Purpose:** a stable, typed, semver API for archive, encode, decode, verify, inspect, locate and extract, with event streams and reports. This is how an integrator or provider embeds VNX.
- **Customer:** engineering teams at synthesis and sequencing providers and at archive-system integrators; research groups that need a maintained codec rather than one-off academic code (30-companies §5).
- **Current state:** PARTIAL.
  - `src/vnxdna/api.py` and `v2/api.py` date from V1–V3. There is no `docs/API.md`, and the V9 SDK has not started.
  - Native kernels (aligner, parser, inner RS) exist, with a pure-Python fallback.
  - V6 kernels are tested on x86-64 only.
  - Structured events (`--events`, `--report`) are PRESENT.
- **Dependencies:**
  - a frozen Archive Format version;
  - the Codec and Recovery Engine behind a stable facade;
  - wheel packaging with native kernels plus fallback;
  - API contract and backward-compatibility tests (canonical roadmap V9).
- **Licensing: open source.**
  - Reasons: the public bar is free, MIT-licensed academic codecs (§4). A paid SDK for a codec with no physical validation would compete with free alternatives on credibility alone, which VNX does not yet have (Judgement). The SDK is also the distribution channel for every paid product. The code it wraps is already MIT.
  - Trade-off: competitors (including integrated vendors) can use it at no cost. Judgement: the effect is small, because they already have in-house codecs (§4).
  - What would change this: a provider asking for an exclusive or white-label build with features that do not exist in the open SDK. That would be sold as a commercial edition. The open SDK would not be withdrawn.

### 5.3 VNX CLI

- **Purpose:** the same functions as the SDK for operators, archivists and researchers. It is also the reference tool for verification and conformance.
- **Customer:** archivists and digital-preservation staff (verification), researchers (benchmarks), VNX's own wet-lab rounds.
- **Current state:** PRESENT (`vnx` with the commands listed in §3; legacy `vnx-dna` from V3). Missing commands relevant to products:
  - `order-export` and primer handling (40-datasets plan b);
  - Sector Zero/One read and write;
  - conformance reports.
- **Dependencies:** SDK; Archive Format; Provider Adapter (for order export and ingestion).
- **Licensing: open source.** No credible case for charging was found (Judgement). The CLI is how auditors verify archives independently, and a closed verifier would defeat its purpose.

### 5.4 VNX Archive Format

- **Purpose:** a self-describing, verifiable archive. It covers:
  - the digital container (file table, chunk table, manifest, Merkle root, AEAD);
  - the DNA framing (frame header with archive tag, group and symbol index; superblock repeated four times).
  - Its job is to let a future reader that has never seen the writer identify, decode and verify the archive.
- **Customer:** archives and libraries (long-term readability, OAIS documentation [S29]); providers (interoperability); standards bodies.
- **Current state:**
  - PRESENT: `docs/VNX4_FORMAT.md` is normative. Superblock v1 and v2 (V6, opt-in). Golden V4/V5 compatibility archives must keep decoding (`tests/compat`).
  - ABSENT: a Sector Zero header (vendor ID + codec ID), Sector One metadata mapping, versioning/append, and primer regions.
- **Dependencies:**
  - DDSA membership, to register vendor and codec identifiers and to follow Sector Zero/One revisions [S20][S21];
  - a decision on the registry ("Rosetta") versus the "archive self-discovery" alternative under DDSA discussion [S24];
  - a strand-length profile that fits vendor limits. Today 313 nt plus two 20-nt primers is 353 nt, over the 350-nt maximum of Twist and IDT pools (40-datasets, vendor specs).
- **Licensing: open specification (permissive text licence) plus open reference implementation; royalty-free.**
  - Reasons:
    - The gap competitors leave is vendor-neutral, standards-conformant reading and multi-vendor portability (30-companies §6.1–6.2). A proprietary format is the opposite of that offer.
    - AtlasBase says customers can decode its capsules with a desktop sequencer and an "open-source script" [S4]. If that ships, a closed VNX format would compare badly on long-term readability.
    - Archive buyers need to be sure they can read their data without the vendor (Judgement, consistent with the LoC focus on verification and OAIS [S18][S29]).
  - Trade-off: anyone can write VNX-compatible archives without paying VNX. Judgement: that is the intended outcome. Revenue comes from verification, conformance and operations.
  - What would change this: nothing foreseeable. Closing the format would remove the main reason to use VNX. Extensions may be developed privately, but published archives should use only the published specification.

### 5.5 VNX Codec

- **Purpose:** turn bytes into synthesis-ready strands under biochemical constraints, with inner ECC (RS + CRC per frame), outer erasure ECC (Cauchy RS; V6 product code with column parity) and sync markers for indel localisation.
- **Customer:** providers without an in-house codec; research consortia; VNX's own Enterprise Server.
- **Current state:**
  - PRESENT: 2 bits/nt mapping; GC (40–60% default) and homopolymer (≤4 default) limits; motif rules; inner RS; outer Cauchy RS; V6 product code (opt-in); redundancy profiles.
  - PARTIAL: constraints are met by screening up to 256 scrambler variants, **not by constrained coding**; LT fountain is experimental.
  - ABSENT: secondary-structure / Tm checks, primers, LDPC / convolutional / indel-correcting codes (VT, HEDGES-like), and biosecurity screening of payloads (a stated DDSA requirement with no tooling anywhere [S23]; 30-companies §6.6).
- **Dependencies:**
  - Archive Format (framing);
  - channel models (to choose redundancy);
  - Provider Adapter (vendor length and constraint limits);
  - FTO review (§10). The codec area has patents from Atlas, Western Digital, Seagate, Microsoft, Molecular Assemblies and Catalog/Biomemory.
- **Licensing: open source.**
  - Reasons: free academic codecs set the public baseline (MIT: Microsoft, Chamaeleo/YYC, DNA-Aeon; MIT plus Schifra terms: HEDGES), and three public harnesses already wrap commodity mappers (20-repos §8.1). No independent codec vendor exists [30-companies §1]. Judgement: that is not proof of an unserved market. It may mean vendors see the codec as strategic and keep it in-house: Atlas filed a codec patent application [P1], and Mimulus markets a proprietary "Mimulus Code" [S16]. Selling a closed, physically unvalidated codec into that market is low-probability.
  - Trade-off: the codec is the most visible technical work, and opening it gives away its value.
  - What would change this: a physically validated, reproducible advantage at matched code rate on the public ETH protocol (§12, E1–E3), together with a buyer willing to pay for a performance profile. In that case a commercial "performance profile" (for example tuned parameters for a specific provider's fitted channel) could be sold. The base codec would stay open.

### 5.6 VNX Recovery Engine

- **Purpose:** recover verified data from noisy, incomplete, unordered reads. It covers:
  - read parsing;
  - orientation detection;
  - marker alignment (native);
  - smart indel recovery;
  - soft decoding;
  - consensus;
  - the inner/outer iterative decode;
  - the recovery planner with budgets;
  - partial verified-file extraction.
  - It never publishes unverified output; 0 false SUCCESS is reported in the V6 Phase 1 sweeps (SIMULATED, `experiments/v6/phase1/summary.md`).
- **Customer:** the same as the Codec, plus archives that need data back from degraded or partial reads ("RECOVER" in §13).
- **Current state:**
  - PRESENT: the items listed under Purpose.
  - PARTIAL: `--select` still parses and aligns every read; streaming decode is in progress.
  - BROKEN: random access on V6 stripe archives (#56).
  - ABSENT: clustering of reads without addresses, trace reconstruction, BAM input, and decoding datasets that use other codecs (the decoder reads VNX4 frames only).
- **Dependencies:** channel models fitted to real data (§12 E2); FTO review on trace-reconstruction and decoding patents [P5][P3][P9]; native kernels on non-x86 targets for portability.
- **Licensing: split.**
  - The reference recovery path is open: everything needed to decode any VNX archive, including the V5 features already released under MIT.
  - Performance and scale extensions are candidates for source-available or commercial licensing: distributed or GPU decode, large read-pool orchestration, and provider-specific fitted models.
  - Trade-offs:
    - Open: this is where credibility is won or lost. Reviewers and archives will want to inspect how "verified" is decided, and the benchmark harnesses expect runnable decoders [R6][R8].
    - Commercial: this is the hardest part to reproduce, so it is the most plausible paid differentiator.
  - Governing rule (Judgement): anything the commercial engine can recover, the open engine must also recover, possibly more slowly. Otherwise VNX recreates vendor lock-in.
  - What would change this:
    - Towards open: if commercial extensions find no buyer within a defined period.
    - Towards commercial: if a provider pays for faster or larger-scale decode at measured throughput.
    - Towards future IP: if FTO review shows room to file on a specific method. Counsel should advise on how the existing public disclosures affect this (§10).

### 5.7 VNX Storage API

- **Purpose:** provider-independent archive verbs (§13), so applications do not depend on which synthesis, containment or sequencing provider is used.
- **Customer:** archive-system integrators, object-storage vendors without a DNA partner (30-companies notes these are untested targets), Enterprise Server users.
- **Current state:** ABSENT. There is no REST service, object store or FUSE layer (Baseline B.7). The canonical roadmap places the service architecture in V10.
- **Dependencies:**
  - Archive Format;
  - Enterprise Server or a minimal reference server;
  - key management (ABSENT);
  - a catalogue;
  - Provider Adapter;
  - alignment with the SNIA Swordfish DNA working draft for management resources [S25].
- **Licensing: open specification** (an OpenAPI-style document). A minimal single-user reference server could be open; the multi-tenant server is commercial (§5.8).
  - Reasons: management is converging on Swordfish/Redfish and the data path on S3 (30-companies §3, item 3). A proprietary API would isolate VNX from both.
  - Trade-off: none of substance (Judgement).

### 5.8 VNX Enterprise Server

- **Purpose:** run DNA archiving as an operated service inside an organisation. Functions:
  - catalogue and search;
  - job orchestration (encode → order → synthesise → store → retrieve → sequence → decode → verify);
  - key management (KMS/HSM, rotation, per-retention-class keys);
  - audit log and signed verification attestations;
  - RBAC and multi-tenancy;
  - retention / legal hold;
  - the optional S3-compatible gateway.
- **Customer:** national archives and libraries, research data centres, and providers that operate a storage service.
- **Current state:** ABSENT. The building blocks that exist are structured events and reports (V6), physical record schemas with a validator, and the integrity chain.
- **Dependencies:** Storage API; SDK; Provider Adapter; key management; a security review (V10 gate: threat model, SBOM, no open CRITICAL/HIGH); at least one design partner to set requirements.
- **Licensing: commercial / enterprise-only.**
  - Reasons: this is where organisations pay for operations, compliance, support and SLAs (Judgement). The Biomemory–Scality partnership shows that integration with enterprise storage has commercial value [S8]. An open read path plus a commercial operations layer is a common pattern in infrastructure software (Judgement; no source gathered on comparables).
  - Trade-offs:
    - It is the most expensive component to build and the furthest from current code.
    - In the "DNA tier inside existing storage software" slot it competes directly with Biomemory + Scality [S7][S8].
    - Selling to foreign government archives from India adds procurement friction (Judgement).
  - What would change this: if no design partner wants an on-premises server, deliver the same functions as a hosted service, or drop the component and integrate into an existing object-storage product through the gateway.

### 5.9 VNX Provider Adapter

- **Purpose:**
  - Translate a VNX archive into a provider's order format: primers, length limits, constraint checks, order CSV, manifest and SHA-256 sums.
  - Ingest that provider's read output: FASTQ/BAM, primer trimming, paired-end merge, basecaller metadata.
  - Record every physical step in the existing record schema.
  - One adapter per synthesis or sequencing provider; one shared interface.
- **Customer:** VNX itself, for the first wet-lab round; providers that want a storage-ready offer (30-companies §5); archives that want to switch providers.
- **Current state:** PARTIAL.
  - PRESENT: FASTA strand export; physical-interface JSON schemas and validator (`experiments/v6/physical/`).
  - ABSENT: vendor adapters, primers, `order-export`, BAM input, read ingestion with primer handling, and a PUBLIC-DATA-DERIVED evidence class (40-datasets).
- **Dependencies:**
  - a strand profile within vendor limits (≤350 nt per vendor specs; 40-datasets recommends ≤300 nt including primers, or a 150–200 nt profile);
  - a primer module;
  - provider quotes and specifications;
  - Recovery Engine read ingestion.
- **Licensing:** open adapter interface plus open "generic" adapters (FASTA/CSV out, FASTQ in). **Certified, provider-specific adapters are commercial**, or co-owned with the provider under contract.
  - Reasons: providers can then write their own adapters, which supports the vendor-neutral claim. Certified adapters for a provider's own process are integration work that someone pays for.
  - Trade-offs:
    - Some likely providers have ties to competing storage efforts: Twist holds Atlas equity [S36], and GenScript works with Mimulus [S17]. They may prefer not to certify a third-party codec.
    - Vendor-specific work does not scale.
  - What would change this: a provider paying for an exclusive adapter (acceptable if time-limited); or a DDSA interoperable-interface specification [S24] making adapters commodity.

### 5.10 VNX Benchmark Suite

- **Purpose:** reproducible, comparable evaluation of codecs under named channel models and real-data-fitted models. It reports against the SNIA codec metrics [S23] and runs inside the public harnesses (dt4dds-benchmark [R7], UNACORM [R8]), with VNX as an external process.
- **Customer:**
  - providers comparing chemistries and codecs;
  - research consortia;
  - investors and reviewers, who need the evidence;
  - VNX itself, for regression gates.
- **Current state:** PARTIAL.
  - PRESENT: 14 named models (not fitted); the competitor comparability framework (12 systems, computed comparability labels, no claims); experiment provenance.
  - ABSENT: integration with dt4dds-benchmark or UNACORM; fitted models; dataset fetch with hash lock; the PUBLIC-DATA-DERIVED class.
- **Dependencies:**
  - the public datasets (D01 ETH codec benchmark, D02 DT4DDS, D03 nanopore, D04 CNR; 40-datasets);
  - GPL tools kept at the process boundary;
  - a ~130–150 nt VNX profile, to compare at matched strand length.
- **Licensing: open-source harness and published results; paid benchmarking engagements.**
  - Reasons: two free harnesses already exist [R7][R8], so the harness itself cannot be sold (Judgement). Credibility requires that anyone can rerun VNX's numbers.
  - Trade-off: competitors can use VNX's comparisons. Judgement: that is acceptable, because neutrality is the product.
  - What would change this: demand for a certified benchmark ("tested to protocol X, attested by VNX") could become a paid conformance service. Whether such demand exists is unknown.

---

## 6. Should VNX remain software-first?

**Answer: yes.** Remain software-first and do not build or own a wet lab at this stage (Judgement). Physical evidence should be bought from providers as an ordinary customer.

**Reasons**
1. **Capital.** The integrated players fund chips and fabs: Atlas raised a $155M seed and works with TSMC and imec on its Thalia chip [S38][S3]. VNX's capital need is low until the physical stages, which the existing dossier already states.
2. **The gap that is open is in software.**
   - No independent codec / format / verification vendor exists [30-companies §1].
   - Sector Zero/One has no reference implementation or conformance suite [S24][S27].
   - Independent integrity verification after decode is unclaimed [30-companies §6.3].
3. **VNX's assets are software.** These are the integrity chain, fail-closed recovery, provenance-tracked experiments and the engineering hygiene: sanitizers, differential fuzzing, golden compatibility archives (Baseline B.10).
4. **Physical evidence can be bought.** Oligo pools (Twist, GenScript, IDT) and sequencing services are commercial products [30-companies §1, §5; 40-datasets vendor specs].
5. **Consolidation favours a neutral layer.** Catalog → Biomemory, Molecular Assemblies → Maravai, Twist storage → Atlas [30-companies §1]. Judgement: fewer, larger integrated vendors increase an archive's need for vendor-neutral reading.

**Risks of staying software-first**
1. **Value capture.** Integrated vendors keep the codec in-house [P1][S16], and buyers may want a turnkey service rather than software. VNX could be squeezed between them.
2. **Credibility without physical results.** "A closed codec without physical validation is easy to dismiss" (30-companies §4.3). This applies to an open one too until there is wet-lab evidence.
3. **Provider dependence and conflicts.** Twist (Atlas equity [S36]) and GenScript (Mimulus partnership [S17]) are both natural suppliers and connected to competitors.
4. **A free decoder from AtlasBase** [S4] would make basic decode for Atlas capsules free.
5. **Timing.** DNA writes cost about $100/MB against a target below $1/MB [S1], and vendors are at KB-to-pilot scale. Software volume revenue may lag for years (Judgement).
6. **The existing dossier contradicts this strategy** in places. It presents an "India bio-compute centre" and an Indian biofoundry as the "laboratory partner" (§14).

**When to revisit:**
- no provider will sell suitable pools and sequencing at a cost VNX can fund; or
- a design partner will pay only for a turnkey end-to-end service; or
- fitted channel models show that VNX's benefit depends on chemistry control it cannot get as a customer.

---

## 7. First product and most realistic first paying customer

All candidates below are hypotheses. No contact, relationship or customer exists.

| Option | Buyer evidence (sourced) | VNX readiness (Baseline) | Competition | Time to revenue (Judgement) | Assessment (Judgement) |
|---|---|---|---|---|---|
| A. Vendor-neutral verification / decoding for archives (LoC/UW-type pilots) | LoC RFI 2025, contract Sep 2025, GB-scale validation [S18][S19]; OAIS orientation in EU projects [S29] | Digital verification PRESENT. **Decoding other codecs ABSENT.** Sector Zero/One ABSENT | None found offering it publicly (30-companies §6.1) | Long: public procurement; needs physical credibility | Strong strategic fit, slow. Pursue as the reference market, not as the first revenue |
| B. Conformance / reference implementation for DDSA specs | "Open source codec TBD" [S24]; no conformance suite found [S27] | Integrity chain PRESENT; Sector Zero/One ABSENT | None found | Little direct revenue | **Recommended first product** (open). Builds standing; enables A and D |
| C. Benchmark-as-a-service | dt4dds and UNACORM are free [R7][R8]; codec white paper sets metrics [S23] | Simulator and comparability PRESENT; fitted models and harness integration ABSENT | Free academic harnesses | Medium | Weak alone (free substitutes). Good as part of D |
| D. Provider adapter + codec integration for a synthesis/sequencing provider | Suppliers bundling codecs: Mimulus + GenScript [S17]; archetype "providers wanting storage-ready offer" (30-companies §5) | Codec/recovery PRESENT; adapters, primers, order export ABSENT; strand length over vendor limit | In-house codecs; Mimulus Code | Shortest plausible path | **Most realistic first paying customer**: paid integration pilot (adapter + benchmark on the provider's error profile + licence/support) |
| E. S3-compatible DNA tier | Biomemory S3 interface [S7]; Scality partnership [S8] | REST/object store ABSENT; no physical backend | Biomemory + Scality occupy the slot | Long | Do not lead with it. Build later as a gateway (§13) |

**Recommendation**
- **First product (open):** "VNX Verify". It is built from parts of the Archive Format, CLI and Benchmark Suite:
  - reader, writer and validator for Sector Zero/One;
  - a conformance test suite;
  - VNX-format verification with a machine-readable report;
  - metrics aligned with the codec white paper.
- **First paid offer:** a fixed-scope **provider integration pilot** (option D). Candidate archetypes from the research:
  - synthesis providers without a storage codec (30-companies §5 lists Ansa, DNA Script, BGI/MGI in Asia; Twist only if it wants an offer independent of Atlas);
  - EU research consortia that need a maintained, standards-conformant codec (DiDAX runs to Oct 2027; 30-companies JSON);
  - [FOUNDER] Indian synthesis or sequencing service providers. No evidence was gathered on these.
- **Reference market, built in parallel:** national archives and libraries (option A). VNX would provide independent verification, OAIS documentation and vendor-neutral reading alongside whichever lab does the chemistry. Indian national and heritage archives are a hypothesis only; there is no evidence (30-companies §5).

---

## 8. Pricing and cost evidence (public figures only)

| Figure | Unit | Source | Caveat |
|---|---|---|---|
| ~$100 per MB written; target below $1/MB; >1,000,000× SSD and 2,500,000× tape | $/MB | [S1] | Industry estimate |
| Biomemory DNA Card: 1 KB, ~$1,000 for two cards (≈ $0.5–1M per MB) | $/KB | [S9][S10] | Novelty product |
| Biomemory target "100 PB for $150,000 in 2026; 1,000 PB in 2030" | target | [S11] | Marketing target; full article not retrieved |
| Bi Sheng-1: $122/MB vs $3,260/MB "commercial" | $/MB | [S30] | Research device; 43.7 KB demo |
| China 2025: synthesis of 2 MB ~$7,000, reading ~$2,000 | $ | [S31] | Researchers' quote |
| ~$0.001 per nt synthesis; DNA ~7 orders of magnitude costlier than tape | $/nt | [S32] | 2022 project baseline |
| Ansa clonal DNA $0.38–0.28/bp | $/bp | [S33] | Biology product, not comparable to storage oligos |
| Iridia: "5–10× lower TCO", 24–48 h retrieval | claim | [S13] | Unverified claim, 2023 |
| Atlas Eon 100 | — | [S2] | **Price not disclosed** |
| Microsoft Silica (glass): "glass cost per TB still lower" | qualitative | [S35] | Competing medium |

**No public figure was found for:**
- the price of DNA codec software or licences;
- benchmark or verification services;
- per-archive verification fees;
- the cost of integration engagements;
- a storage-grade oligo-pool price per nt in 2026 (the Twist and IDT documents gathered are specifications, not price lists; 40-datasets).

VNX pricing therefore cannot be benchmarked against public comparables. It must be discovered with design partners. [FOUNDER] Price points.

**Illustrative arithmetic (not a quote).** The CORDIS 2022 baseline of $0.001/nt [S32] × VNX v4-balanced 9.8469 nt per input byte (`docs/V4_RESULTS.md`) ≈ $9,800 per MB for one synthesised copy. That figure excludes primers, physical redundancy, coverage and sequencing. It is about 100× the Blocks & Files estimate of ~$100/MB [S1].
- The gap shows that the public inputs differ in date and basis. It does not show that VNX is more or less efficient.
- The spread across public figures (~$100/MB to ~$1M/MB) is itself a gap: no vendor publishes a reproducible TCO model (30-companies §6.7).
- Judgement: a transparent, open cost model would be a credible, low-cost VNX asset. Inputs: nt/byte from VNX profiles, redundancy, coverage, and sourced $/nt and $/read.

**Pricing model options (Judgement, no figures)**
- annual support/subscription for SDK users (open code, paid support);
- a fixed-fee integration pilot (option D);
- a per-archive verification attestation fee (option A);
- per-seat or per-node Enterprise Server licences;
- certified-adapter fees from providers.

Avoid pricing per byte encoded until physical volumes exist.

---

## 9. Defensibility (moats)

Honest starting point (Judgement):
- The released code is MIT, the algorithms (RS, marker alignment, AEAD, Merkle trees) are well known, and VNX holds no patents.
- There is no technical moat today that a funded competitor could not reproduce.
- Defensibility has to be built from assets that accumulate.

**Technical moats (to be built)**
1. **Fitted channel models and real-read evidence.** Every public dataset fitted (D01–D04), and every wet-lab round VNX pays for, adds calibration data that competitors would have to collect themselves. This accumulates only if VNX keeps the fitted parameters and records under its own control. Licensing them is a decision for §5.6.
2. **Compatibility record.** Golden archives from every released version must keep decoding (`tests/compat`). For an archival buyer, a long unbroken compatibility record is evidence that cannot be copied quickly.
3. **Assurance engineering.** In place today: differential native/reference fuzzing (700k reads at V5; ~1.05M comparisons for V6 RS), sanitizers, fail-closed publish, 0 false SUCCESS in published sweeps (SIMULATED). These matter more to archives than raw density does.
4. **Conformance assets.** Test vectors and a conformance suite for DDSA specifications, if adopted.

**Commercial moats (to be built)**
1. **Neutrality.** VNX does not sell chemistry, so it does not compete with providers. This holds only while VNX stays software-first (§6).
2. **Standards position.** DDSA membership plus contributions to the codec and interoperability work items [S24]. The DDSA leadership has no visible presence from India or Japan (30-companies §6.8).
3. **Integrations.** Certified provider adapters and the switching costs created by integrating with archive workflows.
4. **Geography.** No India-based DNA storage company remains after BioCompute's reported move to San Francisco [S37].

**Weak or non-existent moats (do not claim)**
- "Better codec" without matched-protocol and physical evidence.
- Speed figures measured on a shared VPS. The V6 native-kernel benchmarks were also recorded on a dirty tree (Baseline A.4).
- Any patent position.

---

## 10. IP, patent and licence clearance

VNX-DNA holds no patents and claims no novelty. The following were flagged during the patent search (30-companies §4). They **require review by an IP lawyer** before VNX makes novelty claims, signs licences that carry warranties, or sells a codec. The "VNX area to compare" column is a pointer for counsel, not a claim of overlap (Judgement).

| # | Patent / application | Title | Assignee | VNX area to compare |
|---|---|---|---|---|
| P1 | CA3249936A1 | Codecs for DNA data storage | Atlas Data Storage, Inc. | Codec, Archive Format |
| P2 | WO2023146570A1 | Encoding and integrity markers for molecular storage applications | Western Digital | Sync markers, frame CRC, integrity chain |
| P3 | US20250174272A1 | Generating and using a state transition matrix for decoding data in a DNA-based storage | Western Digital | Recovery Engine (alignment, soft decoding) |
| P4 | US20260004879A1 | Symbol-linker storage encoding scheme | Seagate | Codec mapping |
| P5 | US11600360B2 | Trace reconstruction from reads with indeterminant errors | Microsoft | Consensus; planned trace reconstruction (V6 roadmap) |
| P6 | US20170141793A1 | Error correction for nucleotide data stores | Microsoft | Inner/outer ECC |
| P7 | US10982276B2 | Homopolymer encoded nucleic acid memory | Molecular Assemblies | Constraint handling (VNX limits homopolymers rather than encoding in them; counsel to confirm) |
| P8 | US12437841B2 | Storing and reading nucleic acid-based data with error protection | Catalog Technologies (now Biomemory) | ECC, Recovery Engine |
| P9 | US20230215516A1 | Joint multi-nanopore sequencing for reliable data retrieval in nucleic acid storage | Quantum | Future nanopore ingestion / multi-read decode |

**Other items for legal review**
1. **VNX's own LICENSE file** appears to omit the standard MIT warranty disclaimer (11 lines; GitHub reports "Other"). Ask counsel whether to correct it, and whether to move future releases to Apache-2.0. The trade-off: an explicit patent grant and contributor clarity, against granting patent rights in VNX's own contributions should VNX ever file.
2. **Effect of public disclosure.** Ask how the public repository (MIT since v0.1) affects any future filing on methods already published. Any filing decision on V6 methods must come before V6 is pushed publicly.
3. **Third-party code.** Keep GPL/AGPL tools (dt4dds, UNACORM, NOREC4DNA, MESA, DNA Fountain ports) at the process boundary. Do not embed HEDGES's bundled Schifra RS without Schifra's commercial terms [R5]. SPIDER-WEB needs BGI permission for commercial use [R9]. mahoraga (PolyForm Noncommercial) cannot be used internally by a for-profit without a licence [R10]. Repositories and datasets without a licence must not be redistributed.
4. **Datasets.** INSDC reads carry no explicit licence. Several GitHub data repositories have no licence file (40-datasets). Keep provenance and attribution for anything committed as a test fixture.
5. **Trademark.** This research did not search for the "VNX" and "VNX-DNA" names in storage-product classes. Do that before a product launch.
6. **Biosecurity and export.** The DDSA's position is "no problem sequences in the pipeline" [S23]. Counsel should advise on screening obligations and on export rules for encryption and for biology-adjacent software, from India and to target countries. Not researched here.

---

## 11. Commercial risks

| Risk | Why (sourced where possible) | Mitigation (Judgement) |
|---|---|---|
| Integrated vendors never buy a third-party codec | In-house codecs everywhere [P1][S16] | Sell to archives (verification) and to new entrants; do not depend on incumbents |
| Atlas releases an open decoder | "open-source script" [S4] | Compete on multi-vendor reading, verification and lifecycle, not on basic decode |
| Biomemory + Scality own the enterprise DNA tier | S3 interface + partnership [S7][S8] | Native archive API first; gateway later; target other object-storage vendors only with evidence |
| Market timing | ~$100/MB vs <$1/MB target [S1]; pilot-scale vendors | Keep burn low; revenue from engagements; cost model as a public asset |
| Competing media (glass) | Silica research complete, lower cost per TB claimed [S35] | Make the format and API medium-agnostic where possible (Judgement: the integrity, catalogue and verification layers are not DNA-specific) |
| No physical validation | Baseline B.11 | §12 E3 before any investor round |
| Simulation does not match reality | 14 unfitted models (Baseline B.4) | §12 E2 fitted models; report SIMULATED vs PUBLIC-DATA-DERIVED separately |
| Credibility damage from overstated pitch material | §14 items | Correct before reuse |
| Patent assertion | §10 | FTO opinion before licensing |
| Small team, single engineering host | Benchmarks on one shared VPS (Baseline B.9); workforce "productivity not proven" (Baseline C.1) | [FOUNDER] hiring plan; CI on every branch |
| Procurement friction for an India-based vendor selling to foreign government archives | Judgement | Partner with a local integrator; start with providers and consortia |
| Standards drift | Sector Zero/One may be revised; "self-discovery" may replace the registry [S24] | Participate in the DDSA; keep the conformance kit versioned |

---

## 12. Evidence needed before seeking investment

Each item has an acceptance test. None exists yet.

| # | Evidence | Acceptance test | Evidence class |
|---|---|---|---|
| E1 | **Same-protocol benchmark** | VNX runs as an external codec in dt4dds-benchmark [R7] (and UNACORM [R8]) at matched code rate (0.5 / 1.0 / 1.5 bit/nt) and strand length (~130–150 nt profile), with a coverage sweep. Competitor results are reproduced in the same run. Configs, seeds and the VNX commit are published. Rerunnable from a tag in CI. No superlatives in the write-up. | SIMULATED |
| E2 | **One channel model fitted to a real public dataset** | Fitter passes round-trip tests (parameters recovered within confidence intervals). Then fitted on D04 CNR (smoke test) and D02 DT4DDS or D01 ETH. Parameter file carries accession, file SHA-256 and fitter commit. Results that use it are reported separately from the 14 unfitted models. | Parameters PUBLIC-DATA-DERIVED; channel results SIMULATED |
| E3 | **One wet-lab round bought from providers** | Pre-registered success criteria. Steps: a VNX-encoded pool with primers and strands ≤300 nt; synthesis from a commercial provider; sequencing by a service lab; decode with the full physical record validated by `experiments/v6/physical/validate.py`. Reported pass or fail as it happens. [FOUNDER] budget; no public quote gathered. | REAL PHYSICAL RESULT |
| E4 | **One design-partner letter of intent** | Non-binding LOI from a provider, consortium or archive. It names the problem, pilot scope, success criteria and willingness to pay. No partner may be named in any material before it is signed. | Commercial |
| E5 | **Standards standing** | DDSA membership; open Sector Zero/One reader and writer with conformance tests; codec metrics reported in the white-paper format [S23]. | Public artefact |
| E6 | **FTO opinion** | Written opinion on P1–P9 and on the licence items in §10. | Legal |
| E7 | **Engineering hygiene** | V6 pushed with CI green; committed full-suite log at a tagged commit; #56 random-access bug fixed; status documents consistent with commits; LICENSE text corrected. | Repository |
| E8 | **Transparent cost model** | Open model: VNX nt/byte per profile × redundancy × coverage × sourced $/nt and $/read, with every input cited. | Public artefact |

Order (Judgement): E7 → E1 + E2 (in parallel) → E5 → E4 → E3 → E6 before any licence is signed → E8 alongside. E3 needs money, so E4 may be needed to fund it. [FOUNDER] Team, funding amount, use of funds.

---

## 13. Object-storage interface: recommendation (architecture only, not an implementation)

**Question:** should VNX expose PUT / GET / DELETE / LIST / VERIFY / RESTORE / ARCHIVE / RECOVER, or an S3-like abstraction?

**Recommendation (Judgement): both, in layers, with the native archive verbs as the source of truth.**

```
Layer 3  S3-compatible gateway (Enterprise Server; built only when a customer asks)
Layer 2  Management resources aligned with the SNIA Swordfish DNA draft [S25]
         (jobs and DNA-process resources: encode, synthesise, store, retrieve,
          sequence, decode, verify)
Layer 1  Native VNX archive API (open specification)
         PUT · ARCHIVE · LIST · GET · RESTORE · VERIFY · RECOVER · DELETE / DESTROY
```

**Why not S3 alone**
- S3-style semantics assume synchronous reads and mutable overwrite. DNA retrieval is quoted in hours to days (Iridia: 24–48 h [S13]), and written molecules cannot be changed.
- Biomemory already offers an S3 interface [S7] with Scality [S8]. S3 compatibility is needed for IT integration, but it would not differentiate VNX.
- The 30-companies analysis recommends S3-compatible object semantics **plus** Swordfish-style job resources (§3, item 3).

**Native verb semantics**

| Verb | Meaning | Notes tied to the Baseline |
|---|---|---|
| PUT | Stage an object into an *open* (unsealed) archive. Digital only; mutable until ARCHIVE. | Uses the existing VNX4 chunking, dedup and AEAD |
| ARCHIVE | Seal the archive. Encode to strands; emit the order through the Provider Adapter; start the physical job. Afterwards the archive is immutable. | Write-once boundary. Needs `order-export` (ABSENT). Sector Zero/One headers (ABSENT) are written here |
| LIST | List objects from the catalogue. The catalogue can be rebuilt from the archive's own file table if the database is lost. | File table and manifest PRESENT |
| GET | Return an object from a digital cache or replica, if one exists. Never touches molecules. | — |
| RESTORE | Asynchronous job: retrieve the sample → sequence → decode → verify → return objects. Returns a job handle and an estimated cost and latency. | Without molecular random access (ABSENT), restoring one object may require sequencing the whole pool. Digital selection reduces decode work, not sequencing. The API must expose this |
| VERIFY | Return an attestation at an explicit level: L0 manifest/Merkle check of a digital copy; L1 re-decode from stored reads; L2 fresh physical sample re-sequenced and decoded. | L0 and L1 PRESENT in substance (`vnx verify`, Merkle proofs); L2 needs E3 |
| RECOVER | Degraded-mode RESTORE with explicit budgets. Returns only verified files and a report of what could not be recovered. Never returns unverified bytes. | PRESENT in substance: recovery planner, budgets, partial verified-file extraction (exit code 9) |
| DELETE | See below. Always reports which erasure level was achieved. | — |
| DESTROY | Request attested physical destruction of a whole pool or capsule through the Provider Adapter. | Physical record schema PRESENT; attestation flow ABSENT |

**DELETE on write-once molecular media**

Molecules cannot be edited, so DELETE cannot mean "the bytes are gone" unless one of these holds:

1. **Tombstone (logical delete).** The catalogue marks the object deleted, and RESTORE and LIST omit it. The data can still be recovered by anyone who holds the pool and the key. Always available.
2. **Crypto-erasure.** Destroy the key that protects the object. This needs key management, which is ABSENT, and **per-object or per-retention-class key domains**.
   - Today VNX derives per-chunk subkeys from one archive master key with HKDF. Destroying a derived subkey is meaningless while the master key exists.
   - Encrypted chunk IDs are HMACs under the archive key, so **deduplication works only within one key domain**. Making objects individually erasable reduces cross-object dedup. This trade-off must be chosen at ARCHIVE time.
3. **Physical destruction (DESTROY).** Destroys every object in the pool or capsule, not just one. Provider-attested.
4. **Rewrite without the object.** A new pool is synthesised and the old one destroyed. This costs another synthesis.

**Recommendation:** DELETE = tombstone + crypto-erasure where the object was archived in an erasable key domain. The response states `logical`, `crypto-erased` or `pending-physical`. Retention class and legal hold are fixed at ARCHIVE time. DESTROY is a separate, privileged, whole-archive operation.

**S3 gateway mapping (when built)**
- PutObject → PUT, plus ARCHIVE on a policy trigger.
- GetObject → GET if cached; otherwise an asynchronous restore request followed by GET.
- ListObjects → LIST.
- DeleteObject → DELETE, with the achieved erasure level returned in metadata.
- No overwrite of sealed objects. A new version creates a new object, so versioning (ABSENT) is required first. The append-only versioning with tombstones in dnastore is a useful design reference [R11].

---

## 14. Corrections to existing pitch material (`/root/vnx-dna-company/vnxco`)

Flagged only; no file was edited. Market corrections come from 30-companies §7.

| Where | Current text | Issue | Correct to |
|---|---|---|---|
| `dossier.py` timeline | "Feb 2026 Biomemory launches DNA Cards [S9]" | Wrong date | Launched 2023, deliveries from Jan 2024 [S9][S12] |
| `dossier.py` competitors | Iridia "backed by Seagate and Western Digital" | Seagate investment not found in a primary source | Western Digital Capital confirmed [S15]; drop Seagate unless a source is found |
| `dossier.py` S11 | Ansa "$68M financing" | Not reconciled | $54.4M Series B, 1 Oct 2025 [S34]; or label $68M as cumulative only if a source shows it |
| `dossier.py`, `deck.py` | "Atlas Data Storage" | Renamed | AtlasBase (atlasds.com redirects); CEO change reported Mar 2026 [S3] |
| `dossier.py` funding | "Biomemory $23.2 million before 2026 [S9]" | Secondary source | €5M (2022) + ~$18M Series A (Dec 2024) [S39] |
| `dossier.py` S14 | Indian entrepreneur building DNA storage (BioCompute, Bengaluru) | Outdated | Relocation to San Francisco reported Jul 2026 [S37] |
| Missing facts | — | — | Atlas Eon 100 [S2]; LoC–MISL pilot [S18]; Scality–Biomemory [S8]; Mimulus–GenScript [S17]; JPEG DNA DIS [S28]; Swordfish DNA draft [S25]; new DDSA co-chairs [S26]; Silica research phase complete [S35] |
| `dossier.py` Product strategy | "Codec SDK / licence … software ready; needs lab validation" | Overstates: the SDK is PARTIAL and V9 has not started | "Codec and CLI ready in simulation; SDK in development; no physical validation" |
| `dossier.py` / `deck.py` | "hardware-agnostic" codec | Untested on any hardware or provider | "designed to be provider-independent; not yet tested with any provider" |
| `deck.py` traction | "0 false archive successes — a wrong file is never published" | Absolute statement | "0 false successes in N simulated trials (file)" |
| `deck.py`, `dossier.py` | 1,013 tests | Correct for v5.0.0 only | Label "at v5.0.0"; no verified count exists for the V6 commit |
| `dossier.py` / `deck.py` Ω pages | "autonomous engineering organisation … running continuously" | The workforce audit states that autonomous code-changing dispatch is not implemented by design and that productivity is not proven (Baseline C.1, C.4) | Describe as a human-started, gated engineering system; verify against the current deployment before reuse |
| `dossier.py` Part C / `deck.py` ask | Lab partner "ideally an Indian biofoundry"; "India bio-compute centre" as offer 4 | Conflicts with the product-first, no-wet-lab strategy | Providers bought as a customer; keep the centre as a long-term option only if the founder confirms |
| Market-size forecasts S2–S5 ($0.7B–$3.3B by 2030) | — | Not re-verified in this research | Re-check before reuse; present as the range of third-party forecasts |
| Pipeline / codec slide | — | Omits primers and vendor length limits | Add "primers and order export: planned" |

---

## 15. Open questions for the founder

1. **V6 licence.** Should V6 and later continue under MIT (or Apache-2.0)? Or should a defined set of Recovery Engine extensions be held back as source-available or commercial? Decide before `build/v6-p1` is pushed publicly.
2. **First paid offer.** Do you accept a provider integration pilot (option D) as the first paid target, with archives (option A) as the reference market? Which provider archetypes, Indian or foreign, should be approached first? No contact exists.
3. **Wet-lab budget.** What can be spent on E3, and should it wait for a design partner (E4)?
4. **India bio-compute centre.** Keep it as a long-term option, or remove it from material that now presents a no-wet-lab strategy?
5. **DDSA membership.** Should VNX join and commit engineering time to Sector Zero/One conformance?
6. **Legal.** Who is IP counsel for the FTO opinion, the LICENSE fix, the trademark search and export/biosecurity questions?
7. **Team.** [FOUNDER] Founders, roles, hires. The existing dossier lists three roles: molecular biologist, coding-theory engineer, business development.
8. **Positioning.** Keep "AI-generated data" for later (§2), or test it now with buyers?

---

## 16. Sources

Accessed during the 2026-10-05 research pass; IDs are local to this document. Some 2026 DDSA newsletter facts came only from search snippets and should be read by a person before they go into investor material (30-companies, source note). snia.org blocked automated fetches; some SNIA PDFs were reached through mirrors.

**Market, companies, standards**
- [S1] Blocks & Files, DNA data storage: when the physics work but the economics don't, 16 Jan 2026. https://blocksandfiles.com/2026/01/16/dna-data-storage-when-the-physics-work-but-the-economics-dont/
- [S2] StorageNewsletter, Atlas Eon 100, 2 Dec 2025. https://www.storagenewsletter.com/2025/12/02/atlas-data-storage-introduces-the-worlds-first-scalable-dna-data-storage-offering/
- [S3] Blocks & Files, AtlasBase Thalia chip, 23 Sep 2026. https://www.blocksandfiles.com/data-protection/2026/09/23/atlasbase-makes-thalia-dna-computer-and-storage-chip/5298506
- [S4] Atlas, DNA Data Storage Product Update, LoC DSA, 10 Mar 2026. https://www.digitalpreservation.gov/meetings/DSA2026/0231_banyai_2026.03.10%20Atlas%20LOC%20v1.pdf
- [S6] DCD, Biomemory acquires Catalog, data-center deployment H2 2026. https://www.datacenterdynamics.com/en/news/dna-data-storage-startup-biomemory-acquires-catalog-technologies-plans-data-center-deployment-in-h2-2026/
- [S7] Blocks & Files, French DNA coming to a datacenter (S3 interface), 6 Mar 2026. https://www.blocksandfiles.com/architecture/2026/03/06/french-dna-coming-to-a-datacenter-near-you-soon/4093753
- [S8] Scality, Biomemory partnership, 2 Jun 2026. https://www.scality.com/press-releases/dna-data-storage-biomemory-scality
- [S9] DCD, Biomemory launches DNA storage product. https://www.datacenterdynamics.com/en/news/biomemory-launches-first-commercially-available-dna-storage-solution/
- [S10] TechRadar, $1,000 per KB. https://www.techradar.com/pro/dna-storage-finally-reaches-mainstream-well-sort-of-but-it-will-cost-you-a-whopping-dollar1000-per-kb-yes-kilobyte
- [S11] TechRadar, Biomemory roadmap headline. https://www.techradar.com/pro/1kb-now-100pb-for-dollar150000-in-2026-1000pb-in-2030-biomemory-ceo-sheds-more-light-on-mind-boggling-expectations-for-its-dna-storage-platform
- [S12] Notebookcheck, Biomemory DNA Cards. https://www.notebookcheck.net/Biomemory-DNA-Cards-to-store-data-as-synthetic-DNA-with-150-year-lifespan.778636.0.html
- [S13] TechTarget, DNA storage to tackle massive archives (Iridia), Nov 2023. https://techtarget.com/searchdatabackup/news/366560519/DNA-storage-to-tackle-massive-archives
- [S15] eeNews Europe, Iridia raises $24M (Western Digital Capital). https://www.eenewseurope.com/en/dna-based-memory-startup-raises-24-million/
- [S16] Blocks & Files, Mimulus molecular DNA storage project, 9 Jun 2026. https://www.blocksandfiles.com/file/2026/06/09/mimulus-molecular-dna-storage-project/5252656
- [S17] BioSpace, GenScript and Mimulus partner … for the AI era, Apr 2026. https://www.biospace.com/press-releases/genscript-and-mimulus-partner-to-industrialize-dna-based-data-storage-for-the-ai-era
- [S18] Library of Congress, DSA 2026 slides (DNA pilot status). https://www.digitalpreservation.gov/meetings/DSA2026/0103_budaSmithColtellino_Designing%20Storage%20Architecture%20Conference%202026%20OCIO%20DSD_VC_NBS.pdf
- [S19] Library of Congress RFI 030ADV25R0036, Synthetic DNA Data Storage. https://oversight.govsignals.ai/opportunity/synthetic-dna-data-storage-library-of-congress-030adv25r0036
- [S20] SNIA, DDSA releases first specifications (Sector Zero). https://www.snia.org/news_events/newsroom/dna-data-storage-alliance-releases-its-first-specifications-storage-digital
- [S21] SNIA, DNA Data Storage Sector One. https://snia.org/standards/technology-standards-software/standards-portfolio/dna-data-storage-sector-one
- [S22] SNIA, DNA Stability Evaluation Method v1.0. https://www.snia.org/educational-library/dna-stability-evaluation-method-dna-data-storage-containment-systems-v10-2024
- [S23] StorageNewsletter, SNIA published two DNA data storage white papers, 16 Jul 2025. https://www.storagenewsletter.com/2025/07/16/snia-published-two-dna-data-storage-white-papers/
- [S24] SNIA, DDSA 2025 preview. https://snia.org/sites/default/files/Preview/2025/20250115%20-%20DNA%20Data%20Storage%20Alliance%20-%20SNIA%20Preview%202025%20v2.pdf
- [S25] SNIA, DNA Data Storage in Swordfish – Working Draft, Jan 2026. https://www.snia.org/sites/default/files/technical-work/swordfish/draft/DNA%20Data%20Storage%20in%20Swordfish%20-%20Working%20Draft.pdf
- [S26] DDSA presentation, LoC DSA 2026. https://www.digitalpreservation.gov/meetings/DSA2026/0230_skilaustas_DNA%20data%20storage%20alliance%20presentation.pdf
- [S27] jchristn/RosettaStone (third-party registry server). https://github.com/jchristn/RosettaStone
- [S28] JPEG, JPEG DNA reaches DIS. https://jpeg.org/items/20260608_press.html
- [S29] CORDIS, DNAMIC project. https://cordis.europa.eu/project/id/101115389
- [S30] CAS, Bi Sheng-1, 9 Apr 2025. https://english.cas.cn/newsroom/cas_media/202504/t20250409_1040848.shtml
- [S31] Xinhua, 9 Jun 2025. https://www.news.cn/tech/20250609/4970eba7b26f48fa9412da27359e07cd/c.html
- [S32] CORDIS, MoSS project. https://cordis.europa.eu/project/id/101058035
- [S33] Drug Discovery News, Ansa 50 kb clonal DNA, Oct 2025. https://www.drugdiscoverynews.com/ansa-biotechnologies-redefines-what-s-possible-in-dna-synthesis-with-50-kb-sequence-perfect-clonal-product-16751
- [S34] Business Wire, Ansa $54.4M Series B, 1 Oct 2025. https://www.businesswire.com/news/home/20251001701666/en/Ansa-Biotechnologies-Secures-54.4-Million-in-Series-B-Financing
- [S35] Microsoft Research, Silica, LoC DSA Mar 2026. https://www.digitalpreservation.gov/meetings/DSA2026/0124_Silica-2026-03-09-Library-of-Congress-sharing.pdf
- [S36] Twist Bioscience, spin-out of DNA data storage. https://investors.twistbioscience.com/news-releases/news-release-details/twist-bioscience-spins-out-dna-data-storage-independent-company
- [S37] News Karnataka, Bengaluru startup's US move, 2 Jul 2026. https://newskarnataka.com/bengaluru/bengaluru-startups-us-move-reignites-brain-drain-debate/02072026/
- [S38] StorageNewsletter, Atlas Data Storage seed financing, 6 May 2025. https://www.storagenewsletter.com/2025/05/06/new-company-atlas-data-storage-announces-initial-close-of-seed-financing/
- [S39] Business Wire, Biomemory $18M Series A, 10 Dec 2024. https://www.businesswire.com/news/home/20241210725720/en/Biomemory-Secures-18-Million-in-Series-A-Funding-to-Revolutionize-Data-Storage-With-Molecular-Technology

**Patents (Google Patents; assignees checked on the patent pages during research)**
- [P1] https://patents.google.com/patent/CA3249936A1/en
- [P2] https://patents.google.com/patent/WO2023146570A1/en
- [P3] https://patents.google.com/patent/US20250174272A1/en
- [P4] https://patents.google.com/patent/US20260004879A1/en
- [P5] https://patents.google.com/patent/US11600360B2/en
- [P6] https://patents.google.com/patent/US20170141793A1/en
- [P7] https://patents.google.com/patent/US10982276B2/en
- [P8] https://patents.google.com/patent/US12437841B2/en
- [P9] https://patents.google.com/patent/US20230215516A1/en

**Repositories**
- [R1] https://github.com/microsoft/TrellisBMA (MIT, archived)
- [R2] https://github.com/microsoft/DNABoundedHomopolymerEncoding (MIT)
- [R3] https://github.com/ntpz870817/Chamaeleo ; https://github.com/BGI-SynBio/YinYangCode (MIT)
- [R4] https://github.com/MW55/DNA-Aeon (MIT)
- [R5] https://github.com/whpress/hedges (MIT; bundles Schifra RS under its own terms)
- [R6] https://github.com/fml-ethz/dt4dds ; Gimpel et al., Nat Commun 2023, https://doi.org/10.1038/s41467-023-41729-1 (GPL-3.0)
- [R7] https://github.com/fml-ethz/dt4dds-benchmark (GPL-3.0)
- [R8] https://github.com/AAnzel/UNACORM ; https://arxiv.org/abs/2608.09673 (GPL-3.0; preprint)
- [R9] https://github.com/HaolingZHANG/DNASpiderWeb (custom BGI licence)
- [R10] https://github.com/jeplb/mahoraga-codec (PolyForm Noncommercial 1.0.0)
- [R11] https://github.com/Mr-PU/dnastore (Apache-2.0)

**Datasets (40-datasets registry)**
- [D01] ETH codec benchmark pool, ENA PRJEB90546 (Gimpel et al., Nat Commun 2026)
- [D02] DT4DDS characterisation, ENA PRJEB65931 (Gimpel et al., Nat Commun 2023)
- [D03] Nanopore channel dataset, https://doi.org/10.5281/zenodo.10943282 (CC BY 4.0; preprint)
- [D04] Clustered Nanopore Reads, https://github.com/microsoft/clustered-nanopore-reads-dataset (MIT; known generation caveat)
