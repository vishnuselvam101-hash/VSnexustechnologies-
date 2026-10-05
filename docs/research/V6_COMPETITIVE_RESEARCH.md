# V6 competitive research: synthesis

Status: V6 directive Phase 10 (documentation), `build/v6-sprint` @ b693254. This page summarises the committed
research-gate documents and adds what V6 measured afterwards. It does not repeat their tables; follow the links for
detail. Dates of the underlying audit: 2026-10-05.

Labels: **SIMULATED** (software strands through a software channel), **PUBLIC-DATA-DERIVED** (statistics from another
group's public reads), **MEASURED** (timing or memory on this host), **THEORETICAL** (arithmetic or specification),
**PHYSICAL** (real DNA; VNX-DNA has none). Competitor figures keep the evidence labels of the source documents
(author-reported in silico or wet-lab, not reproduced here). No DNA has been synthesised, stored or sequenced by
VNX-DNA.

## 1. Sources

| Document | What it holds | Size of the record |
|---|---|---|
| [COMPETITIVE_GAP_ANALYSIS.md](../COMPETITIVE_GAP_ANALYSIS.md) | market, strengths, weaknesses, missing capabilities, V6-V50 priorities | 26 sections |
| [COMPETITOR_MATRIX.md](../COMPETITOR_MATRIX.md) (+ `docs/data/competitor_matrix.{csv,json}`) | capability matrix, VNX-DNA against repositories, companies and standards | 87 rows, 15 columns |
| [GITHUB_ECOSYSTEM_MAP.md](../GITHUB_ECOSYSTEM_MAP.md) | public DNA-storage software and licences | 179 distinct repositories (`research/competitive-2026-10-05/README.md`) |
| [ALGORITHM_COMPARISON.md](../ALGORITHM_COMPARISON.md) | mapping, inner/outer codes, trace reconstruction, clustering, simulators | see the technical synthesis |
| [DNA_STORAGE_DATASET_REGISTRY.md](../DNA_STORAGE_DATASET_REGISTRY.md) (+ `docs/data/dataset_registry.json`) | public sequencing datasets | 38 profiled, 28 further candidates |
| `research/competitive-2026-10-05/` | raw audit records (`10-repos-cluster1`, `20-repos-cluster2`, `30-companies`, `40-datasets`, `.md` and `.json`) | 38 organisations |

Subject of every VNX-DNA statement in those documents: commit 081697b (package version 5.0.0). The statements below
about what changed in V6 cite the V6 files named in each row.

## 2. What the research gate found

Evidence labels are those of the source documents (the gate's labels are [AR-SIM], [AR-WET], [AUDIT], [README],
[PRESS], [SPEC]).

1. **Market.** Vendors pair synthesis, storage and an in-house codec: AtlasBase, Biomemory, Iridia, Mimulus and a
   university pilot with the US Library of Congress ([COMPETITIVE_GAP_ANALYSIS.md](../COMPETITIVE_GAP_ANALYSIS.md) §2;
   [PRESS] unless marked). No independent commercial codec, format or verification vendor was found
   (`research/competitive-2026-10-05/30-companies.md` §1). Public cost figures range from about $100 to about $1M per
   MB depending on source and product [PRESS]; none is a published price for a service at scale.
2. **Open codecs.** The public repositories include HEDGES, DNA-Aeon, DNA Fountain, DNA-RS, YYC, StairLoop,
   TrellisBMA and others, many with wet-lab papers [AR-WET]. Several are copyleft, non-commercial or unlicensed, which
   decides how they may be used ([GITHUB_ECOSYSTEM_MAP.md](../GITHUB_ECOSYSTEM_MAP.md)).
3. **Standards.** The DNA Data Storage Alliance Sector Zero/One work, SNIA Swordfish DNA resources and JPEG DNA are
   early or draft ([COMPETITIVE_GAP_ANALYSIS.md](../COMPETITIVE_GAP_ANALYSIS.md) §19).
4. **VNX-DNA at 081697b.** Strengths the gate could evidence were engineering ones: a verify-before-publish chain,
   authenticated encryption, a versioned format with golden archives, native kernels with differential tests, and
   evidence labelling (gap analysis §20). GREEN in the gate's scheme means "present where the audited competitor
   lacks it", not a performance ranking.
5. **VNX-DNA weaknesses recorded by the gate** (§21): no physical evidence; no same-protocol benchmark; unfitted
   channel models; no indel-correcting inner code or joint multi-read reconstruction; orphan reads when a header is
   damaged; a strand too long for primers inside a 350-nt limit; digital-only random access; an address space of about
   11 TB per archive (THEORETICAL); no API beyond the CLI; no standards conformance.
6. **Unbenchmarked.** At 081697b no VNX-DNA result had been placed next to an external codec under a shared protocol.

## 3. What V6 measured since

### 3.1 Benchmark lab B0 (first same-harness comparison; SIMULATED, timing MEASURED)

VNX-DNA was run as an external codec in the ETH `dt4dds-benchmark` harness next to DNA-RS, DNA Fountain and DNA-Aeon:
same input files, same error generator, same seeds, same success check. 280 trials, every one stored
(`benchmarks/competitors/lab/results/b0/trials_*.jsonl`; method, caveats and tables in
[benchmarks/competitors/lab/README.md](../../benchmarks/competitors/lab/README.md)). Inputs were 19 kB and 5 kB random
files; the channel is the harness's i.i.d. error generator (53/45/2 % substitution/deletion/insertion). It is not the
published ETH protocol (30 trials, logistic fit, 1 h limit), with 3 seeds per point, and timings were taken under
host load.

| Observation | Value (source: lab README, sweeps A-C) |
|---|---|
| False SUCCESS (exit 0 with wrong content) | VNX-DNA 0 of 157 trials; DNA-RS 2; DNA Fountain 16; DNA-Aeon 0 |
| Decode time at 19 kB (SIMULATED channel, MEASURED time) | VNX-DNA about 0.8 s per step, mostly process start-up; DNA-RS-medium median 1.4 s at 0.2 % errors and 123.8 s at 1 % |
| Decode peak memory | VNX-DNA 56-76 MiB per step; DNA-RS 14-16 MiB (776 MiB in one case); DNA-Aeon up to 1.9 GiB |
| Error tolerance (SIMULATED) at about 1.0 bit/nt, 1 % errors (coverage 5 and 10 pooled) | DNA-RS-medium 6/6; VNX-DNA `s184` 5/6, `s280` 3/6, `s148` 4/6 |
| Dropout 10 % (SIMULATED; 19 kB, 0.5 % errors, coverage 10) | DNA-RS-medium 3/3 and DNA Fountain-medium 3/3; VNX-DNA `s184` 0/3, `s280` 0/3 |
| About 1.5 bit/nt (SIMULATED) | VNX-DNA `s456` 0/6 at 0.5 % and 1 % errors; DNA-RS-high 6/6 up to 0.5 % |
| Strand length at about 1.0 bit/nt (computed from the layout) | VNX-DNA 184 nt (`s184`) against 144-152 nt: a 14-byte header and CRC in every strand |
| Constraints (VERIFIED on the B0 strands) | VNX-DNA GC within 40-60 % for every strand, homopolymer at most 4 nt; DNA-RS up to 7-9 nt |

Reading (SIMULATED results): in this grid VNX-DNA does not fail silently and does not slow down with the error rate, and it is weaker than
DNA-RS and DNA Fountain at 10 % dropout, at 1 % errors near 1 bit/nt, and near 1.5 bit/nt, and it writes longer
strands. The numbers do not support a ranking: 3 seeds per point give wide intervals (a pooled 6/6 has a Wilson 95 %
interval of 0.61-1.00), and the harness's channel is not a platform model. Excluded from the lab for licence reasons:
proprietary systems, mahoraga (PolyForm Noncommercial), DNASpiderWeb (custom licence), hedges-soft-decoder
(research-only), unlicensed repositories (lab README, "Exclusions"). HEDGES was built but not run; B1 (job #78) is
open.

### 3.2 Public-data statistics (PUBLIC-DATA-DERIVED)

`experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md` computes per-read substitution/insertion/deletion statistics from
the public Microsoft clustered nanopore reads (269,709 reads, 10,000 clusters of 110 nt). Result (PUBLIC-DATA-DERIVED): 2.16 / 1.66 / 1.95 %
per base, 4.34 % of reads with no indel, insertions about 1.8-2 times more frequent in the first and last bins than in
the middle. VNX-DNA cannot decode these reads (they are not VNX frames), so this is a channel statistic, not a decoder
benchmark. No comparison with BMA or Trellis BMA was made. The dataset has no quality scores.

### 3.3 Gap table: state after V6

"Status" refers to the committed evidence named in the last column; nothing is PHYSICAL.

| Gate finding (gap analysis §21) | Status after V6 | Evidence |
|---|---|---|
| No same-protocol benchmark | partly addressed: B0 against three public codecs, not the published protocol | lab README |
| Channel models unfitted, i.i.d. | framework added (`vnx.channel-model/1`: staged, per-position profiles, 4x4 substitution matrix, lognormal coverage, provenance fields). The 14 shipped models are still unfitted; fitting is V7 | `docs/CHANNEL_MODEL.md`; [V6_DEFERRED.md](../V6_DEFERRED.md) |
| `--select` broken on stripe archives (job #56) | fixed (commit a76d5e7, per the sprint log); two related defects remain open (#62, #66) | [V6_DEFERRED.md](../V6_DEFERRED.md) section 8 |
| No indel-correcting inner code / joint reconstruction | not addressed. Quality-weighted consensus and a retry band were built and measured; neither is a default | [V6_TECHNICAL_RESEARCH.md](V6_TECHNICAL_RESEARCH.md) |
| Orphan reads (damaged header) | measured as the nanopore-like bottleneck; header-independent clustering is V7 | `experiments/v6/align-band/README.md` |
| Strand too long for primers at 350 nt | not addressed; frame 6 and short profiles are specified, V7 | spec §3.5-§3.7 |
| Address space about 11 TB per archive | not addressed; wide class is V8 | spec §3.5 |
| No API beyond the CLI | `vnxdna.sdk` stable API added, with `vnx.result/1` envelopes and stable error codes | `docs/V6_ARCHITECTURE.md` §4; CHANGELOG |
| No standards conformance | not addressed; Sector Zero/One mapping table only (V9). Conformance vectors for VNX-DNA's own formats exist (222 vectors) | `docs/DDSA_MAPPING.md`; `tests/conformance/index.json` |
| Release hygiene (no CI on V6, native benchmarks on a dirty tree, missing report) | native benchmarks re-run on clean builds (installed-path decode 10.8 s to 5.3 s, 2.04x, one host, one workload, MEASURED); CI on V6 is outside this page | `benchmarks/v6/native_packaging/README.md` |
| No physical evidence | unchanged | none |

## 4. What V6 does not claim

- No claim that VNX-DNA is better or worse than any external system beyond the B0 table, and B0 is SIMULATED, small and
  not the published protocol.
- No claim about wet-lab performance, storage lifetime, cost per megabyte or commercial readiness.
- Competitor numbers in the source documents are author-reported unless marked as measured by the audit; several were
  relayed through summaries and flagged "not verified" there. Check them before external quotation.
- Licence readings are the gate's, not legal advice.

## 5. Next steps

Plan of record: [VNX_GLOBAL_ROADMAP.md](../VNX_GLOBAL_ROADMAP.md) and [V6_DEFERRED.md](../V6_DEFERRED.md): B1 benchmark
with the published protocol, fitting channel models to public data (V7), a short-strand profile with primers and order
export (V7), then the first physical order as a separate milestone.
