# V15 CC-3: nanopore-like decode failures, root cause and fix (SIMULATED)

SIMULATED: every read comes from a software channel model (the D13-F1 nanopore-like stress model of V8-V11, or the
versioned `nanopore-like` model). No DNA was synthesised, stored or sequenced. Nothing here is a claim about real
nanopore data.

Pre-registration: [PREREG.md](PREREG.md), committed (c416349) before any listed run, plus amendment 1 (074f6dd)
committed before its seeds ran. Harness: `run.py` and `kaggle.sh`. Rows: `results/kaggle-cc3-*/rows.jsonl`. Combined
summary: `results/summary.json`.

## Root cause
- **10 MB (open since V11, jobs #82/#86).** The read-clustering stage needs slightly more candidate slot pairs than
  its budget allows. On the base tree the stage stopped and produced no cluster frame: seed 93100 used 577,675,344
  slot pairs against 577,669,600 allowed (an overshoot of 5,744), and the decode refused
  (`results/kaggle-cc3-m0/rows.jsonl`). The number of unplaced reads was not recorded in any committed results file.
  On the new tree the same seed requested 2,478,182,873 slot pairs, the bucket cap was lowered to 52 and 574,950,149
  pairs were used, so the stage continued (`results/kaggle-cc3-m0/rows.jsonl`). This was a budget artefact, not a
  channel limit.
- **1 MiB.** Two separate causes:
  1. The default decoder (no read clustering) and preset E leave rows short of the 64-of-80 row-code threshold.
  2. Preset F, which recovers most of those rows, existed only in Python experiments. The CLI and the config file
     could not select it.

## Fix (branch work/v15-cc3)
1. **Bucket cap.** Over the candidate budget, the clustering stage lowers the bucket cap to the largest cap whose
   slot pairs fit, instead of stopping.
   - The cap is computed from the bucket-size histogram, so it depends only on the input.
   - Set by `ClusterConfig.bucket_cap_floor`, default 2. Native cluster kernel ABI 6, byte-equal to the reference.
   - Cluster members are now grouped with one sort, not one scan per cluster.
   - Ported from work/nano-10mb (job 86).
   - Inert below the budget: at 1 MiB, E gives identical outcomes and failed groups on both trees (20/20 seeds).
   - Known open item: the cap selection can choose a bucket size that has no buckets, in which case the stage ends
     with zero candidate pairs; see **Limits** below (OPEN).
2. **Preset F fixes**, ported from work/nanopore-f2: hypothesis selection plus inner trials, and the RANSAC codeword
   scan of every failed frame. The evaluation 4/5 records came with them.
3. **Opt-in decoder preset `nanopore` (= F).** Selected with `vnxdna decode --cluster-preset nanopore` or the decode
   config key `cluster_preset`. It implies `--read-clustering fallback`. The default decode path does not run any of
   the changed code.

Not ported: the `v10-oligo300` strand profile. It does not affect these failures, and it is a Q1 / CC-5 / FZ-3
format-profile decision.

Every frame the clustering stage or F adds is still verified (inner RS + CRC-32). The container SHA-256 still decides
publication.

## Results (Kaggle CPU sessions, 4 vCPU, decoder workers 4; times are Kaggle times, not comparable with VPS times)
Trees: **base** = cfd6ee0 (V15 head before CC-3); **new** = c416349 / 074f6dd / 4e32e18. The only `src/` difference
between those three is a 13-line refactor of `decode_options` in `src/vnxdna/sdk/config.py` (preset plumbing), which
the harness does not execute.

**0 FALSE_SUCCESS over all 271 rows** (`results/summary.json`, `false_success_total: 0`; 271 rows in
`results/kaggle-cc3-*/rows.jsonl`).

### 10 MB, D13-F1, coverage 10, v4-balanced + column parity 128/4 (run M)
| seed | role | base E | new E | new F |
|---|---|---|---|---|
| 93100 | development (job 86) | EXPLICIT_FAILURE, clustering `budget_exceeded`, 193 s | **EXACT**, cap 52, 2022 s | **EXACT**, 7881 s |
| 93110 | held-out of job 86 (run there on V11 code) | EXPLICIT_FAILURE, `budget_exceeded`, 178 s | **EXACT**, cap 52, 1724 s | **EXACT**, 6982 s |
| 93111 | never run before | EXPLICIT_FAILURE, `budget_exceeded`, 182 s | **EXACT**, cap 52, 1739 s | **EXACT**, 6780 s |

- Peak RSS of the decode process: 5,838,464-5,859,984 KiB (5.57-5.59 GiB) on the six new-tree rows and
  5,192,048-5,197,328 KiB (4.95-4.96 GiB) on the three base rows (`results/kaggle-cc3-m*/rows.jsonl`). The harness
  records the maximum of `RUSAGE_SELF` and `RUSAGE_CHILDREN`, so this is the peak of the largest single process, not a
  sum over a process tree.
- Archive build plus read simulation (`prep_seconds`): 1136.1 s (seed 93100), 916.1 s (93110) and 911.9 s (93111) on
  the new tree, against 1160.0 s, 947.6 s and 953.4 s on the base tree (`results/kaggle-cc3-m*/rows.jsonl`).
- F is about 4x slower than E at 10 MB (7881/2022, 6982/1724 and 6780/1739 s, `results/kaggle-cc3-m*/rows.jsonl`). At
  1 MiB the two take about the same time (median decode 264.42 s for E and 269.13 s for F on the new tree,
  `results/kaggle-cc3-h*/rows.jsonl`).
- Three seeds support no rate claim.

### 1 MiB held-out, D13-F1, v4-balanced (no column parity), seeds 93120-93139 (run H)
| | base E | new E | base F | new F |
|---|---|---|---|---|
| exact | 1/20 | 1/20 | 13/20 | **15/20** (Wilson 95 % 0.53-0.89) |

- McNemar exact test, new F vs new E: 14 discordant seeds, all favouring F, p = 1.2e-4 (recomputed from
  `results/kaggle-cc3-h*/rows.jsonl`).
- No seed where base F or new E is exact and new F is not. New F additionally recovers 93123 and 93136.
- New F fails 93124 (row 399), 93126 (rows 136, 331), 93130 (327), 93131 (313) and 93135 (127). Each of these is
  one or two rows short.

### 1 MiB held-out with the existing opt-in column parity (stripe depth 128, 4 column-parity rows), seeds 93150-93169 (run C, amendment 1)
| | default | E | F |
|---|---|---|---|
| exact | 0/20 | **20/20** | **20/20** (Wilson 95 % 0.84-1.00 each) |

- Column parity adds about 3.9 % strands (32,837 strands in run H, 34,117 in run C).
- E needed column recovery on 18 of 20 seeds (0-5 rows each; the two seeds with 0 needed none).
- **Why the seeds changed:** run H showed the remaining F failures are each one or two rows short. V10 W3 had recovered
  10/10 of its seeds (93000-93009) with column parity, but that was a different evaluation. Amendment 1 therefore
  added a new held-out case on never-used seeds 93150-93169 instead of re-running any seen seed. The amendment was
  committed before those seeds ran.

### Reproduction and port check (development seeds, not evidence)
- **R** (base, 93090-93099): default 0/10, E 1/10, F 9/10. A local run of 93090 reproduced evaluation 5 exactly
  (E fails groups 195 and 402; F exact).
- **P** (new F, 93090-93109): 19/20, failing 93097 row 92. This is identical to evaluation 5 at the source commit.

### Regression (run G, seeds 93140-93142, base and new)
- **clean, illumina-like, indel** (p6e INDEL channel): every tree and configuration gives 3/3 EXACT.
- **Versioned `nanopore-like` model:** default 0/3 and E 0/3, F 3/3 on both trees.
  - The six default rows (3 per tree) are an explicit refusal: the committed rows record `status:
    "ERROR:VNXDecodeError"` with `output_sha256: null` and `failed_groups: null`, i.e. no output written
    (`results/kaggle-cc3-g/rows.jsonl`). The error code and the exit status are not recorded in the committed rows
    (a local reproduction for 93140 reported `NO_SUPERBLOCK` and exit 5; that local run is not a committed result).
    These are not crashes.
- The default decoder gives the same status on base and new for every row.
- The p6e benchmark workloads use the default decode path, which executes none of the changed code.

ASan/UBSan: **UNVERIFIED - no committed sanitiser log covers the CC-3 native changes.** The two kernels touched here
are the cluster kernel (C ABI 6 on the new tree, ABI 5 on the base tree, `src/vnxdna/native/c/cluster.c`; the committed
rows record `native.cluster_abi` 6 on new and 5 on base) and the outer-list kernel (C++ ABI 1,
`src/vnxdna/native/cpp/outerlist.cpp`, `VNX_OL_ABI 1`). An earlier version of this file gave the outer-list kernel the
cluster kernel's ABI number; that pairing was wrong. The nearest committed sanitiser logs are
`docs/security/v11-redteam/sanitizers-outerlist.txt` (2026-10-11, kernel source sha256 f7ca10c2..., an earlier
outer-list source) and `benchmarks/v7/native_cluster/results/sanitizers.txt` / `v10_w2_sanitizers.txt` (V7 kernels);
the CC-3 sources are sha256 315bbfab... (outer-list) and 0bc39b34... (cluster), so none of those logs was run against
them. The CC-3 review records a verbal report of a clean run on 2026-10-19 (`docs/V15_CC3_REVIEW.md`); it is not
backed by a committed log, so it is recorded as UNVERIFIED and no sanitiser result is claimed here.

## Recommendation
For nanopore-like reads:
- Encode with `--stripe-depth 128 --column-parity 4`.
- Decode with `--cluster-preset nanopore`, or with `--read-clustering fallback` plus E-style settings when time
  matters. At 10 MB, E is about 4x faster than F, and both were exact on all three seeds.

Without column parity, F recovers 15/20 held-out 1 MiB seeds. The remaining failures are consensus quality (D-3).

## Tests
- `tests/v7/test_read_clustering.py::test_candidate_pair_budget_lowers_the_bucket_cap_and_keeps_clustering` is the
  scaled proxy of the 10 MB budget path. It does not exist on base cfd6ee0: the base tree carries the opposite test
  (`test_candidate_pair_budget_stops_the_stage_and_is_reported`, `tests/v7/test_read_clustering.py:225` on base) and
  its `ClusterConfig` has no `bucket_cap_floor` field, so the base tree cannot be set up for the new path. On the new
  tree the test passes, and, as the CC-3 review records, removing the floor from the new tree makes it fail (the stage
  stops and the report has no `"stopped": false`).
- `tests/v11/test_candidate_cap_lowering.py`: reference semantics, native = reference.
- `tests/arch/test_outer_list.py`: F additions.
- `tests/v7/test_cluster_preset.py`: preset, config, CLI.

## Limits
- **OPEN (should-fix, recorded by the CC-3 review): the cap selection can pick a bucket size that has no buckets.**
  When the candidate-pair budget is exceeded, the stage lowers the bucket cap to the largest bucket size whose slot
  pairs fit, and it takes that size from the bucket-size histogram without checking that the histogram has an entry
  for it. If the selected size has no bucket, the kept set is empty, the stage ends with zero candidate pairs and no
  cluster frame, and the report still says "clustering continued" (`stopped: false`) - a silent drop, not a crash.
  Locations: `src/vnxdna/recovery/cluster/graph.py:150-162` (reference) and `src/vnxdna/native/c/cluster.c:236-248`
  (C kernel). This is not fixed in this branch; the codec change is left to a later decision.
- The 32 MB step of the scaling sweep (step 4 of CC-3 in `docs/V15_FINALIZATION_PLAN.md`) was not run.
- F's 10 MB decode time (about 2 h on 4 vCPU; 7881 s, `results/kaggle-cc3-m0/rows.jsonl`) is the next limit.
- All results are from simulated channels on Kaggle CPU sessions.
