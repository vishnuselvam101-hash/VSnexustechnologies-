# V14 adaptive recovery scheduler (Phase 6, workstream C)

Status: implemented in the streaming engine (`src/vnxdna/native/rust/engine/src/{engine,sched}.rs`), default
`engine_scheduler="strand"` (performance against the V13 rule: §9.2; default open, §9.4). Every number in this document is SIMULATED (channel simulator, evidence category D) and
comes from a committed results file under `experiments/v14/bench/p6c-scheduler/`. Timings are PROVISIONAL: the lab host
was shared with three other agents while they were taken.

## 1. What the scheduler decides

Pass 1 reads every record of the read file. The address scheduler only decides which reads are *attempted* (orientation,
projection or DP alignment, inner RS decode, CRC). A read that is not attempted contributes nothing: no accepted record,
no pending record. Pass 1 is otherwise unchanged; nothing a skip decision uses ever becomes decoded data.

Modes (`DecodeOptions.engine_dedup`, `DecodeOptions.engine_scheduler`, CLI `--engine-dedup/--no-engine-dedup`,
`--engine-scheduler`):

| mode | how | what it skips |
|---|---|---|
| diagnostic | `engine_dedup=False` | nothing: every read is attempted (pass-1 records equal the V13 path's, record for record) |
| `strand` (default) | V14 adaptive scheduler | a read only when the strand check (§3) confirms it as a copy of a verified frame |
| `corroborated` | the corrected V13 rule (f7586b9), kept unchanged for comparison | see §5: it can skip distinct strands |

## 2. Invariants the skip conditions rely on (code and format)

- **I1 (inner code is MDS over the whole frame).** A frame is `variant ‖ (version, kind) ‖ tag ‖ group ‖ symbol ‖
  payload ‖ CRC-32 ‖ inner RS parity` (`dnaenc/frame4.py`; docs/VNX4_FORMAT.md §8). The inner Reed-Solomon parity (r
  bytes) is computed over bytes 0 … 13+P as transmitted, i.e. over the header, the payload and the CRC. A shortened RS
  code is MDS: two different frames differ in at least **r + 1 bytes**, wherever those bytes are (header, payload, CRC
  or parity). r = 16 for v4-balanced and v7-lowcov, 20 for v4-indel/v4-archival, 12 for v4-dense, 4 for v6-high-dropout.
- **I2 (byte-aligned bases).** Frame bases are 2 bits per base, most significant first, and markers are inserted only
  after every `marker_period` frame bases, a multiple of 4 (`Layout.validate`). So the four bases of every frame byte
  are contiguous on the template; the engine checks this when it builds the strand-check segments and never applies the
  check otherwise (`sched::strand_segments` returns no segment).
- **I3 (scrambling).** Bytes 1 … 13+P are XORed with a SHAKE-128 keystream selected by the variant byte. Equal plain
  payloads (zero runs, repeated records, column parity of equal data) therefore give equal strand payloads when the
  variant is equal; payload windows are no evidence of identity. The scrambler also makes a strand's bases at a wrong
  shift agree with another strand's only by chance.
- **I4 (a stored reference frame is a codeword).** A read is accepted when its frame passes the inner RS code and the
  descrambled CRC-32 verifies with a valid version nibble and kind (`frame.rs::decode_frames`). A row with no erasure
  whose CRC already verifies is accepted as read, without a syndrome check, and the CRC does not cover the r parity
  bytes, so such a frame can carry parity errors (review finding). Before a frame becomes the stored reference of its
  address, the engine therefore decodes it once more with the inner code (`Engine::codeword_check`): a codeword is
  stored as is; a frame whose parity bytes alone were wrong is stored with the re-derived parity
  (`store_parity_fixed`); anything else is not stored (`store_rejected`). Claim closes compare against the stored
  frame, never against the accepted read's own frame. The accepted records themselves are unchanged. The CRC is error
  detection only: it is never identity evidence, and the container's SHA-256 check and the ladder are unchanged.
- **I5 (per-read independence).** A read's pass-1 outcome never depends on the other reads of the call
  (`pass1.rs` module comment): skipping one read changes no other read's records.
- **I6 (what pass 2 does with copies).** Accepted records of one address are resolved by strict majority of identical
  payloads (`recovery/consensus.py::resolve_duplicates`); pending records feed the consensus of addresses that have no
  accepted record. A confirmed copy of a verified address therefore adds at most one more identical vote, and its
  pending record (if it had failed) would only have been read for the address its damaged header named.

## 3. The strand check (the only identity evidence)

**Projection.** `sched::strand_project` reads the read (in the orientation the screen estimates) onto the frame's
bytes. It depends on the read alone, never on the frame it is later compared with. The frame's bytes fall into
segments (one marker period each, I2). Every marker block is located at the only exact match of all its bases among
the shifts c, c±1, c±2 around the previous anchored shift c (|shift| ≤ band). A block with no exact match, or with
several (ambiguous), gets no anchor. The read start (shift 0) and the read end (read length − template length) anchor
the first and last segment. A segment is read at shift d when both its anchors are d, or when the established shift
E is firm (confirmed by two exact markers; E starts at 0) and every anchor the segment has equals E. A segment whose
two anchors are both present and differ (by at most 2), with its left anchor equal to the firm E, holds an indel. It is
read at both read-determined shifts, unless it reaches into the CRC + parity tail, where it is not read at all (the
review's tail rule). E then takes the right anchor, which becomes firm only when the next marker agrees. Every other
segment is not read. So:
(a) shift changes are read off the read's own markers and never chosen against a reference;
(b) an indel segment is compared with one best split point (bytes before at the left shift, from it on at the right
    shift) and charged `SHIFT_CHARGE` = 1 extra unconfirmed byte; the byte holding the indel usually mismatches too;
(c) a channel indel can therefore absorb at most its own segment (test `a_channel_indel_absorbs_at_most_its_own_segment`).
A template-length read whose every marker base reads at shift 0 takes a fast path that is decision-identical (proof in
the code comment; test `exact_length_fast_path_equals_the_general_projection`).

**Comparison.** `strand_unconfirmed(proj, ok, K)` counts the frame bytes that are unread or differ from the stored
frame K: a Hamming count. The read is a **confirmed copy of K** when at most `budget` bytes are unconfirmed.
`budget = r + 1 − margin` with `margin = min(8, r + 1)`, and no strand check when r + 1 < 5 (`sched::strand_budget`):
r = 16 → 9; r = 20 → 13; r = 12 → 5; r = 4 → 0 (exact agreement, margin 5); r ≤ 3 → never.

**What this guarantees.** By I1 (MDS) and I4, every other frame K' differs from the stored codeword K in at least r + 1
bytes.
- *Deterministic part.* An error-free reading of K' has all its markers at shift 0, because the encoder writes the
  markers, not the data. It is therefore compared unshifted and leaves at least r + 1 > budget unconfirmed bytes. It is
  never skipped, whatever the payloads are, chosen plaintexts included. The review's construction (K' = K with a base
  inserted and another deleted between markers, plus an address bit flipped: 19 bytes away, previously confirmed with 2
  unconfirmed bytes) is now a regression test that must leave ≥ 19 unconfirmed bytes for every such pair (test
  `review_probe_shifted_frame_far_in_hamming_is_not_confirmed`). The planted shift read through a channel insertion that
  undoes it is the test `indel_variant_of_a_verified_frame_is_not_confirmed`.
- *Allowance for indels (bounded).* A split is possible only in a segment where the read's own markers show an indel.
  Within that segment, a planted content shift in K' that the channel indel happens to undo can be absorbed: at most
  one segment (≤ 6 bytes) per channel indel, at a charge of 1 byte each. This is part of the probabilistic assumption
  below, because it needs a channel indel at the planted position.
- *Probabilistic part* (stated as an assumption about the simulated channel, not proved). A reading of K' passes only if
  the channel turned at least `margin` differing bytes into exactly K's bytes (one specific substitution or more per
  byte, ≤ p_sub/3 each: C(n, 8)·(p_sub/3)^8 for n differing bytes, ~10^-13 at n = 30 and p_sub = 1 %). Or the read
  must be mis-anchored: in some segment, both true markers destroyed by the channel and exact false markers at one
  common wrong shift on both sides. False markers are built from frame bases, which a chosen plaintext can control.
  So a planted pair still needs channel errors in at least two markers per mis-anchored segment, plus enough segments
  to cover the margin. This is the remaining chosen-plaintext caveat.
- *Consequence of a wrong skip.* One reading of K' is withheld. Its other readings are still attempted, the outer code
  and the container SHA-256 still verify the output, and the ladder is unchanged. A skip never changes decoded bytes.

The review asked for the CRC + parity tail to be confirmed with no shift change since the last payload segment. The
projection gives this and more: no segment, tail included, is ever read at a shift that two exact markers have not
confirmed, and a tail segment that holds an indel is unconfirmed.

## 4. Skip conditions (strand mode) and evidence categories

Every skip is a strand-check confirmation against a verified frame; the four places differ only in where the frame
comes from (`engine.rs::batch`):

| reason (counter) | when | frame compared with |
|---|---|---|
| `skip_claim_strand` | a claim (reads linked by payload windows) gets a verified member K; every unresolved member (never attempted, or a failed cheap attempt) is checked | the stored (codeword-checked) frame of K's address; if that fails, the stored frames named by the member's header key and windows (another verified strand) |
| `skip_screen_strand` | before the rounds of a batch, every read; and again when a read's turn comes in a round | stored frames (`FrameStore`, keyed by address) named by the read's raw header key or by verified strands holding one of its windows (`WinMap`) |

Header keys and windows are **leads** only: they choose which frames to compare, never decide. Evidence categories per
read (the decision trace, §7, records them):

- VERIFIED: attempted and accepted (inner RS + CRC + version/kind); the only source of decoded data.
- CONFIRMED COPY: not attempted; strand check passed against a verified frame (the address of that frame is recorded).
- UNVERIFIED LEAD: a header key or window that names a verified strand; never a skip on its own.
- CONFLICTING: the strand check failed against the claim's frame (a different strand sharing payload windows, or a
  copy damaged beyond the budget): the read is split off into a new claim and attempted.
- UNCLASSIFIABLE: no lead, no stored frame, or a read too damaged for the budget: attempted.
- ATTEMPTED, NOT VERIFIED: pending (aligned, kept for pass-2 consensus) or dropped, exactly as in the diagnostic mode.

Claim handling in strand mode: reads that read the same address (two or more readings) form a claim, attempted one
member per round (cheap paths first, then the DP); the reads without such an address wait in a *bulk* claim that starts
in round 2, is screened against the frames verified meanwhile, and attempts the rest together (this removes the
one-strand-per-round chains of pools with many strands sharing windows). A failed cheap attempt is never repeated. A
read left with neither a decision nor an attempt at the end of a batch is attempted in full (`sched_rescued`; never
observed, kept as a safety net). With no strand check available (r + 1 < 5) nothing is skipped.

## 5. Rejected as unsafe (and why)

| candidate rule | why rejected |
|---|---|
| window match alone (the pre-fix V13 rule) | distinct strands share 24-nt windows (I3: equal payloads), V13 report §3 |
| raw header key alone | one substitution in the 40 header bases names a neighbouring address |
| header key + one matching window (the corrected V13 rule, `corroborated`) | on structured data every window matches, so this is the header key alone: in the campaign it skips readings of other strands and loses addresses (§8) |
| skipping a whole window group when one member is corroborated (V13 global screen) | a group of equal payloads holds many strands: on zero-run pools the stream rung loses most addresses and returns FAILURE, and the ladder falls back (§9) |
| unreadable header within 2 bases of the verified header (V13 `skip_claim_near`) | no identity evidence beyond the header bases |
| dropping cheap-failed members when a claim closes (V13 `skip_claim_deferred`) | such members were never compared with anything |
| CRC-32 match | error detection only (I4); not identity |
| nearest supported address by header distance as identity | used by V13 to assign single readings to a claim; in strand mode it never decides a skip |
| a budget above r + 1 − 5 | the margin would fall below 5 bytes |
| a free per-segment shift search against the reference (the first V14 version) | it measured an edit distance while the MDS bound is a Hamming bound: a frame ≥ r + 1 bytes away but a few indels from K passed (review finding) |
| comparing an accepted read's own frame without a codeword check | clean-path frames can carry parity errors (I4) |
| tail (CRC + parity) windows only | they bound pairs with equal payloads but not pairs differing in the payload outside the windows; used only as an early reject |

Conservative cost (documented): layouts with r + 1 < 5 attempt every read; reads damaged beyond the budget are attempted
even when they are copies (V13 skipped many of them on the header key); the strand screen spends checks on leads that
fail (counted in `strand_rejects`).

## 6. Observability, diagnostic mode, determinism, memory

- Counters (`report["engine"]["pass1"]`, `vnxdna.native.engine.STAT_NAMES`): `attempted`, `skipped`, `cheap_attempts`,
  `full_attempts` (DP alignment attempts), one counter per skip reason (`skip_screen_group`, `skip_claim_header`,
  `skip_claim_near`, `skip_claim_deferred` for the V13 rule; `skip_screen_strand`, `skip_claim_strand` for strand mode;
  `skip_other` = a skip without a reason, must be 0), `strand_checks`, `strand_rejects`, `screen_candidates`,
  `redundant_accepts` (an accepted read whose address was already verified: a wasted attempt; a lower bound),
  `store_evictions`, `sched_rescued`, `store_parity_fixed`, `store_rejected`, `split_groups`, `rounds`. `skipped` = the sum of the skip-reason counters.
- Fallback activations stay in `report["engine"]["fallbacks"]` (ladder), `report["engine"]["scheduler"]` names the mode.
- Per-read decision trace: `VNX_ENG_TRACE=<file>` appends one line per read: `index decision kind tag group symbol`, with
  decision `A` (accepted; address verified), `P` (pending), `D` (dropped) or `S<reason>` (skipped; the address of the
  frame it was confirmed against, or −1 when the rule records none). The campaign joins it with the simulator's
  ground truth.
- Diagnostic mode: `engine_dedup=False` / `--no-engine-dedup` attempts every read (unchanged from V13).
- Selection: `DecodeOptions.engine_scheduler` / SDK config key / CLI `--engine-scheduler {strand,corroborated}`; there is
  no environment-variable override.
- Overhead cuts that cannot change a decision: the exact-length projection fast path; a read's projection is computed
  once (it depends on the read alone) and, before the rounds, in the thread pool; a read identical to the claim's
  verified read reuses that read's projection; a screen is not repeated while the store is unchanged since the read's
  last screen (`store_version`).
- Determinism: the screens before the rounds read only tables written before them; all inserts happen serially in read
  order; the trace and spill files are identical across thread counts (tests).
- Memory: strand mode replaces the V13 fingerprint tables (8 + 16 MiB + a 1 MiB Bloom filter) by a frame store of 2^18
  slots (8-byte key + one frame each: 19.5 MiB at 70-byte frames), a window → address map of 2^19 entries (8 MiB) and
  the same Bloom prefilter: fixed size whatever the pool (`table_bytes` reports it), lossy (a lost entry only means a
  later copy is attempted).

## 7. Tests

`tests/v14/test_scheduler.py` (ground truth from `simulate_file_with_truth`): zero runs with stripes and column
parity, zero-heavy with reverse complements, repeated patterns, structured binary records with substitutions, noisy
headers (2 % substitutions, 0.5 % insertions and deletions), dropout + uneven coverage + duplicates, ordered random
reads, neighbouring strands at coverage 1, a markerless dense layout, the 4-parity-byte layout; each checks zero false
skips, the same verified addresses and majority payloads as the diagnostic mode, one reason per skip, a complete trace;
whole decodes equal the diagnostic mode (status, bytes, conflicts, failed groups, outer counters except where pass-2
consensus differs, §8); the V13 rule's loss on a zero pool (ladder fallback); determinism across 1, 2, 5 threads; a
layout without the margin skips nothing; option validation; CLI flag. Rust unit tests in `sched.rs` cover the strand
check (noisy copies, indels, N, out-of-range, a distinct frame with the same payload, reverse complement), the budget
and the lossy tables.

## 8. Preregistration (written and committed before any campaign or A/B row was measured)

Exploration (to find defects and tune the design) used seeds 61901–61902, 71000–71011 and the 4 MiB indel fixture of
seed 95500; none of them is reused below.

**Safety campaign** (`experiments/v14/bench/p6c-scheduler/campaign.py`, pools and parameters in its `POOLS`, seeds
61001 and 61002, one worker): 14 pools × 2 seeds = 28 runs (the `POOLS` dict as committed in f1e7c67; this
sentence first said 12 × 2, a counting slip): random data ordered and shuffled with indels; zero runs with
stripes and column parity; zero-heavy shuffled; repeated pattern; binary records with stripes and substitutions;
reverse complements; dropout with uneven coverage; noisy headers; neighbouring strands at coverage 1; duplicates;
zero-heavy with reverse complements; a markerless dense layout; the 4-parity-byte layout. Each run decodes in the
diagnostic mode (reference), `strand` and `corroborated`.
Acceptance for `strand` (all must hold on every run):
1. false skips (ground truth) = 0, and skips without an evidence address = 0;
2. verified addresses lost against the reference = 0 and majority-payload disagreements = 0;
3. decode status and output bytes equal to the reference's; false successes (SUCCESS with wrong bytes) = 0;
4. no ladder fallback where the reference had none; `skip_other` = `sched_rescued` = 0;
5. duplicate conflicts, failed groups and symbols expected equal to the reference's. Outer-code symbol counters are
   reported with their differences (they can differ through pass-2 consensus, §7) and are not an acceptance criterion.
The same numbers are reported for `corroborated` (no acceptance; it documents the V13 rule).

**Performance** (`experiments/v14/bench/p6c-scheduler/ab.py`): same tree, `strand` vs `corroborated` (+ an A/A
`corroborated` run), 4 MiB random input, channel and seeds of the V13/V14 bench: indel-w1 (95500–95504), indel-w4
(95300–95304), indel-rc-w4 (97000–97004), clean-w4 (95100–95104), substitution-w4 (95200–95204), plus indel-shuffled-w1
(the indel channel with the whole pool shuffled, 97400–97404); order A B B A per seed, ratio corroborated / strand,
median of per-seed ratios with the percentile-bootstrap 95 % CI. Primary endpoint: indel-w1. Acceptance: on indel-w1 the
CI lower bound of the ratio is above 1.0 and above the A/A CI upper bound; on every other scenario the median ratio is
reported, a regression (CI upper bound below 1.0) is reported as such. All rows must be SUCCESS with identical outputs.

Amendment (before the second A/B pass; the first indel-w1 pass is kept and reported): the first pass ran the A/A
config after the A B B A runs of each seed, and on the shared host its wall times drifted with position (the A/A
ratio ranged 0.85–1.29). The harness now orders the three configs by a seed-dependent permutation followed by its
mirror (each config twice, at mirrored positions) and also records the process CPU time (user + system), which the
co-tenants disturb less than wall time at 1 worker. Wall time stays the primary metric; CPU time is secondary.

## 9. Results

All SIMULATED. The review (§3, I4) changed the strand check after the first measurements. The pre-review design's
files are kept as `*-prereview*` and are superseded: its 1.22× indel-w1 gain came partly from the unsafe free shift
search. Current files: `experiments/v14/bench/p6c-scheduler/{campaign.jsonl, campaign-summary.json, ab.jsonl,
ab-summary.json, zero-payload/after-review-*.json}`.

### 9.1 Safety campaign (final code f4d6c09, 28 runs, 3,391,358 reads per mode)

| mode | false skips | skips without evidence address | lost verified addresses | payload disagreements | decodes identical to reference | outer records identical | false successes | ladder fallbacks |
|---|---|---|---|---|---|---|---|---|
| `strand` | **0** | 0 | **0** | 0 | 28/28 | 28/28 | 0 | 2 (as the reference) |
| `corroborated` (V13 rule) | 30,698 | 1,941,993 | 227,404 | 0 | 28/28 (only through the ladder) | 26/28 | 0 | 14 (12 on six structured pools, 2 as the reference) |

`strand` attempted 1,971,093 reads (58 %) and made 879,902 full (DP) attempts, against 3,391,358 for each in the
diagnostic mode. The pre-review design attempted 47.5 % and made 545,178 full attempts. Every acceptance criterion of
§8 holds on every run; `skip_other` = `sched_rescued` = 0. Two preregistered pools cannot be decoded in any mode
(random-dropout-uneven: FAILURE; zeros-noisy-headers: no superblock), and all modes end in the same status there.

### 9.2 End-to-end A/B (PROVISIONAL; idle host, load 1.1–2.2, final code, `ab-summary.json`)

Ratio = corroborated / strand seconds (> 1 = strand faster), n = 5 seeds, 95 % CI = min–max. 180 rows, all SUCCESS
with identical outputs, 0 false successes, 0 fallbacks.

| scenario | strand s | corroborated s | ratio [CI] | A/A [CI] | CPU ratio [CI] | reads attempted / full attempts, strand vs corroborated |
|---|---|---|---|---|---|---|
| indel-w1 (primary) | 4.337 | 3.084 | **0.701 [0.700, 0.718]** | 0.992 [0.973, 1.044] | 0.715 [0.712, 0.733] | 310,552 / 173,712 vs 323,341 / 66,197 |
| indel-w4 | 2.446 | 1.656 | 0.675 [0.649, 0.707] | 0.993 [0.921, 1.053] | 0.683 [0.679, 0.720] | 310,506 / 173,839 vs 323,485 / 66,312 |
| indel-rc-w4 | 2.476 | 1.727 | 0.696 [0.682, 0.720] | 0.992 [0.980, 1.082] | 0.722 [0.699, 0.734] | 313,497 / 175,140 vs 325,237 / 66,831 |
| clean-w4 | 0.741 | 0.579 | 0.780 [0.775, 0.796] | 0.952 [0.877, 1.043] | 0.812 [0.808, 0.831] | 131,253 / 102 vs 131,151 / 0 |
| substitution-w4 | 1.383 | 1.354 | 0.990 [0.972, 1.063] | 1.024 [0.989, 1.114] | 1.022 [1.001, 1.062] | 174,967 / 33,898 vs 365,019 / 21,766 |
| indel-shuffled-w1 | 4.578 | 3.411 | 0.748 [0.728, 0.792] | 0.988 [0.975, 1.053] | 0.756 [0.740, 0.802] | 315,552 / 140,624 vs 329,373 / 104,420 |

**The primary endpoint is NOT met.** With the reviewed (Hamming-exact) check, the strand scheduler is 25–33 % slower
than the V13 rule on the indel workloads, 22 % slower on clean pools, and equal on substitutions.
- *Why.* At 1 % substitutions and 0.4 % indels on 313 nt, many genuine copies leave more than 9 unconfirmed bytes (the
  close checks are rejected: `strand_rejects` in `ab.jsonl`), so they are attempted. Most of them
  carry indels, so their cheap paths fail and they go to the DP: 2.6× the V13 rule's full attempts. The V13 rule skips
  these copies on their header key, which is what makes it unsafe on structured data.
- *Against the diagnostic mode (exploratory, not preregistered, pass 1 only, indel-w1 fixture, median of 3;
  `exploratory-pass1-all-vs-strand.txt`).* Attempt
  every read: 3.53 s at 1 thread, 1.27 s at 4. Strand: 3.89 s, 1.95 s. V13 rule: 2.65 s, 1.26 s. On random indel pools
  the strand scheduler costs more than attempting every read.
- *clean-w4.* Both schedulers attempt the same reads. The overhead is the codeword check, the projections and the
  screens. The decision-identical cuts of §6 (exact-length fast path, projections computed once and in the thread pool,
  reuse for identical reads, no repeated screen of an unchanged store, a quadratic de-duplication removed) reduced it in
  exploratory pass-1 timings (not committed). What remains (end to end: 0.741 s against 0.579 s) is reported as cost.

### 9.3 Zero-run payloads (workstream E's finding)

Before = build/v14 423efa0 (V13 rule); after = this branch.

| workload | before | after (pre-review strand) | after (final strand) |
|---|---|---|---|
| zeros 1 MiB smoke, w1, seed 98085 | stream FAILURE → v13 fallback, 1.77 s, 120.6 MiB | stream, 1.12 s, 107.2 MiB | stream, no fallback, SUCCESS, 1.50 s, 110.0 MiB |
| zeros 4 MiB, w4, seed 98080 | stream FAILURE → v13 fallback, 2.85 s, 489.8 MiB | stream, 2.21 s, 111.1 MiB | stream, no fallback, SUCCESS, 2.82 s, 114.2 MiB |

### 9.4 Limits and the open decision

- The safety argument (§3) is deterministic for error-free readings and a probability under the simulated channel
  otherwise. A channel indel absorbs at most its own segment. The campaign found no false skip.
- Default: `strand` stays the default in this branch because it is the only rule that never loses a strand. It is,
  however, slower than both the V13 rule and the diagnostic mode on random indel pools. Choosing the default (strand,
  the diagnostic mode, or the V13 rule with its documented loss on structured data) is left to the lead and founder.
- Layouts with r ≤ 3 attempt every read; r = 4 needs exact agreement.
