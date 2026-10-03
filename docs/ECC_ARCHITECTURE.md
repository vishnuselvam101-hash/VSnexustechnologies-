# V4 error-correction architecture

V4 separates **detection**, **synchronisation**, **inner correction**, **outer erasure recovery** and
**verification**. No layer is credited with another layer's job.

```
channel ─▶ synchronisation ─▶ inner RS + CRC-32 ─▶ duplicates / consensus ─▶ outer erasure code ─▶ archive verification
(SIMULATED)  (markers → erasures)   (per strand)       (per address)             (per group)          (SHA-256 / Merkle / AEAD)
```

| layer | handles | does **not** handle | module | status |
|---|---|---|---|---|
| synchronisation | indels within ±band net drift → erasures of the hit segments | indel positions inside a segment; drift beyond the band | `sync` | IMPLEMENTED |
| inner RS(n, n−r), GF(2⁸) | `2e + f ≤ r` byte errors e and erasures f per strand | indels (needs sync); missing strands | `codecs.InnerRS`, `rs_fast` | IMPLEMENTED (V3 code) |
| CRC-32 | *detects* a corrupt frame after correction (≈ 2⁻³² false accept per corrupt frame) | correction; adversaries | `frame.decode_frames` | IMPLEMENTED |
| duplicate resolution | identical verified copies; strict majority; ties dropped | — | `decoder.resolve_duplicates` | IMPLEMENTED |
| consensus | reads that fail alone but agree per position | errors common to all copies | `decoder.consensus_*` | IMPLEMENTED |
| outer Cauchy RS (K+M) | any M missing or rejected symbols per group (MDS, proven in [ECC.md](ECC.md)) | locating errors (CRC marks erasures) | `codecs.CauchyRSCodec` | IMPLEMENTED (V3 code) |
| outer GF(2) fountain | probabilistic erasure recovery; rateless | deterministic guarantee | `codecs.LTFountainCodec` | EXPERIMENTAL |
| SHA-256 / Merkle / HMAC / AES-GCM | verifying (and, with a key, authenticating) the bytes | correction | `container`, `archive`, `crypto` | IMPLEMENTED |

## Codec interface

```python
class OuterCodec(Protocol):
    name: str; stability: str
    def symbols_for(self, k: int) -> int          # coded symbols for a block of k source symbols
    def encode(self, data) -> ndarray             # (k, P) → (symbols_for(k), P); row = symbol index
    def decode(self, symbols: dict, k, length)    # {index: symbol} → (k, P) or VNXDecodeError
    def overhead(self, k=None) -> float
    def capabilities(self) -> dict                # mds, rateless, guarantee, field, max_symbols
    def configuration(self) -> dict               # recorded in reports; code id + parameters in the superblock
# benchmark: vnxdna.v4.bench.codec_compare (same data, same loss model, same redundancy budget)
```

Adding a code means: a class implementing the protocol, an entry in `OUTER_CODECS` and `CODE_IDS` (a new
superblock code id, which older readers refuse), tests, an experiment, and documentation. LDPC and other codes are
**PLANNED**. Nothing is registered until it is implemented, tested and measured.

## Inner code and the fast kernels

The inner code is the V3 systematic RS (fcr 0, generator 2, 0x11D), bit-identical to `reedsolo`. Profiling of a
noisy decode showed the V3 batch decoder's Horner syndromes and Chien/Forney evaluations taking ~75 % of
inner-decoding time. `vnxdna.v4.rs_fast` runs the **same algorithm**, but computes syndromes and fixed-point
polynomial evaluations as XORs of precomputed GF(256) table gathers. It is tested word-for-word against the V3
decoder (corrected codeword, success flag, errata count) on random words with errors and erasures, at and beyond the
bound.

| (n, r) | V3 decoder | V4 fast kernels | speed-up | identical |
|---|---|---|---|---|
| (70, 16) | 152 ms | 50 ms | 3.1× | yes |
| (70, 20) | 194 ms | 66 ms | 2.9× | yes |
| (66, 12) | 106 ms | 35 ms | 3.0× | yes |
| (40, 8) | 45 ms | 18 ms | 2.5× | yes |
| (255, 32) | 975 ms | 187 ms | 5.2× | yes |
| (30, 2) | 10 ms | 7 ms | 1.6× | yes |

These are 3,000 noisy words per row on one core. `VNX_RS_REFERENCE=1` switches V4 back to the V3 decoder.

## Outer codes compared (EXP-0007)

Fairness rules: the same data (6,400 symbols of 40 bytes), the same i.i.d. symbol-loss model, the same seeds per
trial, the same **25 % redundancy budget**, and the same success criterion (every data symbol recovered and
compared byte for byte; a wrong decode would abort the experiment as a bug). Results are in
[INDEL_RESEARCH.md](INDEL_RESEARCH.md#outer-codes) and `experiments/EXP-0007-outer-codes/results.json`.

Robust-soliton LT in the *systematic* setting is kept as a documented negative result. Its low-degree droplets
rarely touch the few missing source symbols, so the residual system is rank-deficient even at 15 % reception
overhead (0/30 trials at k = 256). The dense GF(2) distribution behaves like a random linear code:

| received / k | dense GF(2), k = 256 | robust soliton, k = 256 |
|---|---|---|
| 1.00 | 16/30 | 0/30 |
| 1.02 | 29/30 | 0/30 |
| 1.06 | 30/30 | 0/30 |
| 1.15 | 30/30 | 0/30 |

The fountain decoder (peeling + GF(2) Gaussian elimination) costs O(u²·rows) bit operations for u unresolved
symbols. It is fast for k ≤ 1024 and is not designed for very large blocks.

## Guarantees

* **Per group (Cauchy RS):** any M of K + M strands may be lost. Proven, and tested exhaustively for small codes
  (V3 tests) and at the boundary (`test_cauchy_any_k_of_n_and_m_plus_one_fails`).
* **Per strand:** `2e + f ≤ r` (tested at the boundary; the decoder is strictly bounded-distance).
* **Indels:** no deterministic guarantee. Behaviour is measured (EXP-0002/3/8/9).
* **Fountain:** no deterministic guarantee. Behaviour is measured (EXP-0004 variant, EXP-0007).
* **Never wrong output:** every accepted frame passes CRC-32, every published container passes SHA-256 against the
  superblock and full structural validation, and every extracted file passes its chunk IDs and file SHA-256. Wrong
  data would require a CRC-32 *and* a SHA-256 collision on the same reconstruction.
