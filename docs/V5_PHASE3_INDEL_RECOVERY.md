# VNX-DNA V5 — Phase 3: smart indel recovery

All channel results are **SIMULATED**: software-generated strands, the V4 channel simulator (or controlled edits
injected by the harness). No DNA was synthesised or sequenced. The decoder never sees simulator truth; truth is
used only by the experiment harness, after decoding, to score the outputs.

**Result (all SIMULATED).** V5 Phase 3 adds bounded, fail-closed smart indel recovery as an opt-in decoder mode.

* **Erased nucleotides per true indel** (primary metric, coverage 1, V4 channel, 4,096 strands): V4 erases a median of
  **24 nt** (mean 25–28). When the local primitive is applied, V5 erases a **median of 4 nt** (mean 4.2 nt at 0.05 %,
  6.1 nt at 0.1 %, 8.6 nt at 0.2 %; p95 24 nt). With exactly one indel per read, it erases **3.7 nt per deletion and
  0 nt per insertion**, against 25.5 / 31.9 nt for V4 (14,832 controlled reads).
* **Reads decoded at coverage 1:** 3,316 → 3,892 at 0.3 % + 0.3 %, 2,341 → 3,374 at 0.5 % + 0.5 %, and 669 → 1,563 at
  1 % + 1 % (of 4,096).
* **Archives:** the coverage-1 threshold moves from between 0.2 % and 0.3 % to between 0.4 % and 0.5 % insertions +
  deletions (3/3 seeds each). Coverage 2 now survives 0.5 % + 0.5 % (V4 0/3 → V5 3/3), and coverage 5 survives
  1 % + 1 % (0/3 → 3/3).
* **Integrity:** **0 false acceptances** among all frames accepted and checked against truth (EXP-01: 14,832 reads ×
  5 variants; EXP-02: 3,300 reads; EXP-04: 28,672 reads × 2 views; adversarial and fuzz tests). **0 false SUCCESS** in
  202 archive decodes (EXP-03: 150, EXP-05: 52), every SUCCESS verified by the output file's SHA-256.
* **Cost:** decoding is slower wherever many reads fail the V4 rule. Smart mode needs 8.6 s instead of 0.9 s for a
  256 KiB archive at 0.3 % + 0.3 % (where V4 fails), and 19.7 s instead of 10.8 s on the Phase 2 4 MiB workload on 1 worker
  (5.2 s instead of 4.6 s on 8 workers). Peak memory is 7–31 MB higher. The search is bounded per read; §11 explains the cost and the obvious next
  optimisation.
* V4 stays the default. The V4 format, codes, cryptography and integrity checks are unchanged, and the full suite passes
  (839 existing + 77 new = 916 tests).


## 1. The problem

V4 aligns each read to the strand template (markers known, frame bases wildcards). When the path contains an
insertion or deletion, it **erases the whole segment** between the neighbouring markers (24 nt = 6 bytes with the
default 3-nt markers every 24 nt), because the markers alone cannot say *where* in the segment the indel is. Phase 1
measured the cost: about **26 erased frame nucleotides per true indel** at low indel rates. Inner RS(70, 54) corrects
2e + f ≤ 16, so a read survives two or three such segments, and fewer once substitutions are added.

Phase 3 asks: can the information around an indel be recovered safely, so that only the genuinely uncertain symbols
are erased?

## 2. Answer in one paragraph

From a single read, the position of an indel inside a segment is **not identifiable from the read alone**: every
placement explains the read equally well, because frame bases are not known in advance. Two kinds of evidence can
localise it: **(a) the code** (a placement hypothesis is right if the frame it implies passes inner RS + CRC), and
**(b) other reads** of the same address (consensus). Informative base qualities can rank insertion placements, but at
the V4 simulator's quality scale they do not reach the certainty required to keep bases without verification.
V5 therefore treats each placement as an explicit **hypothesis**, never as a known symbol. It lets the unchanged inner
code verify hypotheses within a bounded, integrity-budgeted search, and accepts a frame only when every hypothesis
that verifies agrees.

## 3. Architecture and responsibilities

```
read ──► marker alignment (V4 DP, native kernel)          synchronisation: where are the markers in the read?
            └─ readpos: read index of every template base
       ──► windows between solid markers                   local indel recovery (vnxdna.v5.indel.recovery)
       ──► candidates (every placement), scored             · hypotheses + confidence, no correction
       ──► per-position posterior → known / erased          erasure generation: only uncertain bytes
       ──► trials T0 / T1 / T2  ──► inner RS + CRC ──►      inner code: corrects 2e + f ≤ r (unchanged V4 code)
            unanimity among verified trials                  acceptance: fail closed on disagreement
pending reads ──► consensus realignment (pass 2)            vnxdna.v5.indel.consensus
symbols ──► outer code (Cauchy RS) ──► container SHA-256 + Merkle validation (unchanged)
```

The separation required by the mission (§10) holds: the local algorithm finds synchronisation detail and decides
which symbols are known, which are hypotheses (status CANDIDATE) and which are erased. It never corrects a symbol.
Inner RS corrects within its documented bound. CRC-32 verifies each frame. The outer code recovers missing symbols,
and the container SHA-256 plus structural validation gate publication.

**Integration.** `DecodeOptions(indel_recovery="smart")` (CLI `vnx decode --indel-recovery smart`) is **opt-in**.
The default stays `"segment"` (V4 behaviour, byte-identical reports and outputs; the 723-test V4/V3 suite runs on
the default). In smart mode:

* pass 1 runs the V4 path first. Only aligned reads that the V4 erasure rule cannot decode go through smart recovery.
  Where V4 succeeds, the result is identical by construction;
* pending reads (failed but aligned) also store their raw, oriented read (and qualities), so pass 2 can realign them;
* pass 2 first runs the V4 consensus vote. For addresses that remain missing and have ≥ 2 pending reads, it runs
  consensus realignment.

Files: `src/vnxdna/v5/indel/{path,recovery,consensus}.py`; hooks in `src/vnxdna/v4/decoder.py` (guarded by the
option); one new native entry point `vnx_align_batch_path` (ABI 2) in `src/vnxdna/v5/native/align.c`.

## 4. Step 1: the alignment path

The Phase 2 kernel already walks the traceback. `vnx_align_batch_path` writes one more output, `readpos[t]`: the
read index aligned to template base t on the path, or −1 if that base was deleted (or the read did not align).
The projection outputs are unchanged and stay bit-identical to the V4 reference. A reference twin
(`PathAligner`) reuses the V4 NumPy DP unchanged and only re-implements the traceback, adding `readpos`. Tests
check native = reference on random reads for several layouts, bands and qualities, and that the projection equals
`TemplateAligner.project`.

## 5. Step 2: windows, candidates and scoring

**Solid markers.** A marker is *solid* if all its bases are on the path (no deletion), at consecutive read
positions, and equal to the marker's bases. Solid markers cut the read into **windows**: template interval
[ta, tb) ↔ read interval [ra, rb). A window spans one segment, or more if a marker between segments is not solid.
A window is **active** if the path has an insertion or deletion inside it. Its net shift is
δ = (rb − ra) − (tb − ta).

**Candidates.** For |δ| = 1 (the bounded case):

* δ = −1 (one base missing): an unknown base at every template position k of the window; the read bases fill the
  rest in order. n candidates for a window of n template bases.
* δ = +1 (one extra base): drop read base k, k = 0 … n. Dropping any base of a homopolymer run gives the same
  reconstruction; those candidates are merged (their likelihoods add).

δ = 0 with an indel inside (an insertion and a deletion cancel), |δ| > `max_shift`, or more than
`max_window_segments` segments → **UNRECOVERABLE**: the V4 rule applies to that window (all its segments erased).
With `max_shift = 2` (not the default), two-indel placements are enumerated if there are at most 16 × `max_candidates`.

**Scoring model.** Placements are a priori equally likely: the V4 channel model is position-uniform, apart from
optional homopolymer multipliers that the decoder does not assume. For candidate c with reconstruction x̂:

  log L(c) = Σ_{marker bases m in the window, x̂_m known} log P(read_m | marker_m)
           + [δ = +1] · log( ε_q / (1 − ε_q) )                         (dropped base q, Phred ε = 10^(−Q/10))
           + Σ_{frame positions k} log P(votes_k | x̂_k)                 (pass 2 only)

with the same per-base error model for markers and for other reads' votes: P(x | y) = 1 − p_sub if x = y, else
p_sub / 3. An unknown base is marginalised: P(votes_k | ?) = Σ_b ¼ · P(votes_k | b). `p_sub = 0.005` is not fitted.
Candidates differ by integer numbers of mismatches and votes, so only the scale matters: any 0 < p_sub ≪ ¼ ranks
candidates the same, and the margin enters the posterior. The quality term is the likelihood ratio of "base q is the
error" under the Phred definition. No platform model is assumed. Without qualities, the term is 0.

**Posterior.** w_c ∝ L(c). Per position, P(x_k = b) = Σ_c w_c [x̂_c,k = b] + Σ_{c: x̂_c,k unknown} w_c · π_k(b),
where π is uniform, or P(b | votes) in pass 2. A window is **HIGH** if its best candidate has w ≥ 1 − 10⁻³,
**MEDIUM** if w ≥ ½, **LOW** otherwise, and **UNRECOVERABLE** as above.

## 6. Partial-segment recovery and the symbol representation

**Erasure generation.** Inner RS works on bytes. A byte inside an active window is kept (passed to RS as a known
value) only if P(all its bases right) ≥ 1 − `keep_byte_error`; otherwise it is erased. The default
`keep_byte_error = 10⁻³` is **strict**: local evidence alone never introduces a symbol that is not near-certain (the
property "uncertain recovery never introduces an unverified symbol"). The alternative ½ is the RS-load optimum,
because an erasure costs 1 unit of the 2e + f budget and an error costs 2. EXP-01 measures it as an ablation:
it halves erasures, but about 40 % of reads then carry wrong unverified bytes. That ablation is the reason for the
strict default.

**Symbol representation** (`SYMBOL_DTYPE`, one record per frame nucleotide):

| field | values |
|---|---|
| `base` | 0–3, or 4 = unknown |
| `conf` | posterior probability of `base` (float32) |
| `status` | KNOWN (read, outside indel windows) · RECOVERED (decided by local evidence) · CANDIDATE (set by one hypothesis of a trial) · ERASED |
| `source` | READ · QUALITY · CONSENSUS · CODE (hypothesis verified by RS + CRC) · V4 (whole-segment rule) |

V4's boolean `erased` mask is derived from it (status ERASED), never the other way round.

## 7. Code arbitration (trials) and the integrity budget

For a read the V4 rule could not decode, the trials run in phases, and the first phase with a verified frame decides:

* **T0**: every active window at its posterior mask (one trial);
* **T1**: one window at each of its candidates, the others at their masks;
* **T2**: two windows jointly at every pair of candidates.

Each trial is decoded by the unchanged V4 path, `decode_frames` (inner RS errors + erasures, then CRC-32 and the
version/kind checks). **Acceptance requires unanimity:** if two trials in the deciding phase verify to different
frames, the read is reported *ambiguous* and not accepted. Nearly indistinguishable candidates are never chosen
between; the code either resolves them consistently or the read fails closed.

**False-acceptance budget.** A wrong hypothesis can only be accepted if RS mis-decodes it to some codeword *and*
the CRC passes. With f erased bytes, the code acts like RS(n − f, n − r) with t′ = ⌊(r − f)/2⌋. Under the
random-word model, a wrong word lands within t′ of a codeword with probability V(n − f, t′)/256^(r − f), where
V(N, t) = Σ_{i ≤ t} C(N, i)·255^i. CRC-32 then passes it with probability 2⁻³². For the default frame (n = 70, r = 16):

| erasures f | 0 | 6 | 10 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|---|
| P(trial falsely accepted) | 1.2·10⁻¹⁹ | 1.6·10⁻¹⁵ | 4.7·10⁻¹³ | 5.8·10⁻¹² | 2.0·10⁻¹³ | 5.1·10⁻¹¹ | 9.1·10⁻¹³ | 2.3·10⁻¹⁰ |

Each read gets a budget of **Σ_trials P ≤ 10⁻⁹** (`false_accept_budget`). Within a phase, trials are taken in order
of fewest erasures until the budget or `max_trials` is exhausted. Trials with more than r erasures are never run.
High-erasure trials are the expensive ones, so the budget is what actually limits deep searches. Downstream the outer
code and the container SHA-256 still stand: even a falsely accepted symbol could at worst turn a SUCCESS into a
FAILURE, never into a false SUCCESS. In every experiment, accepted frames are compared with the truth: **0 false
acceptances** (§9).

## 8. Consensus realignment (pass 2)

For an address that is still missing after the V4 vote and has ≥ 2 pending reads (at most
`max_pending_per_address`, default 64):

1. each read is aligned and its windows found;
2. each read votes with weight 1 for every base it knows outside its windows;
3. each window is re-scored with the **leave-one-out** votes of the *other* reads (the model of §5), so a read never
   confirms itself. Only HIGH windows add votes, weighted by their posterior. Two rounds;
4. consensus base = weighted majority. A position is erased if it has no vote or its share is below the V4 threshold
   0.6, so one read cannot outvote two that agree;
5. the consensus frame goes to inner RS + CRC, and must decode to the group's own address (V4 rule). Otherwise each
   read is retried alone with the leave-one-out prior through T0–T2, and accepted only if all reads that verify agree.

## 9. Resource limits

| limit | default | why |
|---|---|---|
| `max_shift` | 1 | one placement per position (n candidates); ±2 is quadratic |
| `max_window_segments` | 2 | a window may cross one unreliable marker |
| `max_candidates` | 64 | > 2 × the largest single-segment window (24–32 nt) |
| `max_trials` | 1,024 | ≥ one full T2 pair of 24 × 24 placements plus T1 |
| `max_joint_windows` | 2 | T3 would be ≥ 24³ trials |
| `false_accept_budget` | 10⁻⁹ per read | §7 |
| `trial_batch` | 8,192 | frames decoded per RS call. Temporary memory ≤ (trial_batch + max_trials) × 350 B ≈ 3.2 MB |
| `max_read_nt` | 8,256 | longer reads are not searched (they cannot align) |
| consensus reads | `max_pending_per_address` (64) | V4 bound, reused |
| consensus rounds | 2 | fixed |

Per read the work is O(windows × candidates) for scoring plus at most `max_trials` RS decodes. A worst-case read
(ten indels in ten segments) stops at its bounds (test `test_worst_case_read_is_bounded_in_time`). A
240-heavy-read batch stays under 40 MB of traced allocations (`test_temporary_memory_is_bounded_by_trial_batch`).

## 10. Experiments (all SIMULATED)

Harness: `experiments/v5/phase3/` (see its README). Truth is used only after decoding. Strands are real V4 strands
(random payloads, default constraints). Each `results.json` stores the full configuration, seeds, input/read hashes
and a provenance block.

### 10.1 P3-EXP-01: exactly one indel (step 3, mission §12)

Every deletion position and every insertion position (random inserted base) of 6 strands per layout: 14,832 reads, no
other errors. Mean erased frame nt per read:

| layout | indel | cases | V4 | V5-T0 strict | V5-T0 ½ (ablation) | **V5-T1 (code)** | false |
|---|---|---|---|---|---|---|---|
| v4-balanced 24/3 | deletion | 1,878 | 25.5 | 23.2 | 12.9 | **3.7** | 0 |
| v4-balanced 24/3 | insertion | 1,878 | 31.9 | 23.9 | 13.2 | **0.0** | 0 |
| period 16/2 | deletion | 1,884 | 17.0 | 16.1 | 7.2 | **3.7** | 0 |
| period 16/2 | insertion | 1,884 | 20.5 | 19.4 | 7.9 | **0.0** | 0 |
| period 32/2 | deletion | 1,776 | 32.5 | 31.7 | 18.9 | **3.9** | 0 |
| period 32/2 | insertion | 1,776 | 42.3 | 40.1 | 18.3 | **0.0** | 0 |
| v4-indel 24/3, r = 20 | deletion | 1,878 | 25.6 | 23.2 | 13.0 | **3.7** | 0 |
| v4-indel 24/3, r = 20 | insertion | 1,878 | 32.3 | 23.9 | 13.0 | **0.0** | 0 |

* Every case decodes in every variant (one indel is within V4's budget). The question here is **localisation**.
  T1 recovers every read with only the byte of the missing base erased (deletion; about 4 nt, sometimes 0 when the
  missing base is a marker base), or nothing (insertion).
* **Localisation is independent of the marker period** (3.7–3.9 nt), whereas V4's cost grows with it (17 → 32 nt per
  deletion). This suggests longer marker periods (fewer markers, fewer nucleotides per byte) may become viable with
  smart recovery. It is not tested here (mission §21: format changes come later).
* Local evidence alone (T0 strict) gains only 2–8 nt. Informative qualities (an inserted base at Q12 among Q35)
  lift the true insertion placement to about 0.9 posterior: MEDIUM, below the strict 0.999. The ½-threshold ablation
  halves erasures, but in about 40 % of reads it keeps bytes that are wrong (they are unverified guesses), which is
  why the strict threshold is the default.
* In about 1 % of cases a chance marker match puts the window next to the true indel. The candidates then do not
  contain the truth, yet the read still decodes correctly through the code: no false acceptance.

### 10.2 P3-EXP-02: several indels per read (mission §13)

v4-balanced, 300 reads per pattern, production phases (T0 → T1 → T2). Reads decoded (V4 / V5) and mean erased nt per
true indel:

| pattern | V4 | V5 | false | erased/indel V4 | V5 | notes |
|---|---|---|---|---|---|---|
| 2 separated | 283 | 296 | 0 | 24.3 | 21.3 | T0 suffices |
| 3 separated | 126 | **280** | 0 | 22.3 | 15.2 | mostly T1 |
| 4 separated | 29 | **235** | 0 | 20.4 | 10.7 | mostly T2 |
| 5 separated | 7 | 50 | 0 | 19.1 | 16.2 | needs 3 joint placements: beyond the bound |
| 10 separated | 0 | 0 | 0 | 11.3 | 11.2 | beyond any bounded search |
| 3 separated + 2 substitutions | 55 | **262** | 0 | 21.8 | 13.8 | |
| adjacent (net shift ±2) | 300 | 300 | 0 | 14.8 | 14.8 | UNRECOVERABLE by design → V4 rule |
| insertion + deletion 3–10 nt apart (either order) | 300 | 300 | 0 | 0 | 0 | invisible to the aligner; RS fixes as substitutions |
| insertion and deletion in neighbouring segments | 250 | 250 | 0 | 0 | 0 | 50 fail in both (§12) |
| marker-adjacent (2) | 280 | 299 | 0 | 23.3 | 20.8 | |

**Where ambiguity starts.** No read was ever *ambiguous* (two verified frames disagreeing). The search does not
become ambiguous; it runs out of bound: from 5 separated indels, a decode needs ≥ 3 jointly placed windows, and
from about 6, the remaining erasures exceed r.

### 10.3 P3-EXP-04: per-strand accounting under the V4 channel (mission §11, §16)

4,096 strands, coverage 1, truth from the verified twin of the V4 simulator. Acceptance comes from the decoder's own
pass-1 function. Per true indel:

| channel (ins + del) | true indels | reads decoded V4 → V5 | false | erased nt/indel V4: mean · median · p95 · max | V5 localisation: mean · median · p95 · max |
|---|---|---|---|---|---|
| 0.05 % + 0.05 % | 1,283 | 4,077 → 4,091 | 0 | 28.3 · 24 · 48 · 48 | **4.2 · 4 · 24 · 24** |
| 0.1 % + 0.1 % | 2,577 | 4,025 → 4,077 | 0 | 27.7 · 24 · 48 · 48 | **6.1 · 4 · 24 · 24** |
| 0.2 % + 0.2 % | 5,119 | 3,757 → 4,009 | 0 | 27.3 · 24 · 48 · 48 | **8.6 · 4 · 24 · 48** |
| 0.3 % + 0.3 % | 7,688 | 3,316 → 3,892 | 0 | 27.0 · 24 · 48 · 48 | **9.9 · 4 · 24 · 48** |
| 0.5 % + 0.5 % | 12,746 | 2,341 → 3,374 | 0 | 26.2 · 24 · 48 · 48 | **10.9 · 4 · 24 · 40** |
| 1 % + 1 % | 25,149 | 669 → 1,563 | 0 | 25.0 · 24 · 48 · 48 | **10.2 · 8 · 24 · 24** |
| mixed L2 (0.5 % sub, 0.2 % + 0.2 %) | 5,119 | 3,539 → 3,957 | 0 | 27.2 · 24 · 48 · 48 | **8.4 · 4 · 24 · 64** |

Distributions are over indels in decoded reads. "V5 localisation" is what the local primitive needs (every read
with an indel through T1/T2). In production, V5 runs only where V4 fails, so its erasure on reads V4 already decodes
equals V4's (V5-production mean 28.0 → 15.5 nt across the rows). Phase 1's aggregate definition (Σ erased / Σ true
indels) gives 27.5 nt for V4 at 0.05 %, consistent with Phase 1's 26.1.

Per-indel classes (share of true indels):

| channel | V4: complete segment erasure | V5 localisation: fully recovered | partially | complete segment | unresolved | undetected |
|---|---|---|---|---|---|---|
| 0.05 % + 0.05 % | 93 % | **84 %** | 1 % | 10 % | 2 % | 4 % |
| 0.1 % + 0.1 % | 86 % | **70 %** | 2 % | 17 % | 3 % | 8 % |
| 0.2 % + 0.2 % | 71 % | **54 %** | 3 % | 25 % | 6 % | 13 % |
| 0.3 % + 0.3 % | 55 % | 43 % | 3 % | 27 % | 9 % | 17 % |
| 0.5 % + 0.5 % | 32 % | 28 % | 2 % | 25 % | 22 % | 23 % |
| 1 % + 1 % | 7 % | 9 % | 1 % | 8 % | 50 % | 32 % |

*Fully recovered* = only the bytes of genuinely missing bases erased. *Undetected* = the indel produced no window
(cancelling pairs, or a shift absorbed as marker mismatches), so RS sees substitutions. *Unresolved* = read not
decoded. `strand_records.jsonl.gz` holds the per-strand record (true indels and substitutions, windows, V4/V5
status, erasures before/after, RS errata and erasures) for all 20,279 strands that have an indel.

**RS load actually consumed** (mean erased bytes per decoded read): V4-decoded reads 7.9 → 16.2 bytes from 0.05 % to
1 %. Reads that only V5 decodes: 8.9–10.5 bytes, with 11.8–12.2 corrected symbols. Localised: 1.2 → 7.9 bytes. The
saved budget is what lets reads with 3–4 indels, or indels plus substitutions, decode.

### 10.4 P3-EXP-03: multi-read consensus (mission §14)

64 KiB archive, fixed coverage, 3 channel seeds per cell, 8 workers. Verified SUCCESS / 3 (fraction of groups
recovered):

| coverage | 0 % | 0.1 % + 0.1 % | 0.2 % + 0.2 % | 0.5 % + 0.5 % | 1 % + 1 % |
|---|---|---|---|---|---|
| 1 | 3 / 3 | 3 / 3 | 3 / 3 | 0 (0.04) / **0 (0.84)** | 0 (0.00) / 0 (0.01) |
| 2 | 3 / 3 | 3 / 3 | 3 / 3 | 0 (0.91) / **3** | 0 (0.02) / 0 (0.04) |
| 3 | 3 / 3 | 3 / 3 | 3 / 3 | 3 / 3 | 0 (0.04) / **0 (0.51)** |
| 5 | 3 / 3 | 3 / 3 | 3 / 3 | 3 / 3 | 0 (0.09) / **3** |
| 10 | 3 / 3 | 3 / 3 | 3 / 3 | 3 / 3 | 3 / 3 |

(V4 / V5 per cell; plain "3 / 3" where both succeed.)

* **Higher coverage does not always help V4.** At 1 % + 1 %, V4 fails at coverages 1–5. Its vote has too few
  unerased copies per position when every read erases two to four segments. V5 succeeds from coverage 5.
* Consensus realignment is active where it matters: at coverage 2 with 0.5 % it recovered 16 addresses; at
  coverage 3–5 with 1 %, 136–160 addresses. Most of the gain still comes from pass-1 single-read recovery.
* 0 false SUCCESS in 150 decodes.
* Decode time where V4 already succeeds rises with the rate: at coverage 10 with 1 % + 1 %, 2.0 s → 70.8 s (§11).

### 10.5 P3-EXP-05: V4 vs V5 on identical read files (mission §15)

256 KiB archive (v4-balanced, Cauchy RS 64 + 16, **9.79 nt per input byte for both**: same strands), 3 channel seeds,
1 worker, each decode in a fresh process:

| channel | V4 | V5 | groups recovered V4 → V5 | decode s V4 → V5 | peak RSS MB V4 → V5 |
|---|---|---|---|---|---|
| 0.2 % + 0.2 %, cov 1 | 3/3 | 3/3 | 1.0 → 1.0 | 0.8 → 3.6 | 93 → 118 |
| 0.3 % + 0.3 %, cov 1 | 0/3 | **3/3** | 0.68 → 1.0 | 0.9 → 8.8 | 93 → 122 |
| 0.4 % + 0.4 %, cov 1 | 0/3 | **3/3** | 0.03 → 1.0 | 1.0 → 16.6 | 94 → 123 |
| 0.5 % + 0.5 %, cov 1 | 0/3 | 0/3 | 0.0 → 0.74 | 1.1 → 24.6 | 94 → 125 |
| mixed L3 (0.5 % sub, 0.1 % + 0.1 %, 5 % dropout), cov 1 | 3/3 | 3/3 | 1.0 → 1.0 | 0.7 → 1.8 | 85 → 108 |
| mixed (0.3 % sub, 0.25 % + 0.25 %, 2 % dropout), cov 1 | 0/3 | **3/3** | 0.72 → 1.0 | 0.9 → 7.1 | 87 → 114 |
| harsh (0.5 % sub, 0.6 % + 0.6 %, 2 % dropout), Poisson cov 3 | 0/3 | 0/3 | 0.03 → 0.93 | 2.9 → 119.7 | 121 → 142 |
| EXP-0011 (0.2 % sub, 0.05 % + 0.05 %, 2 % dropout), Poisson cov 3 | 3/3 | 3/3 | 1.0 → 1.0 | 1.1 → 1.7 | 119 → 127 |
| **4 MiB EXP-0011, 1 worker** | SUCCESS | SUCCESS | | **10.8 → 19.7** | 141 → 152 |
| **4 MiB EXP-0011, 8 workers** | SUCCESS | SUCCESS | | **4.6 → 5.2** | main 169 → 227, largest worker 151 → 166 |

False SUCCESS: 0 / 54. DNA overhead is unchanged: the decoder alone changed.

## 11. Performance and memory

* **Where the time goes.** Smart work runs only on aligned reads the V4 rule could not decode. Per such read: window
  scoring (vectorised) plus up to `max_trials` inner-RS decodes at about 65,000 per second per core. Pass-1 read
  throughput at coverage 1 (EXP-04, 1 core): V4 27,000–80,000 reads/s; V5 36,000 reads/s at 0.05 %, 2,500 at 0.2 %,
  360 at 0.5 %, 160 at 1 %. Local-search time for 4,096 reads: 0.06 s at 0.05 % to 25 s at 1 %.
* **Why it is slow where V4 already succeeds at higher coverage.** Pass 1 cannot know that another copy of the same
  address will decode, so it searches every failing read. EXP-03 at coverage 10 with 1 % shows the waste
  (2 s → 71 s). The obvious optimisation, left for a later phase, is to defer the search to addresses still missing
  after V4 (pass 2). It is not done here, because pass 1 recovers reads whose header is damaged, which pass-2
  grouping cannot reach.
* **Memory.** Smart mode costs memory. The first EXP-05 run showed peak RSS rising from 92 MB to 126–189 MB
  (256 KiB, cov 1), because pass 1 planned every failing read of an 8,192-read batch at once. Pass 1 now plans and
  searches at most `PLAN_CHUNK` = 512 reads at a time, with identical outcomes. Final EXP-05 numbers (§10.5): V5
  needs 22–31 MB more than V4 on the 256 KiB coverage-1 decodes, 7 MB more on the EXP-0011 channel, and 11 MB more on
  the 4 MiB single-worker decode. The extra comes from plans, pass-2 realignment and larger pending records. Temporary trial memory is bounded by `trial_batch` (§9). Pending records in
  smart mode carry the raw read and qualities (+ 2 × 319 B each), spilled to disk as in V4.

## 12. Known failures (explicit)

1. **An insertion and a deletion in neighbouring segments** can be absorbed by the V4 DP as marker mismatches:
   ins + del costs 12, and so do three marker mismatches, and ties go to DIAG. No window is detected, the frame is
   shifted for one segment, and 17 % of such reads fail in both V4 and V5 (EXP-02). Changing the DP costs is a V4
   alignment change, out of Phase 3's scope.
2. **Cancelling indels inside one segment** are invisible to any marker-based method. RS treats them as
   substitutions (EXP-02: still decoded).
3. **Two indels of the same kind in one window** (net shift ±2) are UNRECOVERABLE by default; the V4 rule applies. With
   `max_shift = 2`, the 276 placements of a 24-nt window exceed 16 × the default candidate cap, and are refused.
4. **≥ 5 indel windows in one read** need 3 joint placements: beyond the bound (EXP-02: 50/300 at k = 5, 0/300 at
   k = 10).
5. **Coverage 1 above about 0.5 % + 0.5 %** still loses groups (0.74 recovered).
6. **Quality-only localisation** does not reach the strict threshold at the simulator's Q35/Q12 scale. It is used as a
   ranking only.
7. **Chance marker matches** (about 1 % of single-indel cases) move a window next to the true indel. Such reads still
   decode through the code, or fall back to V4; they are never accepted wrongly.
8. **Throughput** drops sharply at high indel rates (§11).

## 13. Correctness, integrity and security tests

* `tests/v5/test_indel_path.py` (19): native = reference path on random reads (5 layouts × 3 bands, with qualities);
  projection = V4; path semantics; ABI 2; missing-output rejection.
* `tests/v5/test_indel_recovery.py` (33): properties (no indel → no change; uncertain recovery never keeps an
  unverified symbol); single indels at every position class; truth among the candidates; quality concentration;
  multi-indel beyond V4; ±2 windows bounded; `max_trials` / budget / zero budget; batching and batch-composition
  invariance; worst-case time; temporary-memory bound; malformed reads (empty, all-N, out-of-alphabet, ± band,
  20,000 nt); inconsistent path; config validation; false-acceptance formula.
* `tests/v5/test_indel_false_success.py` (13): every adversarial case of mission §20: two hypotheses verifying to
  frames one base apart (must be *ambiguous*); heavily damaged reads; random frames with valid markers; misleading
  qualities; indels beside corrupted markers; a substituted marker motif inside a segment; corrupted majority in
  consensus; a strong foreign read vs two weak correct reads; wrong expected address; consensus fuzz.
* **Sanitizers** (the C kernel gained `vnx_align_batch_path`; kernel source SHA-256 `7005c242…`):
  `benchmarks/v5/native_alignment/sanitizers.sh` is clean. The whole `tests/v5` suite (including the path tests) passes
  under gcc ASan + UBSan and clang UBSan (trap). 2 × 100,000 fuzzed reads give 0 mismatches. The ASan canary fires.
  The 65 Phase 3 recovery, path and adversarial tests also pass under ASan + UBSan.
* `tests/v5/test_indel_decoder.py` (12): default = V4; smart = V4 bytes where V4 succeeds; smart beyond the
  V4 threshold with a verified file SHA-256; 1 vs 3 workers identical; quality-aware FASTQ; consensus path; reverse
  complements; option validation; CLI flag; spill format only extended in smart mode.

## 14. Limitations

* **SIMULATED only.** Every channel result comes from the V4 simulator or harness-injected edits. Nothing here says
  how real synthesis or sequencing errors (context-dependent, bursty, platform-specific qualities) behave.
* One machine (Xeon Gold 6240, 8 CPUs); timings will differ elsewhere.
* The likelihood model is deliberately simple (position-uniform placements, one per-base error prior). It ranks
  hypotheses; it is not a calibrated channel model.
* The false-acceptance budget uses the random-word model of RS mis-decoding, the standard approximation, not a proof
  for adversarial inputs. The container SHA-256 remains the final guard.
* Phase 3 does not change the strand format. The marker-period observation of §10.1 is a hypothesis for a later
  phase.

## 15. Reproducibility

Commands: `experiments/v5/phase3/README.md`. Seeds: EXP-01 3101, EXP-02 3201, EXP-03 input 3301 and channel
33000 + 100·k + coverage, EXP-04 strands 3401 and channel 3402, EXP-05 input 3501 and channel 35000 + 10·k, 4 MiB part
seed 42 and channel 1011 (Phase 1/2 workload). Each results file records the input/read SHA-256 where applicable, the
recovery configuration, and the provenance (V4 control `v4.0.0` = `a358ae8`, commit under test, CPU, Python 3.12.3,
NumPy 2.5.3, gcc 13.3.0). Results were produced from the working tree immediately before the Phase 3 commit
(provenance `b19cb19-dirty`). EXP-01, 02, 04 and 05 ran on the final code. EXP-03 ran before three changes that do not
affect outcomes (a set lookup in pass 2, pass-1 planning in chunks of 512 reads, stage timings kept by the harness):
same per-read decisions, different speed and memory.

