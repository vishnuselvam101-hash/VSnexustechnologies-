# V7 nanopore-like decoding: root cause and fixes (EXPERIMENTAL, SIMULATED)

Deliverable of the founder's V7 nanopore root-cause directive (2026-10-06). Every number below is a **SIMULATED RESULT**:
software strands on the unfitted V6 nanopore-like channel (a stress profile, not fitted to any platform). No DNA was
synthesised or sequenced, and no nanopore model is ADEQUATE yet (`docs/V7_CHANNEL_MODELS.md`). Nothing here supports a
claim of nanopore support.

Test input for every experiment: 20,000 random bytes, uncompressed, 313-nt strands, inner parity r = 16 B per 70-B frame.
v4-balanced = 679 data strands, rows of 64 + 16. v7-lowcov = 967 data strands, rows of 64 + 48.

## 1. Exact funnel

Before any decoder change (A-LOSS, `experiments/v7/a-loss/`, exploration seeds 82046-82055). Data strands, mean per trial of 679:

| stage survived | cov 3 | cov 5 | cov 10 |
|---|---|---|---|
| observed (≥ 1 read) | 604.2 | 650.7 | 675.5 |
| ≥ 2 reads stored | 475.9 | 590.1 | 661.5 |
| clustered, orientation correct, consensus candidate | 452.5 | 576.5 | 657.9 |
| **RS-recoverable (2e + f ≤ r)** | **121.3** | **274.5** | **513.9** |
| archive EXACT | 0/10 | 0/10 | 0/10 |

## 2. First irreversible loss

The loss happens between the per-cluster consensus and the inner Reed-Solomon code, in 30 of 30 trials. At cov 10, 144
strands (21 % of 679) are lost there. Every stage before it loses 18 strands in total, almost all to coverage.

## 3. Root cause

The consensus put indels in the wrong place. In the lost candidates, 89-91 % of the wrong decided bases equal the true base
one position away. The misplaced indels also left more erased bytes than the inner code can correct: mean f = 28.6, 35.7
and 41.7 erased bytes against r = 16.

The ORACLE address test supplied the true strand identity of each read. It found 0 address-caused failures and 1.6-2.1
clustering-caused failures per trial. With perfect grouping, at most 1.8 more data frames per trial were recovered
(diagnostic only).

## 4. Competing hypotheses tested

| hypothesis | test | verdict |
|---|---|---|
| address extraction / assignment (directive phases 2, 6) | ORACLE address test | rejected: 0 address-caused |
| read clustering | ORACLE grouping | minor: ≤ 2.1 strands per trial |
| boundary / payload alignment | ORACLE classes | the lost strands sit inside the consensus (shifted bases + erasures) |
| quality weighting, larger `retry_band` | V6_DEFERRED §3 | no gain |
| more consensus polish rounds (3 / 6 / 10) | probe, dev seed 82043 cov 5 | 463 / 464 / 466 frames: not the lever |
| plurality fill of erasures | fast dev cases | worse: erasures became shifted errors; reverted, not committed |
| inner RS / erasure policy | A-RS, dev seeds 82043-82055 cov 5 | cannot fix cov 5: partial strands add 0 rows |
| outer redundancy sized from measured loss | A-PAR, pre-registered | fixes cov 5 (see 7) |

## 5. Experiments

| id | directory | type | seeds |
|---|---|---|---|
| A-DIAG | `experiments/v7/a-diag` | diagnostic baseline | 82000-82019 |
| A-LOSS + ORACLE | `experiments/v7/a-loss` | diagnostic | 82046-82055 |
| A-CONS | `experiments/v7/a-cons` | pre-registered | frozen corpus, then held-out 82060-82099 |
| A-RS | `experiments/v7/a-rs` | diagnostic | dev 82043-82055 |
| A-PAR | `experiments/v7/a-par` | pre-registered | dev 82043-82045, held-out 82060-82099 |

Frozen regression corpus: `tests/nanopore/` (corpus, expected, diagnostics, `reproduce.py`, `test_nanopore_corpus.py`).

## 6. Implementation changes

All of these are opt-in. The defaults, the frame-4 layout, addressing and clustering are unchanged.

- A1/A2: header-independent read clustering, `read_clustering="fallback"`. Reference implementation plus the native
  `cluster.c` kernel, bit-exact with the reference and ASan/UBSan clean.
- Consensus phase 1: full-template consensus polish with marker-anchored shift moves, `consensus_template="full"`
  (`polish.py`). The old consensus stays the reference path.
- Profile `v7-lowcov`: the v4-balanced strand layout with row code 64 + 48. It uses only existing format options. Test:
  `tests/v6/test_redundancy_profiles.py::test_v7_lowcov_survives_strand_loss_beyond_balanced`.

## 7. Before and after (held-out seeds 82060-82099, 40 per cell)

| cov | V6 / A1 reference | + full consensus (v4-balanced) | + full consensus (v7-lowcov) |
|---|---|---|---|
| 3 | 0/40, 124 / 679 frames | 0/40, 295 / 679 | 0/40, 422 / 967 |
| 5 | 0/40, 280 / 679 | 0/40, 463 / 679 | **38/40** (Wilson 0.84-0.99), 665 / 967 |
| 10 | 0/40, 513 / 679 | **40/40** (Wilson 0.91-1), 618 / 679 | **40/40**, 880 / 967 |

The table shows EXACT (SHA-256) results and mean data frames per trial. There were 0 FALSE SUCCESS and 0 false frames in
every cell.

Cost:
- The full consensus decodes about 15x slower than the reference (cov 10: 42 s per decode). Peak RSS is about 330 MiB.
- v7-lowcov adds 42 % strands and nucleotides, and decodes about 1.4x slower again (cov 10: 58.5 s).

## 8. Remaining limitations

- **cov 3 is a decoding limit on this channel, not a target.** About 207 strands per trial have fewer than 2 reads, and
  57 % of strands are lost with the full consensus. Neither profile's outer budget covers that.
- **cov 5 on v4-balanced** loses about 26 % of strands to 2-read clusters and to strands with fewer than 2 reads. Its
  outer budget is 20 %. Decoding 2-read clusters needs new evidence (for example a quality-guided chase), which needs
  founder approval first.
- Only one archive size and one unfitted channel were tested.
- Not yet run:
  - real-read decoding on D13 and CAS9 (phase 8; D3 characterisation is in `work/v7-nanodata`);
  - the phase 9 channel × coverage matrix;
  - the native edit-cost kernel for the polish.
- The ≥ 95 % frame-recovery-before-RS target is not met: cov 10 reaches 618 of 679 (91 %).

## 9. Reproducible commands

```
PYTHONPATH=src python tests/nanopore/reproduce.py --all --jobs 4
PYTHONPATH=src python -m pytest tests/nanopore
PYTHONPATH=src python experiments/v7/a-loss/aloss.py run --config experiments/v7/a-loss/config.json --jobs 4
PYTHONPATH=src python experiments/v7/a-cons/run.py heldout --jobs 4
PYTHONPATH=src python experiments/v7/a-rs/diag.py --cov 5 --seeds 82043-82055 --jobs 4
PYTHONPATH=src python experiments/v7/a-par/run.py heldout --jobs 4
```

## 10. Acceptance status

**Not accepted.** Results so far:
- The pre-registered criteria of A-CONS (cov 10) and A-PAR (cov 5 ≥ 36/40, cov 10 40/40, 0 false) are met on
  SIMULATED held-out seeds.
- 0 FALSE SUCCESS in every run.
- The decode is deterministic: native output equals the reference output.

Still required before V7 can claim nanopore-like decoding:
- the confirmatory V7 acceptance corpus (seeds ≥ 82100, pre-registered);
- the phase 9 matrix;
- real-read tests on D13 and CAS9;
- an ADEQUATE fitted nanopore model.
