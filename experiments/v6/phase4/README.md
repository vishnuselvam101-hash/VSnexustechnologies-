# V6 Phase 4: indel and soft-decision work, measurement-driven (SIMULATED unless stated)

**SIMULATED** except P4-EXP-03 (PUBLIC-DATA-DERIVED) and the time/memory columns of P4-EXP-04 (MEASURED). No DNA was
synthesised, stored or sequenced. No format change. No default changed.

| experiment | what | outcome |
|---|---|---|
| [P4-EXP-01](P4-EXP-01-failure-taxonomy/README.md) | failure taxonomy of the current decoder: 40 cells (14 named models, coverage ladders, B0-like, 1 MiB), 770 trials, default and smart+soft arms, every failure classified by stage | 0 false SUCCESS in 1,450 decodes; four failure modes, below |
| [PREREG](PREREG.md) | pre-registered design and criteria C1-C7 for the quality-weighted consensus, committed before the comparison | — |
| [P4-EXP-02](P4-EXP-02-qw-consensus/README.md) | the pre-registered paired comparison, 975 trials, fresh seeds | C2 efficacy **REJECT**; C1, C4, C5, C6, C7 **ACCEPT**; no default change proposed |
| [P4-EXP-03](P4-EXP-03-cnr-ids/README.md) | per-read IDS statistics of the public CNR nanopore dataset | PUBLIC-DATA-DERIVED: 2.16 / 1.66 / 1.95 % sub / ins / del; 4.3 % of reads indel-free |
| [P4-EXP-04](P4-EXP-04-cost/README.md) | fresh-process time and peak RSS at 1 MiB | time +32-42 %, peak RSS unchanged |

Code: `phase4.py` (harness: paired arms on the same reads, ground-truth observer of pass-2 consensus, read attribution),
`verdict.py`, `cost.py`, `cnr_ids.py`. Decoder change: `DecodeOptions.consensus_weighting` (`count` default, `quality`
opt-in), `src/vnxdna/recovery/consensus.py::consensus_quality_weighted`; tests `tests/v6/test_consensus_weighting.py`.

**Correction (Phase 10 documentation audit).** P4-EXP-04 recorded peak RSS with `wait4` `ru_maxrss`. On Linux an exec'd
child inherits the spawning parent's high-water mark, so that column reports the harness's own size and its equal values
per pair do not show that the option leaves memory unchanged. The corrected method (the child records its own
`VmHWM`) is in `experiments/v6/align-band/README.md` ("Deviations from the pre-registration", item 1); P4-EXP-04 has not been re-run with it.
The Phase 4 memory claim is therefore withdrawn; the time column is not affected. Criterion C6 of P4-EXP-02 used this
column and is unreliable for the same reason. Result files are unchanged.

## Directive §9/§10 questions, answered from these results

| question | answer (source) |
|---|---|
| Where do alignment errors occur? | Before alignment: reads beyond the band of 6 (nanopore-like 64 %, deletion/insertion-heavy 35 %; P4-EXP-01). Inside consensus: undetected wrong bytes spread evenly over frame segments 2-10 (9-12 % each), few in the header segments (2-6 %, reads are grouped by header) (P4-EXP-01). On real nanopore reads insertions are about twice as frequent at both strand ends (P4-EXP-03) |
| Failure modes | (1) band/superblock (nanopore-like; deletion-heavy at coverage ≤ 3); (2) strand loss above outer parity (dropout-20, burst-loss, `s184` at 10 % dropout); (3) addresses not reaching pass 2 at low coverage; (4) consensus-limited groups. Opt-in smart+soft removes (4) wherever it was the only mode, at 3-10 times the time; nothing tested removes (1)-(3) (P4-EXP-01) |
| Ambiguity | count vote: split → erasure (1-vs-1 always); weighted vote: split resolved by quality, tie → erasure. With constant qualities the weighted vote resolves plurality splits and creates errors (P4-EXP-02) |
| Complexity / memory | weighted vote O(m · L · 4) per address, as the count vote; pass 1 uses the path aligner and gathers qualities (+32-42 % wall time at 1 MiB); pending records +285 bytes (v4-balanced) on disk; peak RSS not established (P4-EXP-04, see correction below) |
| Interaction with RS | every consensus frame is still inner-RS + CRC verified; turning erasures into errors costs 2 instead of 1 of the r = 16 budget; net effect measured as attempts within 2e + f ≤ r (+5.4 per primary trial) |
| Interaction with synchronisation | the weighted vote only re-weights bases the marker alignment already placed; indel-erased segments stay erased; it does not help reads outside the band |
| Interaction with soft decisions | smart/soft already combine per-read quality evidence in their own soft consensus (`_soft_consensus`); the weighted vote is the cheap hard path only and was tested with soft decoding off |
| Confidence representation | per projected base its read's Phred quality (u8) → ε = 10^(−Q/10), clipped to [1e-6, 0.75] |
| Normalisation | summed log-likelihoods normalised under a uniform prior (log-sum-exp); the result is a Phred-interpreted score, **not calibrated** (the simulator's qualities are informative by construction; CNR has no qualities) |
| Propagation | pass 1 (alignment path) → pending record (`pq`, `pqok`) → spill → pass-2 vote; the deferred schedule's decodability check uses the same vote |
| Decoder interface | `DecodeOptions(consensus_weighting="quality")`, `--consensus-weighting quality`, config `decode.consensus_weighting`; report counters `consensus_weighted`, `consensus_weighting_fallback` |
| Fallback | an address group with any read lacking qualities (FASTA) uses the count vote (counted); default `count` leaves pending records and reports byte-for-byte as before |
| Determinism | 15/15 identical decodes for workers 1 and 4 (P4-EXP-02 C7); unit test for workers 1 and 2 |

## What would move the failure modes (not done here; for planning)

* Mode (1): a wider or adaptive band, or a re-alignment of out-of-band reads, is the only lever for nanopore-like and
  deletion-heavy low-coverage cells (and a precondition for any real nanopore data).
* Mode (2): outer parity: `s184` has 8/72 parity against 10 % dropout; a profile with more outer parity (as the B0 README
  proposes) is a measurement to run, not a decoder change.
* Mode (4): the existing opt-in smart+soft path already recovers these cells (20/20 where the default gives 0/20); its
  cost (3-10 times) is the question for a default-schedule decision, which needs its own pre-registered comparison.
