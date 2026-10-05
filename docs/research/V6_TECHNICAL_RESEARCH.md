# V6 technical research: synthesis

Status: V6 directive Phase 10 (documentation), `build/v6-sprint` @ b693254. This page summarises
[ALGORITHM_COMPARISON.md](../ALGORITHM_COMPARISON.md) and the technical parts of
[COMPETITIVE_GAP_ANALYSIS.md](../COMPETITIVE_GAP_ANALYSIS.md), then records what V6 measured on each topic. The market
and benchmark side is in [V6_COMPETITIVE_RESEARCH.md](V6_COMPETITIVE_RESEARCH.md).

Labels: **SIMULATED**, **PUBLIC-DATA-DERIVED**, **MEASURED** (timing or memory on this host), **THEORETICAL**,
**PHYSICAL** (none). External figures are author-reported and were not reproduced here; the source document marks the
ones that were relayed and not checked. They use different error rates, strand lengths, coverages and success
definitions and must not be ranked. No DNA has been synthesised, stored or sequenced by VNX-DNA.

## 1. Map of the field (from ALGORITHM_COMPARISON.md)

| Topic | Approaches in the record | Where VNX-DNA stands at 6.x |
|---|---|---|
| Bytes to bases and constraints (§1) | 1 bit/base (Church), rotating ternary (Goldman), fountain with rejection (DNA Fountain), constrained mappers (YYC, enumerative bounded-homopolymer code) | 2 bits per base after a scrambler; GC, homopolymer, repeat and motif rules are met by screening up to 256 scrambler variants, not by constrained coding. The rate cost of screening has not been measured against an enumerative mapper |
| Inner codes for indels (§2) | HEDGES (hash-guided stack search), DNA-Aeon (arithmetic code with CRC sync and stack decoder), Gungnir, convolutional codes with Viterbi/BCJR, marker-repeat codes | A 3-nt marker every 24 nt with banded dynamic-programming alignment turns an indel into an erased segment for the inner Reed-Solomon code; no indel-correcting code |
| Outer codes (§3) | strand-level and 2-D Reed-Solomon (DNA-RS), Derrick (soft RS), LT/Raptor fountain, LDPC (Chandak, StairLoop, mahoraga) | Cauchy Reed-Solomon (MDS); opt-in product code over stripes (superblock 2); no LDPC |
| Trace reconstruction (§4) | majority vote / MSA, BMA and BMALA, Trellis BMA, beam search, neural (DNAformer, TReconLM), profile-HMM fusion | Consensus over reads grouped by decoded address; soft posteriors and bounded local search for indels (opt-in); no joint multi-trace inference over a code trellis |
| Clustering (§6) | address-based, LSH, tree-based (Clover), CD-HIT-style | Grouping by the decoded address in each frame header; no unaddressed clustering |
| Simulators (§7) | dt4dds (the only public channel fitted to real data), UNACORM, others | A staged, parameterised framework, none of whose 14 models is fitted to a platform |

The gate's recommendation, THEORETICAL until benchmarked: implement a measurement of the mapping-rate gap, a
clean-room HEDGES-class inner code as a comparison, a clustered-read consensus interface, and unaddressed clustering;
run DNA-Aeon, DNA Fountain, YYC, Derrick and others only as external processes; skip copyleft or restricted code that
cannot be embedded (§9). Whether adding joint multi-trace inference would improve decoding of VNX-DNA frames is not
known (§5 of the same document). Nothing in V6 changes that.

## 2. What V6 measured, by topic

All rows below are SIMULATED unless the label says otherwise. Experiment directories are under `experiments/v6/`.

### 2.1 Outer code under strand loss

The V6 product code (stripes with column parity, interleaved order, adaptive planner) raised the i.i.d.
strand-loss threshold, measured as the highest loss at which 20/20 seeds decode, from 0.07 (V5) to 0.16 (adaptive plan
and sequential plan) at redundant-strand overhead 0.251 against 0.249 (`experiments/v6/phase1/summary.md`, grid
P1-EXP-01). The comparison is against VNX-DNA V5 only; no external code was run on this channel. At the B0 benchmark's
profiles with 8 parity strands in 72, a 10 % loss was not decoded (0/3 for `s184` and `s280`, lab README sweep C).

### 2.2 Failure taxonomy of the decoder (P4-EXP-01)

770 trials in 40 cells, default and smart+soft arms: 0 false SUCCESS in 1,450 decodes. Four failure modes, from
`experiments/v6/phase4/README.md` (SIMULATED): (1) reads beyond the alignment band of 6 (nanopore-like 64 %, deletion/insertion-heavy
35 % of reads); (2) strand loss above the outer parity; (3) addresses that do not reach pass 2 at low coverage;
(4) consensus-limited groups. The opt-in smart+soft path removes mode (4) where it was the only mode, at 3-10 times the
decode time; nothing tested removes modes (1)-(3).

### 2.3 Quality-weighted consensus (P4-EXP-02, pre-registered)

A weighted vote with Phred-derived likelihoods against the count vote, 975 paired trials (SIMULATED). Efficacy criterion C2 REJECT:
213/520 against 211/520 exact, difference +0.0038, 95 % CI [-0.0016, +0.0093]. Mechanism C5 ACCEPT (+5.38 multi-read
attempts per trial within the Reed-Solomon bound), no harm, determinism 15/15
(`experiments/v6/phase4/P4-EXP-02-qw-consensus/README.md`). The simulator's qualities are informative by construction
and the score is not calibrated, so the test says little about real basecaller qualities. It stays opt-in. Cost: wall
time 32-42 % higher at 1 MiB (P4-EXP-04, MEASURED); the memory column of that experiment is not reliable (it reads the
harness's own peak, see the correction note in `experiments/v6/phase4/README.md` and the method in
`experiments/v6/align-band/README.md`, "Deviations").

### 2.4 Retry band (AB-EXP-01/02, pre-registered)

A second, wider alignment pass for reads whose drift exceeds the band. 1,910 decodes, 0 false SUCCESS. Deletion-heavy
at coverage 10 went from 0/20 to 6/20; pooled over the four high-indel models the gain was +0.0292 (7/240 against
0/240, 95 % interval [+0.0074, +0.0590]); on nanopore-like it is 0/20 at every coverage. The decision rule gave KEEP
OPT-IN (C2 and C5 REJECT). Native and reference aligners agreed on 102,400 fuzzed reads
(`experiments/v6/align-band/README.md`).

### 2.5 Why nanopore-like fails (AB-DIAG, ground truth)

With band 16, 0.981 of nanopore-like reads align, but only 0.038 carry their exact header address (0.094 within one
byte), about 41 of 70 frame bytes per aligned read are erased by the whole-segment rule, and an oracle count vote
decodes 0.289 (coverage 10) to 0.509 (coverage 15) of strands. The remaining levers are address recovery that does not
trust the header, and indel placement below segment level (job #82, V7). This matches the gate's weakness list (orphan
reads; no indel-native inner code).

### 2.6 Public nanopore statistics (P4-EXP-03, PUBLIC-DATA-DERIVED)

On the Microsoft clustered nanopore reads (PUBLIC-DATA-DERIVED): 2.16 / 1.66 / 1.95 % substitution / insertion / deletion per base, 4.34 % of
reads without an indel, 12.9 % of clusters recovered by a position-wise vote over exactly-110-nt reads. The
measured rates agree with the values the Trellis BMA paper reports for clusters 1-2000, which checks the aligner used
here, not VNX-DNA. An i.i.d. extrapolation to a 313-nt strand puts 94.1 % of reads within the default band; the row
ignores the positional structure the same experiment measures, and the simulated nanopore-like model is much harsher
than this dataset (`experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md`).

### 2.7 Native kernels and fuzzing

Three optional C kernels (aligner, read parser, inner Reed-Solomon) are bit-identical to NumPy references
([NATIVE_KERNELS.md](../NATIVE_KERNELS.md)). A plain `pip install` now builds all three; installed-path decode of a
4 MiB noisy input went from 10.8 s to 5.3 s (2.04x, one host, one workload, one worker, MEASURED;
`benchmarks/v6/native_packaging/README.md`). The AVX-512 path measured 0.94 times the AVX2 path on the development host
(`benchmarks/v6/native_rs/results/bench.json`), so AVX2 is the automatic choice. The libFuzzer harnesses (read parser,
RS, aligner) and ten Python targets ran for 320-630 s each in the committed campaign, with the crashes and one
out-of-memory event documented as V6-FUZZ-01 to -03 and fixed (`docs/security/V6_FUZZ_REPORT.md`). MSan and non-x86
builds were not run.

### 2.8 Benchmark lab B0

Summarised in [V6_COMPETITIVE_RESEARCH.md](V6_COMPETITIVE_RESEARCH.md) §3.1. For the technical comparison: against DNA-RS
and DNA Fountain at about 1.0 bit/nt, VNX-DNA writes longer strands (14 bytes of header and CRC per strand), tolerated
fewer errors at 1 % and fewer lost strands at 10 %, and returned no wrong output with exit 0, where the others did in
18 of their trials (lab README, "Where VNX-DNA loses" and "does not lose").

## 3. Open technical questions

| Question | Why it matters | Where it goes |
|---|---|---|
| Marker alignment against a HEDGES- or Aeon-style inner code | Both have wet-lab papers; VNX-DNA has not been compared on a shared protocol | B1 (job #78), decision in V10 |
| Rate cost of scrambler screening against enumerative bounded-homopolymer mapping | VNX-DNA's rate is unmeasured against an exact rate reference | not scheduled; ALGORITHM_COMPARISON §9.1 |
| Header-independent read-to-strand assignment | Binding limit for nanopore-like reads | V7 (job #82) |
| Calibrated qualities and fitted channels for quality-weighted consensus | The V6 test could not answer the efficacy question for real reads | V7 |
| LDPC against Reed-Solomon, iterative inner/outer decoding | Used by StairLoop and mahoraga; VNX-DNA has none | V10 |

Full list with reasons and evidence: [V6_DEFERRED.md](../V6_DEFERRED.md).
