# A-LOSS: per-strand loss funnel and ORACLE address test of the V7 read-clustering decode (EXPERIMENTAL / DIAGNOSTIC / SIMULATED)

**EXPERIMENTAL / DIAGNOSTIC / SIMULATED.** Software strands, the unfitted V6 nanopore-like channel (stress profile, not
fitted to any platform), the V7 item A reference decoder (`read_clustering="fallback"`, `ClusterConfig()` defaults,
native kernels). No DNA was synthesised, stored or sequenced. Exploration seeds 82046-82055 (protocol §7 range), ten per
cell; no PREREG, no claim. ORACLE rows supply the true source strand of each read and are **not** decoding or acceptance
results. Nothing in the decoder was changed for this run.

## Provenance

| item | value |
|---|---|
| code | commit `7f680eb` (work/v7-nanodecode), tracked tree clean (`code_key` in the `trials.jsonl` header) |
| run | `PYTHONPATH=src nice -n 10 python experiments/v7/a-loss/aloss.py run --config experiments/v7/a-loss/config.json --jobs 6` (21.8 s wall; load average 3.3 at start, 3.8 at end; `log.txt`) |
| summary | `PYTHONPATH=src python experiments/v7/a-loss/aloss.py summarise --config experiments/v7/a-loss/config.json` |
| machinery | `tests/nanopore/nanofunnel.py` (the regression-corpus code): rebuild, simulate with per-read truth (checked base for base), decode, per-strand funnel, ORACLE |
| input | 20,000 random bytes (data seed 6201), uncompressed, v4-balanced: 679 data + 12 superblock strands of 313 nt; inner parity r = 16 B per 70-B frame; rows of 64 + 16 |
| backends | align, reads, RS, cluster: native |
| cache | per trial, keyed by (commit + uncommitted diff of `src/`, `tests/nanopore/`; case; oracle flag) in `/root/vnx-dna-lab/results/cache/a-loss` |

## Funnel (data strands, mean per trial of 679)

| stage survived | cov 3 | cov 5 | cov 10 |
|---|---|---|---|
| observed (≥ 1 read) | 604.2 | 650.7 | 675.5 |
| ≥ 2 reads | 475.9 | 590.1 | 661.5 |
| stored (≥ 2 in the unplaced store) | 475.9 | 590.1 | 661.5 |
| clustered (≥ 2 reads in one cluster it dominates) | 452.5 | 576.5 | 657.9 |
| cluster orientation correct | 452.5 | 576.5 | 657.9 |
| consensus candidate exists | 452.5 | 576.5 | 657.9 |
| **RS-recoverable (2e + f ≤ r)** | **121.3** | **274.5** | **513.9** |
| valid frame | 121.3 | 274.5 | 513.9 |
| data rows decodable (of 9) | 0 | 0 | 2.6 |
| archive EXACT | 0/10 | 0/10 | 0/10 |

FALSE SUCCESS 0/30; false (CRC-valid but untransmitted) frames 0. Cluster read purity 1.000 (0.0-0.3 impure clusters per
trial). Median decode 1.0 / 1.6 / 2.7 s.

**First large irreversible loss: consensus → RS-recoverable** in 30/30 trials (cov 10: 144 strands, 21 % of 679; cov 5:
302; cov 3: 331). Upstream stages lose 18 (cov 10), 89 and 227 strands, mostly to coverage (0 or 1 read).

Lost at the consensus (mean per trial): cov 10: 93.1 shifted segment (indel placed wrong) + 53.7 other errors/erasures;
cov 5: 167.6 + 139.4; cov 3: 161.9 + 175.2. The lost candidates have 4.4 / 3.6 / 3.2 reads on average (cov 10/5/3) and
are lost mainly to erasures (mean f = 28.6 / 35.7 / 41.7 erased bytes against r = 16), with 4.1 / 3.1 / 2.4 wrong
decided bytes; of the wrong decided bases 91 % / 91 % / 89 % equal the true base one position away (a misplaced indel).

## ORACLE address test (DIAGNOSTIC; mean failed strands per trial, data + superblock)

| class | cov 3 | cov 5 | cov 10 |
|---|---|---|---|
| ADDRESS-CAUSED | 0.0 | 0.0 | 0.0 |
| CLUSTERING-CAUSED | 2.1 | 1.8 | 1.6 |
| BOUNDARY-CAUSED (oracle fails; consensus errors are shifted bases) | 170.2 | 173.6 | 95.2 |
| PAYLOAD-CAUSED (oracle fails; errors/erasures beyond r) | 188.5 | 145.6 | 53.9 |
| STRUCTURAL (fewer than 2 stored reads) | 207.0 | 90.5 | 18.0 |
| ORACLE data frames recovered (of 679) | 123.1 | 275.9 | 515.4 |
| ORACLE rows decodable (of 9) / archive SHA match | 0 / 0 of 10 | 0 / 0 of 10 | 2.9 / 0 of 10 |

Reads of failed strands per class (mean per trial, cov 10): BOUNDARY 480, PAYLOAD 175, STRUCTURAL 14, CLUSTERING 10.

## Reading (descriptive)

1. Grouping is not what limits nanopore-like decoding: with the true strand identity the same consensus recovers only 1.4-1.8
   more data frames per trial (515.4 vs 513.9 at cov 10), and the archive still fails in 30/30. Address and clustering together
   explain ≤ 2 strands per trial.
2. The first irreversible loss is the per-cluster consensus: candidates of 2-6 reads leave more than r = 16 bytes
   erased, and most of the decided errors are indels placed one position off. That is the stage the next change must
   target (indel placement and erasure-vs-error handling inside the consensus), within the frame-4 layout.
3. Structural losses (0 or 1 read) set a coverage-dependent floor: about 18 strands per trial at cov 10 (fits the 16-per-row
   outer budget), 90 at cov 5, 207 at cov 3 (exceeds it: cov 3 cannot decode on this model whatever the consensus does;
   a decoding limit, not a target).

## Limitations

Synthetic i.i.d. channel apart from its homopolymer and burst terms, unfitted. One archive, ten seeds per cell. The
BOUNDARY / PAYLOAD split is a heuristic on the oracle consensus (≥ 3 wrong decided bases, ≥ 75 % of them equal to the true
base one position away); both are losses inside the consensus. Stage attribution uses ground truth the decoder never has.
