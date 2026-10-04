# VNX-DNA V5 — Phase 4: soft-decision inner decoding

All channel results are **SIMULATED** (V4 channel simulator, harness-injected errors, or a synthetic graded quality
model that is labelled as such). No DNA was synthesised or sequenced. No probability in this report is claimed to be
calibrated against a real sequencer.

## 1. Summary

**Result (all SIMULATED).** V5 Phase 4 adds a working quality/posterior pathway and bounded, fail-closed soft-decision
inner decoding as an opt-in mode (`soft_decoding` / `--soft-decoding {off,erasure,chase,auto}`, default `off`). The
inner RS code is not changed: soft evidence only chooses which received words the unchanged V4 verifier sees.

* **Beyond the RS bound only with informative evidence.** The hard decoder stays exactly RS(70, 54): all frames with
  2e + f ≤ 16 decode, none with 17. With informative posteriors, soft GMD recovers 500/500 frames at 2e + f = 17, 18
  and 20. Blind search adds a few; misleading evidence defeats it (its few recoveries come from blind Chase search,
  EXP-04/05).
* **Reads at coverage 1** (4,096 strands): with indels + substitutions at 1 % + 1 % and informative qualities, Phase 3 +
  soft `auto` + `min_quality` 20 decodes **3,156** reads, against 3,055 for the best hard configuration, 2,778 for
  Phase 3 alone and 1,499 for V4. With substitutions only, soft `auto` matches or slightly beats the best hard quality
  threshold (+1.1 % at 3 %), and beats a mistuned threshold by up to 18 %. Without informative qualities, blind
  Chase search gives +3.4 % at 3 % substitutions.
* **Archives:** on a substitution-heavy channel (4 % substitutions + 0.25 % + 0.25 % indels), soft consensus makes the
  archive decodable at coverage 5 (V4 0/2, Phase 3 0/2, soft 2/2) and at coverage 10 (0/2, 1/2, 2/2). Elsewhere the
  archive-level gain is small (one extra SUCCESS in EXP-07).
* **Integrity:** **0 false acceptances, 0 ambiguous accepts, 0 wrong-but-verified frames** among every accepted frame
  checked against truth (over 1.5 million reads and frames, including adversarial constructions with looser limits).
  **0 false SUCCESS** in 186 archive decodes, every SUCCESS verified by the output's SHA-256. The CRC-32 and metadata
  checks are load-bearing: they rejected 1,871 RS-valid wrong frames.
* **Cost:** with soft decoding off, none measurable (within the run-to-run spread of Phase 3 on the 4 MiB workload).
  With it on, +13–27 % on a low-noise workload and up to 2.5× on substitution-heavy channels at coverage 1, where
  thousands of reads reach the soft path; `min_quality` cuts that cost. Peak memory: up to +60 MB on 1 worker. Phase 3's own cost on noisy channels (25–35× V4)
  remains the dominant runtime problem (§13, §20).
* Phase 3 reproduced exactly from a clean `3633dfb` worktree (§3). The Phase 3 term "undetected" is now defined
  precisely (§4).
* V4 stays the default. The V4 format, codes, cryptography and integrity checks are unchanged, and the full suite passes
  (916 existing + 81 new = 997 tests).

## 2. Exact commit and tree state

* Branch `feature/vnx-dna-v5`, base commit `3633dfb` (Phase 3), which sits on `v4.0.0` = `a358ae8`. Phase 4 is the
  commit that adds this report. It is local and not pushed.
* Changed V4 files (hooks only, no format or code change): `src/vnxdna/v4/decoder.py` (soft path in pass 1 and soft
  consensus in pass 2, both behind `soft_decoding != "off"`), `config.py` (the `soft_decoding` key), `cli.py`
  (`--soft-decoding`).
* New: `src/vnxdna/v5/soft/` (`symbols.py`, `decoder.py`, `frames.py`), four test files (81 tests),
  `experiments/v5/phase4/` (scripts, results, logs, curves, README), `experiments/v5/phase3/repro-3633dfb/` and
  `repro_compare.py` (§3).
* Results were produced from the working tree on top of `3633dfb` (provenance `worktree_dirty = true`); see §19.
* **Validated on the committed code (Gate A):** P4-EXP-01, 03, 04, 06, 07 and 08 were re-run on a clean worktree of
  `c5b68d6` (`worktree_dirty = false`, native kernel rebuilt from that commit). Every deterministic field is identical
  to the published results, and the full suite passes 997 / 997 there. See `docs/V5_PHASE4_PROVENANCE_VALIDATION.md`
  and `experiments/v5/phase4/committed-c5b68d6/`. The numbers in this report are unchanged.

## 3. Phase 3 reproduction (Phase 4A)

The Phase 3 commit `3633dfb` was checked out into a clean, detached worktree. It got its own in-place native build
(gitignored, so the tree stayed clean), and imports were pinned to that tree. Then:

| check | result |
|---|---|
| V4 tag | `v4.0.0` = tag object `c587fc5` → commit `a358ae8`, identical to `origin` |
| full suite at `3633dfb` | **916 / 916 pass** (7 min 53 s) |
| Phase 3 acceptance tests | **77 / 77 pass** |
| P3-EXP-01 single indel | **identical** in every deterministic field |
| P3-EXP-02 multi-indel | **identical** |
| P3-EXP-04 accounting (incl. 20,279 strand records) | **identical** |
| P3-EXP-05 V4 vs V5 archives | **identical** |
| P3-EXP-03 consensus (re-run) | identical in every outcome, count, hash and class. The one exception: 35 values of `false_accept_bound`, a diagnostic sum, differ by ≤ 3·10⁻¹⁵ relative. The Phase 3 report noted that EXP-03 ran before pass-1 planning was split into chunks of 512 reads, which changes the floating-point summation order. **No material difference.** |

Each run's provenance records `commit_under_test = 3633dfb…`, `worktree_dirty = false`, Intel Xeon Gold 6240,
Python 3.12.3, NumPy 2.5.3 and gcc 13.3.0, plus its configuration SHA-256. Between runs, each experiment's results were
moved out and tracked files restored, so every run saw a clean tree. Everything is stored, without overwriting the
original Phase 3 results, in `experiments/v5/phase3/repro-3633dfb/`: results, stdout, `/usr/bin/time` (wall, CPU,
peak RSS), `log.txt`, and `comparison.json` (produced by `repro_compare.py`). The original Phase 3 results recorded
`b19cb19-dirty` provenance: they were produced from the working tree just before the commit. The reproduction
closes that gap.

## 4. Phase 3 terminology audit

P3-EXP-04 classifies each **true** (simulated) indel of an *aligned* read:

| class | exact meaning (as computed in `exp04_accounting.py`) |
|---|---|
| fully recovered | the read was decoded and the indel's window erased at most 4 nt per true deletion inside it (only the bytes of genuinely missing bases) |
| partially recovered | decoded; the window erased fewer nt than V4 erased there, but more than the full-recovery bound |
| complete segment erasure | decoded; the window erased at least what V4 erased (the whole segment or more) |
| unresolved | the indel lies in a detected window, but the read was **not** decoded |
| undetected | **no detected indel window contains the indel's true position.** This includes indel pairs that cancel inside one segment, shifts the aligner absorbed as marker mismatches, and indels the aligner attributed to a neighbouring window. It says nothing about whether the read decoded: undetected indels in failed reads are counted here, not as unresolved |

**"Undetected" does not mean "no physical indel existed".** It means the marker/alignment layer did not localise the
indel. Its effect (shifted or substituted bases) reached the inner code as ordinary symbol errors, or was lost with the
read. `tests/v5/test_phase3_terminology.py` pins this down: a cancelling insertion/deletion pair is classified
undetected, both physical indels are still counted, and RS still decodes the read. A second test checks that a
detected shift whose window does not contain the indel's true position counts as undetected.

**Where "undetected" comes from** (recomputed for this audit on the same reads, seeds 3401/3402):

| channel | true indels in aligned reads | undetected | inside a window with no net shift (cancelling pair, or a shift the aligner explained as marker mismatches) | in a read with no indel window at all | at a marker position the path still treats as a solid marker |
|---|---|---|---|---|---|
| 0.1 % + 0.1 % | 2,577 | 213 (8.3 %) | 59 (28 %) | 116 (54 %) | 38 (18 %) |
| 1 % + 1 % | 25,149 | 8,106 (32.2 %) | 7,585 (94 %) | 180 (2 %) | 341 (4 %) |

Every read in the "no indel window" column carries both insertions and deletions (2 or 4 indels) that cancel or are
absorbed. **No single-indel read is ever missed entirely**, but in 24 of 1,364 single-indel reads at 0.1 % + 0.1 %
(1.8 %), and 2 of 48 at 1 % + 1 %, the shift was detected in the *neighbouring* window: an indel at or beside a marker
that the path placed on the other side of it. The class rule counts those as undetected, because no window covers
the true position. At 1 % + 1 % a 313-nt read carries about 6 indels, so
most undetected indels are pairs whose net shift cancels inside a window. Their damage reaches RS as substitutions,
or is lost with a failed read; these are not harmless indels. P3-EXP-04 also counts only reads that aligned within the
band (|net drift| ≤ 6). At 1 % + 1 %, 39 of 4,096 reads (374 of 25,523 true indels, 1.5 %) drifted further and are
excluded from the totals; at 0.5 % + 0.5 %, 3 reads (0.2 %). They fail in V4 and V5 alike. The historical numbers
are left exactly as published. This section only states precisely what they mean.

## 5. Soft-symbol representation (Phase 4B)

Module `vnxdna.v5.soft.symbols`.

**Per base.** An (n, 4) float64 array of **normalised log-probabilities**, L[k, b] = log P(base_k = b), with
log-sum-exp_b L[k, b] = 0. The full distribution is kept, not "base + confidence": 0.51 / 0.49 / 0 / 0 and
0.51 / 0.163 / 0.163 / 0.163 are different arrays with different entropies (test `test_distribution_shape_is_preserved`).
−∞ (probability exactly 0) is allowed; NaN and +∞ never are.

**Conversion paths** (none calibrated against a real instrument):

| source | P(true base = b) |
|---|---|
| read base x with Phred Q | 1 − ε if b = x, ε/3 otherwise; ε = 10^(−Q/10) clipped to [10⁻⁶, 0.75] |
| read base without quality (FASTA) | the same with ε = `default_error` = 0.005, an explicit assumption |
| N call, erased base, unknown | ¼ each |
| Phase 3 window posterior π | π(b)(1 − ε) + (1 − π(b)) ε/3: an unverified placement stays a distribution, and a RECOVERED base never reaches probability 1 |
| Phase 3 UNRECOVERABLE window | ¼ each over its segments |
| several reads (consensus) | Σ over reads of log P(observation \| b) + uniform prior, normalised |

Verified information is kept separate. A frame that the code verified (Phase 3 `CODE`) is a *result* and never enters
a posterior as evidence.

**Per byte.** A byte is four bases b0 b1 b2 b3 (most significant first). Given the evidence, bases are treated as
independent, so log P(byte = v) = Σ_i L[4j + i, v_i]. This is the exact Cartesian product, kept in **factorised
form** (16 numbers per byte). Three consequences, all exact:

* hard byte = per-base argmax;
* byte reliability = log P(best) − log P(second best) = min over the four bases of their top-two log-gap, because
  the runner-up byte differs from the best in exactly one base;
* the top-K bytes come from a K-best merge over the four sorted base lists (a heap, O(K log K)).

The full 256-value posterior is available (`byte_log_posterior`) and tests check the factorised results against it
(`test_byte_factorisation_is_exact`). Cost per 280-nt frame: 143 KB and 105 µs for the full table, against 9 KB and
40 µs factorised. Decoding uses the factorised form only.

**Independence** is an assumption. It is reasonable for substitutions in the V4 simulator. It is not exact inside
indel windows, where the Phase 3 candidates are jointly correlated; their per-position marginals lose that joint
structure (a limitation, §18).

## 6. Algorithm selected (Phase 4D/4E)

Module `vnxdna.v5.soft.decoder`. The inner code is **not** rewritten. Soft information only chooses *which received
words* the unchanged V4 decoder (`decode_frames`: RS errors + erasures → CRC-32 → version/kind checks) sees:

* **`erasure` = GMD** (Forney's generalised minimum distance): the hard word with the s least reliable bytes erased,
  s = s0, s0 + 2, …, r, where s0 = bytes already unknown. At most r/2 + 1 = 9 trials.
* **`chase` = Chase-II with top-m values**: the t = 4 least reliable known bytes each take one of their top m = 2
  values (the hard value plus the runner-up). At most m^t = 16 patterns, with at most `max_bytes_per_trial` changed.
* **`auto`**: GMD first, then Chase only if no GMD trial verifies.

**Acceptance.** At least one trial verifies, *and every verified trial of the deciding mode decodes to the same frame*.
Two different verified frames → `ambiguous` → rejected. **Likelihood never breaks a tie between verified frames.** It
is used to order trials and to report the accepted trial's rank. With an expected address (pass 2), a verified frame
with any other address is discarded. The Phase 3 per-read false-acceptance budget applies to every trial:
Σ P(wrong frame passes RS + CRC-32) ≤ 10⁻⁹ under the random-word model.

**Where it runs.** In pass 1, on reads that every hard path failed, including exact-length reads whose markers all
match (V4 does not re-align those; they are the substitution-heavy reads). In pass 2, as soft consensus for addresses
still missing after V4 and Phase 3. Default `soft_decoding = "off"`; CLI `--soft-decoding {off,erasure,chase,auto}`.

**What this does and does not claim.** Every accepted frame is an RS codeword within 2e + f ≤ r of *one trial word*,
and that trial word was chosen by soft evidence. This is "bounded soft-information search recovered additional
verified codewords in simulated channels". It is **not** correction beyond the RS bound. P4-EXP-04 shows the hard
decoder is exactly as before (2e + f ≤ 16 decodes, 17 does not), and that the extra recoveries happen only when the
soft evidence points at the right bytes.

## 7. Algorithms considered and rejected

* **A. Symbol reliability + top-K byte candidates.** Implemented as the `chase_values` parameter (m = 2–4). With DNA
  substitutions the true base is, a priori, any of the three other bases. The runner-up byte is therefore the truth
  only about ⅓ of the time, unless qualities single it out. EXP-05 shows Chase helps essentially only when the truth
  *is* the runner-up. Larger m grows the trials as m^t. m = 2 is the default; `auto` keeps it as a fallback.
* **B. Reliability-guided erasure (GMD).** Selected (`erasure`, first stage of `auto`). It needs at most 9 trials,
  and it is the most effective mode in every experiment.
* **C. Chase-style test patterns.** Selected as the second stage of `auto`. It is the only mode with any gain when
  qualities carry no information (blind search, about 10 % at 2e + f = 17–18 in EXP-04).
* **D. Algebraic soft-decision RS (Koetter–Vardy interpolation).** Not implemented. A–C showed no limitation that
  KV would remove *on these channels*: with informative evidence, GMD already recovers all frames up to 2e + f = 20 in
  EXP-04. Without informative evidence, no algebraic soft decoder can create information that the posteriors do not
  contain. KV would also replace the verified V4 RS implementation with a much larger new one (interpolation,
  factorisation), against the mission's preference for simple, verified code.

## 8. Candidate-generation bounds

| bound | default | effect |
|---|---|---|
| `chase_bytes` (max_soft_symbols) | 4 | bytes perturbed by Chase |
| `chase_values` (max candidates per byte) | 2 | values per perturbed byte |
| `max_bytes_per_trial` | 4 | bytes changed in one Chase trial |
| `max_soft_trials` | 64 per read | all modes together (GMD ≤ 9, Chase ≤ 16 by default) |
| `false_accept_budget` (soft budget) | 10⁻⁹ per read | Σ P(false pass) over trials |
| batch | 512 reads per soft call | bounded memory in pass 1 |

The worst case per read is 64 inner-RS decodes. Configuration validation rejects out-of-range values, and a test
checks that 8 × 4 = 65,536 possible patterns are cut to the trial limit.

## 9. Probability model

Section 5 gives the conversions. Two further points. Combining reads multiplies independent likelihoods (sums of logs),
each read contributing only its own evidence, so a read never confirms itself. Leave-one-out, which Phase 3 needs
because it re-scores windows with other reads' votes, is automatic in the product. All arithmetic is in the log domain
(log-sum-exp for normalisation), deterministic, and needs no random seed (ties break by position and base code).

## 10. Numerical stability

Tests (`tests/v5/test_soft_symbols.py`, `test_soft_decoder.py`) cover: underflow (log-probabilities down to −10⁴,
denormals), overflow (logits of ±10³⁰⁰ normalise to exact one-hot), NaN, ±∞, negative probabilities, unnormalised
rows, all-zero rows, one-hot rows (−∞ entries), nearly identical probabilities (deterministic tie-break), wrong dtype,
wrong shape, length mismatches between posteriors and reads, and candidate explosion. Every malformed soft input makes
the frame `invalid`: no trial runs, nothing is accepted, and the read keeps its hard result. NaN can never become
confidence: validation rejects it before any arithmetic.

## 11. Adversarial testing

All 17 items of mission §14 have tests (`tests/v5/test_soft_decoder.py`, numbered as in the mission), plus a random
fuzz. P4-EXP-06 repeats the constructions at scale with counts (§12). Two of the cases cannot be built from valid
codewords: two RS- and CRC-valid candidates for one frame (the RS distance of 17 makes that astronomically unlikely
by chance). Those are injected by replacing the verifier's output, and must come out `ambiguous` and rejected.

## 12. Results

All counts are **SIMULATED** and come from `experiments/v5/phase4/P4-EXP-*/results.json`, each with its configuration
(and SHA-256) and provenance. Read-level and archive experiments compare decoders on **identical** read files, never on
different channel realisations. "Verified correct" means the decoder accepted the frame *and* the harness confirmed
afterwards, against the simulator's truth, that fields and payload are exact. The decoder never sees the truth.

Decoder configurations:

| name | meaning |
|---|---|
| V4 | V4 decoder, qualities unused |
| V4+minQ, V4+minQ20 | V4 with `min_quality` 13 or 20: bases below the threshold become erasures (V4's existing hard use of qualities) |
| V5-hard | Phase 3 smart indel recovery, hard decisions |
| V5-hard+minQ20 | Phase 3 + `min_quality` 20 |
| V5-soft-erasure / -chase / -auto | Phase 3 + Phase 4 soft decoding in that mode |
| V5-soft-auto+minQ20 (archive: +minQ, Q13) | Phase 3 + Phase 4 `auto` + `min_quality` |

Quality models: the V4 simulator's **two-level** model (Q35 on a correct base; Q12 on an erroneous base with probability
`informative` ∈ {0, 0.5, 1}, otherwise Q35), and a **synthetic graded** model (correct bases Q ~ U{20..40}, erroneous bases
Q ~ U{4..24}; the ranges overlap, so no single threshold separates errors from correct bases). The graded model is
labelled synthetic; it is not fitted to any instrument.

### 12.1 P4-EXP-01: substitution sweep (4,096 strands, coverage 1, reads decoded verified-correct)

| substitutions | qualities | V4 | V4+minQ | V4+minQ20 | V5-hard | V5-hard+minQ20 | soft-erasure | soft-chase | soft-auto | soft-auto+minQ20 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.1–0.5 % | all four | 4,096 | 4,096 | 4,096 | 4,096 | 4,096 | 4,096 | 4,096 | 4,096 | 4,096 |
| 1 % | informative 0 | 4,088 | 4,088 | 4,088 | 4,088 | 4,088 | 4,088 | 4,088 | 4,088 | 4,088 |
| 1 % | informative 0.5 | 4,088 | 4,095 | 4,095 | 4,088 | 4,095 | 4,095 | 4,094 | 4,095 | 4,095 |
| 1 % | graded (synthetic) | 4,088 | 4,094 | 4,096 | 4,088 | 4,096 | 4,096 | 4,095 | 4,096 | 4,096 |
| 2 % | informative 0 | 3,689 | 3,689 | 3,689 | 3,689 | 3,689 | 3,694 | 3,717 | **3,718** | **3,718** |
| 2 % | informative 0.5 | 3,689 | 4,024 | 4,024 | 3,689 | 4,024 | 4,026 | 3,897 | **4,036** | **4,036** |
| 2 % | informative 1 | 3,689 | 4,096 | 4,096 | 3,689 | 4,096 | 4,096 | 3,919 | 4,096 | 4,096 |
| 2 % | graded (synthetic) | 3,689 | 4,007 | 4,092 | 3,689 | 4,092 | 4,092 | 3,901 | **4,093** | **4,093** |
| 3 % | informative 0 | 2,410 | 2,410 | 2,410 | 2,410 | 2,410 | 2,417 | 2,492 | **2,493** | **2,493** |
| 3 % | informative 0.5 | 2,410 | 3,505 | 3,505 | 2,410 | 3,505 | 3,524 | 2,941 | **3,544** | **3,544** |
| 3 % | informative 1 | 2,410 | 4,087 | 4,087 | 2,410 | 4,087 | 4,087 | 3,010 | 4,087 | 4,087 |
| 3 % | graded (synthetic) | 2,410 | 3,342 | 3,946 | 2,410 | 3,946 | 3,949 | 3,011 | **3,950** | **3,950** |

0 false acceptances in all 216 decodes. Reading the table:

* The honest baseline is **not** V4 but V4 with a well-chosen `min_quality`. V4 already turns low-quality bases into
  erasures, and with informative qualities that alone recovers most of what soft decoding recovers.
* Against the **best** hard threshold, soft `auto` adds a little: +1.1 % of reads at 3 % / informative 0.5
  (3,544 vs 3,505), +0.3 % at 2 % / informative 0.5, +0.1 % with graded qualities. It never loses reads.
* Against a **mistuned** threshold it adds a lot: with graded qualities at 3 %, Q13 recovers 3,342 and `auto`
  3,950 (+18 %). Soft decoding needs no threshold.
* With **uninformative** qualities (every base Q35), no hard rule can use qualities; blind Chase search adds +3.4 %
  at 3 % (2,410 → 2,493) and +0.8 % at 2 %.
* Chase alone is worse than GMD whenever qualities carry information, because the runner-up base is the truth only
  about one third of the time (§7 A). `auto` (GMD, then Chase) is best or tied-best everywhere.

### 12.2 P4-EXP-02: k substitutions per frame (500 real frames per cell)

All decoders recover 500/500 up to k = 8 base substitutions per 280-nt frame (at most 8 byte errors, 2e ≤ 16).
Beyond the RS bound:

| k | qualities | hard | hard+minQ13 | hard+minQ20 | soft-erasure | soft-chase | soft-auto |
|---|---|---|---|---|---|---|---|
| 10 | uninformative | 31 | 31 | 31 | 35 | 55 | **56** |
| 10 | two-level | 29 | 500 | 500 | 500 | 270 | 500 |
| 10 | graded (synthetic) | 41 | 406 | 500 | 500 | 294 | 500 |
| 12 | uninformative | 0 | 0 | 0 | 0 | 0 | 0 |
| 12 | two-level | 0 | 500 | 500 | 500 | 44 | 500 |
| 12 | graded (synthetic) | 4 | 156 | 470 | **472** | 38 | **472** |

(The hard decoder recovers some k = 10 frames because two substitutions can fall in one byte.) 0 false, 0 ambiguous,
0 wrong-but-verified frames in all 10,500 frame decodes per mode (21 cells × 500).

### 12.3 P4-EXP-03: indel + substitution sweep (4,096 strands, coverage 1, verified-correct reads)

| indels (ins + del) + substitutions | qualities | V4 | V4+minQ20 | V5-hard | V5-hard+minQ20 | soft-auto | soft-auto+minQ20 |
|---|---|---|---|---|---|---|---|
| 0.05 % + 0.05 % + 0.1 % | any | 4,089 | 4,089 | 4,095 | 4,095 | 4,095 | 4,095 |
| 0.1 % + 0.1 % + 0.2 % | informative 0 | 3,991 | 3,991 | 4,074 | 4,074 | 4,078 | 4,078 |
| 0.1 % + 0.1 % + 0.2 % | informative 1 | 3,991 | 4,006 | 4,074 | 4,076 | 4,080 | 4,080 |
| 0.25 % + 0.25 % + 0.5 % | informative 0 | 3,215 | 3,215 | 3,860 | 3,860 | 3,877 | 3,877 |
| 0.25 % + 0.25 % + 0.5 % | informative 0.5 | 3,215 | 3,279 | 3,868 | 3,896 | 3,897 | **3,915** |
| 0.25 % + 0.25 % + 0.5 % | informative 1 | 3,215 | 3,379 | 3,870 | 3,929 | 3,912 | **3,946** |
| 0.25 % + 0.25 % + 0.5 % | graded (synthetic) | 3,215 | 3,336 | 3,867 | 3,911 | 3,900 | **3,926** |
| 0.5 % + 0.5 % + 1 % | informative 0 | 1,499 | 1,499 | 2,743 | 2,743 | 2,808 | 2,808 |
| 0.5 % + 0.5 % + 1 % | informative 0.5 | 1,499 | 1,638 | 2,761 | 2,897 | 2,881 | **2,984** |
| 0.5 % + 0.5 % + 1 % | informative 1 | 1,499 | 1,842 | 2,778 | 3,055 | 2,977 | **3,156** |
| 0.5 % + 0.5 % + 1 % | graded (synthetic) | 1,499 | 1,749 | 2,772 | 2,976 | 2,916 | **3,065** |

(Full 16 × 9 table, including V4+minQ13, erasure and Chase, in `results.json`.) 0 false acceptances in all 144 decodes.

* With indels, **Phase 3 is the large step** (1,499 → 2,743 reads at 1 % + 1 %). Soft decoding adds +2.4 % on top
  with uninformative qualities (2,808).
* A finding from the first run of this sweep: Phase 3 + `min_quality` 20 beat Phase 3 + soft (3,055 vs 2,977 at
  1 % + 1 % / informative 1). The soft path starts only after every hard path has failed, and Phase 3's hard path is
  stronger when low-quality bases are erased first. That motivated the **combined configuration** (soft `auto` +
  `min_quality` 20), added and re-run here. It is the best configuration at every point with informative or graded
  qualities: **3,156 vs 3,055** (+3.3 %) at 1 % + 1 % / informative 1, 2,984 vs 2,897 (+3.0 %) at informative 0.5,
  3,065 vs 2,976 (+3.0 %) with graded qualities. With uninformative qualities `min_quality` changes nothing and it
  equals soft `auto`.

### 12.4 P4-EXP-04: the RS boundary (500 frames per cell; e byte errors, f erased bytes; r = 16)

| 2e + f | posteriors | hard | soft-erasure | soft-chase | soft-auto |
|---|---|---|---|---|---|
| 15, 16 (12 (e, f) splits) | uninformative / informative / misleading | 500 | 500 | 500 | 500 |
| 17 (6 splits) | uninformative | **0** | 1–7 | 20–66 | 22–67 |
| 17 | informative | **0** | 500 | 345–412 | 500 |
| 17 | misleading | **0** | 0 | 0–23 | 0–23 |
| 18 (6 splits) | uninformative | **0** | 3–11 | 42–81 | 42–83 |
| 18 | informative | **0** | 500 | 386–422 | 500 |
| 18 | misleading | **0** | 0 | 0 | 0 |
| 20 (6 splits) | uninformative | **0** | 0 | 0–3 | 0–3 |
| 20 | informative | **0** | 500 | 192–214 | 500 |
| 20 | misleading | **0** | 0 | 0 | 0 |

The hard decoder behaves exactly as RS(70, 54) must: every frame with 2e + f ≤ 16 decodes, none with 2e + f ≥ 17
does. Soft decoding recovers frames beyond the bound **only** when the posteriors point at the erroneous bytes
(informative), recovers a few by blind search (uninformative), and essentially none when the evidence is misleading.
The few misleading-case recoveries at 2e + f = 17 with e = 3 come from Chase blind search, not from the misleading
evidence. Every recovered frame is within 2e + f ≤ 16 of one trial word (§6). 0 false, 0 ambiguous, 0 wrong-but-verified.

### 12.5 P4-EXP-05: posterior shapes (500 frames per cell; e erroneous bytes, controlled posterior on each erroneous base)

| e | shape on the erroneous base (P(truth)) | hard | soft-erasure | soft-chase | soft-auto |
|---|---|---|---|---|---|
| 6 | any | 500 | 500 | 500 | 500 |
| 9 | 0.99 / 0.0033 × 3 (0.0033), confidently wrong | 0 | 4 | 31 | 34 |
| 9 | 0.90 / 0.033 × 3 (0.033) | 0 | 500 | 396 | 500 |
| 9 | 0.70 / 0.10 × 3 (0.10) | 0 | 500 | 398 | 500 |
| 9 | 0.55 / 0.45 / 0 / 0 (0.45, truth is runner-up) | 0 | 500 | 500 | 500 |
| 9 | 0.55 / 0.45 / 0 / 0 (0, truth excluded) | 0 | 500 | **0** | 500 |
| 10 | 0.99 / … (confidently wrong) | 0 | 0 | 1 | 1 |
| 10 | 0.90 / … and 0.70 / … | 0 | 500 | 191–208 | 500 |
| 10 | 0.51 / 0.49 / 0 / 0 (0.49) | 0 | 500 | 500 | 500 |
| 12 | 0.90 / … and 0.70 / … | 0 | 500 | 9–10 | 500 |
| 12 | 0.55 / 0.45 and 0.51 / 0.49 (truth runner-up) | 0 | 500 | 500 | 500 |
| 12 | 0.55 / 0.45 / 0 / 0 (truth excluded) | 0 | 500 | 0 | 500 |

GMD only needs to know *where* the evidence is weak. Chase needs the truth to be the runner-up; when the posterior
excludes the truth, Chase recovers nothing and GMD recovers everything. A confidently wrong posterior (0.99 on the
wrong base) defeats both, as it should. 0 false, 0 ambiguous, 0 wrong-but-verified.

### 12.6 P4-EXP-06: adversarial ambiguity (500 cases each)

This experiment deliberately uses a **looser** soft configuration than the default (`max_soft_trials` 256,
`chase_bytes` 6, so up to 2⁶ = 64 Chase patterns, and a per-read budget of 10⁻⁶ instead of 10⁻⁹). That gives the
adversary more trials than production would allow. It is why the mean trial count reaches 65–66.

| construction | verified correct | false | ambiguous | rejected | wrong-but-verified | trials | RS-valid trials | of which CRC/checks rejected |
|---|---|---|---|---|---|---|---|---|
| A misleading qualities, e = 6 | 500 | 0 | 0 | 0 | 0 | 4,500 | 2,356 | 624 |
| A misleading qualities, e = 9 | 6 | 0 | 0 | 494 | 0 | 32,658 | 637 | 631 |
| A misleading qualities, e = 12 | 0 | 0 | 0 | 500 | 0 | 33,000 | 616 | 616 |
| B wrong candidate more likely, e = 9 | 500 | 0 | 0 | 0 | 0 | 4,500 | 4,000 | 0 |
| B wrong candidate more likely, e = 12 | 500 | 0 | 0 | 0 | 0 | 4,500 | 2,500 | 0 |
| C near-identical posteriors (no information) | 0 | 0 | 0 | 500 | 0 | | | |
| D random frames, confident qualities | 0 | 0 | 0 | 500 | 0 | | | |
| E decodable frames, wrong expected address | 0 | 0 | 0 | 500 | 0 | | | |
| F consensus favours another strand | 0 | 0 | 0 | 500 | 0 | | | |

(Blank cells: see `results.json`.) The CRC-32 and metadata checks are **load-bearing**. Under misleading qualities,
1,871 trial words were valid RS codewords of the *wrong* frame (RS miscorrections), and every one was rejected by the
CRC or the version/kind/address checks. RS alone would have accepted them. No construction produced a false
acceptance or a wrong-but-verified frame.

### 12.7 P4-EXP-07: whole archives on identical read files (128 KiB, 1 worker, 3 seeds)

Archive SUCCESS counts only when the extracted output's SHA-256 equals the input's. Each cell: verified SUCCESS / 3,
and in brackets the mean fraction of groups recovered. "+minQ" here is `min_quality` 13.

| channel | V4 | V4+minQ | V5-hard | V5-hard+minQ | soft-erasure | soft-auto | soft-auto+minQ |
|---|---|---|---|---|---|---|---|
| sub 3 %, informative 0.5, cov 1 | 0 (0.000) | 0 (0.955) | 0 (0.000) | 0 (0.955) | 0 (0.962) | **1** (0.968) | **1** (0.968) |
| sub 4 %, informative 1, cov 1 | 0 (—) | 3 (1.000) | 0 (—) | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) |
| sub 2.5 %, uninformative, cov 1 | 0 (0.314) | 0 (0.314) | 0 (0.314) | 0 (0.314) | 0 (0.327) | 0 (**0.397**) | 0 (**0.397**) |
| indel 0.25 % + 0.25 % + sub 0.5 %, informative 0.5, cov 1 | 0 (0.577) | 0 (0.705) | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) |
| indel 0.4 % + 0.4 % + sub 1 %, informative 0.7, cov 1 | 0 (0.000) | 0 (0.000) | 0 (0.481) | 0 (0.776) | 0 (0.647) | 0 (0.654) | 0 (**0.872**) |
| EXP-0011 (0.2 % sub, 0.05 % + 0.05 %, 2 % dropout), Poisson cov 3 | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) | 3 (1.000) |

(— : in 2 of 3 seeds V4 could not decode the superblock, so the group count is unknown; the third seed recovered 0.)
0 false SUCCESS in 126 archive decodes. At archive level the gain from soft decoding is small. There is
one extra archive SUCCESS: seed 1 of sub 3 % / informative 0.5, where the best hard configuration recovered 0.981 of
groups and soft `auto` all of them. Elsewhere soft decoding raises the group fraction where every decoder fails. The combined configuration again recovers the most groups with indels (0.872 vs 0.776 for the best
hard configuration). On the low-noise reference channel (EXP-0011) all decoders succeed, and the soft path is entered
by at most 3 reads.

### 12.8 P4-EXP-08: coverage (32 KiB archive, 14 groups, 8 workers, 2 seeds, fixed coverage)

Each cell: verified SUCCESS / 2, and in brackets the mean fraction of groups recovered (— : the superblock could not be
decoded in either seed, so the group count is unknown).

| channel | coverage | V4 | V5-hard | V5-soft-auto |
|---|---|---|---|---|
| indel 0.5 % + 0.5 % + sub 1 %, informative 0.5 | 1 | 0 (0.000) | 0 (0.107) | 0 (0.143) |
| | 2 | 0 (0.071) | **2** (1.000) | **2** (1.000) |
| | 3 | 0 (0.714) | 2 (1.000) | 2 (1.000) |
| | 5, 10 | 2 (1.000) | 2 (1.000) | 2 (1.000) |
| indel 0.25 % + 0.25 % + sub 4 %, informative 0.5 | 1 | 0 (—) | 0 (—) | 0 (0.000) |
| | 2 | 0 (—) | 0 (0.000) | 0 (0.071) |
| | 3 | 0 (—) | 0 (0.000) | 0 (0.071) |
| | 5 | 0 (0.000) | 0 (0.071) | **2** (1.000) |
| | 10 | 0 (0.679) | 1 (0.964) | **2** (1.000) |

0 false SUCCESS in 60 archive decodes.

* On the indel-dominated channel, Phase 3 alone moves the coverage needed from 5 to 2. Soft consensus adds nothing at
  archive level: Phase 3's hard path already recovers every group from coverage 2.
* On the substitution-heavy channel (4 % substitutions plus indels), **soft consensus is what makes the archive
  decodable**: at coverage 5 V4 and V5-hard recover 0 and 0.071 of groups, soft `auto` recovers all of them in both
  seeds (39–41 addresses recovered by soft consensus per decode). At coverage 10 it succeeds 2/2 against 1/2 for
  V5-hard and 0/2 for V4. This is the clearest archive-level gain of Phase 4.
* Below coverage 5 on that channel no decoder succeeds; soft consensus recovers 24–40 addresses per decode, which is
  not enough to complete one group.

## 13. Runtime

**Default path** (`runtime_default_path.py`): the Phase 2/3 workload (4 MiB, EXP-0011 channel, Poisson coverage 3)
decoded by the Phase 4 tree and by a clean `3633dfb` worktree on the **same read file**, each decode in a fresh
interpreter, trees alternating, 3 repetitions. Every decode was SUCCESS with the output SHA-256 verified.

| workers | configuration | Phase 3 tree (median) | Phase 4 tree (median) | peak RSS Phase 3 / Phase 4 |
|---|---|---|---|---|
| 1 | V4 default | 10.89 s | 10.93 s (+0.4 %) | 142–145 / 146–150 MB |
| 1 | V5 smart (Phase 3) | 19.35 s | 19.42 s (+0.4 %) | 152–155 / 152–153 MB |
| 1 | V5 smart + soft `auto` | — | 24.60 s | — / 157–158 MB |
| 8 | V4 default | 3.89 s | 4.03 s (+3.5 %) | 160–161 / 159–163 MB |
| 8 | V5 smart (Phase 3) | 4.60 s | 4.61 s (+0.3 %) | 211–214 / 206–213 MB |
| 8 | V5 smart + soft `auto` | — | 5.20 s | — / 231–241 MB |

With soft decoding off, Phase 4 costs nothing measurable: the differences are within the run-to-run spread (the
8-worker V4 runs spread 3.80–4.05 s across both trees). **Turning soft `auto` on costs +27 % (1 worker) and +13 %
(8 workers) on this low-noise workload**, although at most a handful of reads reach the soft path. The cost is fixed
per-decode work (soft consensus bookkeeping in pass 2 and per-read soft preparation), not trials; EXP-07's EXP-0011
row shows the same ratio (1.26 s vs 0.98 s). It is listed in §16.

**Noisy archives** (EXP-07, 128 KiB, 1 worker, median of 3 seeds; EXP-08, 32 KiB, 8 workers, median of 2):

| channel | V4 | V5-hard | V5-hard+minQ | soft-auto | soft-auto+minQ | reads on the soft path |
|---|---|---|---|---|---|---|
| EXP-07 sub 3 %, informative 0.5 | 0.68 s | 1.48 s | 0.82 s | 3.18 s | 1.91 s | 1,136–1,159 (auto), 45–47 (+minQ) |
| EXP-07 sub 4 %, informative 1 | 0.59 s | 1.82 s | 0.58 s | 4.59 s | 0.81 s | 2,952–3,005 (auto), 0 (+minQ) |
| EXP-07 indel 0.4 % + 0.4 % + sub 1 % | 0.57 s | 15.7 s | 11.6 s | 17.9 s | 14.2 s | 91–107 / 47–57 |
| EXP-07 EXP-0011 reference, cov 3 | 0.75 s | 0.98 s | 0.98 s | 1.26 s | 1.25 s | 0–3 |
| EXP-08 indel 0.5 % + 0.5 % + sub 1 %, cov 10 | 1.4 s | 39.7 s | — | 46.8 s | — | |
| EXP-08 indel 0.25 % + 0.25 % + sub 4 %, cov 10 | 1.6 s | 50.2 s | — | 63.2 s | — | |

* Soft decoding costs time in proportion to the reads that reach it: at most 64 inner-RS decodes per read (§8). On
  top of Phase 3 it adds 10–30 % on the indel channels, and up to 2.5× on substitution-heavy channels at coverage 1,
  where thousands of reads fail every hard path.
* `min_quality` makes soft decoding **cheaper**: erasing low-quality bases lets the hard path succeed first, so fewer
  reads reach the soft path (sub 4 %: 0 instead of ~3,000 reads, 0.81 s instead of 4.59 s).
* The dominant cost on noisy channels is **Phase 3**, not Phase 4: V5-hard is 25–35× slower than V4 at coverage 10
  in EXP-08. This is the known Phase 3 cost (smart search on every read V4 fails, including redundant reads of
  addresses that are already recovered). It is the first item of §20.

## 14. Memory

Peak RSS is each decode child's own `VmHWM`. (The first EXP-07/08 runs used `ru_maxrss`, which on Linux survives
fork + exec and reported the parent's peak. Those results are kept in `experiments/v5/phase4/superseded-rss/`; their
recovery outcomes are identical, only the memory columns were wrong.)

* Default path (soft off): see §13; the same as Phase 3 within measurement noise.
* Soft path, 1 worker, EXP-07 substitution channels: 97–139 MB against 78–85 MB for V5-hard on the same reads (on the
  indel channels soft and hard are within 10 MB of each other, 100–113 MB). The worst case is sub 4 % / informative 1
  without `min_quality` (139 MB, ~3,000 reads on the soft path). Pass-1 soft decoding runs in batches of 512 reads,
  so this is bounded by the batch, not by the archive size. With `min_quality` the same channel needs 79 MB.
* EXP-08 (8 workers, coordinator process): soft-auto 64–113 MB, V5-hard 65–118 MB, V4 62–102 MB. Soft consensus in
  pass 2 adds no measurable memory over Phase 3.

## 15. Integrity: false acceptance and false SUCCESS

Every count below was checked against simulator truth by the harness after decoding.

| experiment | accepted frames / reads / archives checked | false | ambiguous accepted | wrong-but-verified |
|---|---|---|---|---|
| P4-EXP-01 substitution sweep | 844,148 verified reads (216 decodes of 4,096 reads) | 0 | — | — |
| P4-EXP-02 k substitutions | 53,435 frames | 0 | 0 | 0 |
| P4-EXP-03 indel + substitution | 514,397 verified reads (144 decodes) | 0 | — | — |
| P4-EXP-04 RS boundary | 97,349 frames | 0 | 0 | 0 |
| P4-EXP-05 posterior shapes | 31,283 frames | 0 | 0 | 0 |
| P4-EXP-06 adversarial (looser limits) | 1,506 frames, 211,158 trials, 64,681 RS-valid trial words | 0 | 0 | 0 |
| P4-EXP-07 archives | 126 decodes, 53 SUCCESS | **0 false SUCCESS** | | |
| P4-EXP-08 archives | 60 decodes, 25 SUCCESS | **0 false SUCCESS** | | |

The summed per-read false-acceptance bounds (random-word model) are at most 4.3·10⁻⁵ per experiment, over 11–39
million soft trials in EXP-07/08. The CRC-32 and metadata checks were load-bearing (§12.6): RS alone would have
accepted 1,871 wrong frames under misleading qualities.

## 16. Known failures (explicit)

* **Misleading evidence defeats soft decoding.** A confidently wrong posterior (0.99 on a wrong base) recovers almost
  nothing beyond the RS bound (EXP-05: 34 of 500 at e = 9, by blind Chase search). It fails closed; it does not
  accept wrong frames.
* **Uninformative qualities give little.** Without quality information only blind Chase search helps: +3.4 % of reads
  at 3 % substitutions, and essentially nothing at 2e + f ≥ 20.
* **Soft decoding alone loses to a hard quality threshold when indels are present** (EXP-03: 2,977 vs 3,055 reads at
  1 % + 1 %). It must be combined with `min_quality` (`soft-auto+minQ20`: 3,156). Soft decoding does not choose the
  threshold for the Phase 3 hard path.
* **Small archive-level gain on most channels.** EXP-07 has one extra SUCCESS (of 18 cells × seeds). The decisive
  archive gain is EXP-08's substitution-heavy channel at coverage 5–10.
* **Runtime grows with the number of failed reads** (§13); there is no global time budget, only the per-read bound.
* **Fixed overhead when soft decoding is on**, even on clean data: +27 % (1 worker) / +13 % (8 workers) on the 4 MiB
  low-noise workload, where almost no reads reach the soft path (§13). It is not yet profiled.
* **Independence inside indel windows** (§5) discards the joint structure of Phase 3 candidates.

## 17. Tests

`tests/v5/test_soft_symbols.py` (26), `test_soft_decoder.py` (40), `test_soft_integration.py` (13) and
`test_phase3_terminology.py` (2): 81 new tests. Full suite: **997 / 997 pass** (916 existing + 81 new, 10 min 35 s),
including the V4 suite unchanged. Soft decoding is off by default (`test_default_is_off` checks the option and that
the report then carries no soft fields); the existing V4 and Phase 3 tests pass unchanged with the new hooks in place,
and §13 compares the default path against the clean Phase 3 tree on identical reads.

## 18. Limitations

* **SIMULATED only.** The qualities come from the V4 simulator's two-level model or a synthetic graded model. Neither
  is fitted to a real instrument, and no posterior here is calibrated. Real platforms have context-dependent,
  miscalibrated and bursty qualities; how GMD ranks reliabilities on them is untested.
* One machine (Xeon Gold 6240, 8 CPUs); timings will differ elsewhere.
* The false-acceptance budget uses the random-word model of RS mis-decoding, not a proof for adversarial inputs. The
  CRC-32, metadata checks and the container SHA-256 remain the guards.
* Soft decoding works per frame on the inner code. It does not pass soft information to the outer code (Phase 5's
  domain).
* Koetter–Vardy algebraic soft decoding was not implemented (§7 D).

## 19. Reproducibility

Commands, seeds and run times: `experiments/v5/phase4/README.md`. Every `results.json` records its configuration and
SHA-256, the SHA-256 of every read file (read and archive experiments compare decoders on identical reads), and the
provenance: V4 control `v4.0.0` = `a358ae8`, base commit `3633dfb`, CPU, Python 3.12.3, NumPy 2.5.3, gcc 13.3.0.
Results were produced from the working tree on top of `3633dfb` just before the Phase 4 commit (provenance
`worktree_dirty = true`), from the final code except for edits to the harness (configurations added, the peak-RSS
measurement). Recovery curves as CSV: `experiments/v5/phase4/curves/` (`make_curves.py`).

## 20. Recommendation for Phase 5

1. **Before Phase 5, or as its first step: make Phase 3 cheaper.** Defer the smart search to addresses that are still
   missing after the cheap pass, instead of searching every read V4 fails. At coverage 10 that is most of the 25–35×
   slowdown (§13), and the outer code needs only one good read per address.
2. **Phase 5 (outer-code resilience: larger/adaptive groups, interleaving, dropout).** EXP-08 shows the archive
   failure mode on noisy channels is *groups that miss a few addresses*. Soft decoding recovered 24–40 extra addresses
   per decode where no group completed. An outer code that tolerates more missing addresses per group, or interleaving
   that spreads missing addresses across groups, would turn those partial recoveries into SUCCESS. Phase 5 should
   measure that on the EXP-08 channels as well as on dropout.
3. If soft decoding is kept, recommend it **together with `min_quality`** (faster and never worse in these
   experiments), and keep it opt-in until it has been tested on real sequencer qualities.
