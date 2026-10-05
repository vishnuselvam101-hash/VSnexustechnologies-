# VNX-DNA global roadmap (V6 → V12 → V50)

Status: **PROPOSAL for founder approval**, 2026-10-05. It comes out of the research gate:
- [COMPETITIVE_GAP_ANALYSIS.md](COMPETITIVE_GAP_ANALYSIS.md)
- [GITHUB_ECOSYSTEM_MAP.md](GITHUB_ECOSYSTEM_MAP.md)
- [COMPETITOR_MATRIX.md](COMPETITOR_MATRIX.md)
- [ALGORITHM_COMPARISON.md](ALGORITHM_COMPARISON.md)
- [DNA_STORAGE_DATASET_REGISTRY.md](DNA_STORAGE_DATASET_REGISTRY.md)
- [VNX_PRODUCT_STRATEGY.md](VNX_PRODUCT_STRATEGY.md)
- [VNX_BENCHMARK_PLAN.md](VNX_BENCHMARK_PLAN.md)
- [RESEARCH_AUTOMATION_PLAN.md](RESEARCH_AUTOMATION_PLAN.md)

The decisions are summarised in [VNX_STRATEGIC_DECISION_REPORT.md](VNX_STRATEGIC_DECISION_REPORT.md).

On approval this file replaces the V5–V12 order of 2026-10-04 (`/root/vnx-dna-ai/workflows/VERSIONS.md`). Until then, that order stays canonical. [ROADMAP.md](ROADMAP.md) keeps the release history.

Evidence rule for every line below: VNX results are SIMULATED unless stated otherwise. External facts carry the evidence label from the research files. "Expected" gains are THEORETICAL until an experiment runs.

## 0. Strategic direction (preserved)

VNX-DNA becomes a **software-first, product-first infrastructure layer for molecular/DNA archival storage**. VNX does not own a wet lab. Synthesis, sequencing and containment providers are bought as services, or integrated as partners.

```
digital data → VNX Archive → compression/dedup → encryption/integrity → VNX Codec → ECC
            → alignment/indel recovery → channel modelling → provider-independent storage API
            → synthesis / sequencing providers
```

Positioning: *the software infrastructure layer for reliable molecular/DNA archival storage*. The later option, *the archival layer for AI-generated data*, is held back until there is physical evidence (see §5).

## 1. What the research changed

| Finding (source) | Consequence for the roadmap |
|---|---|
| **No independent codec vendor exists.** Every seller builds an in-house codec (Atlas/AtlasBase codec patent application CA3249936A1, Mimulus Code, Biomemory, Iridia). Free academic codecs set the public bar. (30-companies) | Our value is not in "one more codec". It is in **vendor-neutral verification, standards conformance, recovery quality and system features**. Plain mappers are commodity. |
| **Credible simulation already has a standard harness:** ETH dt4dds (calibrated on 40 sequencing experiments) and dt4dds-benchmark (6 codecs, 6 clusterers, real-read workflows), both GPL-3.0. VNX can plug in as an external process. (20-repos) | Benchmark against the field **early (V6)**, on that harness, before any competitive claim. Today VNX has **no same-protocol comparison at all**. |
| **VNX's channel models are not fitted to any platform.** Public datasets with ground truth exist: ETH benchmark PRJEB90546, DT4DDS PRJEB65931, nanopore Zenodo 10943282, Microsoft CNR. (40-datasets, 00-baseline) | Channel intelligence (fitting to public data) moves to **V7**, ahead of probabilistic/adaptive coding, which needs fitted models to be meaningful. |
| **VNX strands carry no primers.** 313 nt + 2×20 nt primers = 353 nt, above the 350-nt Twist and IDT pool limits. (40-datasets) | A vendor-compatible short-strand profile, a primer module and order export are prerequisites for any wet-lab round. They move to **V7**. |
| **DNA Data Storage Alliance specs** (Sector Zero/One 2024, codec metrics white paper 2025, Swordfish DNA management draft Jan 2026) have **no reference implementation and no conformance suite**. JPEG DNA (ISO/IEC 25508-1) reached DIS in April 2026. (30-companies) | A standards-conformant archive engine is an open, defensible position. It belongs in **V9 (storage engine)**. |
| **Random access at scale needs molecular (primer/partition) selection**, which VNX lacks. VNX's digital `--select` is broken on V6 stripe archives (job #56). (00-baseline, gap analysis §10) | Fix #56 in V6. Design molecular random access for 100 TB–1 EB in **V8 (extreme scale)**. |
| **Biomemory already offers S3 plus a Scality partnership.** dnastore shows the object-API shape. (30-companies, 10-repos) | The object API is a product layer (V12), not a research priority. Molecular media is write-once, so DELETE must be a tombstone/crypto-shred, never a physical claim. |
| **Inner indel codes with wet-lab data exist:** HEDGES (PNAS 2020), DNA-Aeon (Nat Commun 2023), StairLoop (Nat Commun 2025). Trace reconstruction has public baselines: Trellis BMA, BMA, bbs. (10/20-repos) | VNX's marker + banded-DP + consensus approach must be **benchmarked against them (V6/V7)** before we either keep it or adopt an inner code (V10 decision). |

## 2. Proposed version sequence

The founder's initial structure for the 2026-10-05 brief is kept. Its order is justified by the dependencies above. The differences from the 2026-10-04 order:
- "advanced indel/consensus" is split between V6 (baseline + bug fixes) and V7 (fitted-channel consensus);
- streaming/scalability becomes part of V8 "extreme scale";
- SDK/API moves into V9 as the storage engine's public surface, with V12 as the product.

| Version | Theme | One-line goal |
|---|---|---|
| **V6** | **Architectural foundation, specification and competitive baseline** (founder V6 directive) | Formal spec, versioning, conformance vectors, layered codec with physical abstraction, channel-model framework, security model; fix the P1 bugs; **first same-protocol comparison** with public codecs |
| **V7** | **Channel intelligence** | Fit the V6 channel framework to public data; quality-weighted consensus; a vendor-compatible strand profile, primers and order export |
| **V8** | **Extreme-scale storage and recovery** | Streaming and parallel pipelines, and a random-access architecture for 100 TB–1 EB logical archives |
| **V9** | **Storage engine / archive infrastructure** | A Sector Zero/One conformant archive engine, versioning, catalogue, stable SDK and format registry |
| **V10** | **Adaptive coding / autonomous optimisation** | Soft-input decoding, adaptive redundancy from an estimated channel, and the inner-code decision (markers vs. HEDGES/Aeon-style) |
| **V11** | **Physical-provider integration** | A provider adapter interface, **repeatable wet-lab rounds bought from providers** (beyond the V7 first order), a Swordfish-aligned management API and biosecurity screening |
| **V12** | **Unified commercial platform** | SDK + CLI + Storage API + Enterprise Server + verification service, with the licensing split from VNX_PRODUCT_STRATEGY |

**Physical evidence is a milestone, not a version.** Order the first small oligo pool as soon as V7 delivers the short-strand profile and order export. Do not wait for V11. V11 is about *integrating* providers. The first proof of physical decode should come earlier, because it is the main credibility gap. Cost is unknown until we get quotes; public per-MB figures range from about $100/MB to about $1M/MB (30-companies §2).

### V6: architectural foundation, specification and competitive baseline

V6 follows the founder's **V6 Master Engineering & Research Directive (2026-10-05)**. That directive is authoritative for V6 scope and order. Its goal is a modular, formally specified, reproducible codec platform with a physical abstraction layer. The research gate adds three things to it:
- the competitive benchmark lab;
- the two P1 bug fixes;
- the measurements that later versions depend on.

Base: `build/v6-p1` @ 081697b. Phase 1 of the outer-code work is mostly done (SIMULATED).

| Directive phase | Content (summary) | Research-gate additions |
|---|---|---|
| 0. V5 forensic audit | `docs/V6_BASELINE_AUDIT.md`: re-run tests, sanitizers, fuzzing and benchmarks; architecture and debt inventory | Use the verified facts from 00-baseline; correct the 1781-vs-1690 count and the dirty-tree benchmarks |
| 1. Architecture + specification | `docs/spec/VNX-DNA-SPEC-V6.md`; explicit layers; versioning of archive, codec, strand format, channel model and provider adapter | Align field naming with DDSA Sector Zero/One so the V9 conformance work is additive. Evaluate the 4-byte group index / 2-byte archive tag limits (V8 address-space item) |
| 2. Codec/API refactor | One explicit canonical pipeline; `encode/decode/inspect/verify/simulate/benchmark/extract/list` | — |
| 3. Channel-model framework | ErrorModel interfaces; synthesis → storage → amplification → sequencing stages; machine-readable model metadata; DATA_SOURCE provenance | Schema fields for position profiles, a 4×4 substitution matrix, deletion-run length, lognormal coverage and a synthesis/sequencing split, **so V7 fitting to public data needs no schema change** |
| 4. Indel + soft-decision | Measurement-driven improvements only | Design and baseline for quality-weighted consensus, compared against BMA / Trellis BMA / bbs on the CNR dataset |
| 5. Performance/native | Native layer audit, sanitizers, component benchmarks at 1 MiB–1 GiB | Re-run the native benchmarks at a clean commit (`git_dirty: false`) |
| 6. Security + fuzzing | `docs/security/V6_SECURITY_MODEL.md`, fuzz all parsers and kernels | libFuzzer harnesses for RS and the aligner (#10) |
| 7. Interoperability + lab interface | `DNAWriter/DNAReader/DNAProvider`, ReferenceSimulatorProvider only, export/import manifests | Record the 350-nt vendor limit in the export package checks; primers stay V7 |
| 8. Conformance + reproducibility | `tests/conformance/` golden vectors; experiment manifest | — |
| 9. Benchmarking | Standard benchmark framework | **Benchmark lab B0/B1:** VNX as an external codec in dt4dds-benchmark against DNA-Aeon, HEDGES, DNA-RS, YYC and DNA Fountain; mapping rate gap against DNABoundedHomopolymerEncoding |
| 10. Documentation + release audit | Completion report, `V6_DEFERRED.md`, tag only if all criteria pass | — |

Also in V6: fix job #56 (`--select` on stripe archives) and #13 (`_FlatReads[-1]`) with regression tests; write the missing `docs/V6_PHASE1_REPORT.md`; CI on the V6 SHA (push needs the founder's go).

**Not in V6** (see `docs/V6_DEFERRED.md` when written): LDPC, ML decoders, object/S3 API, enterprise server, real provider adapters, primers/order export, and fitting to public data. Fitting moves to V7, but the V6 schema is ready for it.

### V7: channel intelligence

| Item | Why | Exit criterion |
|---|---|---|
| Fit the V6 channel-model framework (schema fields already in V6) | The 14 models are i.i.d. and unfitted (40-datasets) | No schema change needed; v1 models still valid |
| Fitter with round-trip tests, then fits CNR → nanopore Zenodo 10943282 → DT4DDS → ETH benchmark | Real-data credibility without a wet lab | `illumina-twist-fit`, `illumina-electrochem-fit`, `ont-hac-fit`, `ont-fast-fit` labelled PUBLIC-DATA-DERIVED, with dataset hashes |
| Evidence class PUBLIC-DATA-DERIVED in the physical record schema | Honest labelling | Validator test |
| Quality-weighted consensus and trace reconstruction (from V6's design) | The gap against Trellis BMA / DNAformer-class decoders | Recovery vs. coverage on fitted models compared with V6 at equal overhead; 0 false SUCCESS |
| Short-strand vendor profile (≤ 300 nt including primers; also a ~150–200 nt profile) | Twist/IDT limit is 350 nt; public benchmarks use 110–157 nt | Round-trip and recovery parity tests |
| Primer module plus `vnx order-export` plus read ingestion (primer trim, paired-end merge) | Prerequisites for any wet-lab round | Pipeline tested on D03/D01 reads; order files validated against vendor constraints |
| **Milestone: first oligo-pool order** (founder budget decision) | Converts SIMULATED claims into physical evidence | REAL PHYSICAL RESULT record, decoded, or the failure documented |

### V8: extreme-scale storage and recovery

| Item | Why | Exit criterion |
|---|---|---|
| Streaming encode and decode with bounded memory; parallel pipeline for 1–8 workers (milestones 7–9) | V4's noisy runs stop at 16 MiB; the 1 GiB run used a clean channel | Peak RSS flat from 32 KiB to 1 GiB on a noisy channel; scaling curve MEASURED on this VPS |
| Random-access architecture for 100 TB / 1 PB / 1 EB logical archives: hierarchical partitions with primer-addressed pools, a molecular index/catalogue pool, selective decode | Gap analysis §10 (THEORETICAL arithmetic) | Design doc plus a simulation of selective retrieval cost (reads sequenced per file) at the three scales |
| Address space beyond one flat 32-bit group index: about 11 TB per archive at v4-balanced. A 2-byte archive tag collides at about 300 random archive IDs in one pool (COMPETITIVE_GAP_ANALYSIS §10, THEORETICAL) | PB/EB logical archives and multi-archive pools need it | A versioned frame/superblock with a wider index and collision-free archive identifiers; V4/V5/V6 archives still decode |
| Selection pass that skips parsing non-selected reads | `--select` still parses and aligns every read (00-baseline B.6) | Selective decode time proportional to the selected data |
| Scale benchmarks on the benchmark lab (B3) | Comparable scaling numbers | Published with provenance |

### V9: storage engine / archive infrastructure

| Item | Why | Exit criterion |
|---|---|---|
| Sector Zero/One reader/writer, open-source reference implementation, plus a conformance test suite | None exists publicly (30-companies §3) | Round trip of DDSA examples; suite published |
| Codec/vendor registry or "self-discovery" support | The DDSA work item has no product; the RosettaStone server has been inactive since 2023 | Spec plus implementation |
| Archive versioning (append-only, tombstones, snapshots); catalogue | V4/V6 have no versioning (00-baseline B.2) | Format version with compatibility tests (V4/V5 golden archives still decode) |
| JPEG DNA payload wrapping | ISO/IEC 25508-1 reached DIS in April 2026 | Wrap/unwrap test vectors |
| Stable SDK (semver API, typed, packaged wheels with native kernels, API.md) | The current API dates from V1–V3 (00-baseline B.7) | Contract and backward-compatibility tests |

### V10: adaptive coding / autonomous optimisation

| Item | Why | Exit criterion |
|---|---|---|
| Soft-input decoding end to end (posteriors → LLRs) | Present only opt-in and partially (V5 soft) | Calibration plus the frontier against V9 on fitted models |
| LDPC vs. RS research and prototype (job R-02) | StairLoop and mahoraga use LDPC; VNX has none | A go/no-go decision backed by benchmark |
| **Inner-code decision:** markers + DP (VNX) vs. a HEDGES / DNA-Aeon-style inner code | Both have wet-lab papers; VNX has not compared | Same-protocol benchmark on fitted channels; decision recorded |
| Adaptive redundancy from an estimated channel; profile auto-selection | Needs V7 fitted models | Overhead–recovery frontier improvement, SIMULATED |
| ML reconstruction evaluation (DNAformer-class, TReconLM) with licence check | ML decoders are emerging (20-repos) | An evaluation report; adopt only with benchmark evidence and a usable licence |

### V11: physical-provider integration

| Item | Why | Exit criterion |
|---|---|---|
| Provider adapter interface (synthesis order, sequencing intake, containment records) | Provider-independent API is the strategic layer | Adapters for 2 synthesis and 2 sequencing providers (order formats only, no partnership claimed) |
| Repeatable wet-lab rounds bought as a customer | Physical evidence beyond the V7 milestone | ≥ 2 REAL PHYSICAL RESULT records |
| Swordfish-aligned management API (design and implementation) | SNIA working draft, Jan 2026 | Endpoints mapped to the draft; conformance notes |
| Codec-level biosecurity screening plus attestation | A DDSA requirement with no tooling (30-companies §6) | Screening tests; documented limits |
| Data-retention calculator using the DDSA stability method | DDSA work item, unclaimed | Calculator with sourced parameters |

### V12: unified commercial platform

SDK, CLI, Storage API (S3-compatible data path with write-once semantics; Swordfish-style management), Enterprise Server, Provider Adapters, Benchmark Suite and a verification service. The packaging and licensing split follows [VNX_PRODUCT_STRATEGY.md](VNX_PRODUCT_STRATEGY.md). The release gate is COMMERCIAL_READINESS: licence review, IP review of the patents flagged in 30-companies §4, a security review with no open CRITICAL/HIGH, and SDK/API docs.

## 3. Proposed features: the founder's ten questions

The full table for every candidate feature is in COMPETITIVE_GAP_ANALYSIS §22. These are the V6–V8 headline items:

| Feature | Why / problem | Who needs it | Competitor evidence | Benchmark | Cost | Complexity | Security impact | Commercial value | Moat? |
|---|---|---|---|---|---|---|---|---|---|
| Same-protocol benchmark lab (V6) | We cannot claim anything comparative today | Investors, partners, DDSA | dt4dds-benchmark, UNACORM exist (GPL) | It *is* the benchmark | Low–medium (adapters, compute on the VPS) | Medium | None (process boundary) | High (credibility) | Low by itself; enables all other claims |
| Fitted channel models (V7) | Models are unfitted, so SIMULATED results are weakly grounded | Everyone evaluating VNX | dt4dds is fitted on 40 experiments | Round-trip fitting tests | Medium (downloads ~1–12 GB per dataset) | Medium | Data licences (CC BY / INSDC) | High | Medium (curated fitted-model library) |
| Short-strand profile + primers + order export (V7) | Cannot order synthesis today (353 nt > 350 nt) | First wet-lab round | Storage-D, Organick use primers | Vendor preflight tests | Low–medium | Medium | Order files carry data, so treat them as sensitive | High (unlocks physical evidence) | Low |
| Random access at scale (V8) | No molecular selection; select bug | Archive buyers (LoC-type) | Organick 2018 (35 files, primers) | Selective retrieval simulation | Medium | High | Index privacy (encrypted catalogue) | High | Medium–high (scale design) |
| Sector Zero/One conformance (V9) | No reference implementation exists | DDSA members, multi-vendor buyers | None public | Conformance suite | Medium | Medium | Low | High | **High** (neutral reference implementation) |

## 4. V13–V50: themes, gated by saturation (not by numbering)

The numbering is a capacity plan, not a promise. A band starts only when the band before it meets its exit evidence. It is cut short when the saturation criteria (§5) show that the design space is covered.

- **V13–V20, advanced storage infrastructure:**
  - multi-pool / multi-vendor archives;
  - erasure coding across physical pools and sites;
  - catalogue at billions of objects;
  - incremental and deduplicated append across years;
  - key management (rotation, multi-recipient, escrow, HSM/KMS) — absent today (00-baseline B.8);
  - cryptographic proof of decode (verifiable decode transcripts);
  - long-term format migration;
  - OAIS packaging.
- **V21–V30, provider interoperability and ecosystem:**
  - certified adapters per provider and chemistry (Illumina, nanopore R10.x, enzymatic synthesis, electrochemical arrays);
  - cross-vendor archive reading;
  - a public fitted-channel library;
  - DDSA conformance programme participation;
  - interchange with JPEG DNA and other payload standards;
  - a hosted registry if the DDSA adopts one.
- **V31–V40, large-scale archival and AI-data applications:**
  - tiering from object stores (S3 lifecycle to the DNA tier);
  - dataset and model-weight archival with provenance;
  - content-addressed, deduplicated corpora;
  - selective retrieval economics at PB scale;
  - only with physical round-trip evidence at GB scale.
- **V41–V50, advanced molecular-storage research and platform maturity:**
  - composite alphabets and enzymatic-synthesis-native codes;
  - in-storage search (similarity search, as in UW cas9-similarity-search);
  - ML decoders if they beat classical ones on fitted channels;
  - formal verification of decoder fail-closed properties;
  - long-horizon ageing models;
  - external validation by independent labs.

If research shows a better sequence, the order changes and the reason is recorded in this file.

## 5. Saturation criteria (when to stop adding versions)

Development stops adding versions for a theme when **all** of the following hold. Each item has a measurable check and is reviewed at every version gate.

1. **Ecosystem coverage:** every public codec/decoder with a usable licence and a level ≥ 3 paper has been benchmarked by VNX on the common harness. Check: the benchmark lab participant list against the ecosystem map shows 0 unbenchmarked level-3+ entries.
2. **Competitor capability coverage:** no RED row in `docs/data/competitor_matrix.csv` is P0–P2 without a decision (build / support / reject).
3. **Error-model coverage:** fitted models exist for Illumina (≥ 2 synthesis chemistries) and nanopore (≥ 2 basecaller settings), including position-dependent, synthesis-vs-sequencing, coverage and ageing stages.
4. **Recovery mechanisms benchmarked:** for each of dropout, burst loss, substitution, insertion, deletion and low coverage, the VNX threshold is measured and has at least one alternative algorithm compared on the same protocol.
5. **Scalability understood:** measured curves up to the largest feasible size, plus a validated model (prediction within ±20 %) for extrapolating to PB/EB, with the bottleneck stage identified.
6. **Stable interfaces:** archive format, SDK, Storage API and provider adapter have had no breaking change across 2 consecutive minor versions, and compatibility tests cover every released format.
7. **Interoperability:** Sector Zero/One conformance passes. At least one external archive or format (e.g. JPEG DNA or a DDSA example) round-trips.
8. **Security maturity:** threat model current; fuzzing on every parser and kernel; key management implemented; no open CRITICAL/HIGH; an external review done.
9. **Physical integration boundaries defined:** provider adapter contracts are frozen, and ≥ 2 REAL PHYSICAL RESULT records exist.
10. **Commercial requirements validated:** at least one design partner has confirmed requirements in writing (an LOI or a pilot scope).
11. **Research watch is quiet:** the research automation reports no NEW ALGORITHM / NEW BENCHMARK event in two consecutive quarters that would change a decision.

If saturation holds at V17, there will be no V18–V50. If major gaps remain at V50, work continues.

## 6. Readiness for V6 execution

Per the founder's sequence (VALIDATION → REPORT/STATUS → HARDENING → READINESS GATE → V6 EXECUTION), see `/root/vnx-dna-ai/AUTONOMOUS_WORKFORCE_READINESS.md`. New V6 work in this roadmap starts when:
- the founder approves this research gate (G11);
- the founder's inbound Telegram check passes (G1).

Bug fixes (#56, #13) and test, lint and backlog hygiene may proceed now.
