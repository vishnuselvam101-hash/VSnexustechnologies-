# V14 adaptive recovery scheduler (Phase 6, workstream C)

Status: implemented in the streaming engine (`src/vnxdna/native/rust/engine/src/{engine,sched}.rs`), default
`engine_scheduler="strand"`. Every number in this document is SIMULATED (channel simulator, evidence category D) and
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
- **I4 (a verified frame is a codeword).** A read is accepted only when its frame decodes with the inner RS code, the
  descrambled CRC-32 verifies and the version nibble and kind are valid (`frame.rs::decode_frames`). The engine keeps
  that corrected frame (`Batch::vframe`, `FrameStore`) and compares later reads with it. The CRC is error detection: it
  is never used as identity evidence, and the container's SHA-256 check and the ladder are unchanged.
- **I5 (per-read independence).** A read's pass-1 outcome never depends on the other reads of the call
  (`pass1.rs` module comment): skipping one read changes no other read's records.
- **I6 (what pass 2 does with copies).** Accepted records of one address are resolved by strict majority of identical
  payloads (`recovery/consensus.py::resolve_duplicates`); pending records feed the consensus of addresses that have no
  accepted record. A confirmed copy of a verified address therefore adds at most one more identical vote, and its
  pending record (if it had failed) would only have been read for the address its damaged header named.

## 3. The strand check (the only identity evidence)

`sched::strand_unconfirmed(segs, band, t, read, frame_words, budget)`: the read (in the orientation the screen
estimates) is compared with the verified frame K byte by byte. The frame's bytes are split into segments of at most 6
bytes whose bases are contiguous on the template (a marker period). Walking the segments with a running shift d (read
position = template position + d), each segment takes the shift among d, d−1, d+1, d−2, d+2 (|d| ≤ band) at which the
most bytes agree in all four bases; a segment counts only when at least half of its bytes agree at that shift (otherwise
all its bytes are unconfirmed and d is kept); a byte is *confirmed* when its four bases equal K's. N bases and positions
outside the read never agree. A pre-check scores the last two segments first, anchored at the read end; it can only
reject. The read is a **confirmed copy of K** when at most `budget` of the frame's bytes are unconfirmed.

`budget = r + 1 − margin` with `margin = min(8, r + 1)`, and no strand check at all when r + 1 < 5
(`sched::strand_budget`): r = 16 → budget 9 (margin 8), r = 20 → 13, r = 12 → 5, r = 4 → 0 (exact agreement, margin 5),
r ≤ 3 → never.

**Why a confirmed copy of K is not a different strand K'.** By I1, K' differs from K in at least r + 1 bytes. The read
agrees with K on all but `budget` bytes, so on at least `margin` bytes where K' differs from K the read shows K's byte.
A reading of K' shows K's byte there only if the channel changed it into exactly K's byte (at least one specific
substitution per such byte, probability ≤ p_sub/3 each), or a wrong shift was chosen in that segment, which needs at
least half of the segment's bytes to agree at the wrong shift (I3: about 256^-3 for a 6-byte segment). With margin 8 and
p_sub = 1 %, a pair (reading of K', verified K) with n differing bytes passes with probability about
C(n, 8)·(0.0033)^8 (n = 30: 8·10^-14). This bound holds for every pair of distinct strands, including strands with
identical payloads, neighbouring addresses one base apart, column-parity strands, and strands of other archives in the
pool: it uses only I1, never the payload, the header or the CRC. It is a probability under the simulated channel model,
not a certainty; a wrong skip loses one reading (other readings of K' are still attempted), never data, and every output
is still verified by the outer code, the container SHA-256 and the ladder.

## 4. Skip conditions (strand mode) and evidence categories

Every skip is a strand-check confirmation against a verified frame; the four places differ only in where the frame
comes from (`engine.rs::batch`):

| reason (counter) | when | frame compared with |
|---|---|---|
| `skip_claim_strand` | a claim (reads linked by payload windows) gets a verified member K; every unresolved member (never attempted, or a failed cheap attempt) is checked | K's frame; if that fails, the stored frames named by the member's header key and windows (another verified strand) |
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
  `store_evictions`, `sched_rescued`, `split_groups`, `rounds`. `skipped` = the sum of the skip-reason counters.
- Fallback activations stay in `report["engine"]["fallbacks"]` (ladder), `report["engine"]["scheduler"]` names the mode.
- Per-read decision trace: `VNX_ENG_TRACE=<file>` appends one line per read: `index decision kind tag group symbol`, with
  decision `A` (accepted; address verified), `P` (pending), `D` (dropped) or `S<reason>` (skipped; the address of the
  frame it was confirmed against, or −1 when the rule records none). The campaign joins it with the simulator's
  ground truth.
- Diagnostic mode: `engine_dedup=False` / `--no-engine-dedup` attempts every read (unchanged from V13).
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
61001 and 61002, one worker): 12 pools × 2 seeds = 24 runs: random data ordered and shuffled with indels; zero runs with
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

## 9. Results

Filled in from the committed result files after the campaign and the A/B runs (next commit).
