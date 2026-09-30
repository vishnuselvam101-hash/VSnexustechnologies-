# Error correction, erasure recovery and their guarantees (V2)

VNX-DNA separates detection, synchronization, correction and verification into distinct layers. They are not
interchangeable, and none of them is claimed to do another's job:

```
nucleotide errors ─▶ synchronization ─▶ per-strand check/correction ─▶ strand erasure ─▶ outer ECC ─▶ archive verification
 (channel)          (cluster/align/vote)  (inner RS + CRC-32)             (CRC fails → erased) (Cauchy K+M)  (SHA-256, AES-GCM, HMAC)
```

| layer | handles | does **not** handle | where |
|---|---|---|---|
| consensus / synchronization | indels and substitutions in individual reads when coverage > 1; exposes ambiguity as `N` | errors common to all reads of a strand | [SYNCHRONIZATION.md](SYNCHRONIZATION.md), [CONSENSUS.md](CONSENSUS.md) |
| inner Reed–Solomon (r bytes per strand) | `e` byte errors + `f` erasures (N, low-quality bases) inside one strand when `2e + f ≤ r` | indels; missing strands | `vnxdna.ecc.inner_rs`, `vnxdna.v2.frame` |
| CRC-32 per strand | *detects* a still-corrupt strand after correction (≈ 2⁻³² undetected per corrupt read) → the strand becomes an erasure | correct anything; resist an attacker | `vnxdna.v2.crc` |
| duplicate resolution | merges validated copies; strict majority wins; a tie becomes an erasure | count as parity | `vnxdna.v2.decoder.resolve_copies` |
| outer Cauchy Reed–Solomon (K+M per ECC group) | *any* M missing/rejected strands of a group | locate errors (the CRC marks them) | `vnxdna.ecc.cauchy`, `vnxdna.v2.encoder.cauchy_parity` |
| SHA-256 (stored chunk, plaintext chunk, object), AES-GCM, HMAC | *verify* (and, with a key, authenticate) the bytes; refuse to output anything else | correct anything | [SECURITY.md](SECURITY.md) |

Reed–Solomon is never presented as an indel code. Insertions and deletions are the synchronization layer's job.

## ECC groups for large files

The outer code never forms a large matrix. A stored chunk of `s` bytes is cut into shards of P bytes and grouped
into **ECC groups (stripes) of K data shards**. Each group gets M parity shards of its own:

```
10 GB input ─▶ ~10,000 chunks (1 MiB) ─▶ each chunk: ⌈s / (K·P)⌉ groups of K+M strands ─▶ ~2.3 × 10⁸ strands (balanced, mixed data)
```

Every group is decoded independently (only its K+M strands), which is what makes streaming decoding, random access
and damage isolation possible. The last group of each chunk is shortened: data shards that would contain only zero
padding are not emitted. The decoder knows they are zero. A shortened MDS code is still MDS, so the guarantee holds
for shortened groups too.

## Parameters and guarantees per profile

| profile | K | M | N = K+M | parity overhead | guaranteed erasures per group | symbol | shard (block) | group data bytes | inner r | inner guarantee | strand |
|---|---|---|---|---|---|---|---|---|---|---|---|
| compact | 128 | 12 | 140 | 9.4 % | any 12 of 140 | GF(2⁸) byte | 48 B | 6,144 B | 6 | 3 byte errors (or 6 erasures) | 276 nt |
| balanced | 64 | 16 | 80 | 25 % | any 16 of 80 | GF(2⁸) byte | 40 B | 2,560 B | 8 | 4 byte errors (or 8 erasures) | 252 nt |
| resilient | 48 | 24 | 72 | 50 % | any 24 of 72 | GF(2⁸) byte | 36 B | 1,728 B | 12 | 6 byte errors (or 12 erasures) | 252 nt |
| archival | 32 | 32 | 64 | 100 % | any 32 of 64 | GF(2⁸) byte | 32 B | 1,024 B | 16 | 8 byte errors (or 16 erasures) | 252 nt |
| metadata strands (all) | 8 | 8 | 16 | 100 % | any 8 of 16 | GF(2⁸) byte | P | 8·P | r | as profile | as profile |

"Guaranteed" means proven (theorem below) and tested at the boundary. Recovery of an archive is **guaranteed** when
every ECC group has at most M unusable strands. A strand is unusable when it is missing (dropout, zero reads), when
all its reads are beyond the inner code and consensus, or when its valid copies tie. If any group exceeds M, decoding
fails with `INSUFFICIENT_REDUNDANCY` (exit 5), names the chunks and groups, and writes nothing. Under *random*
damage, success is a probability that depends on the channel. That is measured, not proven:
[CHANNEL_MODEL.md](CHANNEL_MODEL.md), [EXPERIMENTS.md](EXPERIMENTS.md).

## Outer code: systematic Cauchy Reed–Solomon (MDS)

Arithmetic is in GF(2⁸) with primitive polynomial x⁸+x⁴+x³+x²+1 (0x11D), generator 2 (`vnxdna/ecc/gf256.py`).
For K data shards and M parity shards (K ≥ 1, M ≥ 0, K + M ≤ 256) the generator matrix is

```
G = [ I_K ]        C[i][j] = 1 / (x_i + y_j),   x_i = K + i  (i < M),   y_j = j  (j < K)
    [  C  ]
```

**Theorem (MDS).** Any K rows of G form an invertible matrix, so any K of the K+M shards reconstruct the data, and
any M erasures are recoverable.

*Proof.* Choose K surviving rows: r data rows (set D) and K−r parity rows (set P), with missing data columns
E = {0..K−1} \ D. With columns ordered (D, E) the submatrix is block lower-triangular `[[I_r, 0], [C[P,D], C[P,E]]]`,
so its determinant is `det C[P,E]`. `C[P,E]` is a square Cauchy matrix with distinct x's, distinct y's and no
`x_i + y_j = 0`, so its determinant `∏(x_i−x_k)·∏(y_j−y_l) / ∏(x_i+y_j)` is nonzero. ∎

**Encoder (V2).** `cauchy_parity` computes the M parity shards as an XOR over the K data columns of one (M × 256)
table gather each. The result is bit-identical to the generic GF(2⁸) matrix product (tested for several (K, M)).
**Decoder.** For a group missing data shards E (|E| ≤ M), solve `C[P,E]·d_E = p_P ⊕ C[P,D]·d_D` with a
vectorised Gauss–Jordan inverse. Groups sharing an erasure pattern share the inverse.

## Inner code and CRC

Systematic RS(n, n − r) over the same field (fcr = 0) on the frame bytes (n ≤ 255). V2 computes parity with a
per-column table gather, bit-identical to `reedsolo` (tested). Decoding uses reedsolo's Berlekamp–Massey/Forney
errors-and-erasures decoder, only for reads whose CRC fails, and a result is accepted **only if the CRC-32 verifies
afterwards**, which guards against miscorrection beyond r/2 errors. With the 2bit mapping one substitution corrupts one
byte. `N` calls, and optionally low-quality bases (`--quality-erasure-below`), are passed as erasures, which doubles
what the code can absorb per strand.

## Verification boundary (tests that must stay)

The V1 ECC boundary tests are unchanged and still run (`tests/unit/test_ecc_cauchy.py`, `tests/integration/*`):

| configuration | method | patterns |
|---|---|---|
| 8+4 | exhaustive: every loss of size 0..4, including the V0.1 counterexample {4,5,7,11}, garbage in erased positions | 794 |
| 1+0 … 16+4 (15 configurations) | exhaustive over all loss patterns ≤ M; several also invert every K-row subset | up to 6,196 each |
| 32+8, 64+16, 100+28, 200+56 | deterministic sampled losses | 160 |
| 192+64 | 64 erasures, all on data shards (worst case for the solver) | 2 |
| random 1..20 + 0..8 | Hypothesis property test | 60 per run |
| M + 1 losses | must raise, never miscorrect | tested |

V2 adds, on the full pipeline with frame format 5: exactly M strands deleted in **every** group → exact recovery;
M + 1 in one group → clean failure with exit 5, no output, and `verify` naming the chunk (`tests/v2/test_dna_v2.py`,
`tests/v2/test_streaming_scale_v2.py`), and the same at 1 GB scale with 50 damaged groups
([LARGE_FILES.md](LARGE_FILES.md#large-file-corruption-acceptance)). The inner code is tested at 0–4 byte errors
(corrected) and beyond (rejected by the CRC).
