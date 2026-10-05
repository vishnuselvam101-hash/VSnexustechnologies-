# VNX-DNA strategic decision report

Date: 2026-10-05. Status: **for founder approval** (research gate G11). Inputs:
- [COMPETITIVE_GAP_ANALYSIS.md](COMPETITIVE_GAP_ANALYSIS.md)
- [GITHUB_ECOSYSTEM_MAP.md](GITHUB_ECOSYSTEM_MAP.md)
- [COMPETITOR_MATRIX.md](COMPETITOR_MATRIX.md) with `data/competitor_matrix.csv`
- [ALGORITHM_COMPARISON.md](ALGORITHM_COMPARISON.md)
- [DNA_STORAGE_DATASET_REGISTRY.md](DNA_STORAGE_DATASET_REGISTRY.md)
- [VNX_PRODUCT_STRATEGY.md](VNX_PRODUCT_STRATEGY.md)
- [VNX_BENCHMARK_PLAN.md](VNX_BENCHMARK_PLAN.md)
- [RESEARCH_AUTOMATION_PLAN.md](RESEARCH_AUTOMATION_PLAN.md)
- [VNX_GLOBAL_ROADMAP.md](VNX_GLOBAL_ROADMAP.md)

Ground rules for this report:
- Every VNX result is **SIMULATED**. VNX has not synthesised, stored or sequenced DNA.
- External numbers are the authors' own unless marked "measured by this audit".
- **No same-protocol benchmark between VNX and any other codec exists yet.** Every comparative statement below is therefore about *capabilities present or absent*, not about performance ranking.
- Threat ratings and customer hypotheses are analyst judgement.

## Research completion status

| Item | Count / state | Source |
|---|---|---|
| GitHub repositories surfaced by search | ≈ 1,000 raw hits (≈ 240 in cluster 1, ≈ 790 in cluster 2, overlapping, mostly name collisions) | 10/20-repos |
| Repositories catalogued with metadata | **179 unique** (76 in cluster 1 + 157 in cluster 2, 54 in both) | 10/20-repos JSON (deduplicated by GitHub URL) |
| Repositories audited in depth (code or README + paper read) | **39 unique** in depth, plus 11 at medium depth. Executed by this audit: 3 (DNABoundedHomopolymerEncoding, whpress/hedges, Chamaeleo) | 10/20-repos |
| Organisations mapped | 38 (22 companies, 10 academic/EU, 3 standards, 1 archive buyer, 2 other) | 30-companies |
| Algorithms compared | 21 codec/ECC schemes in the algorithm table, plus reconstruction and clustering methods | 20-repos §6, ALGORITHM_COMPARISON |
| Datasets identified | 38 profiled, plus 28 candidate accessions | 40-datasets |
| Patents surfaced (freedom-to-operate flags, not legal analysis) | 53 verified records; 9 flagged for IP review | 30-companies |

Limitations:
- The GitHub search is relevance-based and not exhaustive.
- Several paper numbers came through summaries and must be checked against the PDFs before external use.
- snia.org blocks automated fetches.
- Only 3 repositories were executed.

## The 25 questions

**1. What does VNX-DNA already do better than the public ecosystem?**

Nothing can be called *better* without a same-protocol benchmark, and none exists. What VNX has that **none of the 39 repositories audited in depth was found to combine in one maintained, tested codebase** (feature presence, not performance):
- an authenticated archive container: AES-256-GCM, a Merkle tree, content-addressed dedup, and partial verified-file extraction;
- a product outer code with column parity, iterative stripe decoding and interleaving. SIMULATED: 16 % i.i.d. dropout threshold versus 7 % for V5, survives 4,096-strand bursts, decodes at mean coverage 2.0;
- marker-based indel handling with a native C aligner and native SIMD RS;
- fail-closed decoding with 0 false SUCCESS across all committed sweeps;
- a security and test culture: sanitizers, differential fuzzing, golden compatibility archives, provenance on every result.

The closest public analogues are dna-storage/reframed (BSD-style, active 2026) for architecture and SSL-ACTX/helix (AGPL, simulation only) for a systems stack.

**2. Where are we behind?**

- **Physical evidence:** level 1 against level 3–4 for HEDGES, DNA Fountain, YYC, DNA-Aeon, Storage-D, Organick.
- **Unfitted channel models.**
- **No primers or molecular random access;** strands are too long for vendor pools (353 nt with primers).
- **No inner indel code** with published wet-lab depth/rate data. HEDGES and DNA-Aeon have one.
- **No constrained coding:** VNX screens scrambler variants. Measured by this audit: Microsoft's enumerative code reaches 1.918 / 1.982 / 1.991 bits/base for k = 2 / 3 / 4 at N = 110.
- **No clustering without addresses,** no trace-reconstruction baseline comparison, no ML decoder evaluation.
- **No standards conformance** (Sector Zero/One).
- **No benchmark on dt4dds,** the field's de facto simulation standard.

**3. What should we NOT build?**

The full list with reasons is in COMPETITIVE_GAP_ANALYSIS, appendix "Features we should NOT build". Headlines:
- our own wet lab;
- our own copy of the GPL benchmark harnesses (use them as external tools);
- another plain mapper chasing bits/nt records;
- an S3 DELETE that pretends molecular media is mutable;
- GPU kernels before profiling demands them;
- an ML decoder before fitted channels exist;
- a proprietary channel simulator competing with dt4dds (fit ours to public data and cross-check against dt4dds instead);
- a hosted service before the SDK and format are stable;
- any embedding of GPL/AGPL/PolyForm-NC/BGI-licensed code.

**4. What should we build immediately?**

In V6, in this order:
1. fix the P1 correctness bugs #56 (`--select` on stripe archives) and #13;
2. land the performance work on a clean commit and run CI on V6;
3. write the missing V6 Phase 1 report;
4. **benchmark lab B0/B1**: VNX as an external codec inside dt4dds-benchmark, against DNA-Aeon, HEDGES, DNA-RS, YYC and DNA Fountain;
5. measure the constrained-mapping rate gap against DNABoundedHomopolymerEncoding;
6. add RS and aligner fuzz harnesses.

**5. What should wait?**

- the object/S3 API (V12);
- the enterprise server and hosted service (V12);
- LDPC and the inner-code switch (V10, after fitted channels);
- ML decoders (V10 evaluation);
- composite alphabets and enzymatic-native codes (V41+);
- the "AI-data archival layer" positioning (until GB-scale physical evidence).

**6. Which competitor or repository is the most technically threatening?**

- **Company: AtlasBase** (ex-Atlas Data Storage). It has:
  - a $155M seed;
  - its own synthesis chip;
  - a commercial service (Atlas Eon 100, Dec 2025);
  - a codec patent application (CA3249936A1);
  - a stated plan to let customers decode with an "open-source script".

  A free Atlas decoder would make basic decoding of Atlas capsules free.
- **Close second: Biomemory**, with an S3 interface, the Scality partnership, the Catalog patents and a DDSA co-chair.
- **Repositories:**
  - **MW55/DNA-Aeon** (MIT; constrained arithmetic-coded inner code with CRC markers, a stack decoder for substitutions and indels, Raptor outer code; Nat Commun 2023 with in-vitro data). It is the strongest public indel-capable codec with constraints and a working repository.
  - **dna-storage/reframed** (BSD-style, active 2026). It is the closest architectural analogue.

**7. Which open-source project should we benchmark most heavily?**

**fml-ethz/dt4dds-benchmark** (GPL-3.0, run as an external harness). It is the only public harness with experimentally calibrated workflows and published real reads (ENA PRJEB90546), and it already wraps DNA-Aeon, DNA Fountain, DNA-RS, Goldman, HEDGES and YYC. Within it, benchmark **DNA-Aeon** and **HEDGES** most heavily: both are indel-capable and both have wet-lab evidence.

**8. Which algorithms should we implement?**

These are recommendations. Each is THEORETICAL until benchmarked.
- **Channel-model fitting** to public data (V7).
- **Quality-weighted multi-read consensus / trace reconstruction**, compared with BMA, Trellis BMA and bbs (V6 design, V7 implementation).
- **Primer design and screening** for a short-strand profile (V7).
- **Hierarchical molecular addressing** for random access at scale (V8).
- **Soft-input decoding** end to end (V10).
- **Enumerative or arithmetic constrained coding,** only if the V6 rate-gap measurement shows a material loss.

**9. Which algorithms should we merely support or compare?**

- HEDGES, DNA-Aeon, DNA Fountain/LT/Raptor, YYC, Goldman/Church/Grass, DBGPS and StairLoop: compare via the harness.
- Trellis BMA / BMA: reconstruction baselines.
- LDPC: research comparison (R-02) before any adoption.
- ML decoders (DNAformer, TReconLM): evaluate where the licence allows.
- JPEG DNA: support as a payload format (V9).

**10. What should V6 contain?**

The founder's V6 Master Directive (2026-10-05) defines V6. It runs in 11 phases: forensic audit → specification and versioning → explicit pipeline and API → channel-model framework → measured indel and soft-decision work → native/performance → security and fuzzing → provider abstraction and lab exchange format → conformance vectors and reproducibility → benchmarking → release audit.

This research adds four things to it:
- benchmark lab B0/B1 on dt4dds-benchmark;
- the fixes for #56 and #13;
- a channel schema ready for public-data fitting;
- spec fields aligned with DDSA Sector Zero/One.

See VNX_GLOBAL_ROADMAP §V6.

**11. V7?**

Channel intelligence:
- channel schema v2 and the fitter;
- PUBLIC-DATA-DERIVED fits on CNR, the nanopore Zenodo set, DT4DDS and the ETH benchmark;
- quality-weighted consensus;
- a short-strand vendor-compatible profile, a primer module, order export and read ingestion;
- **milestone: the first oligo-pool order** (founder budget decision).

**12. V8?**

Extreme-scale storage and recovery:
- bounded-memory streaming encode and decode;
- a parallel pipeline;
- a random-access architecture for 100 TB–1 EB logical archives (hierarchical partitions, a molecular catalogue);
- a selection pass that skips non-selected reads;
- scale benchmarks.

**13. V9?**

Storage engine / archive infrastructure:
- a Sector Zero/One reference reader/writer plus a conformance suite;
- a registry / self-discovery service;
- versioning (append-only, tombstones);
- a catalogue;
- JPEG DNA wrapping;
- a stable SDK.

**14. V10?**

Adaptive coding:
- soft-input decoding;
- the LDPC vs. RS decision;
- the inner-code decision (markers vs. HEDGES/Aeon-style);
- adaptive redundancy from an estimated channel;
- an ML-decoder evaluation.

**15. V11?**

Physical-provider integration:
- a provider adapter interface;
- repeatable wet-lab rounds bought as a customer;
- a Swordfish-aligned management API;
- biosecurity screening plus attestation;
- a data-retention calculator.

**16. V12?**

Unified commercial platform: SDK, CLI, Storage API (S3-compatible data path with write-once semantics), Enterprise Server, Provider Adapters, Benchmark Suite and a verification service. The licensing split follows VNX_PRODUCT_STRATEGY.

**17. V13–V25?**

- **V13–V20, advanced storage infrastructure:** multi-pool and multi-vendor archives, erasure across physical pools, billion-object catalogue, key management (absent today), cryptographic proof of decode, format migration, OAIS packaging.
- **V21–V25, start of provider interoperability:** certified adapters per chemistry and platform, cross-vendor reading, a public fitted-channel library, DDSA conformance programme.

**18. V26–V50?**

- **V26–V30:** finish ecosystem interoperability.
- **V31–V40:** large-scale archival and AI-data applications, *only after GB-scale physical round trips*.
- **V41–V50:** research — composite alphabets, enzymatic-native codes, in-storage search, ML decoders if they beat classical ones on fitted channels, formal verification of fail-closed decoding, external validation.

The numbering stops early if the saturation criteria (roadmap §5) are met.

**19. What would make VNX-DNA commercially defensible?**

- Being the **vendor-neutral, standards-conformant reference implementation** (Sector Zero/One, codec metrics, Swordfish) at a time when every vendor's codec is proprietary and no conformance suite exists.
- An **independent verification layer** (cryptographic proof that a decode is correct) that vendors cannot credibly provide for themselves.
- Accumulated **fitted-channel and benchmark data** that partners rely on.

All three are positions, not patents. They depend on joining and contributing to the DDSA.

**20. What would make VNX-DNA technically defensible?**

Measured recovery quality on fitted and then physical channels, at a published overhead, reproduced by others. The fail-closed property (0 false SUCCESS) needs to be shown at scale and ideally formally verified. Scale engineering is a further defence: bounded memory, selective retrieval cost proportional to the data selected, native kernels. A feature list alone is not defensible; free academic code covers most individual algorithms.

**21. What is the most realistic first paying customer?**

Hypothesis; no contact or customer exists. The most realistic first payer is **a synthesis or sequencing provider paying for an integration pilot** (examples: Twist after the Atlas spin-out, GenScript, Ansa, BGI/MGI). The pilot would cover:
- a VNX provider adapter;
- a benchmark on that provider's error profile;
- a licence and support.

EU consortia (DNAMIC/Genomika-type) are a parallel route. National archives following the Library of Congress template are the slower reference market, because they will ask for physical evidence first. See VNX_PRODUCT_STRATEGY.

**22. What is the most realistic product?**

**"VNX Verify"**: an open reader, verifier and conformance kit for the VNX archive format and the DNA Data Storage Alliance Sector Zero/One specs. No public reference implementation or conformance suite for those specs exists. The licensing principle is that **no VNX archive may need a paid component to be read back**:
- **open:** format spec, codec, reference decoder, CLI, core SDK, benchmark harness;
- **commercial:** Enterprise Server, certified provider adapters, verification and conformance services, and support.

Every release up to v5.0.0 is already MIT, so the only open licensing decision concerns V6 onwards. The repository LICENSE file (11 lines) lacks the standard MIT warranty disclaimer and needs legal review. The Storage API order is native verbs first, Swordfish-style management second, and an S3 gateway only on customer demand. Details are in VNX_PRODUCT_STRATEGY.

**23. What is the biggest technical risk?**

That VNX's simulated recovery thresholds, so far shown only on unfitted i.i.d.-style simulated channels, **does not survive fitted or physical channels**:
- position-dependent deletions near the 5' end on electrochemical synthesis;
- clustered deletions (mean run 2.6 nt);
- lognormal coverage with σ up to 1.3;
- all measured by Gimpel et al. 2023.

A related risk is that marker-based indel handling at 313 nt underperforms a HEDGES/Aeon-style inner code at equal overhead. The mitigation is V6 benchmark plus V7 fitting before any further feature work.

**24. What is the biggest commercial risk?**

Vertical integration by well-funded players: AtlasBase (own chip, service, codec patent application, possible open-source decoder) and Biomemory (S3, Scality, Catalog IP). Either could make a neutral software layer unnecessary for its own customers. Secondary risks:
- the economics: public cost estimates of about $100/MB written, against a < $1/MB target, may keep the whole market small for years;
- freedom to operate on codec patents (9 flagged for legal review).

**25. What evidence would justify seeking investment?**

Concrete and checkable, in order:
1. same-protocol benchmark results on dt4dds-benchmark against DNA-Aeon / HEDGES / DNA Fountain, published with provenance;
2. channel models fitted to ≥ 2 public datasets (Illumina and nanopore), with VNX recovery under them;
3. one physical round trip: a small oligo pool bought from a vendor, sequenced by a service and decoded by VNX, recorded as a REAL PHYSICAL RESULT;
4. a Sector Zero/One conformance implementation, and DDSA membership or contribution;
5. one design partner's written requirements or LOI;
6. a freedom-to-operate opinion on the flagged patents.

Items 1–2 can be done in software within V6–V7. Item 3 needs a modest purchase; quotes have not yet been obtained.

## Answers to the founder's report checklist (§25 of the brief)

| # | Item | Answer |
|---|---|---|
| 1 | Research completion status | **Complete for this gate**, with the stated limitations: search not exhaustive, 3 repositories executed, some paper figures via summaries |
| 2 | GitHub repositories discovered | ≈ 1,000 raw search hits; **179 unique relevant repositories catalogued** |
| 3 | Actually audited | **39 in depth** (3 executed), 11 medium, the rest metadata-level |
| 4 | Competitors mapped | **38 organisations** |
| 5 | Algorithms compared | **21 codec/ECC schemes**, plus reconstruction/clustering methods |
| 6 | Datasets identified | **38 profiled + 28 candidates** |
| 7 | VNX strengths | Q1 |
| 8 | VNX weaknesses | Q2 |
| 9 | Top 10 missing capabilities | COMPETITIVE_GAP_ANALYSIS §22 |
| 10 | Top 10 unnecessary features | COMPETITIVE_GAP_ANALYSIS appendix and Q3 |
| 11 | Most important V6 changes | Q4 / Q10 |
| 12 | Revised V6–V12 roadmap | VNX_GLOBAL_ROADMAP §2 |
| 13 | V13–V50 direction | Q17–18, roadmap §4 |
| 14 | Saturation criteria | Roadmap §5 (11 measurable criteria) |
| 15 | Commercial product recommendation | Q22, VNX_PRODUCT_STRATEGY |
| 16 | Remain software-first? | **Yes.** Buy synthesis and sequencing as a customer for evidence; do not build a lab. The open position is neutral software and standards, and every hardware player is integrating vertically |
| 17 | Readiness for V6 implementation | **Not yet.** Workforce criteria G2–G8 PASS. G1 (founder inbound Telegram) and G11 (approval of this report) are open. Bug fixes may proceed |
| 18 | Exact next action | Founder: (a) approve or amend this report and the roadmap order; (b) send "ping" to @Vnxdna_bot. Then: open V6 jobs for #56, #13 and benchmark lab B0 via `vnx-task` |
