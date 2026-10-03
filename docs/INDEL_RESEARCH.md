# Indel and erasure research (V4)

The measured side of [INDEL_ENGINE.md](INDEL_ENGINE.md) and [ECC_ARCHITECTURE.md](ECC_ARCHITECTURE.md). Every
number here is **SIMULATED** (software strands, the V4 channel model) and comes from `experiments/*/results.json`.
The complete generated tables are in [V4_RESULTS.md](V4_RESULTS.md). Unless stated: 256 KiB random input, 10 trials
per point, coverage "fixed" (exactly that many reads per surviving strand). Small trial counts mean wide
uncertainty near thresholds: 10/10 is consistent with a true failure rate of up to ~26 % (one-sided 95 %).

Two rounds were run. **Round 1** (commit 2cd82b3; results in git at eb8d225) used v4-balanced with 2-nt markers
every 32 nt and no address snapping. **Round 2** (commit a387b34, the current experiment directories) uses the
changes round 1 motivated: 3-nt markers every 24 nt, and address snapping (engineering log L12, L13).

## 1. Is RS alone enough for indels? No.

EXP-0008, coverage 1, equal insertion and deletion rates, the same outer code (Cauchy RS 64 + 16):

| layout | nt / input byte | 0 | 0.05 % + 0.05 % | 0.1 % + 0.1 % | 0.2 % + 0.2 % | 0.3 % + 0.3 % | 0.5 % + 0.5 % |
|---|---|---|---|---|---|---|---|
| no markers (v4-dense, r = 12) | 8.02 | 10/10 | **0/10** | 0/10 | 0/10 | 0/10 | 0/10 |
| v4-balanced round 1 (2-nt / 32 nt, r = 16) | 9.31 | 10/10 | 10/10 | 10/10 | 1/10 | 0/10 | 0/10 |
| v4-balanced round 2 (3-nt / 24 nt, r = 16) | 9.85 | 10/10 | 10/10 | 10/10 | **10/10** | 0/10 | 0/10 |
| v4-indel (3-nt / 24 nt, r = 20, P = 36) | 10.95 | 10/10 | 10/10 | 10/10 | 10/10 | 6/10 | 0/10 |

Without synchronisation, a single indel per read is fatal. RS sees it as a burst to the end of the strand. Markers
plus erasures move the coverage-1 threshold from below 0.05 % to between 0.2 % and 0.3 % per base, for 23 % more
nucleotides than the marker-free layout.

## 2. Marker design (EXP-0009)

Coverage 1, 0.2 % insertions + 0.2 % deletions, the same outer code. Success vs cost:

| P | r | period | length | strand nt | nt/B | success |
|---|---|---|---|---|---|---|
| 40 | 16 | — | 0 | 280 | 8.81 | 0/10 |
| 40 | 16 | 48 | 2 | 290 | 9.12 | 0/10 |
| 40 | 16 | 32 | 2 | 296 | 9.31 | 2/10 |
| 40 | 16 | 24 | 2 | 302 | 9.50 | 9/10 |
| 40 | 16 | 32 | 3 | 304 | 9.56 | 5/10 |
| 40 | 16 | 32 | 4 | 312 | 9.82 | 6/10 |
| **40** | **16** | **24** | **3** | **313** | **9.85** | **10/10** |
| 40 | 16 | 16 | 2 | 314 | 9.88 | 10/10 |
| 36 | 20 | 32 | 3 | 304 | 10.63 | 10/10 |
| 36 | 20 | 24 | 3 | 313 | 10.95 | 10/10 |
| 36 | 20 | 16 | 3 | 331 | 11.58 | 10/10 |
| 32 | 24 | 24 | 3 | 313 | 12.31 | 10/10 |

(Identical in rounds 1 and 2: the same configuration and seeds reproduced every outcome after the decoder changes.)

Findings:
- **Marker period matters more than length.** Shorter segments cost less inner parity per indel (S/4 erased
  bytes).
- **Longer markers help mostly by reducing chance matches** (1/16 for 2 nt vs 1/64 for 3 nt), which otherwise
  misplace an indel into the neighbouring segment.
- Shifting bytes from payload to inner parity (P 36, r 20) buys robustness at a higher cost per byte than shortening
  the period.

The default became 24/3 (decision L12).

## 3. Consensus and grouping (EXP-0005)

Coverage 1–100× (Poisson), 1 % substitutions + 0.4 % insertions + 0.4 % deletions, 64 KiB input, 5 trials:

| coverage | 1 | 2 | 5 | 10 | 20 | 30 | 50 | 100 |
|---|---|---|---|---|---|---|---|---|
| round 1 (no snapping) | 0/5 | 0/5 | **1/5** | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| round 2 (snapping + 24/3 markers) | 0/5 | 0/5 | **5/5** | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |

Decode time grows roughly linearly with the number of reads (median 2.4 s at 5× and 48 s at 100× in round 1, single
worker). Coverage beyond the point where every strand has one decodable read buys nothing but cost. In isolation,
consensus over 5 reads recovered 99.3 % of symbols at these error rates. The bottleneck was **grouping** reads by
their (corrupted) address, which snapping addresses (L13).

## 4. Substitutions, insertions, deletions separately (EXP-0001/2/3, round 2)

| channel (per base) | coverage 1 | coverage 5 |
|---|---|---|
| substitution ≤ 1 % | 10/10 at every point | 10/10 |
| substitution 2 % | 9/10 | 10/10 |
| insertion ≤ 0.1 % | 10/10 | 10/10 |
| insertion 0.5 % / 1 % | 0/10 / 0/10 | 10/10 / 10/10 |
| deletion ≤ 0.1 % | 10/10 | 10/10 |
| deletion 0.5 % / 1 % | 5/10 / 0/10 | 10/10 / 10/10 |

## 5. Mixed channel (EXP-0006, round 2)

| level (sub, ins, del, dropout) | coverage 1 | coverage 5 (Poisson) |
|---|---|---|
| L1 (0.1 %, 0.02 %, 0.02 %, 1 %) | 10/10 | 10/10 |
| L2 (0.2 %, 0.05 %, 0.05 %, 2 %) | 10/10 | 10/10 |
| L3 (0.5 %, 0.1 %, 0.1 %, 5 %) | 9/10 (round 1: 0/10) | 10/10 |
| L4 (1 %, 0.2 %, 0.2 %, 10 %) | — | 2/10 (round 1: 0/10) |
| L5 (2 %, 0.5 %, 0.5 %, 15 %) | — | 0/10 |

L4 and L5 fail mainly through **dropout plus small outer-code groups**. Ten to fifteen percent of strands lost,
plus strands lost to errors, exceed 16 of 80 in some group (§6).

## 6. Strand dropout and outer codes

**EXP-0004** (coverage 1, no base errors): Cauchy RS 64+16 (20 % parity) recovers up to 5 % dropout reliably, 8/10
at 10 %, 0/10 at 15 %. Cauchy RS 32+32 (50 % parity, 17.5 nt/B) recovers up to 30 % dropout and 0/10 at 40 %.
Losing 15 % of strands with 20 % parity fails because some of the ~100 groups lose more than 16 of their 80 strands
(binomial tail). The guarantee is per group.

<a id="outer-codes"></a>**EXP-0007** (erasure only, 6,400 symbols, the same 25 % parity/data for all, 200 trials):

| loss | 0.10 | 0.12 | 0.14 | 0.16 | 0.18 |
|---|---|---|---|---|---|
| Cauchy RS 64+16 | 0.805 | 0.25 | 0 | 0 | 0 |
| Cauchy RS 200+50 | 1.0 | 1.0 | 0.905 | 0.25 | 0 |
| fountain, dense GF(2), 256+64 | 1.0 | 0.995 | 0.91 | 0.305 | 0 |
| fountain, robust soliton, 256+64 | 0 (already 0 at 0.05) | 0 | 0 | 0 | 0 |

**EXP-0016** (the same comparison through the full DNA pipeline, 10 trials, round 2):

| dropout | 0.10 | 0.12 | 0.14 | 0.16 | 0.18 |
|---|---|---|---|---|---|
| Cauchy RS 64+16 (default) | 9/10 | 2/10 | 0/10 | 0/10 | 0/10 |
| Cauchy RS 200+50 | 10/10 | 10/10 | 9/10 | 1/10 | 0/10 |
| fountain dense 256+64 (EXPERIMENTAL) | 10/10 | 10/10 | 9/10 | 3/10 | 0/10 |

Conclusions:
- **At an equal redundancy budget, block length dominates; code family barely matters.** The dense fountain over
  256 symbols and RS over 250 symbols are statistically indistinguishable. Both clearly beat RS over 80 symbols.
- **The fountain code is not automatically superior.** It is not MDS, its guarantee is probabilistic, and its
  advantage is structural: no 256-symbol limit, and rateless operation (more droplets can be generated later).
- **Recommendation (not yet the default):** for dropout-dominated channels, use larger outer groups (e.g. RS
  200+50, the maximum the GF(256) Cauchy code allows) or a large-block fountain. The default stays 64+16, which costs
  less per group to decode and limits damage per group. Making this choice adaptive is a V5 item.
- **Classical LT is a negative result** in the systematic setting (L2).

## 7. Channel-model sensitivity (EXP-0013, round 2)

| model | result |
|---|---|
| homopolymer indel multiplier ×1 / ×5 / ×20 (0.1 % + 0.1 % base, coverage 1) | 10/10 / 10/10 / 0/10 (round 1 ×5: 9/10) |
| GC coverage bias strength 0 / 1 / 4 (coverage 3, Poisson) | 10/10 / 10/10 / 6/10 |
| negative-binomial coverage k = 1 / 0.5 (mean 3) | 0/10 / 0/10: 26–39 % of strands get zero reads, beyond 20 % parity |

Screening keeps homopolymers ≤ 4, so homopolymer-dependent indels hurt only at extreme multipliers. Very uneven
coverage is an outer-code problem (more redundancy or larger blocks), not a synchronisation problem.

## 8. V3 vs V4 under the identical channel (EXP-0010, round 2)

The same 128 KiB input, the same channel implementation, parameters and per-trial seeds, the same success criterion.
V3 uses its default balanced profile (8.08 nt/B); V4 uses v4-balanced (9.92 nt/B, **+23 % nucleotides**).

| channel | V3 default | V3 best (indel + burst repair) | V4 |
|---|---|---|---|
| cov 1, sub 0.2 % | 6/6 | 6/6 | 6/6 |
| cov 1, del 0.05 % | 6/6 | 6/6 | 6/6 |
| cov 1, del 0.1 % | 0/6 | 6/6 | 6/6 |
| cov 1, del 0.2 % | 0/6 | 6/6 | 6/6 |
| cov 1, ins 0.1 % + del 0.1 % | 0/6 | 6/6 | 6/6 |
| cov 1, ins 0.2 % + del 0.2 % | 0/6 | 1/6 | **6/6** |
| cov 1, dropout 10 % | 6/6 | 6/6 | 6/6 |
| cov 5, sub 0.5 %, ins/del 0.1 %, dropout 2 % | 6/6 | n/a | 6/6 |
| cov 5, sub 1 %, ins/del 0.4 %, dropout 2 % | 0/6 | n/a | **6/6** |

Median decode time: V4 is 4–15× faster on every point (e.g. 4.3 s vs 25.6 s on the hardest coverage-5 point,
single worker). V3's best-mode repair matches V4 at moderate indel rates at coverage 1. V4's advantage is at higher
indel rates, on noisy multi-read data, and in speed. Its cost is nucleotides.

## 9. Open research questions (V5)

1. Indel position search inside an erased segment (try each position, verify with RS) to shrink erasures from S/4
   bytes to ~1–2.
2. Realignment against the consensus (second pass with known bases instead of wildcards) for multi-read data.
3. Larger or adaptive outer groups, and cross-group interleaving, for dropout and uneven coverage.
4. Watermark/VT/marker-code hybrids with soft-decision decoding using the consensus posterior.
5. Channel models fitted to published sequencing data, and physical validation.
