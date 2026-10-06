# VNX-DNA V7 architecture (design)

- Status: **DESIGN, V7 Phase 0, 2026-10-05.** Nothing here is implemented. Code citations are `file:line` in
  `build/v7-sprint` at v6.0.0 (16b5811). The normative V6 formats are in [`docs/spec/VNX-DNA-SPEC-V6.md`](spec/VNX-DNA-SPEC-V6.md)
  (cited as "spec §n").
- Inputs: [V6_DEFERRED.md](V6_DEFERRED.md), [V6_COMPLETION_REPORT.md](V6_COMPLETION_REPORT.md),
  [V6_ARCHITECTURE.md](V6_ARCHITECTURE.md), spec §3.5, §3.6, §3.8, [experiments/v6/align-band/README.md](../experiments/v6/align-band/README.md)
  (cited as "AB"), the decided V7 order: **D** fitted channel models, then **A** nanopore-like decoding (job #82), then
  **C** soft decisions (job #81), then **B** frame 6 and superblock 3; **G** defects in parallel; **E/F** last.
- Labels as in V6: **VERIFIED**, **SIMULATED**, **PUBLIC-DATA-DERIVED**, **THEORETICAL** (arithmetic or specification,
  not run). No DNA has been synthesised, stored or sequenced by VNX-DNA. Every number in this document that is not
  cited from a committed results file is THEORETICAL arithmetic and is marked so.

## 0. Decisions in one table

| # | Question | Decision |
|---|---|---|
| 1 | Does header-independent clustering need a format change? | **No.** It is a decoder-only stage on the existing frame-4 layout. Every V4/V5/V6 archive is a valid input; nothing new is written. |
| 2 | Does indel placement below segment level need a format change? | **No, when a strand has at least 2 reads**: aligning each read to a per-cluster consensus template places indels at base resolution. **For a single read**, a wildcard template cannot place an indel inside a segment (`sync/template.py:12-19`); only code arbitration (V5 smart recovery, already shipped) or denser markers can. Denser markers are an existing `Layout` parameter, not a new feature. |
| 3 | Where do cluster frames enter the decoder? | A new stage **D7c `cluster`** after pass 1 and the deferred rounds. Its frames are **fill-only**: they are used only for superblock symbols when the V6 superblock decode fails, and only for addresses of rows that the V6 symbols cannot decode. Every frame still passes inner RS + CRC-32 (I-5); the container SHA-256 still decides SUCCESS (FC-1). |
| 4 | Default? | **Opt-in** (`--read-clustering fallback`) in 7.0. It may become the default only if the pre-registered criteria in §9 all ACCEPT and the founder approves. |
| 5 | Should frame 6 include an indel-resilience feature? | **No new sync mechanism.** Markers stay a profile parameter (period and length) and EXP-F6-1 freezes them with the V7 decoder as an arm. One conditional, non-indel feature is held open: address-keyed payload whitening (§6.3), adopted only if the pre-registered low-entropy cell (A-EXP-01, cell L) shows that near-duplicate strands cost availability. |
| 6 | CRC-16 for short frame-6 profiles (spec §12 question 5) | **Keep CRC-32.** Cluster decoding multiplies verification trials; at 8 × 10⁶ trials per archive the expected false frames are 0.0019 with CRC-32 and 122 with CRC-16 (THEORETICAL, §5.8). |
| 7 | What A needs from D | Fitted IDS rates with homopolymer context, position profile, read-length distribution, and above all the **coverage distribution** (negative-binomial mean and dispersion), frozen by SHA-256 before A's pre-registration (§4). |

## 1. Problem statement (V6 evidence)

The nanopore-like model decodes 0/20 at coverage 3, 5 and 10 with every V6 arm (AB, AB-EXP-01 table; V6_DEFERRED §2).
AB-DIAG (ground truth, descriptive) separates four causes:

1. **Address step.** With band 16, 98.1 % of reads align but only 3.8 % carry their exact address in the header (9.4 %
   within one byte) at coverage 10 (AB, AB-DIAG). Pending reads are grouped by the tentative header address
   (`recovery/pass1.py:237-262`, `recovery/consensus.py:163-178`), so the reads of one strand are scattered over wrong
   addresses. A wrong byte 0 selects the wrong keystream (`dnaenc/scrambler.py:15`, `dnaenc/frame4.py:148`) and garbles the
   whole header.
2. **Segment erasure.** The template has bases only at marker positions; inside a segment every placement of an indel
   costs the same, so the whole segment (6 B at v4-balanced, `dnaenc/layout.py:217-219, 252`) is erased
   (`sync/template.py:12-19, 222-250`). Result: 41.3 of 70 frame bytes erased per aligned read, and an oracle count vote
   over reads grouped by the *true* strand decodes only 28.9 % (cov 10) to 50.9 % (cov 15) of strands (AB-DIAG).
3. **Reads beyond the band are discarded.** A read with |len − T| > band is never stored: `pend` keeps only aligned reads
   (`recovery/pass1.py:239`), and raw reads are kept only when smart or soft recovery is on (`pipeline/decode.py:98`,
   `recovery/spill.py:14-26`). At band 6 that is 64 % of nanopore-like reads (AB, "reads within band 6" 0.357-0.361).
4. **Orientation** is decided from marker positions counted from the read start (`recovery/pass1.py:176-195`). Net drift
   of about −8 nt (`experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md`, interpretation) moves later markers off their
   expected positions.

The V5 tools address (2) only per read and only for small shifts: smart recovery handles windows with net shift ±1
spanning at most 2 segments (`sync/smart/recovery.py:58-71`), and its consensus mode still needs reads grouped by a
(snapped) address (`sync/smart/consensus.py:1-19`). Neither touches (1) or (3).

**Correction to V6_DEFERRED §2.** That page says header-free clustering and sub-segment indel placement do not fit "in
V6 without a format change". The analysis in §5 shows both fit **without** a format change; they did not fit V6's
*scope*. V6_DEFERRED is a V6 record and is not edited; this document carries the correction.

## 2. Goals and non-goals

**Goals (7.0)**

- G-A1: decode archives on the nanopore-like model and on fitted nanopore models (D) at the coverages pre-registered in
  §9, on the **unchanged** frame-4 layout.
- G-A2: never return wrong data (FC-1 … FC-7 unchanged, plus FC-9 in §7), and never decode an archive worse than 6.0
  does (fill-only precedence, §5.4).
- G-A3: bounded memory and time, worker-count independent output, a native kernel with a NumPy reference and a
  native = reference test.
- G-B: frame 6 compact class and superblock 3 per spec §3.5/§3.8, profiles frozen by EXP-F6-1 run on fitted models
  with the V7 decoder.
- G-D: at least two public datasets fitted into `vnx.channel-model/1`, licences and fit metrics recorded.

**Non-goals (7.0)**

- No learned decoder (DNAformer class), no BCJR/Trellis-BMA soft trace reconstruction, no HEDGES/VT inner code (V10,
  V6_DEFERRED §6).
- No clustering of pools beyond the memory budget (§5.6); bounded-memory streaming clustering is V8 (V6_DEFERRED §4,
  streaming row).
- No decoding of non-VNX public reads. Public data is used to fit channels and to benchmark the consensus engine in
  isolation (PUBLIC-DATA-DERIVED), never as a VNX decode.
- No change of the V4/V5/V6 strand output, the default decoder path, or any existing format value (spec §4.6).
- No reuse of GPL-licensed clustering or consensus code (Clover, dt4dds-benchmark, reframed are GPL or custom,
  `docs/ALGORITHM_COMPARISON.md:70, 108-109`); every algorithm here is implemented from the cited papers.

## 3. Pipeline placement

```
D0 ingest → D1 probe+layout → D3 orient → D4 sync → D5 inner → D6 spill ─┬─ pending (by tentative address, V6)
                                                                         └─ UNPLACED store (new, opt-in): every read
                                                                            with no verified frame, raw, both strands
D7 recover (V5/V6 rounds S/A/B, unchanged)
D7c cluster (new, opt-in):  sketch → candidate pairs → verify → components → refine
                            → consensus per cluster → inner RS + CRC → decode-and-peel
                            → CLUSTER store (verified frames, by verified group, separate files)
D8 superblock:  V6 symbols first; CLUSTER superblock symbols only if V6 raises       (fill-only)
D9 consensus:   V6 symbols and V6 pending consensus first; then CLUSTER symbols for addresses still missing,
                only in rows the V6 symbols cannot decode                               (fill-only)
D10-D14 unchanged
```

The stage runs lazily: D7c is executed only if, after D7, the superblock cannot be decoded from V6 symbols or at least one
row has fewer than k_g V6 symbols (computed as `recovery/schedule.py:51` `_group_decodable` already does). A pool that 6.0
decodes therefore never pays for clustering beyond the unplaced store written in pass 1.

## 4. Item D interplay: which fitted models A needs

A's targets are only meaningful on channels whose parameters come from data. The V6 models are unfitted stress profiles
(`src/vnxdna/simulation/models/nanopore-like.json`: "parameters are not fitted to any measured platform"). A needs:

| Parameter (schema field in `vnx.channel-model/1`) | Why A needs it | Source datasets (`docs/DNA_STORAGE_DATASET_REGISTRY.md`) |
|---|---|---|
| per-base substitution / insertion / deletion rates, deletion run length | k-mer length, distance threshold θ, consensus slack (§5.2-5.3) | D03 (nanopore, GenScript, guppy fast and HAC; CC BY 4.0), D04 CNR (MIT; 2.16/1.66/1.95 % already measured, P4-EXP-03) |
| homopolymer indel and substitution multipliers | ambiguity of indel placement inside runs (§5.3) | D03, D04 |
| position profile | CNR shows 1.8-2× insertions at the ends (P4-EXP-03) | D03, D04 |
| coverage model: negative-binomial mean and dispersion, dropout | **sets the feasible coverage**: strands with ≤ 1 read cannot be helped by consensus (table below) | D04 cluster sizes (median 21, p10-p90 9-54), D02/D01 (Illumina), D14 (licence unclear: statistics only) |
| read length and truncation | `min_read_nt`, band cap | D03, D04 |
| reverse-complement share, contamination | cluster purity | D03 |
| quality calibration (Phred vs empirical) | item C; optional weighting in the consensus vote | D03 (FASTQ), D01/D02; D04 has no qualities |

Feasibility arithmetic (THEORETICAL; assumes the simulator draws per-strand read counts from NB(mean μ, dispersion 4) as
the derived models state, e.g. `experiments/v6/align-band/AB-EXP-01-retry-band/models/nanopore-like-cov3.json`; A0 checks
this by counting):

| μ | P(0 reads) | P(≤ 1 read) | P(≤ 2 reads) | v4-balanced row tolerance |
|---|---|---|---|---|
| 3 | 0.107 | 0.289 | 0.485 | 16 of 80 strands (20 %) |
| 5 | 0.039 | 0.126 | 0.246 | |
| 10 | 0.007 | 0.026 | 0.060 | |
| 15 | 0.002 | 0.008 | 0.020 | |

At μ = 3 more than 20 % of strands have at most one read, and a single nanopore-like read carries about 19 errors over
313 nt (rates 2 % + 1 % + 3 %), so coverage 3 is not a reachable target for any consensus method on this model; it stays
descriptive in §9. μ = 10 and 15 are the primary cells; μ = 5 is borderline.

**Rule.** D's fitted models are frozen (file SHA-256 recorded) before A's pre-registration is committed. A's thresholds
cite those hashes. A-EXP-01 runs on (i) the V6 unfitted models, for continuity with AB-EXP-01, and (ii) the fitted
models.

**Optional D deliverable (recommended for B).** A *transcript-replay* channel: align each public read to its reference,
keep the edit transcript, and replay transcripts on VNX strands of the same length. It carries measured error positions
and correlations onto VNX strands of 150-160 nt (the f6-s160 candidates, spec §3.7), but not sequence-context effects.
Label: SIMULATED (replay of PUBLIC-DATA-DERIVED transcripts).

## 5. Item A design

### 5.1 Unplaced-read store (pass 1, opt-in)

- With `read_clustering != "off"`, pass 1 writes one fixed-width record per read that yields no verified frame,
  including reads beyond the band: raw codes as read (not re-oriented), length, qualities if present.
  Width W = ⌈1.25 · strand_nt⌉; reads shorter than ⌊0.5 · strand_nt⌋ or longer than W are counted
  (`unplaced_too_short`, `unplaced_too_long`) and not stored.
- File `unpl.bin` in the spill directory, one file (not bucketed by tentative address, which is meaningless here),
  read back by memory map as `Spill.load_orphans` does (`recovery/spill.py:59`).
- With the option off nothing is written and pass 1 is byte-for-byte the V6 code path.

### 5.2 (a) Header-independent clustering

Input: the unplaced reads, n of them. Output: clusters (lists of read indices with a relative orientation bit).

1. **Sketch.** For each read compute canonical k-mers (the smaller of the k-mer and its reverse complement, 2 bits per
   base, k = 12 by default; reads with N are split at N). For each of s = 32 fixed hash functions
   h_i(x) = splitmix64(x ⊕ c_i) (constants fixed in the code and the spec), keep the minimum over the read's k-mers
   (MinHash, Broder 1997) and the orientation bit of the k-mer that attains it. Sketch: s × (u32 hash, 1 bit). No
   positional masking: markers, the shared header bytes and (with frame 6) primers are handled by the bucket cap below.
2. **Candidates.** Bucket reads by (i, h_i). Buckets with more than `bucket_cap` (default 256) reads are ignored: they come
   from k-mers shared by many strands (marker-adjacent k-mers, the header bytes common to an archive under variant 0,
   primers, low-entropy payloads). Candidate pair = two reads sharing at least one retained bucket. The relative
   orientation of the pair is the majority XOR of the orientation bits over the shared slots.
3. **Verify.** Unit-cost edit distance of the oriented pair by the bit-parallel algorithm of Myers (1999), banded to
   |ΔL| + 32. Accept the edge iff distance ≤ θ · max(La, Lb), θ = 0.30 by default. Pairs whose endpoints are already in one
   component are skipped (verification work is about n, not about the candidate count).
4. **Components.** Union-find over accepted edges, processed in ascending (min index, max index) order. Orientation is
   propagated along the spanning tree.
5. **Refine.** A component larger than `max_cluster_reads` (default 64, the V6 `max_pending_per_address`,
   `recovery/options.py`) is split by deterministic nearest-representative assignment: representatives are chosen
   greedily in read-index order among reads farther than θ from every existing representative; each read joins its
   nearest representative (ties: lower index); two passes.

THEORETICAL check of the defaults at the nanopore-like total error rate ε ≈ 0.06: a given 12-mer survives in a read with
probability 0.94¹² = 0.48 and in two reads with 0.23, so two reads of one strand share about 57 of about 250 k-mers
(Jaccard ≈ 0.13). With s = 32 a same-strand pair shares no slot with probability 0.87³² = 0.012; a read of a strand with
c ≥ 3 reads is isolated with probability ≤ 0.012^(c−1) (independence approximation). Two unrelated random 313-nt reads
share an expected 250²/4¹² ≈ 0.004 12-mers. Expected same-strand edit distance ≈ 2εL ≈ 38 nt; unrelated random strands
are about half their length apart, so θ = 0.30 (94 nt) separates them with wide margin. **Exception:** strands with an
identical payload (identical 40-byte symbols under the same variant keystream) differ only in header, CRC and inner
parity (about 116 of 313 nt) and are about 58 nt apart: they can merge. Decode-and-peel (§5.3, step 6) separates them;
cell L of §9 measures whether it is enough.

Rashtchian et al. (2017) cluster DNA-storage reads with q-gram signatures, hashing and edit-distance verification; the
design above is the same family, specialised to one pool and to a known strand length.

### 5.3 (b) Consensus and indel placement below segment level

Per cluster, independently (parallel over clusters; results merged in cluster-ID order, ID = smallest read index):

1. **Cluster orientation.** Orient all reads by the relative bits, then choose the cluster orientation by the summed
   marker-template alignment cost of both orientations (the existing DP, `sync/template.py:132-187`). Markerless layouts:
   both orientations go through steps 2-5 and the first one that yields a verified frame is used (bounded: 2×).
2. **Seed template (round 0).** Layout with markers: align every read to the marker template with a per-cluster band
   B = min(32, max |len − T| + 4) and take the V6 count vote (`recovery/consensus.py:30-38`, threshold 0.6). Markerless
   layouts (v4-dense, v6-high-dropout, CNR benchmark): the seed is the cluster medoid (minimum summed edit distance over
   at most 16 members), mapped onto the template coordinates when its length equals T, otherwise used as a free template
   and projected onto the frame by the final alignment.
3. **Consensus template.** A per-cluster template C_t holds the known marker bases plus, at frame positions, the consensus
   base where the round-(t−1) vote was decided, and a wildcard elsewhere. Costs: marker mismatch 4 (as `SyncCosts`,
   `sync/template.py:39-44`), known-frame mismatch `c_sub` (default 3), wildcard 0, insertion and deletion 6.
4. **Certain calls by forward-backward.** For each read, compute forward F and backward G of the banded DP against C_t.
   With opt = F[T][L], template position i is
   - *matched to read base j on an optimal path* iff F[i][j] + c(C_t[i], r_j) + G[i+1][j+1] ≤ opt + δ, and
   - *deleted on an optimal path* iff F[i][j] + c_del + G[i+1][j] ≤ opt + δ for some j.
   The read's call at i is **certain** iff no optimal path deletes i and every optimal match at i reads the **same base
   value**. Otherwise the call is erased for this read only. δ ≥ 0 is the slack (default 0, integer costs, exact
   equality). Base-value agreement, not read-index agreement, is the criterion: a deletion inside a homopolymer run is
   placed anywhere in the run, but every placement yields the same bases, so the run stays certain. This is the
   sub-segment indel placement: the erased span is exactly the set of positions whose base depends on where the indel is
   put, typically 0-2 nt, instead of a whole 24-nt segment.
5. **Vote and iterate.** Per template position: votes from certain calls only; the consensus base needs share ≥ 0.6 and
   at least `min_votes` = 2 votes (one read never decides a base, as in V5, `sync/smart/consensus.py:13-14`). Repeat
   steps 3-5 until C_t = C_{t−1} or t = `rounds` (default 3). Output: frame bases, erasure mask, per-position vote margin.
6. **Decode, then peel.** Map to bytes (`dnaenc/mapping.py`), erasures to byte erasures
   (`sync/template.py:274-275`), and call the unchanged `decode_frames` (`dnaenc/frame4.py:97-140`). If it fails, a GMD
   ladder erases the lowest-margin unerased bytes in steps of 2, at most 4 more trials (the V5 soft "erasure" mode,
   `recovery/options.py` `soft_decoding`). A frame is accepted only per I-5 (`frame4.py:137`). The **header is read from
   the verified frame**, never from a read. Then *peel*: re-create the exact transmitted strand of the verified frame
   (its bytes include the variant byte and the inner parity, so `bytes_to_nt` + `insert_markers`,
   `dnaenc/frame4.py:70-71`, reproduce it), assign every member read whose edit distance to that strand is ≤ θ and
   smaller than to the residual consensus, remove them, and repeat steps 2-6 on the residual if it has ≥ 2 reads.
   At most `max_peels` = 3 per cluster.
7. **Trial budget.** At most `max_trials` = 8 inner-RS + CRC trials per cluster per peel, counted and reported.

Literature for the iterative consensus family: Yazdi, Gabrys and Milenkovic (2017) decode nanopore reads by iterative
alignment and consensus; Organick et al. (2018) cluster then reconstruct; Sabary et al. (2024) review and compare
reconstruction algorithms; trace reconstruction background: Batu et al. (2004). None of their code is used.

### 5.4 Merging cluster frames: fill-only precedence

- Cluster frames are written to separate files `cacc{b}.bin` (bucket = *verified* group mod B, as `Spill.append_acc`
  does for deferred recovery, `recovery/spill.py:67-77`), never to the V6 `acc` files.
- **Superblock (D8).** `_decode_superblock` (`recovery/superblock.py:81`) runs on V6 symbols exactly as now. Only if it
  raises are the cluster superblock symbols added and the decode repeated, with the same tag and ambiguity rules
  (spec §3.10 step 7).
- **Data rows (D9/D10).** In `_pass2` (`recovery/outer.py:117-125`), after `resolve_duplicates` and the V6 pending
  consensus, cluster symbols are added only for addresses in `missing` that are still missing, and only for rows whose
  V6 symbol count is below k_g. Rows the V6 symbols decode are never touched.
- Cluster frames at addresses that V6 already resolved are not used. They are counted: `confirmations` (same payload) and
  `conflicts` (different payload). A conflict is reported and adds the hint "cluster/V6 conflict" to any later failure.

Consequence (THEORETICAL, to be tested as property P-1 in §8): for a superblock-1 archive, if 6.0 publishes a container,
7.0 with clustering publishes the same bytes, because every row and the superblock see exactly the V6 symbols. For a
superblock-2 archive, a filled row could in principle make a row decode that the V6 column code would otherwise have
recovered; this changes the outcome only if the filled symbol is a CRC false accept (§5.8), and then FC-1 refuses
publication. So the stage can turn FAILURE into SUCCESS, never SUCCESS into wrong data.

### 5.5 What needs a format change (the precise answer)

| Capability | On the V6 frame-4 layout, decoder-only? | Notes |
|---|---|---|
| Group reads of one strand without trusting any header | **Yes** | content sketches; header comes from the verified consensus frame |
| Use reads beyond the alignment band | **Yes** | unplaced store, per-cluster band up to 32 |
| Indel placement below segment level, strand with ≥ 2 reads | **Yes** | forward-backward against the consensus template (§5.3 step 4) |
| Same, single read | **No further decoder-only gain** | a wildcard template cannot locate an indel inside a segment; V5 smart recovery already arbitrates ±1 shifts with the inner code. More markers are possible as a *profile* (`Layout(marker_period, marker_len)`, `dnaenc/layout.py:177-197`); a new frame-4 profile needs a reader registry entry (`recovery/probe.py:129-132`) and older readers fail closed with LAYOUT_UNDETECTED (exit 3) |
| Markerless layouts (v4-dense, v6-high-dropout) | **Yes** | medoid seed (§5.3 step 2) |
| Separate strands with identical payloads | **Partly** (decode-and-peel) | the complete fix is format-level: address-keyed payload whitening (frame-6 candidate W, §6.3) |

Because A writes nothing, there is no format version, superblock value or feature flag to add for A. The decode report
gains an optional `clustering` block (additive; schema minor bump if the schema rules require it).

### 5.6 Complexity and budgets

| Step | Time | Memory |
|---|---|---|
| unplaced store | O(n · W) I/O in pass 1 | disk: n · (2W + 4) B |
| sketch | O(n · L · s) hash evaluations (native) | n · s · 4 B (128 B per read at s = 32) |
| candidates | sort of n · s keys | n · s · 8 B transient |
| verify | about n edit distances, O(L · ⌈L/64⌉) words each | O(1) per pair |
| components and refine | near-linear (union-find); refine O(m · r) per oversized component | n · 4 B |
| consensus | rounds × reads × T × (2B + 1) × 2 (forward and backward), B ≤ 32 | one cluster at a time per worker |

Budgets (FC-5): new `RecoveryBudget.max_cluster_reads` (default None; the CLI default is 4,000,000 reads, about 512 MB of
sketches) and the existing wall-time and RSS checkpoints (`recovery/planner.py:50-57`). If a budget is exceeded the stage
stops, its partial results are still fill-only verified frames, and the report names the budget. Clustering of larger
pools by content partitioning is V8.

Determinism: hash constants fixed; edges and components in index order; per-cluster work independent; merge in cluster
ID order. Output must not depend on worker count (criterion C6).

### 5.7 Native kernels

- `native.cluster`: k-mer sketch and Myers edit distance (new C file next to `v5/native/align.c`), with a NumPy reference.
- `native.align`: a new entry point for a **per-read template** (base codes at frame positions, per-position mismatch
  cost) with forward and backward passes. The existing entry points `vnx_align_batch` and `vnx_align_batch_path`
  (`v5/native/align.c:357, 370`) are not changed, so the V6 path stays bit-identical; the ABI version is bumped only for
  the added symbol.

As built (A2): both live in one new kernel, `src/vnxdna/native/c/cluster.c` (binding `vnxdna.native.cluster`,
extension `vnxdna._vnx_cluster`), which also runs the candidate-pair step and the verification loop. The per-read
template forward-backward pass is `vnx_cl_fb` there rather than a new `align.c` entry point, so `align.c` and its ABI
are untouched. Equivalence evidence: `tests/v7/test_native_cluster.py`; speed: `benchmarks/v7/native_cluster/README.md`.

### 5.8 Failure modes and refusal

| Failure | Detection | Effect |
|---|---|---|
| Two strands in one cluster | consensus columns split; RS fails or decodes the majority strand; peel separates | at worst a missing address (an outer-code erasure) |
| One strand split over clusters | duplicate verified frames | resolved by `resolve_duplicates` (strict majority, `recovery/consensus.py:75`) |
| Consensus wrong but CRC-valid (false accept) | probability about 2⁻³² per trial after RS (`frame4.py:106-107` comment) | fill-only; row decode; container SHA-256 refuses (FC-1). Expected false frames per archive = trials × 2⁻³²: at 10⁶ strands × 8 trials, 0.0019 (THEORETICAL) |
| Frame of another archive in the pool | tag ≠ superblock pool tag | ignored, as now (`recovery/outer.py:104`) |
| Budget exceeded | planner | stage stops, reported (FC-5) |
| Malformed reads (length, N, binary) | store limits; reads parser limits (`v4/reads.py:24`) | counted and skipped; fuzz target in §8 |

**FC-9 (new, normative for every 7.x decoder path).** A frame produced by a stage that does not read the header from the
read itself (clustering) may only add symbols at addresses and superblock positions that the 6.0 decoder path left
unresolved, and only after passing the I-5 acceptance.

## 6. Item B: frame 6 and superblock 3 in the light of A

### 6.1 No new indel mechanism in frame 6

- A shows the two V6 walls are decoder problems (§5.5). A frame-6 sync word, watermark or per-strand cluster ID would
  cost rate at the short lengths that motivate frame 6 (spec §3.3 N1) without addressing a measured gap.
- Markers stay as spec §3.5 states ("Mapping and markers: as VNX4 §11"). Their period and length are **profile**
  parameters. EXP-F6-1 (V6_ARCHITECTURE §9) is changed so that it (a) runs on D's fitted models, (b) adds marker period
  {16, 24, 32, none} at ℓ ∈ {2, 3} to the grid, and (c) has the V7 cluster decoder as an arm next to the default and
  smart+soft arms. Without (c) the experiment would measure markers against a decoder V7 does not ship.
- Inner-code changes (HEDGES, VT, markers + DP) stay the V10 decision (V6_DEFERRED §6).

### 6.2 CRC

Keep CRC-32 in every frame-6 profile (decision 6, arithmetic in §5.8: 0.0019 vs 122 expected false frames per 8 × 10⁶
trials). Revisit only if EXP-F6-1 measures a rate gain that the founder judges worth the availability loss.

### 6.3 Candidate W: address-keyed payload whitening (conditional)

Problem: the scrambler keystream depends only on the variant v (`dnaenc/scrambler.py:15`), and most strands use v = 0
(spec §3.10 implementation notes), so equal payload symbols (uncompressed low-entropy containers: `--compression none`
exists, `commands/cli.py:148`) give strands that differ only in header, CRC and parity. Candidate W for frame 6: XOR
payload bytes H … H+P−1 additionally with SHAKE-128("VNX6 payload" ‖ tag ‖ group ‖ symbol); the header keeps the I-2
scrambling, the CRC stays over the unwhitened bytes (I-4 unchanged), the decoder unwhitens after reading the verified
header. Cost: one XOF call per frame. **Adopt only if** A-EXP-01 cell L (§9) fails its secondary criterion; the decision
is recorded before EXP-F6-1's pre-registration and, if adopted, uses frame-6 flags bit 3, today RESERVED-must-reject
(spec §3.5.1), so 7.0 readers that do not implement W refuse such frames.

### 6.4 Primers and superblock 3

- Primers (spec §3.6) are constant flanks; the sketch bucket cap (§5.2 step 2) removes their k-mers without
  configuration, and D2 trimming (spec §5.2) runs before the unplaced store.
- Superblock 3 (spec §3.8.3) needs no cluster field: every clustering parameter is decoder-side. Its 12 reserved bytes
  stay reserved for the index-root pointer (spec §2.4).
- Cluster frames must satisfy the superblock-3 rule "the tag of every frame grouped under this superblock equals the
  pool tag" exactly like V6 frames.

## 7. Compatibility plan

1. **Readers.** 7.x MUST decode, bit-exactly, `tests/fixtures/v4_0`, `v5_0`, `v6_0` with clustering off **and on**
   (spec §4.6.2 extended). V1-V3 stay with `vnx-dna`.
2. **Default output.** With default options, 7.x strand output stays byte-identical to 6.0 for the same container bytes
   (spec §4.6.3). A writes nothing new.
3. **Decoder default.** Off. Turning on must not change any outcome where 6.0 succeeds (FC-9, property P-1).
4. **Old readers and new formats.** 6.x refuses frame 6 and superblock 3 with exit 6 (spec §3.10 step 6, §4.6.4); the
   stored 6.0 answers become negative conformance vectors when B lands. Candidate W, if adopted, is refused by any reader
   without it (flags bit 3).
5. **Never reinterpret** (spec §4.6.1): no existing value changes meaning; new registry entries only (frame-6 profiles,
   superblock 3, the `clustering` report block).
6. **Spec 7.0 text changes.** §5.2 adds stage D7c `cluster` and the unplaced store; §7.3 adds FC-9; §3.5 records the
   EXP-F6-1 outcome (profiles, marker parameters, W yes or no); §12 question 5 is closed by decision 6.

## 8. Test plan

| Kind | Tests | Where |
|---|---|---|
| Unit | canonical k-mers and sketch against a pure-Python reference; Myers distance against the full DP on random pairs; union-find order independence; forward-backward certainty against brute-force enumeration of all optimal paths for T ≤ 24; consensus of identical reads equals the read; homopolymer deletion stays certain; peel re-creates the transmitted strand bit-exactly for all four profiles | `tests/unit/cluster`, `tests/v7` |
| Property (hypothesis) | **P-1 fill-only**: for random small pools on random models, decode with clustering on succeeds whenever off succeeds and publishes the same container SHA-256; **P-2 never wrong**: clusters built from reads of 2-4 different strands yield either no frame or a verified frame equal to one constituent strand; **P-3** cluster output invariant under read permutation and worker count; **P-4** markerless seed path round trip | `tests/property` |
| Golden | v4_0, v5_0, v6_0 with clustering off and on; a new `v7_0` set: a small nanopore-like pool (reads file stored) whose expected outcome with clustering on is pinned (status, container SHA-256, cluster counters) | `tests/compat` |
| Cross-version | 6.0 answers for frame-6 / superblock-3 pools (exit 6) stored and asserted; when B lands, 7.x decodes V4/V5/V6 goldens | `tests/compat`, `tests/conformance/negative` |
| Native = reference | sketch, Myers, per-read-template DP (forward, backward, certainty mask) on ≥ 100,000 reads incl. edge lengths | `tests/v7/test_native_cluster.py` |
| Adversarial | duplicate-payload strands (`--compression none`, zero-filled input); two archives with equal tags in one pool; all reads one strand; reads of random sequence only (must end in FAILURE with no frames) | `tests/adversarial` |
| Resource | budget exceeded → stage stops, named in the report, no unverified output (FC-5) | `tests/v7` |
| Fuzz | new target: the cluster stage on random and mutated read files (length, N, extreme duplicates) | `tests/fuzz`, outside the default suite |
| Architecture | `recovery.cluster` in layer 4, `native.cluster` in layer 1; R1-R6 (`tests/architecture/test_layers.py`) | `tests/architecture` |

The test count never drops; no existing test is changed to pass.

## 9. Experiment plan (pre-registered; SIMULATED unless stated)

Every result file records software, spec, commit, dirty flag, backends, seeds, model SHA-256s and the label
(`vnx.experiment/1`). Thresholds below are **proposals**; the PREREG commit fixes them after A0 and before any
confirmatory seed is run. A0 uses disjoint seeds and is descriptive only.

| ID | Question | Grid | Seeds | Metrics |
|---|---|---|---|---|
| A0 (exploratory) | Upper bound of the consensus engine and the clustering, with ground truth | nanopore-like cov 5/10/15, deletion-heavy cov 5, fitted nanopore model(s) cov 10; v4-balanced, 20,000 B; extends AB-DIAG (`experiments/v6/align-band/funnel.py`) with oracle clusters → V7 consensus | 82000-82009 | strands decoded by oracle-cluster consensus; erased bytes per consensus frame; 2e + f ≤ r share; cluster purity and recall; parameter sweep k ∈ {10, 12, 14}, s ∈ {16, 32, 48}, θ ∈ {0.25, 0.30, 0.35}, δ ∈ {0, 1, 2}, rounds 1-4 |
| A-EXP-01 (confirmatory) | Does `read_clustering=fallback` decode nanopore-like and fitted nanopore pools with no false SUCCESS and no harm? | 14 shipped models × cov {3, 5, 10}, nanopore-like cov 15, fitted models (D) × cov {5, 7, 10, 15}; v4-balanced 20,000 B; secondary profiles v4-dense, v6-high-dropout; **cell L**: `--compression none`, zero-filled input, illumina-like and nanopore-like cov 10; arms `default`, `cluster`, `smart-auto`, `cluster+smart-auto`, `cluster-w4` | 82100-82119 | exact decode (container SHA-256), failure stage, frames filled, conflicts, false frames vs ground truth, cluster purity |
| A-EXP-02 (cost) | Time and memory | mixed-mild, illumina-like, nanopore-like, deletion-heavy, fitted nanopore; 1 MiB; fresh child process, child VmHWM (AB deviation 1) | 82200-82202 | wall-time ratio, VmHWM ratio, seconds per stage |
| A-EXP-03 (PUBLIC-DATA-DERIVED) | Consensus engine and clustering on public reads, in isolation | D04 CNR: per-cluster consensus vs centre (markerless seed); D03: shuffle reads, re-cluster, compare with the oligo assignment | all clusters | exact reconstruction share, per-base error, purity, recall; no comparison with published algorithms (different protocols) |
| A-FUZZ | Robustness | cluster stage on mutated read files | 82300, 400 rounds | crashes, unverified output (must be 0) |

**Primary criteria (proposed).**

| id | criterion | rule |
|---|---|---|
| C1 | no false SUCCESS, no unverified bytes published, every arm and cell | 0 of all decodes |
| C2 | nanopore-like cov 10 and 15 pooled (40 paired seeds) | `cluster` ≥ 32/40 exact and paired gain Newcombe 95 % lower bound > 0.50 |
| C2f | fitted nanopore model(s) | smallest coverage in {5, 7, 10, 15} with ≥ 19/20 exact is ≤ 10 |
| C3 | no harm | 0 seeds where only `default` decodes, over every cell |
| C4 | time where `default` decodes (≥ 10/20) | median ratio ≤ 1.10 |
| C5 | peak RSS (child VmHWM) where `default` decodes | every pair ≤ 1.20 |
| C6 | determinism, 1 vs 4 workers | 0 differences in status, container SHA-256 and cluster counters |
| C7 | native = reference | 0 mismatches on ≥ 100,000 reads |

**Secondary (descriptive, thresholds stated in advance).** S1 cluster purity ≥ 0.999 and strand recall ≥ 0.95 at
nanopore-like cov 10 (ground truth). S2 CNR exact reconstruction share above the V6 position-wise vote (1,288 / 10,000,
P4-EXP-03), with Wilson interval. S3 cell L: `cluster` ≥ `default` and no FAILURE caused by merged duplicates (if
S3 fails, candidate W of §6.3 is adopted for frame 6). S4 deletion-heavy, insertion-heavy and mixed-harsh gains, pooled.
S5 nanopore-like cov 3 and 5 reported without claim.

**Decision rule.** C1, C3, C6 or C7 REJECT: not shipped until fixed and re-registered. C2, C2f, C4 or C5 REJECT: ships
opt-in only. All ACCEPT: eligible for a default change, which needs a separate founder decision. Results stay SIMULATED
(or PUBLIC-DATA-DERIVED for A-EXP-03) and are quoted with their models' fit status.

**Item C (job #81) after A.** The #81 pre-registration gains a `cluster` arm, and quality weighting is re-tested on the
fitted models that carry calibrated qualities (D03, D01/D02). The consensus vote of §5.3 accepts per-read weights through
the same interface as `consensus_quality_weighted` (`recovery/consensus.py:41`), off by default.

## 10. Phases and dependencies

| Phase | Content | Depends on | Exit |
|---|---|---|---|
| P0 | this design; literature and dataset rounds | — | design reviewed |
| D1 | fit D03, D04 (nanopore), D02/D01 (Illumina), coverage statistics; optional transcript replay | P0 | models frozen by SHA-256, fit metrics, licences recorded |
| A1 | reference implementation: unplaced store, sketch, clustering, consensus, peel, fill-only merge, report block; unit and property tests | P0 (parallel to D1) | tests green, goldens unchanged |
| A0 | exploratory funnel (§9) on unfitted, then fitted models | A1 prototype; D1 for the fitted rows | parameters chosen; PREREG written |
| A2 | native kernels and native = reference | A1 | C7-type test green |
| A3 | PREREG commit, A-EXP-01/02/03, A-FUZZ, verdict | A0, A2, D1 | verdict applied |
| C1 | #81 pre-registration and run with the `cluster` arm; quality re-test on fitted models | A3, D1 | verdict applied |
| B0 | EXP-F6-1 pre-registration on fitted models with V7 arms; decision on W from A-EXP-01 cell L | A3, D1 | profiles chosen |
| B1-B3 | frame 6 compact class, superblock 3, primers module, goldens `v7_0`, cross-version vectors | B0 | spec §3.5 profiles frozen with fixtures |
| G | #62, #66, #58, MSan attempt, memory/scaling (parallel) | — | each with a regression test |
| E/F | benchmark lab B1 (#78), `vnx order-export`, short-strand vendor profiles | B1-B3 | — |
| R | release gate as V6 | all | founder go on the exact SHA |

## 11. Risks

1. **The consensus engine is not good enough at nanopore-like rates.** AB-DIAG shows the segment-erasure vote reaches
   only 29-51 % of strands. A0 is the gate: if oracle-cluster consensus decodes too few strands for the outer code, the
   fallback is a soft (posterior) inner decode within the same stage or more markers via EXP-F6-1, and C2 is
   re-scoped before any confirmatory run, never after.
2. **Fitted models are harsher or context-dependent** (homopolymers, ends). Mitigation: δ slack and the position profile
   are parameters chosen in A0; claims are limited to the fitted models named in the PREREG.
3. **Near-duplicate strands** (low-entropy uncompressed containers). Mitigation: peel; measured by cell L; format fix W
   held for frame 6.
4. **Memory at large pools.** Mitigation: budget and stop (FC-5); V8 streaming. The unplaced store adds disk I/O in pass
   1 when the option is on (C4/C5 measure it).
5. **False accepts grow with trials.** Mitigation: trial cap, fill-only, CRC-32 kept, reported expected false frames.
6. **Scope drift** into learned or BCJR decoders. Non-goal for 7.0.
7. **Licences.** GPL tools are reference reading only; public datasets with unclear licences (D14) are used for summary
   statistics only, as the registry records.
8. **Simulator coverage parameterisation** may not be NB(μ, 4) per strand as assumed in §4; A0 counts it before the
   targets are fixed.

## 12. Open questions for the founder

1. Default CLI budget for clustering (4,000,000 unplaced reads proposed).
2. Whether a passing A-EXP-01 should make `read_clustering=fallback` the 7.0 default, or wait for #81's combined
   default decision.
3. Candidate W: accept the rule "adopt only if cell L fails" now, so that EXP-F6-1 is not delayed.

## References

- Batu T, Kannan S, Khanna S, McGregor A. Reconstructing strings from random traces. SODA 2004.
- Broder AZ. On the resemblance and containment of documents. Compression and Complexity of Sequences 1997.
- Myers G. A fast bit-vector algorithm for approximate string matching based on dynamic programming. J ACM 46(3), 1999.
- Organick L, et al. Random access in large-scale DNA data storage. Nat Biotechnol 36:242-248 (2018). doi:10.1038/nbt.4079
- Rashtchian C, et al. Clustering billions of reads for DNA data storage. NeurIPS 2017.
- Sabary O, Yucovich A, Shapira G, Yaakobi E. Reconstruction algorithms for DNA-storage systems. Sci Rep 14 (2024).
  doi:10.1038/s41598-024-51730-3
- Srinivasavaradhan SR, Gopi S, Pfister HD, Yekhanin S. Trellis BMA. ISIT 2021, arXiv:2107.06440 (CNR dataset).
- Yazdi SMHT, Gabrys R, Milenkovic O. Portable and error-free DNA-based data storage. Sci Rep 7:5011 (2017).
  doi:10.1038/s41598-017-05188-1
- Datasets D01-D04, D14: `docs/DNA_STORAGE_DATASET_REGISTRY.md`.
