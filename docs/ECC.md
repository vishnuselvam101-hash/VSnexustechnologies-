# Error correction and erasure recovery

VNX-DNA uses four distinct mechanisms. They are not interchangeable:

| mechanism | what it does | what it does **not** do |
|---|---|---|
| **CRC-32 per strand** | *detects* a corrupted strand (undetected-error probability ≈ 2⁻³² per corrupt read) | correct anything; resist a deliberate attacker |
| **inner Reed–Solomon per strand** (r parity bytes) | *corrects* `e` byte errors and `f` known erasures inside one strand when `2e + f ≤ r` | correct insertions/deletions; recover a missing strand |
| **outer Cauchy Reed–Solomon across strands** (K+M per stripe) | *recovers* any M missing or rejected strands of a stripe (erasures) | locate errors; it relies on the CRC to mark bad strands |
| **duplicate copies** (from sequencing coverage) | extra independent observations of the same strand; each copy is validated separately | add redundancy that the code can count on; copies are not parity |
| **SHA-256 / AES-GCM / HMAC** | *verify* the final bytes and (with a key) authenticate them | correct anything |

## Outer code: systematic Cauchy Reed–Solomon (MDS)

Arithmetic is in GF(2⁸) with primitive polynomial x⁸+x⁴+x³+x²+1 (0x11D), generator 2 (`vnxdna/ecc/gf256.py`).
For K data shards and M parity shards (K ≥ 1, M ≥ 0, K + M ≤ 256) the generator matrix is

```
G = [ I_K ]        C[i][j] = 1 / (x_i + y_j),   x_i = K + i  (i < M),   y_j = j  (j < K)
    [  C  ]
```

`{x_i}` and `{y_j}` are disjoint subsets of GF(2⁸). Hence `x_i + y_j = x_i ⊕ y_j ≠ 0`, and C is a Cauchy matrix.

**Theorem (MDS).** Any K rows of G form an invertible matrix, so any K of the K+M shards reconstruct the data. Put
differently, *any M erasures are recoverable*.

*Proof.* Choose K surviving rows: r data rows (set D) and K−r parity rows (set P), with the missing data columns
E = {0..K−1} \ D, |E| = K−r. Order the columns as (D, E). The chosen submatrix is block lower-triangular
`[[I_r, 0], [C[P,D], C[P,E]]]`, so its determinant is `det C[P,E]`. `C[P,E]` is a square submatrix of a Cauchy
matrix and is therefore itself a Cauchy matrix. Its determinant is
`∏_{i<k}(x_i−x_k)·∏_{j<l}(y_j−y_l) / ∏_{i,j}(x_i+y_j)`, which is nonzero because the x's are distinct, the y's are
distinct, and no `x_i + y_j` is zero. ∎

**Decoder.** For a stripe missing data shards E (|E| = e ≤ M), take the first e present parity rows P and solve
`C[P,E]·d_E = p_P ⊕ C[P,D]·d_D` with a vectorized GF(2⁸) Gauss–Jordan inverse of the e×e matrix. Stripes that share
an erasure pattern share the inverse. A stripe with fewer than K present shards raises `InsufficientRedundancyError`.
The decoder never guesses.

**Shortening.** The last stripe of each chunk omits data shards that lie entirely in zero padding. They are known
zeros, so present on decode. Deleting known symbols from an MDS code gives a shortened code that is still MDS, and the
guarantee "any M emitted strands of the stripe may be lost" still holds.

**Why not the V0.1 code.** V0.1 used `G = [I; a_i^j]` with `a_i = i+1`. That generator is not MDS: for 8+4, 10 of the
495 four-shard losses are singular, among them {4,5,7,11} (see `docs/V0.1_BASELINE.md`). `tests/integration/test_legacy_compat.py`
keeps that counterexample as regression evidence against the old matrix.

## Verification boundary (what was checked, how)

| configuration | method | patterns |
|---|---|---|
| 8+4 | **exhaustive**: every loss of size 0..4, including {4,5,7,11}, with garbage in erased positions | 794 |
| 1+0, 1+1, 1+3, 1+5, 2+1, 2+2, 3+2, 4+2, 4+4, 5+3, 6+2, 6+6, 10+4, 12+4, 16+4 | exhaustive, all loss patterns ≤ M; for several, also every K-row subset inverted | up to 6,196 per configuration |
| 32+8, 64+16, 100+28, 200+56 | deterministic sampled losses (40 each, `random.Random(f"K/M")`) | 160 |
| 192+64 | 64 erasures at random positions, and all 64 on data shards (worst case for the solver) | 2 |
| any 1..20 + 0..8 | Hypothesis: random K-subsets, random data | 60 per run |
| M+1 losses | must raise, never miscorrect | tested |
| 64+16 default, full pipeline | exactly M strands removed from every stripe (adversarial boundary) → exact recovery; M+1 in one stripe → clean failure | integration tests |

Beyond these, the guarantee for other (K, M) rests on the theorem above.

## Inner code: per-strand Reed–Solomon

`vnxdna/ecc/inner_rs.py`: a systematic RS(n, n−r) over the same field (fcr = 0), with n = frame bytes ≤ 255.
Parity is computed as a vectorized linear map and checked bit-for-bit against `reedsolo`. Decoding uses reedsolo's
Berlekamp–Massey/Forney errors-and-erasures decoder, and runs only for reads whose CRC fails. A decoded frame is
accepted **only if its CRC-32 verifies afterwards**, which guards against miscorrection beyond `r/2` errors.

With the 2bit mapping, one base substitution corrupts exactly one byte. With the default r = 8, the inner code
corrects up to 4 substitutions per 244-nt strand. Measured thresholds are in [CHANNEL_MODEL.md](CHANNEL_MODEL.md).

## Recovery guarantees (default profile 64+16, P = 40, r = 8)

Recovery is guaranteed (proven, and tested at the boundary) if **every stripe has at most 16 unusable strands**. A
strand is unusable when it is missing (dropout / zero reads), its only reads have more than 4 byte errors, its only
reads have an indel (unless experimental repair fixes them), or all its valid copies tie in a conflict. The metadata
stripes tolerate 8 of 16.

If any stripe exceeds that, decoding fails with `INSUFFICIENT_REDUNDANCY` (exit 5) and writes nothing. For random
damage, the probability of success depends on the rates. That is an empirical question, and CHANNEL_MODEL.md gives
the measurements. Increase `--parity-shards` for harsher channels (for example 96+48 tolerates 48 losses in 144).
