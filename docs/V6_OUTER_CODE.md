# V6 outer code: stripes, column parity, interleaved order (superblock version 2)

Status: **IMPLEMENTED, opt-in** (V6 Phase 1, reference implementation `src/vnxdna/v6/`). This document extends
[VNX4_FORMAT.md](VNX4_FORMAT.md) §12–§14; everything not stated here (container, frame, inner code, DNA mapping,
markers, superblock coding, strand files) is unchanged. Archives encoded without the V6 options use superblock
version 1 and are byte-identical to VNX-DNA 5.0.0.

## 1. Superblock version 2

Same 96 bytes as version 1 (VNX4_FORMAT §12) with these differences:

| offset | size | field (version 2) |
|---|---|---|
| 6 | 1 | superblock version `2` |
| 7 | 1 | outer code: MUST be `1` (cauchy-rs) |
| 17 | 1 | fountain distribution: MUST be `0` |
| 18 | 2 | stripe depth D (1 … 65535): maximum data groups per stripe |
| 20 | 1 | column parity Mc (0 … 255): column-parity groups per stripe |
| 21 | 1 | strand order: `0` sequential, `1` interleaved |

A reader MUST reject version 2 if K + M > 256, D = 0, Mc > 0 with D + Mc > 256, or any field above is out of range.
`group count` (offset 86) still counts **data** groups only: G = ⌈container size / (K·P)⌉. Readers that only know
version 1 refuse version 2 as an unsupported superblock version (fail closed).

## 2. Stripes and column-parity groups

* S = ⌈G / D⌉ stripes. Stripe s holds data groups ⌊s·G/S⌋ … ⌊(s+1)·G/S⌋ − 1 (balanced: sizes differ by at most
  one, each ≤ D) and column-parity groups G + s·Mc … G + s·Mc + Mc − 1. Total groups G + S·Mc.
* **Full-position view.** Row g is a vector of K + M symbols of P bytes. Data group g with k_g source symbols
  (VNX4_FORMAT §13) places transmitted symbol t at position t (t < k_g) or K + t − k_g (row parity);
  positions k_g … K − 1 are zero and are not transmitted. Column-parity groups are full rows (k = K, symbols 0 … K + M − 1).
* **Column code.** For each stripe with d data rows and each position j, the vector
  (row_0[j], …, row_{d−1}[j], 0 × (D − d), cp_0[j], …, cp_{Mc−1}[j]) is a systematic Cauchy RS codeword with D data
  and Mc parity symbols over GF(2⁸)/0x11D, parity matrix `C[i][r] = 1 / ((D + i) ⊕ r)` — the same construction as
  the row code with (K, M) replaced by (D, Mc). Missing data rows (d < D) are zeros (shortened code).
* Because both codes are linear and act on different axes, the column-parity rows are themselves row codewords:
  an encoder MAY compute them either as column parity of the full row codewords or as the row encoding of the
  column parity of the data rows (identical result).
* Column-parity groups are carried in ordinary data frames (kind 0) with group index ≥ G.

## 3. Strand order

* **sequential (0):** superblock strands first, then each stripe in order; within a stripe its data groups then its
  column-parity groups, each group's symbols in index order. With Mc = 0 and a single stripe this is the V4 order.
* **interleaved (1):** each stripe position-major: for j = 0 … K + M − 1, for each row of the stripe (data rows then
  column-parity rows) the symbol at position j, skipping not-transmitted padding positions. Superblock strand i
  (0 ≤ i < 4·Ks) is written before data strand ⌊(2i + 1)·N / (2·4·Ks)⌋, where N is the number of non-superblock
  strands (spread evenly over the file).

Order is a property of the written file only; decoders never rely on it. `vnx locate` given the same V6 options as
`vnx encode` reports the records of that file in this order (`Geometry.data_index`, `file_index`, `superblock_index`):
merged `[first, count]` runs for every strand of the located groups, for the data strands holding the bytes, for the
column-parity groups of their stripes and for the superblock strands (job #62).

## 4. Decoding

1. Decode the superblock (unchanged).
2. Decode every row (data and column-parity groups) independently with the row code, exactly as V4 pass 2.
3. For each stripe that has a row with fewer than k verified symbols: build the full-position stripe (decoded rows
   re-encoded; failing rows with their verified symbols; padding as known zeros) and iterate — any row with ≥ K known
   positions is erasure-decoded and re-encoded; any position with ≥ D known rows is erasure-decoded and re-encoded —
   until no change. Data rows that become fully known are written; the others are reported as failed (PARTIAL).
4. The container SHA-256 and full structural validation decide SUCCESS, as in V4 (fail closed).

Erasure decoding never creates information: every symbol it fills is determined by verified symbols. A wrong
verified symbol (an undetected CRC failure) can propagate into wrong data; the final SHA-256 check then refuses to
publish.

## 5. Encoder options

| option (`DNAOptions` / config `dna.*` / `vnx encode`) | meaning |
|---|---|
| `stripe_depth` / `--stripe-depth` | D; `0` = automatic: all data groups in one stripe (≤ 65535) when Mc = 0, otherwise min(G, 128) |
| `column_parity` / `--column-parity` | Mc |
| `strand_order` / `--strand-order` | `sequential` (default for fixed plans) or `interleaved` (default for adaptive) |
| `outer_plan` / `--outer-plan` | `fixed` (K, M from the profile or -K/-M) or `adaptive` |
| `redundancy_budget` / `--redundancy-budget` | adaptive only: maximum redundant strands per data strand (default: profile M / K) |

**Adaptive plan** (`vnxdna.v6.outer.plan`): for row lengths n ∈ {64, 96, 128, 160, 192, 224, 255}, stripe depths
{16, 32, 64, 128} and Mc ∈ {0 … 4}, take the largest M whose exact overhead (row parity including the short last
group, plus column-parity rows) fits the budget, and keep the geometry with the largest analytic i.i.d. dropout
threshold (failure bound ≤ 10⁻³, §6); ties prefer fewer column-parity rows, then shorter rows. Deterministic.

## 6. Analytic estimates

* `failure_bound(geometry, p)`: upper bound on P(not decodable) under i.i.d. strand loss p, superblock excluded. A row
  fails row-wise with probability q = P(Binomial(n_row, p) > M); a stripe certainly decodes if at most Mc of its rows
  fail row-wise; rows are independent. It ignores what further row/column iterations recover, so it is conservative.
* `burst_tolerance(geometry)`: the longest contiguous run of lost strands (file order, any start, superblock strands
  excluded) after which every data row still decodes; exact (tests compare it with brute force).
