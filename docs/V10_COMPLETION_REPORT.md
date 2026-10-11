# VNX-DNA V10 — completion report (hybrid native architecture)

Release: tag `v10.0.0` on the merge of `arch/hybrid-native` into `main`. Base: v9.0.0 (`e76e24b`).
Evidence label: **SIMULATED** throughout — software channel models only; no DNA was synthesised, stored or sequenced.

## Scope
V10 began as feature work (`docs/V10_ENGINEERING_PLAN.md`) and became, by founder directive (2026-10-10), the hybrid
native architecture: hot paths moved to justified native code with Python references, plus a decoder for the
nanopore-like channel that V9 could not recover. The V10 feature drafts not merged here remain outside this release.

## What changed (details in CHANGELOG.md and docs/HYBRID_ARCHITECTURE.md)
| Area | Language | Module | Reference |
|---|---|---|---|
| Strand builder (encode) | C11 | `native/c/strand.c`, ABI 1 | Python strand builder |
| Fused polish kernel | C11 | `native/c/cluster.c`, ABI 5 | `recovery/cluster/polish` |
| Frame CRC-32 batches | Rust | `native/rust/integrity`, ABI 1 | `core/crc` |
| Outer-code list decoding | C++17 | `native/cpp/outerlist.cpp`, ABI 1 | `recovery/outer_list.py` |
| Web contracts and dashboard | TypeScript | `web/` | JSON schemas in `core/schemas` |

Every kernel: bit-exact equivalence with its reference (golden hashes, randomized tests), ASan/UBSan builds, libFuzzer.

## Results
**Compatibility.** v9.0.0 and v10.0.0 produce byte-identical FASTA on 10/10 encodes and identical outcomes and
containers on 31/31 decodes; 0 false success (docs/ARCH_COMPARISON_REPORT.md).

**Speed versus v9.0.0** (one host, paired seeds): encode 1.15–1.23×; decode 1.02–1.14× on standard channels;
nanopore-like 1 MiB decode 7.2× (about 1,017 s → 140 s, both EXPLICIT_FAILURE under preset E).

**Nanopore-like recovery (preset F, opt-in).** Three pre-registered held-out evaluations, D13-F1, 1 MiB, coverage 10:

| evaluation | seeds | E exact | F exact | false success | McNemar p |
|---|---|---|---|---|---|
| 1 (F at 1cb416d) | 93020–93029 | 2/10 | 7/10 | 0 | — |
| 2 (F + RANSAC) | 93030–93049 | 1/20 | 15/20 | 0 | 0.0001 |
| 3 (F + round fix) | 93050–93069 | 2/20 | 15/20 | 0 | 0.0002 |

F never lost a case that E recovered, and outputs are identical where both succeed. Code changed between rounds, so
the three rounds are not pooled into one estimate.

## Verification at the release commit
Full test suite, ruff, install test (`VNXDNA_INSTALL_TEST=1`), TypeScript tests, Rust tests; native sanitizer and
libFuzzer runs per kernel (logs under `results/arch/` and `/root/vnx-dna-lab/tmp/rel10/`). Counts are recorded in the
release commit message.

## Known limits
- 5 of the 20 seeds in evaluation 3 still fail under F (one or two rows each): their failed frames have too few
  correct bytes. Next levers: individual pending reads as candidates; better read consensus (V10.1 work).
- 10 MB nanopore-like archives remain EXPLICIT_FAILURE.
- Default strands are 313 nt, above common 300-nt oligo-pool limits; a shorter profile is needed before ordering DNA.
- All results are simulated on one host.
