# Indel engine (marker-based synchronisation → erasures)

Status: **IMPLEMENTED** (SIMULATED channel only). Code: `src/vnxdna/v4/sync.py` (alignment),
`src/vnxdna/v4/decoder.py` (orchestration and consensus), `src/vnxdna/v4/frame.py` (template). Measurements:
[INDEL_RESEARCH.md](INDEL_RESEARCH.md).

Reed–Solomon codes correct *substitutions* and *erasures* at known positions. An insertion or deletion shifts every
later base, so to a symbol-aligned code one indel looks like a burst of errors to the end of the strand. **RS alone
does not handle indels**, and nothing in V4 claims it does. The indel engine's job is to turn indels into a small
number of *erasures* that the inner RS code can afford.

## 1. Strand template

The frame (`4·(14+P+r)` bases) is interleaved with short known markers (§11 of [VNX4_FORMAT.md](VNX4_FORMAT.md)):

```
[ segment 0: S frame bases ][m0][ segment 1: S frame bases ][m1] … [ last segment ]
```

The decoder knows the template: the marker bases at fixed positions, and "unknown" (wildcard) at frame positions.
With S a multiple of 4, every segment is exactly S/4 frame bytes.

## 2. Alignment (global, banded, vectorised)

For a read `x` (length n) and the template `t` (length T), with band B (default 6, the maximum net drift):

```
D[i][d] = minimal cost of aligning t[:i] with x[:i+d],   |d| ≤ B
D[i][d] = min( D[i−1][d]   + c_sub(t[i−1], x[i−1+d]),    match / substitution (diagonal)
               D[i−1][d+1] + c_del(t[i−1]),              template base missing from the read (deletion)
               D[i][d−1]   + c_ins )                     extra read base (insertion)
```

* `c_sub` = 0 at frame positions (content unknown) and `marker_mismatch` (4) at marker positions when the bases
  differ.
* `c_ins = c_del = 6`. A deletion *at a marker base* costs 6 + 1: an exact tie-break that prefers blaming the frame
  base, so only one segment is erased (measured below).
* Initial row: leading insertions; the end state is `D[T][n − T]`. Reads with `|n − T| > B` are not aligned.
* **Retry band (V6, opt-in, `--retry-band R`, default 0 = off).** Reads with `B < |n − T| ≤ R` are aligned once more
  by the same DP with band R (native kernel unchanged, contract valid for R ≤ 64); reads with `|n − T| ≤ B` never reach
  it, so their projection is identical. Measured in `experiments/v6/align-band` (SIMULATED): deletion-heavy at
  coverage 10 goes from 0/20 to 6/20 with R = 16 and no cell loses a decode, but nanopore-like stays 0/20 (its reads
  then align, yet their headers rarely give the right address and whole-segment erasure leaves too little for
  consensus), so it is not the default.
* The insertion recurrence inside a row is solved for all offsets at once:
  `D'[w] = w·c + min_{k ≤ w}(D[k] − k·c)` (a cumulative minimum). This produces the same alignment as the
  sequential loop (verified: identical decode results on the indel test set). In the profiled noisy decode it cut
  the aligner's own time from 7.6 s to 5.1 s (cProfile self-time).
* Vectorisation: the Python loop runs over template positions (T ≈ 300) and NumPy handles all reads of a batch
  (2048) and all band offsets together. Traceback stores one int8 pointer per cell: T × (2B+1) × batch bytes
  (≈ 8 MB per batch).

**Complexity:** O(T·(2B+1)) time and pointer memory per read. Measured at about 9,000 reads/s per core including
traceback ([PERFORMANCE.md](PERFORMANCE.md)).

## 3. From alignment to erasures

During traceback every indel is assigned to a segment:

* a **deleted frame base** blames its own segment;
* a **deleted marker base** blames both neighbouring segments (ambiguous);
* an **inserted base** between template positions i−1 and i blames the adjacent *frame* segment(s). If one
  neighbour is a marker, only the frame side is blamed: a marker aligned without shift shows the shift starts on its
  frame side.

All frame bytes of a blamed segment become erasures. So do bases aligned to `N`, bases below `--min-quality`, and
template bases deleted from the read. Bytes outside blamed segments keep their read values; residual substitutions
are left to RS, which corrects `e` errors and `f` erasures when `2e + f ≤ r`.

**Why the whole segment.** Frame positions are wildcards, so every placement of an indel inside a segment has the
same cost. The DP cannot know where in the segment the shift started, and the bytes between the true and the chosen
position would be wrong without being flagged. Erasing the whole segment bounds the damage at S/4 bytes per indel
without guessing.

Measured on 500 random v4-balanced strands (S = 32, ℓ = 2, r = 16) before and after the blame rules:

| case | erased bytes per read (naive rule) | after "frame side" insertion rule | after marker-deletion tie-break |
|---|---|---|---|
| 1 insertion | 14.6 | 9.8 | 9.8 |
| 1 deletion | 14.8 | 15.0 | 8.2 |
| 1 ins + 1 del: decoded | 54.8 % | 58.0 % | 75.6 % |

## 4. Read pipeline around the aligner

1. **Orientation.** Marker agreement at the unshifted positions, forward vs reverse complement. A read is flipped
   when the reverse complement agrees by more than max(2, markers/8) bases. Failures are retried in the other
   orientation.
2. **Fast path.** Reads of exactly the strand length: markers stripped, CRC first, then RS. Exact-length reads
   whose markers all match skip the DP (it would reproduce the same frame).
3. **Sync path.** Everything else within the band: DP → erasures → RS (no errors-only retry, because sync
   erasures come from detected indels, not from unreliable flags) → CRC.
4. **Pending reads.** Aligned reads that still fail, and whose 10 header bytes are all unerased, are grouped by
   their tentative address. Reads whose header is erased are counted as *orphans*.
5. **Consensus.** Per address, a vote over the projected reads (erased positions abstain) gives a posterior per
   position (`consensus_soft`). Bases with posterior < 0.6 become erasures; then RS, then CRC. The decoded address
   must equal the group's address.
6. The outer code fills in missing or failed symbols. The final container SHA-256 decides success.

## 5. Assumptions

* Indels are sparse enough that the net drift stays within the band (B = 6 by default).
* Markers are not systematically destroyed. A substitution inside a marker costs 4, roughly two-thirds of an indel,
  so an isolated marker hit is absorbed as a mismatch.
* The channel model is i.i.d. per base, plus the optional homopolymer multiplier and bursts. Real platforms have
  context-dependent errors that are **NOT VALIDATED** here.

## 6. Failure modes (all measured or tested)

| failure | effect | detection |
|---|---|---|
| two indels in different segments plus substitutions exceeding r | read fails RS | CRC/RS reject → pending → consensus or outer code |
| +1 −1 within a few markers | short markers may "explain" the shift as marker mismatches, so the shifted bytes become *errors* (2 units each) | RS fails → CRC; longer markers (ℓ = 3) reduce this (EXP-0009) |
| chance marker match (1/16 for ℓ = 2) next to an indel | indel blamed in the neighbouring segment; some bytes wrong but not erased | RS/CRC; shows up as a lower success rate, never as wrong output |
| net drift > band | read not aligned (unless the opt-in retry band covers it) | counted (`unaligned`; `retry_band_reads` with the option) |
| header bytes erased | read cannot be grouped for consensus | counted (`orphans`); recovery relies on other reads or the outer code |
| burst longer than the band | read not aligned | counted |
| indel in every copy of a strand (synthesis error) | consensus reproduces it | the strand fails → outer code |

None of these can produce wrong output. Every accepted frame passed CRC-32, and every published container passed its
SHA-256. Fuzz test: `test_fuzz_frames_never_accept_wrong_payload`.

## 7. Alternatives considered (see [INDEL_RESEARCH.md](INDEL_RESEARCH.md))

| approach | status | why |
|---|---|---|
| RS only (no sync) | measured as the `v4-dense` profile | fails as soon as reads carry indels at coverage 1 |
| V3 single-read realignment / burst repair | V3, measured in EXP-0010 | 1 indel (or up to 3 same-direction) or 1 burst per read; costly for 2–3 indels |
| **marker template + erasures** | **IMPLEMENTED (V4)** | bounded damage per indel, vectorised, no hypothesis search |
| marker template + per-segment position search (try every indel position, check with RS) | PLANNED | could reduce an erased segment to a few bytes, at a combinatorial cost |
| watermark / Davey–MacKay codes, VT codes | PLANNED | stronger synchronisation per redundancy bit; much more complex decoding |
| neural / learned decoders | not planned for V4 | classical baseline first, as the mission requires |
